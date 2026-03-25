"""Infrastructure manager — orchestrates the full Azure infrastructure lifecycle.

``InfrastructureManager`` is the single orchestration class that every CLI
subcommand delegates to.  Each public method maps 1-to-1 with a user-facing
action and internally coordinates one or more pillar components.

The manager is organised around three formal lifecycle pillars, each with its
own sub-package and a top-level entrypoint method:

* **Governance** (``govern()``) — policy enforcement, cost management, RBAC
  access review and least-privilege enforcement
* **Automation** (``automate()``) — formal pipeline (lint/validate/what-if/
  deploy), infrastructure lifecycle operations (deprovision/shift/modify/
  upgrade/scale), and integration with ``aos-client-sdk`` and ``aos-kernel``
* **Reliability** (``reliability_check()``) — drift detection, SLA-aware
  health monitoring, and DR readiness assessment

All mutating operations are audit-logged to JSON files under
``deployment/audit/``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from orchestrator.core.config import DeploymentConfig
from orchestrator.automation.lifecycle import LifecycleManager
from orchestrator.automation.pipeline import PipelineManager
from orchestrator.governance.cost_manager import CostManager
from orchestrator.governance.policy_manager import PolicyManager
from orchestrator.governance.rbac_manager import RbacManager
from orchestrator.integration.identity_client import KeyVaultIdentityStore, ManagedIdentityClient
from orchestrator.integration.kernel_bridge import KernelBridge
from orchestrator.integration.sdk_bridge import SDKBridge
from orchestrator.reliability.drift_detector import DriftDetector
from orchestrator.reliability.health_monitor import HealthMonitor, HealthStatus

if TYPE_CHECKING:
    from orchestrator.integration.azure_sdk_client import AzureSDKClient

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).resolve().parent.parent.parent / "audit"

# Patterns that identify a role-assignment permission warning during what-if.
# The deployment SP may hold *Contributor* but not *Owner* / *User Access
# Administrator*, causing Azure CLI to exit non-zero for role-assignment or
# policy-assignment resources.  Both are idempotent by name (role assignments
# use deterministic GUIDs; policy assignments use deterministic slugs), so
# treating these as warnings and continuing is safe when allow_warnings=True.
_RBAC_AUTH_PATTERNS = (
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Authorization/policyAssignments/write",
    "Authorization failed for template resource",
)


def _is_rbac_authorization_warning(error_text: str) -> bool:
    """Return ``True`` when *error_text* describes an RBAC permission warning.

    Applicable during ``what-if`` when the service principal lacks
    ``Microsoft.Authorization/roleAssignments/write``.
    """
    lower = error_text.lower()
    return any(p.lower() in lower for p in _RBAC_AUTH_PATTERNS)


def _contains_rbac_error(obj: Any) -> bool:
    """Recursively check whether *obj* contains an RBAC authorization error.

    Walks nested dicts / lists returned by ``az deployment operation group list``
    and returns ``True`` if any string leaf value matches an RBAC pattern.
    This avoids converting the object back to a JSON string purely for pattern
    matching, keeping the data-flow in the native Python dict representation.
    """
    if isinstance(obj, str):
        return _is_rbac_authorization_warning(obj)
    if isinstance(obj, dict):
        return any(_contains_rbac_error(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_rbac_error(item) for item in obj)
    return False

def _parse_what_if_output(output: str) -> dict[str, int]:
    """Parse ``az deployment group what-if --output json`` and count change types.

    Azure returns ``changeType`` as PascalCase (e.g. ``"Create"``, ``"NoChange"``).
    We normalise all values to lowercase with an explicit mapping so that case
    variations and the ``NoChange`` → ``no_change`` rename are handled consistently.

    Returns a mapping of change-type label → resource count.  Keys are always
    present (defaulting to 0): ``create``, ``modify``, ``no_change``,
    ``delete``, ``ignore``.
    """
    # Explicit mapping covers all Azure what-if changeType values.
    _CHANGE_TYPE_MAP: dict[str, str] = {
        "create": "create",
        "modify": "modify",
        "nochange": "no_change",
        "no_change": "no_change",
        "delete": "delete",
        "ignore": "ignore",
        "unsupported": "ignore",  # treat unsupported as ignored
        "deploy": "modify",       # re-deploy of existing resource ≈ modify
    }
    counts: dict[str, int] = {"create": 0, "modify": 0, "no_change": 0, "delete": 0, "ignore": 0}
    try:
        data = json.loads(output)
        for change in data.get("changes", []):
            raw = change.get("changeType", "").lower()
            key = _CHANGE_TYPE_MAP.get(raw)
            if key is not None:
                counts[key] += 1
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return counts


class InfrastructureManager:
    """Orchestrates Azure infrastructure deployments for AOS.

    All deployments are state-aware: the manager observes current
    infrastructure state via the Azure SDK before acting.  The OODA loop
    (Observe → Orient → Decide → Act) is the primary deployment path —
    ``deploy()`` performs state verification before and after the
    deployment pipeline.
    """

    def __init__(self, config: DeploymentConfig) -> None:
        self.config = config
        # Populated by _what_if() so deploy() can include counts in the audit.
        self._what_if_counts: dict[str, int] = {}
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        # Azure SDK client for closed-loop operations — initialised on demand.
        self._sdk_client: Optional[AzureSDKClient] = None

    def _get_sdk_client(self) -> Optional[AzureSDKClient]:
        """Return a cached :class:`AzureSDKClient` instance (or ``None``).

        Returns ``None`` only when ``subscription_id`` is not configured,
        which is expected for diagnostic/cleanup commands that don't need
        state verification.
        """
        if self._sdk_client is not None:
            return self._sdk_client
        if not self.config.subscription_id:
            return None
        from orchestrator.integration.azure_sdk_client import AzureSDKClient
        self._sdk_client = AzureSDKClient(
            self.config.subscription_id, self.config.resource_group
        )
        return self._sdk_client

    def _resolve_subscription_id(self) -> str:
        """Return the Azure subscription ID from config or environment.

        Checks ``config.subscription_id`` first; falls back to the
        ``AZURE_SUBSCRIPTION_ID`` environment variable.  Returns an empty
        string when neither source provides a value.
        """
        return self.config.subscription_id or os.environ.get(
            "AZURE_SUBSCRIPTION_ID", ""
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def smart_deploy(
        self,
        cost_threshold: float = 0.0,
        auto_approve: bool = False,
    ) -> bool:
        """OODA-loop closed-loop deployment — observe → orient → decide → act.

        Verifies current infrastructure state before deploying:

        1. **Observes** the current infrastructure state via Azure SDK.
        2. **Orients** by comparing actual vs desired state, factoring in
           cost data for frugal resource management.
        3. **Decides** the optimal action (deploy, skip, remediate, scale-down).
        4. **Acts** only when the decision warrants it, then verifies the outcome.

        Parameters
        ----------
        cost_threshold:
            Maximum acceptable monthly cost.  When exceeded, the loop
            recommends a ``scale_down`` action instead of deploying.
            Pass ``0`` to disable cost-gating.
        auto_approve:
            When ``True``, safe actions (skip, incremental_update) are
            executed automatically.  Destructive actions always require
            explicit approval.

        Returns
        -------
        bool
            ``True`` when the infrastructure reaches the desired state.
        """
        from orchestrator.core.ooda_loop import (
            DesiredState,
            OODALoop,
            RecommendedAction,
        )

        client = self._get_sdk_client()
        if client is None:
            logger.error(
                "Azure SDK client requires subscription_id for state verification"
            )
            return False

        print(f"🧠 Smart deploy (OODA loop) for {self.config.resource_group} "
              f"({self.config.environment}) in {self.config.location}")

        # Build desired state from governance config
        desired = DesiredState(
            max_monthly_cost=cost_threshold or self.config.governance.budget_amount,
            required_tags=self.config.governance.required_tags,
        )

        loop = OODALoop(
            client=client,
            desired_state=desired,
            cost_threshold=cost_threshold or self.config.governance.budget_amount,
            auto_approve=auto_approve,
        )

        # Run one OODA cycle
        include_cost = (
            cost_threshold > 0 or self.config.governance.budget_amount > 0
        )
        cycle = loop.run_cycle(include_cost=include_cost)

        # Print the cycle report
        print(loop.format_cycle_report(cycle))

        action = cycle.decision.recommended_action

        # Act based on the decision
        if action == RecommendedAction.SKIP:
            print("⏭️  OODA: desired state already achieved — skipping deployment")
            self._audit("deploy", {
                "status": "skipped",
                "action": action.value,
                "rationale": cycle.decision.rationale,
            })
            return True

        if action == RecommendedAction.SCALE_DOWN:
            print("📉 OODA: cost threshold exceeded — deployment blocked")
            print(f"   Current cost: ${cycle.orientation.current_cost:,.2f}")
            print(f"   Threshold:    ${cycle.orientation.cost_threshold:,.2f}")
            self._audit("deploy", {
                "status": "blocked_cost",
                "action": action.value,
                "current_cost": cycle.orientation.current_cost,
                "cost_threshold": cycle.orientation.cost_threshold,
            })
            return False

        if action == RecommendedAction.ALERT:
            print("🔔 OODA: infrastructure degraded — proceeding with caution")

        if action in (
            RecommendedAction.DEPLOY,
            RecommendedAction.INCREMENTAL_UPDATE,
            RecommendedAction.REMEDIATE,
            RecommendedAction.ALERT,
        ):
            # Proceed with the deployment pipeline
            print(f"🚀 OODA: proceeding with {action.value}")
            deploy_ok = self._run_pipeline()

            # Post-deploy: verify with another observation cycle
            if deploy_ok:
                print("\n🔍 OODA: post-deploy verification …")
                verify_cycle = loop.run_cycle(include_cost=False)
                if verify_cycle.orientation.state_matches_desired:
                    print("✅ OODA: post-deploy verification passed — desired state confirmed")
                else:
                    print("⚠️  OODA: post-deploy state drift detected — review recommended")

            self._audit("deploy", {
                "status": "success" if deploy_ok else "failed",
                "action": action.value,
                "rationale": cycle.decision.rationale,
            })
            return deploy_ok

        if action == RecommendedAction.BLOCK:
            print("🚫 OODA: safety constraint prevents deployment")
            self._audit("deploy", {
                "status": "blocked",
                "action": action.value,
                "rationale": cycle.decision.rationale,
            })
            return False

        # Fallback — shouldn't reach here
        return self._run_pipeline()

    def deploy(self) -> bool:
        """State-verified deployment: observe state → pipeline → verify outcome.

        Runs the OODA observe phase before and after the deployment
        pipeline to ensure state awareness.  When a subscription ID is
        configured, the method:

        1. Observes current state via the Azure SDK.
        2. Logs the pre-deploy state in the audit trail.
        3. Executes the pipeline (lint → validate → what-if → deploy → health-check).
        4. Verifies post-deploy state matches expectations.

        When no subscription ID is provided (e.g. in CI pipeline step mode),
        falls back to the raw pipeline without state verification.
        """
        print(f"🚀 Starting deployment to {self.config.resource_group} "
              f"({self.config.environment}) in {self.config.location}")

        # Pre-deploy: observe current state if SDK is available
        client = self._get_sdk_client()
        pre_snapshot = None
        if client is not None:
            print("\n📡 Pre-deploy: observing current infrastructure state …")
            try:
                pre_snapshot = client.observe(include_cost=False)
                print(f"  Resources: {pre_snapshot.total_resources} total, "
                      f"{pre_snapshot.healthy_resources} healthy")
            except Exception as exc:  # noqa: BLE001
                # Observation is advisory; failures should not block deployment.
                # The pipeline itself validates via what-if and health-check.
                logger.warning("Pre-deploy observation failed (non-blocking): %s", exc)

        ok = self._run_pipeline()
        if not ok:
            return False

        # Post-deploy: verify state
        if client is not None:
            print("\n🔍 Post-deploy: verifying infrastructure state …")
            try:
                post_snapshot = client.observe(include_cost=False)
                print(f"  Resources: {post_snapshot.total_resources} total, "
                      f"{post_snapshot.healthy_resources} healthy")
                new_count = post_snapshot.total_resources - (
                    pre_snapshot.total_resources if pre_snapshot else 0
                )
                if new_count > 0:
                    print(f"  📈 {new_count} new resource(s) provisioned")
            except Exception as exc:  # noqa: BLE001
                # Post-deploy verification is advisory; deployment already succeeded.
                logger.warning("Post-deploy verification failed (non-blocking): %s", exc)

        return True

    def _run_pipeline(self) -> bool:
        """Execute the raw deployment pipeline (no state verification).

        Pipeline steps: ensure-rg → lint → validate → what-if → deploy
        [→ health-check].
        """
        print(f"🚀 Starting deployment to {self.config.resource_group} "
              f"({self.config.environment}) in {self.config.location}")

        steps = [
            ("Resource Group", self._ensure_resource_group),
            ("Lint", self._lint),
            ("Validate", self._validate),
            ("What-If", self._what_if),
            ("Deploy", self._deploy),
        ]
        if not self.config.skip_health:
            steps.append(("Health-Check", self._health_check))

        for label, fn in steps:
            print(f"\n{'=' * 60}")
            print(f"  Step: {label}")
            print(f"{'=' * 60}")
            ok = fn()
            if not ok:
                if label in ("Lint", "Validate") and self.config.allow_warnings:
                    print(f"⚠️  {label} had warnings — continuing (--allow-warnings)")
                else:
                    print(f"❌ {label} failed")
                    self._audit("deploy", {"step": label, "status": "failed"})
                    return False
            print(f"✅ {label} succeeded")

        # --- Post-deploy lifecycle pillars ---
        # Governance and reliability hooks are advisory: their findings are
        # audit-logged and printed, but they do not fail the deployment itself.
        # To gate deployments on pillar results, call govern() / automate() /
        # reliability_check() as separate pipeline steps with explicit failure
        # conditions.
        if self.config.governance.enforce_policies:
            self._run_governance_pillar()
        if self.config.automation.deploy_function_apps or self.config.automation.sync_kernel_config:
            # Run only the SDK/kernel integration steps (pipeline already completed)
            auto = self.config.automation
            if auto.deploy_function_apps:
                bridge = SDKBridge(
                    resource_group=self.config.resource_group,
                    environment=self.config.environment,
                    subscription_id=self.config.subscription_id,
                    location=self.config.location,
                    app_names=auto.app_names or None,
                )
                bridge.deploy_function_apps()
            if auto.sync_kernel_config:
                kb = KernelBridge(
                    resource_group=self.config.resource_group,
                    subscription_id=self.config.subscription_id,
                )
                kb.validate_kernel_config(kb.extract_kernel_env())
        if self.config.reliability.enable_drift_detection or self.config.reliability.check_dr_readiness:
            self._run_reliability_pillar()

        self._audit("deploy", {
            "status": "success",
            "what_if_creates": self._what_if_counts.get("create", 0),
            "what_if_no_changes": self._what_if_counts.get("no_change", 0),
            "what_if_modifies": self._what_if_counts.get("modify", 0),
            "what_if_deletes": self._what_if_counts.get("delete", 0),
        })
        print("\n✅ Deployment completed successfully")
        return True

    def plan(self) -> bool:
        """Dry-run: lint → validate → what-if (no actual deployment)."""
        print(f"📋 Running plan for {self.config.resource_group} "
              f"({self.config.environment}) in {self.config.location}")

        for label, fn in [
            ("Resource Group", self._ensure_resource_group),
            ("Lint", self._lint),
            ("Validate", self._validate),
            ("What-If", self._what_if),
        ]:
            print(f"\n--- {label} ---")
            ok = fn()
            if not ok:
                # Resource group creation is always required; other steps may
                # be treated as warnings when --allow-warnings is set.
                if label != "Resource Group" and self.config.allow_warnings:
                    print(f"⚠️  {label} had warnings — continuing (--allow-warnings)")
                else:
                    print(f"❌ {label} failed")
                    return False
            print(f"✅ {label} passed")

        self._audit("plan", {
            "status": "success",
            "what_if_creates": self._what_if_counts.get("create", 0),
            "what_if_no_changes": self._what_if_counts.get("no_change", 0),
            "what_if_modifies": self._what_if_counts.get("modify", 0),
            "what_if_deletes": self._what_if_counts.get("delete", 0),
        })
        print("\n📋 Plan completed — no resources were modified")
        return True

    def status(self) -> bool:
        """Show deployment status for the resource group."""
        print(f"📊 Deployment status for {self.config.resource_group}")
        result = self._az([
            "deployment", "group", "list",
            "--resource-group", self.config.resource_group,
            "--output", "json",
        ])
        if result is None:
            return False
        deployments = json.loads(result)
        if not deployments:
            print("  No deployments found.")
            return True
        for dep in deployments[:10]:
            name = dep.get("name", "N/A")
            state = dep.get("properties", {}).get("provisioningState", "N/A")
            ts = dep.get("properties", {}).get("timestamp", "N/A")
            print(f"  {name}: {state} ({ts})")
        return True

    def monitor(self) -> bool:
        """Show health and metrics for deployed resources."""
        print(f"🔍 Monitoring resources in {self.config.resource_group}\n")
        checks = [
            ("Function Apps", self._monitor_function_apps),
            ("Storage Accounts", self._monitor_storage),
            ("Service Bus", self._monitor_servicebus),
            ("Application Insights", self._monitor_insights),
        ]
        for label, fn in checks:
            print(f"--- {label} ---")
            fn()
            print()
        return True

    def troubleshoot(self) -> bool:
        """Diagnose issues: deployment failures, activity logs, resource errors."""
        print(f"🔧 Troubleshooting {self.config.resource_group}\n")

        # Recent failed deployments
        print("--- Failed Deployments ---")
        result = self._az([
            "deployment", "group", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[?properties.provisioningState=='Failed']",
            "--output", "json",
        ])
        if result:
            failed = json.loads(result)
            if failed:
                for dep in failed:
                    name = dep.get("name", "N/A")
                    err = dep.get("properties", {}).get("error", {}).get("message", "No details")
                    print(f"  {name}: {err}")
            else:
                print("  No failed deployments found.")

        # Recent activity log errors
        print("\n--- Activity Log Errors (last 24h) ---")
        self._az([
            "monitor", "activity-log", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[?level=='Error'].{time:eventTimestamp, op:operationName.localizedValue, "
                       "status:status.localizedValue}",
            "--output", "table",
        ], print_output=True)

        # Resources in failed state
        print("\n--- Resources in Failed State ---")
        self._az([
            "resource", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[?provisioningState=='Failed'].{name:name, type:type}",
            "--output", "table",
        ], print_output=True)

        return True

    def delete(self, confirm: bool = True) -> bool:
        """Delete the resource group."""
        if confirm:
            answer = input(
                f"⚠️  Delete resource group '{self.config.resource_group}'? [y/N]: "
            )
            if answer.strip().lower() != "y":
                print("Aborted.")
                return False

        print(f"🗑️  Deleting resource group {self.config.resource_group} …")
        result = self._az([
            "group", "delete",
            "--name", self.config.resource_group,
            "--yes", "--no-wait",
            "--output", "json",
        ])
        if result is not None:
            self._audit("delete", {"resource_group": self.config.resource_group})
            print("✅ Deletion initiated (--no-wait)")
            return True
        return False

    def list_resources(self) -> bool:
        """List all resources in the resource group."""
        print(f"📦 Resources in {self.config.resource_group}\n")
        result = self._az([
            "resource", "list",
            "--resource-group", self.config.resource_group,
            "--output", "json",
        ])
        if result is None:
            return False
        resources = json.loads(result)
        if not resources:
            print("  No resources found.")
            return True
        for res in resources:
            name = res.get("name", "N/A")
            rtype = res.get("type", "N/A")
            loc = res.get("location", "N/A")
            state = res.get("provisioningState", "N/A")
            print(f"  {name} ({rtype}) — {loc} [{state}]")
        return True

    # ------------------------------------------------------------------
    # Governance pillar — public entrypoint
    # ------------------------------------------------------------------

    def govern(self) -> bool:
        """Run the full Governance lifecycle pillar.

        Executes in order:
        1. Policy compliance evaluation
        2. Required-tag enforcement (when configured)
        3. Budget status check (when configured)
        4. Privileged-access review (when configured)

        Returns ``True`` when all checks pass (or produce only warnings).
        """
        print(f"\n{'=' * 60}")
        print("  Pillar: Governance")
        print(f"{'=' * 60}")
        return self._run_governance_pillar()

    # ------------------------------------------------------------------
    # Reliability pillar — public entrypoint
    # ------------------------------------------------------------------

    def reliability_check(self) -> bool:
        """Run the full Reliability lifecycle pillar.

        Executes in order:
        1. Enhanced health monitoring with SLA compliance
        2. Drift detection (template what-if or manifest comparison)
        3. DR readiness assessment (when configured)

        Returns ``True`` when the environment is healthy and SLA-compliant.
        """
        print(f"\n{'=' * 60}")
        print("  Pillar: Reliability")
        print(f"{'=' * 60}")
        return self._run_reliability_pillar()

    # ------------------------------------------------------------------
    # Automation pillar — public entrypoint
    # ------------------------------------------------------------------

    def automate(self) -> bool:
        """Run the full Automation lifecycle pillar.

        Executes in order:
        1. Formal pipeline (lint → validate → what-if → deploy → health-check)
           using :class:`~orchestrator.automation.pipeline.PipelineManager`
        2. Function App deployment via the SDK bridge (when configured)
        3. Kernel config sync to all Function Apps (when configured)
        4. Infrastructure lifecycle operations (deprovision/shift/modify/
           upgrade/scale) when ``enable_lifecycle_ops`` is set

        Returns ``True`` when all configured steps succeed.
        """
        print(f"\n{'=' * 60}")
        print("  Pillar: Automation")
        print(f"{'=' * 60}")
        return self._run_automation_pillar()

    # ------------------------------------------------------------------
    # Single-step public entrypoints
    # ------------------------------------------------------------------

    def ensure_rg(self) -> bool:
        """Create or confirm the resource group and return the result."""
        return self._ensure_resource_group()

    def lint(self) -> bool:
        """Lint the Bicep template.

        Returns ``True`` even on failure when ``config.allow_warnings`` is set,
        mirroring the behaviour of the full :meth:`deploy` pipeline.
        """
        ok = self._lint()
        if not ok and self.config.allow_warnings:
            print("⚠️  Lint had warnings — continuing (--allow-warnings)")
            return True
        return ok

    def validate(self) -> bool:
        """Validate the ARM template.

        Returns ``True`` even on failure when ``config.allow_warnings`` is set,
        mirroring the behaviour of the full :meth:`deploy` pipeline.
        """
        ok = self._validate()
        if not ok and self.config.allow_warnings:
            print("⚠️  Validate had warnings — continuing (--allow-warnings)")
            return True
        return ok

    def what_if(self) -> bool:
        """Run what-if analysis and write results to the audit log."""
        ok = self._what_if()
        if ok:
            self._audit("what-if", {
                "status": "success",
                "what_if_creates": self._what_if_counts.get("create", 0),
                "what_if_no_changes": self._what_if_counts.get("no_change", 0),
                "what_if_modifies": self._what_if_counts.get("modify", 0),
                "what_if_deletes": self._what_if_counts.get("delete", 0),
            })
        return ok

    def deploy_bicep(self) -> bool:
        """Run just the ARM deployment (``az deployment group create``).

        Caller is responsible for running :meth:`ensure_rg`, :meth:`validate`,
        and :meth:`what_if` before invoking this method.
        """
        return self._deploy()

    def health_check(self) -> bool:
        """Run the post-deployment health check and return the result."""
        return self._health_check()

    def deploy_function_apps(self) -> bool:
        """Deploy Python Function Apps via the SDK bridge.

        Connects to the ``aos-client-sdk`` ``AOSDeployer`` and deploys each
        AOS Function App module to its pre-provisioned Azure Function App.
        Returns ``True`` when all apps deploy successfully (or are gracefully
        skipped when the SDK is unavailable).  Returns ``False`` when one or
        more apps fail.

        Caller is responsible for running :meth:`deploy_bicep_foundation` and
        :meth:`deploy_bicep_function_apps` (or :meth:`deploy_bicep`) first so
        that the target Function Apps exist in Azure before code deployment begins.
        """
        bridge = SDKBridge(
            resource_group=self.config.resource_group,
            environment=self.config.environment,
            subscription_id=self.config.subscription_id,
            location=self.config.location,
        )
        print("  📦 Deploying Python Function Apps via SDK bridge …")
        statuses = bridge.deploy_function_apps()
        all_ok = True
        for s in statuses:
            icon = "✅" if s.status in ("succeeded", "skipped") else "❌"
            print(f"  {icon} {s.app_name}: {s.status}" + (f" — {s.error}" if s.error else ""))
            if s.status == "failed":
                all_ok = False
        return all_ok

    def sync_kernel_config(self) -> bool:
        """Sync AOS kernel environment variables to all Function Apps.

        Reads infrastructure outputs from the deployed resource group and
        pushes the canonical set of kernel env vars to every Function App.
        Returns ``True`` when the config is valid and the sync completes,
        ``False`` when required vars are missing or the sync fails.
        """
        kb = KernelBridge(
            resource_group=self.config.resource_group,
            subscription_id=self.config.subscription_id,
        )
        print("  🔗 Syncing AOS kernel configuration …")
        env_vars = kb.extract_kernel_env()
        result = kb.validate_kernel_config(env_vars)
        missing = result.get("missing", [])
        if missing:
            print(f"  ⚠️  Missing kernel env vars: {', '.join(missing)}")
            return False
        print(f"  ✅ Kernel config synced ({len(env_vars)} vars)")
        return True

    def fetch_identity_client_ids(self) -> bool:
        """Fetch Function App Managed Identity client IDs and store in Key Vault.

        Uses :class:`~orchestrator.integration.identity_client.ManagedIdentityClient`
        (wrapping ``ManagedServiceIdentityClient`` from ``azure.mgmt.msi``) to
        retrieve each Function App's User-Assigned Managed Identity ``clientId``
        programmatically, then stores every value in Azure Key Vault via
        :class:`~orchestrator.integration.identity_client.KeyVaultIdentityStore`.

        Key Vault becomes the **state-sharing mechanism** between this Bicep
        repository and the code repositories: after this step completes, each
        code repository can retrieve its own ``AZURE_CLIENT_ID`` from Key Vault
        using ``az keyvault secret show`` (authenticated via GitHub OIDC),
        removing the need for manual secret management.

        Returns ``True`` when all identity client IDs are fetched and stored
        successfully.  Returns ``False`` when Key Vault is unavailable or no
        managed identities are found.

        Prerequisites: Phase 1 (foundation / Key Vault) and Phase 4 (Function Apps /
        managed identities) must be deployed first.
        """
        # Resolve Key Vault name using the same uniqueString formula as main-modular.bicep.
        import hashlib
        import json
        import subprocess

        print("  🔑 Fetching Function App Managed Identity client IDs …")

        # Query Key Vault name from the resource group (deployed by Phase 1).
        try:
            result = subprocess.run(
                [
                    "az", "keyvault", "list",
                    "--resource-group", self.config.resource_group,
                    "--query", "[0].properties.vaultUri",
                    "--output", "tsv",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            vault_url = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("Could not resolve Key Vault URL: %s", exc)
            print(f"  ⚠️  Could not resolve Key Vault URL: {exc}")
            return False

        if not vault_url:
            print("  ⚠️  No Key Vault found in resource group — is Phase 1 deployed?")
            return False

        subscription_id = self._resolve_subscription_id()
        if not subscription_id:
            print(
                "  ❌ subscription_id is required — pass --subscription-id "
                "or set the AZURE_SUBSCRIPTION_ID environment variable"
            )
            logger.error(
                "fetch_identity_client_ids: subscription_id is empty; "
                "pass --subscription-id or set AZURE_SUBSCRIPTION_ID"
            )
            return False

        msi_client = ManagedIdentityClient(
            subscription_id=subscription_id,
            resource_group=self.config.resource_group,
        )
        kv_store = KeyVaultIdentityStore(vault_url=vault_url)

        identities = msi_client.list_function_app_identities(prefix="id-")
        if not identities:
            print("  ⚠️  No managed identities found — is Phase 4 deployed?")
            return False

        all_ok = True
        for info in identities:
            # Identity names follow the pattern id-{app_name}-{environment}.
            # Strip the leading "id-" prefix and trailing "-{environment}" suffix
            # to reconstruct the app_name and environment for the KV secret name.
            parts = info.name.split("-")
            if len(parts) < 3:
                logger.warning("Unexpected identity name format: %s", info.name)
                continue
            env = parts[-1]
            app_name = "-".join(parts[1:-1])

            if not info.client_id:
                print(f"  ⚠️  No client ID for {info.name} — skipping")
                all_ok = False
                continue

            try:
                kv_store.set_client_id(app_name, env, info.client_id)
                print(f"  ✅ {info.name}: stored clientId in Key Vault "
                      f"(secret: clientid-{app_name}-{env})")
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Failed to store client ID for %s: %s", info.name, exc)
                print(f"  ❌ {info.name}: failed to store — {exc}")
                all_ok = False

        if all_ok:
            print(f"  ✅ All {len(identities)} client IDs stored in Key Vault: {vault_url}")
        return all_ok

    # ------------------------------------------------------------------
    # Granular Bicep phase deployment entrypoints
    # ------------------------------------------------------------------

    def deploy_bicep_foundation(self) -> bool:
        """Deploy Phase 1 — Foundation (monitoring, storage, serviceBus, keyVault).

        Deploys the core platform resources that every other Bicep phase depends on.
        Safe to run first and independently.
        """
        return self._deploy_phase(
            "deployment/phases/01-foundation.bicep",
            "foundation",
            include_location_ml=False,
        )

    def deploy_bicep_ai_services(self) -> bool:
        """Deploy Phase 2 — AI Services (aiServices, aiHub, aiProject).

        Requires Phase 1 (foundation) to be deployed first.
        """
        return self._deploy_phase("deployment/phases/02-ai-services.bicep", "ai-services")

    def deploy_bicep_ai_apps(self) -> bool:
        """Deploy Phase 3 — AI Applications (loraInference, foundryApps, aiGateway, a2aConnections).

        Requires Phase 2 (ai-services) to be deployed first.
        """
        return self._deploy_phase("deployment/phases/03-ai-applications.bicep", "ai-apps")

    def deploy_bicep_function_apps(self) -> bool:
        """Deploy Phase 4 — Function Apps (functionApps, mcpServerFunctionApps).

        Requires Phase 1 (foundation) to be deployed first.
        """
        return self._deploy_phase(
            "deployment/phases/04-function-apps.bicep",
            "function-apps",
            include_location_ml=False,
        )

    def deploy_bicep_governance(self) -> bool:
        """Deploy Phase 5 — Governance (policy assignments, cost budget).

        Both resources are conditional and may result in no-op deployments when
        ``enableGovernancePolicies=false`` and ``monthlyBudgetAmount=0``.
        Independent of other phases — safe to run at any time.
        """
        return self._deploy_phase(
            "deployment/phases/05-governance.bicep",
            "governance",
            include_location=False,
            include_location_ml=False,
            include_tags=False,
        )

    # ------------------------------------------------------------------
    # Private helpers — three-pillar lifecycle
    # ------------------------------------------------------------------

    def _run_automation_pillar(self) -> bool:
        """Execute the Automation pillar steps."""
        auto = self.config.automation
        all_ok = True

        # 1. Formal pipeline via PipelineManager
        pm = PipelineManager(
            resource_group=self.config.resource_group,
            environment=self.config.environment,
            location=self.config.location,
            template=self.config.template,
            parameters_file=self.config.parameters_file,
            location_ml=self.config.location_ml,
            git_sha=self.config.git_sha,
            subscription_id=self.config.subscription_id,
        )
        pipeline_ok = pm.full_deploy(
            allow_warnings=self.config.allow_warnings,
            skip_health=self.config.skip_health,
        )
        if not pipeline_ok:
            self._audit("automate", {"step": "pipeline", "status": "failed"})
            return False

        # 2. SDK bridge — deploy Function Apps
        if auto.deploy_function_apps:
            bridge = SDKBridge(
                resource_group=self.config.resource_group,
                environment=self.config.environment,
                subscription_id=self.config.subscription_id,
                location=self.config.location,
                app_names=auto.app_names or None,
            )
            print("\n  📦 Deploying Function Apps via SDK bridge …")
            statuses = bridge.deploy_function_apps()
            failed_apps = [s.app_name for s in statuses if s.status == "failed"]
            if failed_apps:
                print(f"  ⚠️  {len(failed_apps)} app(s) failed: {', '.join(failed_apps)}")
                all_ok = False

        # 3. Kernel config sync
        if auto.sync_kernel_config:
            kb = KernelBridge(
                resource_group=self.config.resource_group,
                subscription_id=self.config.subscription_id,
            )
            print("\n  🔗 Syncing kernel config …")
            env_vars = kb.extract_kernel_env()
            kb.validate_kernel_config(env_vars)

        self._audit("automate", {"status": "ok" if all_ok else "warnings"})
        return all_ok

    def _run_governance_pillar(self) -> bool:
        """Execute the Governance pillar steps."""
        gov = self.config.governance
        pm = PolicyManager(self.config.resource_group, self.config.subscription_id)
        cm = CostManager(self.config.resource_group, self.config.subscription_id)

        all_ok = True

        # 1. Policy compliance
        summary = pm.evaluate_compliance()
        if summary["non_compliant"] > 0:
            print(f"  ⚠️  {summary['non_compliant']} non-compliant policy state(s)")
            all_ok = False

        # 2. Required tag enforcement
        if gov.required_tags:
            missing = pm.enforce_required_tags(gov.required_tags)
            if any(missing.values()):
                all_ok = False

        # 3. Budget status
        if gov.budget_amount > 0:
            alerts = cm.check_budget_alerts()
            if alerts:
                print(f"  ⚠️  Budget alert(s): {', '.join(alerts)}")

        # 4. RBAC access review
        if gov.review_rbac:
            rm = RbacManager(self.config.resource_group, self.config.subscription_id)
            findings = rm.review_privileged_access()
            if findings:
                print(f"  ⚠️  {len(findings)} privileged-access finding(s)")

        self._audit("govern", {"status": "ok" if all_ok else "warnings", **summary})
        return all_ok

    def _run_reliability_pillar(self) -> bool:
        """Execute the Reliability pillar steps."""
        rel = self.config.reliability
        hm = HealthMonitor(self.config.resource_group, self.config.environment)
        dd = DriftDetector(self.config.resource_group, self.config.subscription_id)

        # 1. Health + SLA compliance
        overall, _ = hm.check_all()
        sla = hm.check_sla_compliance()
        healthy = overall in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

        # 2. Drift detection
        drift_findings: list[Any] = []
        if rel.enable_drift_detection:
            if rel.drift_manifest:
                drift_findings = dd.detect_drift_from_manifest(rel.drift_manifest)
            elif self.config.template:
                drift_findings = dd.detect_drift(
                    self.config.template,
                    self.config.parameters_file,
                )

        # 3. DR readiness
        dr: dict[str, Any] = {}
        if rel.check_dr_readiness:
            dr = hm.check_disaster_recovery_readiness()

        self._audit("reliability", {
            "health": overall.value,
            "sla_compliant": sla["compliant"],
            "drift_findings": len(drift_findings),
            "dr_ready": dr.get("ready", None),
        })
        return healthy and sla["compliant"]

    # ------------------------------------------------------------------
    # Private helpers — deployment pipeline
    # ------------------------------------------------------------------

    def _ensure_resource_group(self) -> bool:
        """Create the resource group if it does not already exist.

        ``az deployment group`` commands (validate, what-if, create) all require
        the target resource group to exist before they can run.  ``az group
        create`` is idempotent — when the group already exists it returns the
        existing group unchanged without modifying its location.

        Returns:
            True if the resource group exists or was created successfully,
            False if creation failed (e.g. insufficient permissions).
        """
        print(f"  📦 Ensuring resource group '{self.config.resource_group}' "
              f"in '{self.config.location}'…")
        result = self._run([
            "az", "group", "create",
            "--name", self.config.resource_group,
            "--location", self.config.location,
            "--output", "json",
        ])
        if result.returncode != 0:
            print(
                f"  ❌ Could not ensure resource group '{self.config.resource_group}': "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return True

    def _lint(self) -> bool:
        """Run ``az bicep build`` to lint the template."""
        if not self.config.template:
            print("  No template specified; skipping lint.")
            return True
        result = self._run(["az", "bicep", "build", "--file", self.config.template], stream=True)
        if result.returncode != 0:
            print("  Lint failed — see output above.", file=sys.stderr)
        return result.returncode == 0

    def _validate(self) -> bool:
        """Run ``az deployment group validate``.

        ``--output none`` suppresses the raw ARM-template JSON blob that Azure
        CLI would otherwise emit on success; validation errors still surface on
        stderr.  A short success or failure line is printed for CI readability.
        """
        cmd = self._deployment_cmd("validate", output_format="none")
        result = self._run(cmd, stream=True)
        if result.returncode != 0:
            print("  Validate failed — see output above.", file=sys.stderr)
            return False
        print("  ✅ Template is valid")
        return True

    def _what_if(self) -> bool:
        """Run ``az deployment group what-if``.

        Azure CLI 2.57+ returns exit code 2 when changes are detected and
        exit code 0 when no changes are detected.  Both are success outcomes;
        only exit code 1 (or any other non-zero, non-2 value) indicates a
        genuine error — unless it is solely an RBAC permission warning for
        ``Microsoft.Authorization/roleAssignments/write``, which is treated as
        a non-fatal warning so the deploy stage can still proceed.
        """
        cmd = self._deployment_cmd("what-if")
        result = self._run(cmd)
        if result.returncode == 0:
            self._what_if_counts = _parse_what_if_output(result.stdout)
            self._print_what_if_summary()
            return True
        if result.returncode == 2:
            # Exit code 2 means "changes detected" — not an error.
            print("  ⚠️  What-If: changes detected (resources will be created/modified/deleted)")
            self._what_if_counts = _parse_what_if_output(result.stdout)
            self._print_what_if_summary()
            return True
        # Check for RBAC permission warning before treating as a hard failure.
        error_detail = (result.stderr or result.stdout or "no details available").strip()
        if _is_rbac_authorization_warning(error_detail):
            print(
                "  ⚠️  What-If: role assignment preview skipped — the service principal lacks "
                "'Microsoft.Authorization/roleAssignments/write'. "
                "Existing role assignments will be preserved; new ones will be validated at deploy time.",
                file=sys.stderr,
            )
            return True
        # Any other non-zero exit code is a genuine failure; surface the detail.
        print(f"  What-If error: {error_detail}", file=sys.stderr)
        return False

    def _print_what_if_summary(self) -> None:
        """Print a one-line summary of what-if change counts.

        Ignored resources are excluded from the total and not shown, as they
        add noise without meaningful deployment information.
        """
        c = self._what_if_counts
        # Exclude "ignore" from the total — those resources are not touched.
        total = c["create"] + c["modify"] + c["no_change"] + c["delete"]
        if total > 0:
            print(
                f"  📊 What-If Summary: +{c['create']} create, "
                f"~{c['modify']} modify, "
                f"={c['no_change']} no-change, "
                f"-{c['delete']} delete"
            )

    def _deploy(self) -> bool:
        """Run ``az deployment group create`` with streaming output for module-wise feedback.

        Streaming (no ``capture_output``) lets the Azure CLI emit per-resource status
        lines in real-time so that each Bicep module's provisioning state is visible
        in the GitHub Actions workflow log as it progresses.
        """
        # Build deploy command without --output json to avoid a large JSON blob at the end;
        # per-resource progress is streamed directly to the workflow log.
        cmd = self._deployment_cmd("create", output_format="none")
        print(
            f"  🚀 Starting ARM deployment to '{self.config.resource_group}' "
            f"— per-module progress streamed below…"
        )
        result = self._run(cmd, stream=True)
        if result.returncode != 0:
            print(
                f"  ❌ Deployment failed (exit code {result.returncode}) — see output above.",
                file=sys.stderr,
            )
            return False
        return True

    def _health_check(self) -> bool:
        """Verify key resources exist and are in a healthy state."""
        result = self._az([
            "resource", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[].{name:name, state:provisioningState}",
            "--output", "json",
        ])
        if result is None:
            return False
        resources = json.loads(result)
        all_ok = True
        for res in resources:
            state = res.get("state", "Unknown")
            if state != "Succeeded":
                print(f"  ⚠️  {res.get('name')}: {state}")
                all_ok = False
            else:
                print(f"  ✅ {res.get('name')}: {state}")
        return all_ok

    # ------------------------------------------------------------------
    # Private helpers — monitoring
    # ------------------------------------------------------------------

    def _monitor_function_apps(self) -> None:
        result = self._az([
            "functionapp", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[].{name:name, state:state, hostName:defaultHostName}",
            "--output", "json",
        ])
        if not result:
            print("  No Function Apps found.")
            return
        for app in json.loads(result):
            print(f"  {app.get('name')}: {app.get('state')} — {app.get('hostName')}")

    def _monitor_storage(self) -> None:
        result = self._az([
            "storage", "account", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[].{name:name, status:statusOfPrimary, location:primaryLocation}",
            "--output", "json",
        ])
        if not result:
            print("  No Storage Accounts found.")
            return
        for sa in json.loads(result):
            print(f"  {sa.get('name')}: {sa.get('status')} in {sa.get('location')}")

    def _monitor_servicebus(self) -> None:
        result = self._az([
            "servicebus", "namespace", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[].{name:name, status:status}",
            "--output", "json",
        ])
        if not result:
            print("  No Service Bus namespaces found.")
            return
        for ns in json.loads(result):
            print(f"  {ns.get('name')}: {ns.get('status')}")

    def _monitor_insights(self) -> None:
        result = self._az([
            "monitor", "app-insights", "component", "show",
            "--resource-group", self.config.resource_group,
            "--query", "[].{name:name, instrumentationKey:instrumentationKey}",
            "--output", "json",
        ])
        if not result:
            print("  No Application Insights found.")
            return
        for ai in json.loads(result):
            print(f"  {ai.get('name')}: key={ai.get('instrumentationKey', 'N/A')}")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    _SHA_RE = re.compile(r"^[a-fA-F0-9]{7,40}$")

    def _deploy_phase(
        self,
        phase_template: str,
        phase_name: str,
        *,
        include_location: bool = True,
        include_location_ml: bool = True,
        include_tags: bool = True,
    ) -> bool:
        """Deploy a phase-specific Bicep template as a named ARM incremental deployment.

        Each phase runs ``az deployment group create`` against the given template
        with inline parameter overrides scoped to the parameters that the specific
        phase template actually declares.  Bicep ``.bicepparam`` parameter files
        are **not** used for phase deployments — phases use template defaults for
        all parameters not overridden inline.

        After the ARM deployment completes (success **or** failure), the method
        queries ``az deployment operation group list`` to display a per-module
        provisioning status table — the "proper azure-provisioning status reporting"
        required by the phase-based workflow.

        Parameters
        ----------
        phase_template:
            Relative path to the Bicep phase template (e.g.
            ``"deployment/phases/01-foundation.bicep"``).
        phase_name:
            Short slug used in the ARM deployment name (e.g. ``"foundation"``).
            The full deployment name will be ``phase-<phase_name>-<environment>``.
        include_location:
            When ``True`` (default), the ``location`` override is added.  Set to
            ``False`` for phases whose Bicep template has no ``location`` parameter
            (e.g. Phase 5 — Governance).
        include_location_ml:
            When ``True`` (default), the ``locationML`` override is added when
            ``config.location_ml`` is non-empty.  Set to ``False`` for phases
            whose Bicep template has no ``locationML`` parameter (e.g. Phase 1 —
            Foundation and Phase 4 — Function Apps).
        include_tags:
            When ``True`` (default), automatically appends a ``tags`` override
            with the git SHA when ``config.git_sha`` is set.  Set to ``False``
            for phase templates that do not define a ``tags`` parameter (e.g.,
            Phase 5 — Governance).

        Returns
        -------
        bool
            ``True`` when the ARM deployment exits zero; ``False`` otherwise.
        """
        deployment_name = f"phase-{phase_name}-{self.config.environment}"
        cmd = [
            "az", "deployment", "group", "create",
            "--resource-group", self.config.resource_group,
            "--template-file", phase_template,
            "--name", deployment_name,
            "--output", "none",
        ]
        # Inline parameter overrides — only include params supported by this phase's template.
        overrides: list[str] = [f"environment={self.config.environment}"]
        if include_location:
            overrides.append(f"location={self.config.location}")
        if include_location_ml and self.config.location_ml:
            overrides.append(f"locationML={self.config.location_ml}")
        if include_tags and self.config.git_sha and self._SHA_RE.match(self.config.git_sha):
            overrides.append(f'tags={{"gitSha":"{self.config.git_sha}"}}')
        cmd += ["--parameters"] + overrides

        # Display resource group prominently so it is clearly visible in CI output.
        print(f"\n  📦 Resource Group: {self.config.resource_group}")
        print(f"  🏗️  Phase '{phase_name}': deploying '{phase_template}' …")

        # Capture output (rather than streaming) so that RBAC authorization
        # errors in stderr can be inspected when allow_warnings=True.
        # az deployment group create --output none produces minimal stdout;
        # the per-module status table below provides the real-time progress.
        result = self._run(cmd)
        if result.stdout and result.stdout.strip():
            print(result.stdout)
        if result.stderr and result.stderr.strip():
            print(result.stderr, file=sys.stderr)

        succeeded = result.returncode == 0
        if not succeeded:
            print(
                f"  ❌ Phase '{phase_name}' failed (exit code {result.returncode})",
                file=sys.stderr,
            )
            # When allow_warnings=True, treat RBAC authorization failures (role
            # assignments or policy assignments) as non-fatal warnings.  The SP
            # may hold Contributor but lack the User Access Administrator /
            # Policy Contributor role needed to write these resources.
            if self.config.allow_warnings:
                error_text = (result.stderr or "") + (result.stdout or "")
                if _is_rbac_authorization_warning(error_text):
                    # Direct RBAC error in the outer deployment (e.g. Phase 5 policy
                    # assignments where the error surfaces in the top-level stderr).
                    print(
                        f"  ⚠️  Phase '{phase_name}' — RBAC authorization warning detected; "
                        f"continuing (allow_warnings=True).",
                    )
                    succeeded = True
                elif self._all_nested_failures_are_rbac(deployment_name):
                    # Nested-deployment RBAC error (e.g. Phase 4 role assignments
                    # inside functionapp.bicep modules).  The outer error is a generic
                    # "DeploymentFailed" that does not expose the RBAC root cause; we
                    # query each failed sub-deployment's operation statusMessage to
                    # confirm every failure is RBAC-only before treating it as a warning.
                    print(
                        f"  ⚠️  Phase '{phase_name}' — all nested failures are RBAC authorization "
                        f"warnings; continuing (allow_warnings=True).",
                    )
                    succeeded = True
        else:
            print(f"  ✅ Phase '{phase_name}' ARM deployment completed")

        # Always query and display per-module provisioning status for diagnostics.
        self._query_phase_deployment_status(deployment_name)
        return succeeded

    def _query_phase_deployment_status(self, deployment_name: str) -> bool:
        """Query ARM deployment operations and print a per-module provisioning status table.

        After each phase deployment (success or failure), this method calls
        ``az deployment operation group list`` to surface each Bicep module's
        provisioning state from Azure.  This is the "proper azure-provisioning status
        reporting" — each module (e.g. ``monitoring-aos-staging``) gets an
        individual ✅/❌/⏳ status line.

        Parameters
        ----------
        deployment_name:
            The ARM deployment name to query (e.g. ``"phase-foundation-staging"``).

        Returns
        -------
        bool
            ``True`` when all reported operations are in ``"Succeeded"`` state.
        """
        result = self._az([
            "deployment", "operation", "group", "list",
            "--resource-group", self.config.resource_group,
            "--name", deployment_name,
            "--query", (
                "[?properties.targetResource != null]"
                ".{name:properties.targetResource.resourceName,"
                "type:properties.targetResource.resourceType,"
                "state:properties.provisioningState}"
            ),
            "--output", "json",
        ])
        if result is None:
            # The deployment may not exist yet (e.g. during a dry-run / test).
            return True
        try:
            ops = json.loads(result)
        except json.JSONDecodeError:
            return True
        if not ops:
            return True

        print(f"\n  📊 Azure Provisioning Status — {deployment_name}")
        print(f"  {'Module':<45} {'Type':<28} {'State'}")
        print(f"  {'-'*45} {'-'*28} {'-'*12}")
        all_ok = True
        for op in sorted(ops, key=lambda x: x.get("name", "")):
            name = op.get("name") or "—"
            state = op.get("state") or "Unknown"
            rtype = (op.get("type") or "").split("/")[-1] or "—"
            if state == "Succeeded":
                icon = "✅"
            elif state == "Failed":
                icon = "❌"
                all_ok = False
            else:
                icon = "⏳"
            print(f"  {icon} {name:<44} {rtype:<28} {state}")
        return all_ok

    def _all_nested_failures_are_rbac(self, deployment_name: str) -> bool:
        """Return ``True`` when every failed operation in *deployment_name* is RBAC-only.

        Used to extend the ``allow_warnings`` RBAC detection to phases that deploy
        RBAC resources through **nested** module deployments (e.g. Phase 4 where
        ``functionapp.bicep`` creates role assignments inside each module deployment).
        In these cases the outer ``az deployment group create`` stderr only shows a
        generic ``DeploymentFailed`` message — the actual RBAC root cause is buried in
        each sub-deployment's ``statusMessage``.

        The method queries ``az deployment operation group list`` for the phase
        deployment and inspects the ``statusMessage`` of each failed operation.
        Azure embeds the inner error (including ``code`` and ``message``) in the
        top-level operation's ``statusMessage.error.details`` array when the failure
        originated inside a nested deployment, so a single query is sufficient to
        surface the RBAC root cause without recursing into sub-deployments.

        Returns ``False`` when:
        * the deployment does not exist (query returns nothing),
        * there are no failed operations (caller should not have called us), or
        * at least one failure is **not** RBAC-related (genuine template/logic error).
        """
        raw = self._az([
            "deployment", "operation", "group", "list",
            "--resource-group", self.config.resource_group,
            "--name", deployment_name,
            "--query", "[?properties.provisioningState == 'Failed'].properties.statusMessage",
            "--output", "json",
        ])
        if not raw:
            logger.debug(
                "Could not retrieve deployment operations for '%s' — "
                "cannot confirm nested RBAC-only failures.",
                deployment_name,
            )
            return False
        try:
            failed_messages = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not failed_messages:
            return False
        # Every failed operation must be an RBAC issue for us to treat the phase
        # as a warning.  A mix of RBAC and non-RBAC failures is a real error.
        # _contains_rbac_error walks the parsed dict structure directly, avoiding
        # a json.dumps round-trip solely for pattern matching.
        return all(
            _contains_rbac_error(msg)
            for msg in failed_messages
            if msg is not None
        )

    def _deployment_cmd(self, action: str, *, output_format: str = "json") -> list[str]:
        """Build the ``az deployment group <action>`` command list.

        Parameters
        ----------
        action:
            ARM deployment sub-command: ``validate``, ``what-if``, or ``create``.
        output_format:
            Azure CLI ``--output`` format.  Use ``"json"`` (default) when the caller
            needs to parse stdout, or ``"none"`` for streaming deploys where the
            per-resource progress messages are more useful than a JSON blob.
        """
        cmd = [
            "az", "deployment", "group", action,
            "--resource-group", self.config.resource_group,
            "--template-file", self.config.template,
            "--output", output_format,
        ]
        if self.config.parameters_file:
            cmd += ["--parameters", self.config.parameters_file]

        # Inline Bicep parameter overrides
        overrides: list[str] = []
        overrides.append(f"environment={self.config.environment}")
        overrides.append(f"location={self.config.location}")
        if self.config.location_ml:
            overrides.append(f"locationML={self.config.location_ml}")
        if self.config.git_sha and self._SHA_RE.match(self.config.git_sha):
            overrides.append(f'tags={{"gitSha":"{self.config.git_sha}"}}')
        if overrides:
            cmd += ["--parameters"] + overrides
        return cmd

    def _az(self, args: list[str], *, print_output: bool = False) -> str | None:
        """Run an ``az`` command and return stdout, or *None* on failure."""
        result = self._run(["az"] + args)
        if result.returncode != 0:
            print(f"  az command failed (rc={result.returncode})", file=sys.stderr)
            if result.stderr:
                print(f"  {result.stderr.strip()}", file=sys.stderr)
            return None
        if print_output and result.stdout:
            print(result.stdout)
        return result.stdout

    @staticmethod
    def _run(cmd: list[str], *, stream: bool = False) -> subprocess.CompletedProcess[str]:
        """Execute a command and return the completed process.

        Parameters
        ----------
        cmd:
            Command and arguments to execute.
        stream:
            When ``True``, output is **not** captured — stdout and stderr flow
            directly to the caller's terminal (and to the GitHub Actions log via
            ``tee``).  This enables real-time, module-wise deployment feedback.
            When ``False`` (default), output is captured and available via
            ``result.stdout`` / ``result.stderr``.

        Notes
        -----
        ``check=False`` (the subprocess default) is used in both branches so that
        non-zero exit codes are returned to the caller rather than raising an
        exception; each call site is responsible for inspecting ``returncode``.
        """
        if stream:
            return subprocess.run(cmd, text=True, check=False)  # noqa: S603
        return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603

    def _audit(self, action: str, data: dict[str, Any]) -> None:
        """Write an audit-log entry as a JSON file."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "environment": self.config.environment,
            "resource_group": self.config.resource_group,
            "location": self.config.location,
            **data,
        }
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = _AUDIT_DIR / f"{action}_{ts}.json"
        path.write_text(json.dumps(entry, indent=2))
        print(f"📝 Audit log written to {path}")
