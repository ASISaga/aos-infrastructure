"""Closed-loop OODA orchestration — Observe, Orient, Decide, Act.

Implements the `Boyd OODA loop <https://en.wikipedia.org/wiki/OODA_loop>`_
for intelligent infrastructure management.  Instead of fire-and-forget CLI
invocations (open-loop), the OODA loop continuously observes the actual
infrastructure state, orients by comparing it to the desired state and cost
constraints, decides what actions are needed, acts with precision, and then
verifies the outcome.

The four phases:

1. **Observe** — capture a full infrastructure snapshot (resource state,
   deployment history, cost data) via :class:`AzureSDKClient`.
2. **Orient** — analyse the gap between desired and actual state, factor in
   cost thresholds and health status to produce a situational assessment.
3. **Decide** — generate a list of recommended actions (deploy, skip, scale,
   alert) based on the orientation analysis.
4. **Act** — execute approved actions and record the outcome.

Usage
-----
>>> loop = OODALoop(sdk_client, desired_state, cost_threshold=500.0)
>>> cycle = loop.run_cycle()
>>> print(cycle.decision.recommended_action)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from orchestrator.integration.azure_sdk_client import (
    AzureSDKClient,
    CostSummary,
    InfrastructureSnapshot,
    ProvisioningState,
    ResourceState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and data models for each OODA phase
# ---------------------------------------------------------------------------

class HealthAssessment(str, Enum):
    """Aggregated health assessment of the infrastructure."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class RecommendedAction(str, Enum):
    """High-level action the OODA loop recommends after orientation."""

    DEPLOY = "deploy"                # Full deployment needed
    INCREMENTAL_UPDATE = "incremental_update"  # Only changed resources
    SKIP = "skip"                    # Desired state already achieved
    REMEDIATE = "remediate"          # Fix unhealthy resources
    SCALE_DOWN = "scale_down"        # Cost threshold exceeded
    ALERT = "alert"                  # Needs human attention
    BLOCK = "block"                  # Safety constraint prevents action


@dataclass
class Observation:
    """Phase 1 result — raw infrastructure snapshot."""

    snapshot: InfrastructureSnapshot
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = self.snapshot.timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_resources": self.snapshot.total_resources,
            "healthy_resources": self.snapshot.healthy_resources,
            "unhealthy_count": len(self.snapshot.unhealthy_resources),
            "cost": self.snapshot.cost.to_dict() if self.snapshot.cost else None,
        }


@dataclass
class Orientation:
    """Phase 2 result — situational analysis.

    Compares the observed state against the desired state and external
    constraints (cost thresholds, health requirements) to produce an
    assessment that the Decide phase can act on.
    """

    health: HealthAssessment = HealthAssessment.UNKNOWN
    drift_detected: bool = False
    missing_resources: list[str] = field(default_factory=list)
    unhealthy_resources: list[str] = field(default_factory=list)
    unexpected_resources: list[str] = field(default_factory=list)
    cost_within_budget: bool = True
    current_cost: float = 0.0
    cost_threshold: float = 0.0
    state_matches_desired: bool = False
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "drift_detected": self.drift_detected,
            "missing_resources": self.missing_resources,
            "unhealthy_resources": self.unhealthy_resources,
            "cost_within_budget": self.cost_within_budget,
            "current_cost": self.current_cost,
            "cost_threshold": self.cost_threshold,
            "state_matches_desired": self.state_matches_desired,
            "details": self.details,
        }


@dataclass
class Decision:
    """Phase 3 result — recommended action with rationale."""

    recommended_action: RecommendedAction = RecommendedAction.DEPLOY
    rationale: str = ""
    target_resources: list[str] = field(default_factory=list)
    approved: bool = False
    cost_impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_action": self.recommended_action.value,
            "rationale": self.rationale,
            "target_resources": self.target_resources,
            "approved": self.approved,
            "cost_impact": self.cost_impact,
        }


@dataclass
class ActionResult:
    """Phase 4 result — outcome of executed action."""

    action: RecommendedAction
    success: bool = False
    resources_affected: int = 0
    details: str = ""
    error: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "success": self.success,
            "resources_affected": self.resources_affected,
            "details": self.details,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class OODACycle:
    """Complete record of one OODA loop iteration."""

    observation: Observation
    orientation: Orientation
    decision: Decision
    action_result: Optional[ActionResult] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.to_dict(),
            "orientation": self.orientation.to_dict(),
            "decision": self.decision.to_dict(),
            "action_result": (
                self.action_result.to_dict() if self.action_result else None
            ),
        }


# ---------------------------------------------------------------------------
# Desired-state specification
# ---------------------------------------------------------------------------

@dataclass
class DesiredState:
    """Declarative specification of the desired infrastructure state.

    Used by the Orient phase to compare against observed state and detect
    drift, missing resources, or unexpected resources.
    """

    expected_resources: list[dict[str, str]] = field(default_factory=list)
    """List of expected resources with ``name`` and ``type`` keys."""

    required_healthy: bool = True
    """If ``True``, all expected resources must be in ``Succeeded`` state."""

    max_monthly_cost: float = 0.0
    """Maximum acceptable monthly cost (0 = no limit)."""

    required_tags: dict[str, str] = field(default_factory=dict)
    """Tags that every resource must carry."""


# ---------------------------------------------------------------------------
# OODA Loop implementation
# ---------------------------------------------------------------------------

class OODALoop:
    """Closed-loop infrastructure orchestration using the OODA pattern.

    Parameters
    ----------
    client:
        Azure SDK client for infrastructure observation.
    desired_state:
        Declarative specification of the target infrastructure state.
    cost_threshold:
        Monthly cost threshold in subscription currency (0 = no limit).
    auto_approve:
        When ``True``, the Decide phase automatically approves safe actions
        (skip, incremental_update).  Destructive actions (scale_down, deploy)
        always require explicit approval via :meth:`approve_action`.
    """

    def __init__(
        self,
        client: AzureSDKClient,
        desired_state: Optional[DesiredState] = None,
        cost_threshold: float = 0.0,
        auto_approve: bool = False,
    ) -> None:
        self.client = client
        self.desired_state = desired_state or DesiredState()
        self.cost_threshold = cost_threshold
        self.auto_approve = auto_approve
        self._cycles: list[OODACycle] = []

    @property
    def cycles(self) -> list[OODACycle]:
        """Return the history of completed OODA cycles."""
        return list(self._cycles)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_cycle(self, include_cost: bool = False) -> OODACycle:
        """Execute one complete OODA cycle.

        Parameters
        ----------
        include_cost:
            Query cost data during observation (slower but enables
            cost-aware decisions).

        Returns
        -------
        OODACycle
            Record of the observation, orientation, decision, and
            (if auto-approved) action result.
        """
        # Phase 1: Observe
        observation = self.observe(include_cost=include_cost)
        logger.info("OODA Observe: %d resources, %d healthy",
                     observation.snapshot.total_resources,
                     observation.snapshot.healthy_resources)

        # Phase 2: Orient
        orientation = self.orient(observation)
        logger.info("OODA Orient: health=%s, drift=%s, cost_ok=%s",
                     orientation.health.value,
                     orientation.drift_detected,
                     orientation.cost_within_budget)

        # Phase 3: Decide
        decision = self.decide(orientation)
        logger.info("OODA Decide: action=%s, rationale=%s",
                     decision.recommended_action.value,
                     decision.rationale)

        cycle = OODACycle(
            observation=observation,
            orientation=orientation,
            decision=decision,
        )

        # Phase 4: Act (only if auto-approved or safe action)
        if decision.approved:
            action_result = self.act(decision)
            cycle.action_result = action_result
            logger.info("OODA Act: success=%s, affected=%d",
                         action_result.success,
                         action_result.resources_affected)

        self._cycles.append(cycle)
        return cycle

    # ------------------------------------------------------------------
    # Individual OODA phases
    # ------------------------------------------------------------------

    def observe(self, include_cost: bool = False) -> Observation:
        """Phase 1 — Capture the current infrastructure state."""
        snapshot = self.client.observe(include_cost=include_cost)
        return Observation(snapshot=snapshot)

    def orient(self, observation: Observation) -> Orientation:
        """Phase 2 — Analyse the gap between observed and desired state."""
        snapshot = observation.snapshot
        desired = self.desired_state

        # Health assessment
        if snapshot.total_resources == 0:
            health = HealthAssessment.UNKNOWN
        elif snapshot.healthy_resources == snapshot.total_resources:
            health = HealthAssessment.HEALTHY
        elif snapshot.healthy_resources > 0:
            health = HealthAssessment.DEGRADED
        else:
            health = HealthAssessment.UNHEALTHY

        # Drift detection — compare expected vs observed resource names
        observed_names = {r.name.lower() for r in snapshot.resources}
        expected_names = {
            r["name"].lower() for r in desired.expected_resources if "name" in r
        }

        missing = [
            name for name in sorted(expected_names - observed_names)
        ]
        unexpected = [
            name for name in sorted(observed_names - expected_names)
        ] if expected_names else []
        unhealthy = [
            r.name for r in snapshot.unhealthy_resources
        ]

        drift = bool(missing or unexpected or unhealthy)
        state_matches = (
            not missing
            and not unhealthy
            and health in (HealthAssessment.HEALTHY, HealthAssessment.UNKNOWN)
        )

        # Cost analysis
        current_cost = snapshot.cost.total_cost if snapshot.cost else 0.0
        threshold = self.cost_threshold or desired.max_monthly_cost
        cost_within_budget = (threshold <= 0) or (current_cost <= threshold)

        details_parts: list[str] = []
        if missing:
            details_parts.append(f"{len(missing)} missing resource(s)")
        if unhealthy:
            details_parts.append(f"{len(unhealthy)} unhealthy resource(s)")
        if unexpected:
            details_parts.append(f"{len(unexpected)} unexpected resource(s)")
        if not cost_within_budget:
            details_parts.append(
                f"cost ${current_cost:,.2f} exceeds threshold ${threshold:,.2f}"
            )
        if state_matches:
            details_parts.append("desired state achieved")

        return Orientation(
            health=health,
            drift_detected=drift,
            missing_resources=missing,
            unhealthy_resources=unhealthy,
            unexpected_resources=unexpected,
            cost_within_budget=cost_within_budget,
            current_cost=current_cost,
            cost_threshold=threshold,
            state_matches_desired=state_matches,
            details="; ".join(details_parts) if details_parts else "no issues",
        )

    def decide(self, orientation: Orientation) -> Decision:
        """Phase 3 — Determine the recommended action based on orientation."""

        # Cost threshold exceeded → scale down or alert
        if not orientation.cost_within_budget:
            return Decision(
                recommended_action=RecommendedAction.SCALE_DOWN,
                rationale=(
                    f"Monthly cost ${orientation.current_cost:,.2f} exceeds "
                    f"threshold ${orientation.cost_threshold:,.2f}"
                ),
                cost_impact=(
                    f"Over budget by ${orientation.current_cost - orientation.cost_threshold:,.2f}"
                ),
                approved=False,  # Always requires manual approval
            )

        # Unhealthy resources → remediate
        if orientation.unhealthy_resources:
            return Decision(
                recommended_action=RecommendedAction.REMEDIATE,
                rationale=f"{len(orientation.unhealthy_resources)} resource(s) in failed state",
                target_resources=orientation.unhealthy_resources,
                approved=self.auto_approve,
            )

        # Missing resources → deploy
        if orientation.missing_resources:
            if len(orientation.missing_resources) == len(
                self.desired_state.expected_resources
            ):
                # All resources missing → full deploy
                return Decision(
                    recommended_action=RecommendedAction.DEPLOY,
                    rationale="All expected resources are missing — full deployment needed",
                    target_resources=orientation.missing_resources,
                    approved=False,  # Full deploy needs approval
                )
            else:
                # Partial → incremental
                return Decision(
                    recommended_action=RecommendedAction.INCREMENTAL_UPDATE,
                    rationale=f"{len(orientation.missing_resources)} resource(s) missing",
                    target_resources=orientation.missing_resources,
                    approved=self.auto_approve,
                )

        # Desired state achieved → skip
        if orientation.state_matches_desired:
            return Decision(
                recommended_action=RecommendedAction.SKIP,
                rationale="Desired infrastructure state already achieved — no action needed",
                approved=True,  # Skip is always safe
            )

        # Health is degraded but no missing resources → alert
        if orientation.health == HealthAssessment.DEGRADED:
            return Decision(
                recommended_action=RecommendedAction.ALERT,
                rationale="Infrastructure is degraded but all expected resources exist",
                approved=True,
            )

        # Default — no drift, healthy, within budget → skip
        return Decision(
            recommended_action=RecommendedAction.SKIP,
            rationale="Infrastructure state is satisfactory — no action required",
            approved=True,
        )

    def act(self, decision: Decision) -> ActionResult:
        """Phase 4 — Execute the decided action.

        .. note::
            This method records the decision outcome.  The actual deployment
            is performed by the caller (e.g. ``InfrastructureManager.deploy()``),
            which uses the OODA cycle context to optimise its execution.
        """
        start = datetime.now(timezone.utc)

        if not decision.approved:
            return ActionResult(
                action=decision.recommended_action,
                success=False,
                details="Action not approved — requires manual confirmation",
            )

        if decision.recommended_action == RecommendedAction.SKIP:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            return ActionResult(
                action=RecommendedAction.SKIP,
                success=True,
                details="No action needed — infrastructure state matches desired state",
                duration_seconds=elapsed,
            )

        if decision.recommended_action == RecommendedAction.ALERT:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            return ActionResult(
                action=RecommendedAction.ALERT,
                success=True,
                details=f"Alert raised: {decision.rationale}",
                duration_seconds=elapsed,
            )

        # For DEPLOY, INCREMENTAL_UPDATE, REMEDIATE, SCALE_DOWN — the caller
        # is responsible for executing the actual Azure operations.  We return
        # a pending result that the caller will complete.
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return ActionResult(
            action=decision.recommended_action,
            success=True,
            resources_affected=len(decision.target_resources),
            details=f"Action '{decision.recommended_action.value}' initiated for "
                    f"{len(decision.target_resources)} resource(s)",
            duration_seconds=elapsed,
        )

    def approve_action(self, cycle: OODACycle) -> None:
        """Manually approve a pending action decision."""
        cycle.decision.approved = True

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def format_cycle_report(self, cycle: OODACycle) -> str:
        """Format a human-readable report for an OODA cycle."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("OODA CYCLE REPORT")
        lines.append("=" * 60)

        # Observation
        obs = cycle.observation
        lines.append(f"\n📡 OBSERVE ({obs.timestamp})")
        lines.append(f"  Resources: {obs.snapshot.total_resources} total, "
                     f"{obs.snapshot.healthy_resources} healthy")
        if obs.snapshot.cost:
            lines.append(f"  Cost: {obs.snapshot.cost.currency} "
                         f"{obs.snapshot.cost.total_cost:,.2f} "
                         f"({obs.snapshot.cost.period_start} → "
                         f"{obs.snapshot.cost.period_end})")

        # Orientation
        ori = cycle.orientation
        icons = {
            HealthAssessment.HEALTHY: "✅",
            HealthAssessment.DEGRADED: "⚠️",
            HealthAssessment.UNHEALTHY: "❌",
            HealthAssessment.UNKNOWN: "❓",
        }
        lines.append(f"\n🧭 ORIENT")
        lines.append(f"  Health: {icons[ori.health]} {ori.health.value}")
        lines.append(f"  Drift: {'yes' if ori.drift_detected else 'no'}")
        lines.append(f"  Budget: {'✅ within' if ori.cost_within_budget else '❌ exceeded'}")
        lines.append(f"  State match: {'✅ yes' if ori.state_matches_desired else '❌ no'}")
        if ori.details:
            lines.append(f"  Details: {ori.details}")

        # Decision
        dec = cycle.decision
        action_icons = {
            RecommendedAction.DEPLOY: "🚀",
            RecommendedAction.INCREMENTAL_UPDATE: "🔄",
            RecommendedAction.SKIP: "⏭️",
            RecommendedAction.REMEDIATE: "🔧",
            RecommendedAction.SCALE_DOWN: "📉",
            RecommendedAction.ALERT: "🔔",
            RecommendedAction.BLOCK: "🚫",
        }
        lines.append(f"\n🎯 DECIDE")
        lines.append(f"  Action: {action_icons.get(dec.recommended_action, '❓')} "
                     f"{dec.recommended_action.value}")
        lines.append(f"  Rationale: {dec.rationale}")
        lines.append(f"  Approved: {'yes' if dec.approved else 'no (requires approval)'}")
        if dec.target_resources:
            lines.append(f"  Targets: {', '.join(dec.target_resources[:5])}"
                         + (f" (+{len(dec.target_resources) - 5} more)"
                            if len(dec.target_resources) > 5 else ""))

        # Action result
        if cycle.action_result:
            ar = cycle.action_result
            lines.append(f"\n⚡ ACT")
            lines.append(f"  {'✅' if ar.success else '❌'} {ar.action.value}: {ar.details}")
            if ar.resources_affected:
                lines.append(f"  Resources affected: {ar.resources_affected}")
            if ar.duration_seconds:
                lines.append(f"  Duration: {ar.duration_seconds:.1f}s")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
