"""Governance pillar — Azure resource scale-down-to-zero audit.

``ScaleDownAuditor`` inspects every resource in a resource group and
classifies whether it can scale to zero when idle.  Resources that cannot
scale to zero (or are not currently configured to do so) are reported as
*violations* together with actionable recommendations for switching to a
consumption / serverless alternative.

The auditor uses :class:`AzureSDKClient` for all Azure queries — no CLI
fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from orchestrator.integration.azure_sdk_client import AzureSDKClient, ResourceState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resource-type classification catalogue
# ---------------------------------------------------------------------------
# Maps lowercase resource type → classification metadata.
#
# ALL keys are stored in lowercase because _classify() normalises the type
# string via str.lower() before lookup — this gives case-insensitive
# matching regardless of how the ARM API or Azure SDK returns the type
# (e.g. "Microsoft.Web/serverFarms" and "microsoft.web/serverfarms" both
# resolve to the same entry).
#
# supports_scale_to_zero:
#   True  — resource natively scales to zero (e.g. Consumption Function App)
#   False — resource keeps running even at zero utilisation (violation)
#
# For resources that may or may not scale to zero depending on SKU/tier we
# apply additional SKU-level logic in ScaleDownAuditor._classify().

_RESOURCE_CATALOGUE: dict[str, dict[str, Any]] = {
    # ---------- Azure Functions / App Service ----------
    "microsoft.web/serverfarms": {
        "supports_scale_to_zero": False,
        "condition": "sku_not_consumption",  # Y1 == Consumption (OK); EP/P/S/B == violation
        "recommendation": (
            "Migrate to a Consumption (Y1) or Flex Consumption App Service Plan. "
            "Consumption plans scale to zero automatically and incur no charge when idle. "
            "Alternatively, use Azure Container Apps (Consumption) for event-driven workloads."
        ),
        "alternatives": [
            "Azure Functions – Consumption plan (Y1 SKU)",
            "Azure Functions – Flex Consumption plan",
            "Azure Container Apps – Consumption workload profile",
        ],
    },
    "microsoft.web/sites": {
        "supports_scale_to_zero": True,  # Function Apps on Consumption scale to zero
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    # ---------- Container / Serverless compute ----------
    "microsoft.containerinstance/containergroups": {
        "supports_scale_to_zero": True,
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.app/containerapps": {
        "supports_scale_to_zero": True,
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.app/managedenvironments": {
        "supports_scale_to_zero": True,  # Consumption workload profile = scale to zero
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    # ---------- Virtual Machines ----------
    "microsoft.compute/virtualmachines": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Stop/deallocate VMs when not in use via Azure Automation runbooks or "
            "VM auto-shutdown schedules (Azure DevTest Labs policy). "
            "Consider replacing always-on VMs with Azure Functions, Container Apps, "
            "or AKS with scale-to-zero node pools."
        ),
        "alternatives": [
            "Azure Functions – Consumption plan",
            "Azure Container Apps – scale-to-zero",
            "AKS – scale-to-zero node pools with KEDA",
            "Azure Automation – VM auto-shutdown schedule",
        ],
    },
    "microsoft.compute/virtualmachinescalesets": {
        "supports_scale_to_zero": True,  # VMSS supports 0 instances
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    # ---------- Databases ----------
    "microsoft.sql/servers/databases": {
        "supports_scale_to_zero": False,
        "condition": "sku_not_serverless",  # Serverless tier auto-pauses
        "recommendation": (
            "Switch to Azure SQL Database Serverless tier, which auto-pauses after "
            "a configurable idle period and resumes on the next request. "
            "Alternatively, use Azure Cosmos DB (serverless) or Azure Table Storage "
            "for lower-throughput workloads."
        ),
        "alternatives": [
            "Azure SQL Database – Serverless tier (auto-pause)",
            "Azure Cosmos DB – Serverless capacity mode",
            "Azure Table Storage (for simple key-value access)",
        ],
    },
    "microsoft.documentdb/databaseaccounts": {
        "supports_scale_to_zero": False,
        "condition": "sku_not_serverless",  # Serverless Cosmos scales to zero
        "recommendation": (
            "Switch to Azure Cosmos DB Serverless capacity mode, which charges only "
            "for the request units consumed and scales to zero when idle. "
            "Provisioned-throughput accounts always incur a minimum hourly charge."
        ),
        "alternatives": [
            "Azure Cosmos DB – Serverless capacity mode",
            "Azure Table Storage (for simple access patterns)",
        ],
    },
    "microsoft.dbforpostgresql/flexibleservers": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Enable the auto-stop (compute auto-pause) feature on Azure Database for "
            "PostgreSQL Flexible Server, available for dev/test tiers. "
            "For production, consider Azure Cosmos DB for PostgreSQL (serverless) or "
            "schedule server stops via Azure Automation."
        ),
        "alternatives": [
            "Azure Database for PostgreSQL Flexible Server – compute auto-pause (dev/test)",
            "Azure Cosmos DB for PostgreSQL – serverless",
            "Azure Automation – scheduled server stop/start",
        ],
    },
    "microsoft.dbformysql/flexibleservers": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Enable the auto-stop (compute auto-pause) feature on Azure Database for "
            "MySQL Flexible Server for dev/test environments. "
            "For production, schedule stops via Azure Automation or migrate to a "
            "serverless data store."
        ),
        "alternatives": [
            "Azure Database for MySQL Flexible Server – compute auto-pause (dev/test)",
            "Azure Cosmos DB – Serverless capacity mode",
            "Azure Automation – scheduled server stop/start",
        ],
    },
    # ---------- Messaging ----------
    "microsoft.servicebus/namespaces": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure Service Bus (Basic/Standard/Premium) maintains a minimum hourly "
            "charge regardless of message volume. "
            "For event-driven workloads that are truly idle for long periods, consider "
            "Azure Storage Queue (pay-per-operation) or Azure Event Grid (serverless, "
            "pay-per-event) as lower-cost alternatives."
        ),
        "alternatives": [
            "Azure Storage Queue – pay-per-operation, no minimum charge",
            "Azure Event Grid – serverless, pay-per-event",
        ],
    },
    "microsoft.eventhub/namespaces": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure Event Hubs charges a minimum of 1 Throughput Unit (or 1 PU for "
            "Premium) per hour regardless of load. "
            "If the namespace is idle for extended periods, consider Azure Event Grid "
            "(serverless) or Azure Storage Queue for simpler pub/sub patterns."
        ),
        "alternatives": [
            "Azure Event Grid – serverless, pay-per-event",
            "Azure Storage Queue – pay-per-operation",
        ],
    },
    # ---------- Storage & Identity (pay-per-use, no idle compute charge) ----------
    "microsoft.storage/storageaccounts": {
        "supports_scale_to_zero": True,  # Pay-per-GB stored + operations; no idle compute cost
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.keyvault/vaults": {
        "supports_scale_to_zero": True,  # Pay-per-operation only; no minimum compute charge
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.managedidentity/userassignedidentities": {
        "supports_scale_to_zero": True,  # Free resource; no compute cost
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    # ---------- Monitoring (pay-per-ingestion, no idle compute charge) ----------
    "microsoft.operationalinsights/workspaces": {
        "supports_scale_to_zero": True,  # Pay-per-GB ingested; no cost when idle
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.insights/components": {
        "supports_scale_to_zero": True,  # Pay-per-GB telemetry ingested; free tier available
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    # ---------- AI / ML ----------
    "microsoft.cognitiveservices/accounts": {
        "supports_scale_to_zero": True,  # Pay-as-you-go / standard metered = zero when idle
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.machinelearningservices/workspaces": {
        "supports_scale_to_zero": True,  # Workspace itself has no compute charge
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.machinelearningservices/workspaces/computes": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "AML Compute clusters should have a minimum node count of 0 so they "
            "scale to zero when idle. Compute instances (single-node dev VMs) should "
            "have auto-shutdown enabled. Use serverless compute (AML Serverless) for "
            "ad-hoc training where available."
        ),
        "alternatives": [
            "AML Compute Cluster – set minNodeCount=0",
            "AML Compute Instance – enable auto-shutdown",
            "AML Serverless Compute (preview)",
        ],
    },
    "microsoft.machinelearningservices/registries": {
        "supports_scale_to_zero": True,  # Registry metadata + storage only; no idle compute charge
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.machinelearningservices/workspaces/serverlessendpoints": {
        "supports_scale_to_zero": True,  # Serverless endpoints bill per-token; zero cost when idle
        "condition": None,
        "recommendation": "",
        "alternatives": [],
    },
    "microsoft.machinelearningservices/workspaces/onlineendpoints": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure ML Managed Online Endpoints back deployments with dedicated VM instances "
            "that incur an hourly charge regardless of request volume. "
            "Set 'minimumReplicaCount: 0' and enable 'scale-to-zero' on the deployment "
            "autoscale policy, or replace with a Serverless Online Endpoint "
            "(Microsoft.MachineLearningServices/workspaces/serverlessEndpoints) which "
            "bills per token and scales to zero when idle."
        ),
        "alternatives": [
            "AML Serverless Endpoint (serverlessEndpoints) – pay-per-token, true scale-to-zero",
            "AML Managed Online Endpoint – set minimumReplicaCount=0 in autoscale policy",
            "Azure Container Apps – event-driven autoscaling to zero",
        ],
    },
    # ---------- Search ----------
    "microsoft.search/searchservices": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure AI Search charges by the hour for each provisioned Search Unit "
            "and cannot scale below 1 replica × 1 partition. "
            "For development or low-traffic scenarios, downscale to the Free tier "
            "(no charge) or Basic tier (1 SU minimum). "
            "For full scale-to-zero, consider Azure Cognitive Search with consumption "
            "pricing (not yet GA) or use Azure AI Search only for active workloads and "
            "delete/recreate indexes on demand."
        ),
        "alternatives": [
            "Azure AI Search – Free tier for dev/test",
            "Azure AI Search – Basic tier (minimal cost)",
            "Delete and recreate the index on demand (GitOps pattern)",
        ],
    },
    # ---------- Storage / Registry ----------
    "microsoft.containerregistry/registries": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure Container Registry (Basic/Standard/Premium) has a fixed monthly "
            "storage fee regardless of pull activity. "
            "For non-production registries, downgrade to Basic tier or use GitHub "
            "Container Registry (ghcr.io), which charges only for storage used. "
            "Delete unused images and use retention policies to reduce storage cost."
        ),
        "alternatives": [
            "Azure Container Registry – Basic tier (lowest fixed cost)",
            "GitHub Container Registry (ghcr.io) – pay-per-storage",
            "Retention policy to purge unused images",
        ],
    },
    "microsoft.cache/redis": {
        "supports_scale_to_zero": False,
        "condition": None,
        "recommendation": (
            "Azure Cache for Redis cannot scale to zero — it always incurs an hourly "
            "charge per tier. "
            "For caching in non-production environments, use in-process caching "
            "(e.g. Python functools.lru_cache) or a lower-cost tier. "
            "For production, ensure the Redis instance is sized correctly to avoid "
            "over-provisioning."
        ),
        "alternatives": [
            "In-process caching (functools.lru_cache, cachetools)",
            "Azure Cache for Redis – Basic C0 tier (lowest cost)",
            "Azure Cosmos DB for Redis (serverless, pay-per-request)",
        ],
    },
    # ---------- API Management ----------
    "microsoft.apimanagement/service": {
        "supports_scale_to_zero": False,
        "condition": "sku_not_consumption",  # Consumption tier is truly serverless
        "recommendation": (
            "Migrate to the Azure API Management Consumption tier, which is fully "
            "serverless (pay-per-call, no hourly charge, scales to zero). "
            "Developer, Basic, Standard, and Premium tiers all have a minimum hourly "
            "charge."
        ),
        "alternatives": [
            "Azure API Management – Consumption tier (pay-per-call, scale to zero)",
        ],
    },
}

# SKU names that represent a Consumption / serverless tier (scale to zero).
# EP (Elastic Premium) is NOT included — it does not scale to zero.
_CONSUMPTION_SKUS: frozenset[str] = frozenset({
    "y1", "consumption", "dynamic", "serverless", "flexconsumption",
})

# SKU names that represent a Serverless database tier
_SERVERLESS_DB_SKUS: frozenset[str] = frozenset({"serverless", "serverlessgp"})


# ---------------------------------------------------------------------------
# Audit result data classes
# ---------------------------------------------------------------------------

@dataclass
class ScaleDownViolation:
    """A single resource that does not support scale-to-zero."""

    resource_name: str
    resource_type: str
    resource_id: str
    location: str
    sku: str
    recommendation: str
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "location": self.location,
            "sku": self.sku,
            "recommendation": self.recommendation,
            "alternatives": self.alternatives,
        }


@dataclass
class ScaleDownAuditReport:
    """Aggregated result from a scale-down audit run."""

    resource_group: str
    subscription_id: str
    violations: list[ScaleDownViolation] = field(default_factory=list)
    compliant: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_resources: int = 0

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_group": self.resource_group,
            "subscription_id": self.subscription_id,
            "total_resources": self.total_resources,
            "violation_count": len(self.violations),
            "compliant_count": len(self.compliant),
            "skipped_count": len(self.skipped),
            "violations": [v.to_dict() for v in self.violations],
        }

    def format_issue_body(self, environment: str = "") -> str:
        """Render a markdown-formatted GitHub issue body."""
        env_label = f" (`{environment}`)" if environment else ""
        lines: list[str] = [
            f"## ⚠️ Scale-Down-to-Zero Audit Violations{env_label}",
            "",
            f"**Resource group:** `{self.resource_group}`  ",
            f"**Resources audited:** {self.total_resources}  ",
            f"**Violations found:** {len(self.violations)}  ",
            "",
            (
                "The following Azure resources cannot scale to zero when idle, "
                "resulting in unnecessary costs outside of active usage periods."
            ),
            "",
        ]

        for i, v in enumerate(self.violations, 1):
            lines += [
                f"### {i}. `{v.resource_name}` — {v.resource_type}",
                "",
                f"| Field | Value |",
                f"|-------|-------|",
                f"| Resource | `{v.resource_name}` |",
                f"| Type | `{v.resource_type}` |",
                f"| SKU / Tier | `{v.sku or 'N/A'}` |",
                f"| Location | `{v.location}` |",
                f"| Resource ID | `{v.resource_id}` |",
                "",
                "**Recommendation:**",
                "",
                v.recommendation,
                "",
            ]
            if v.alternatives:
                lines.append("**Alternatives that support scale-to-zero:**")
                lines.append("")
                for alt in v.alternatives:
                    lines.append(f"- {alt}")
                lines.append("")

        lines += [
            "---",
            "",
            "### How to resolve",
            "",
            "1. Review each violation above and choose the recommended alternative.",
            "2. Update the corresponding Bicep module in `deployment/modules/` or `deployment/phases/`.",
            "3. Re-run the [Infrastructure Governance workflow](.github/workflows/infrastructure-governance.yml) "
            "to validate compliance.",
            "4. Re-run the [Cost Management workflow](.github/workflows/cost-management.yml) "
            "to confirm violations are resolved.",
            "",
            "> This issue was automatically created by the "
            "[Cost Management workflow](.github/workflows/cost-management.yml).",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------

class ScaleDownAuditor:
    """Audits Azure resources in a resource group for scale-to-zero compliance.

    Uses :class:`AzureSDKClient` to list all resources and classifies each
    against the :data:`_RESOURCE_CATALOGUE`.  Resources whose type is not in
    the catalogue are recorded as *skipped* (neither compliant nor a violation).

    Parameters
    ----------
    resource_group:
        Azure resource group to audit.
    subscription_id:
        Azure subscription containing the resource group.
    """

    def __init__(self, resource_group: str, subscription_id: str) -> None:
        self.resource_group = resource_group
        self.subscription_id = subscription_id
        self._client = AzureSDKClient(subscription_id, resource_group)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit(self) -> ScaleDownAuditReport:
        """Run a full scale-down audit on the resource group.

        Returns
        -------
        ScaleDownAuditReport
            Structured report with violations, compliant resources, and skipped types.
        """
        logger.info(
            "Auditing scale-down-to-zero compliance for %s",
            self.resource_group,
        )
        resources = self._client.list_resources()

        report = ScaleDownAuditReport(
            resource_group=self.resource_group,
            subscription_id=self.subscription_id,
            total_resources=len(resources),
        )

        for resource in resources:
            result = self._classify(resource)
            if result is None:
                report.skipped.append(resource.name)
                logger.debug("Skipped (unknown type): %s (%s)", resource.name, resource.resource_type)
            elif result is True:
                report.compliant.append(resource.name)
                logger.debug("Compliant: %s (%s)", resource.name, resource.resource_type)
            else:
                report.violations.append(result)
                logger.info(
                    "Violation: %s (%s) — %s",
                    resource.name,
                    resource.resource_type,
                    result.recommendation[:80],
                )

        logger.info(
            "Audited %d resource(s): %d violation(s), %d compliant, %d skipped.",
            report.total_resources,
            len(report.violations),
            len(report.compliant),
            len(report.skipped),
        )
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify(
        self, resource: ResourceState
    ) -> "ScaleDownViolation | bool | None":
        """Classify a single resource.

        Returns
        -------
        True
            Resource is compliant (supports scale-to-zero).
        ScaleDownViolation
            Resource is a violation.
        None
            Resource type is not in the catalogue (skipped).
        """
        rtype = resource.resource_type.lower()
        entry = _RESOURCE_CATALOGUE.get(rtype)
        if entry is None:
            return None  # Unknown type — skip

        supports = entry["supports_scale_to_zero"]
        condition = entry.get("condition")

        # Apply conditional SKU-level logic
        if condition == "sku_not_consumption":
            sku_lower = resource.sku.lower()
            # If SKU matches a consumption tier → compliant
            if any(sku_lower.startswith(c) for c in _CONSUMPTION_SKUS):
                return True
            # Otherwise → violation (even if supports_scale_to_zero default is False)
            supports = False

        elif condition == "sku_not_serverless":
            sku_lower = resource.sku.lower()
            if any(sku_lower.startswith(s) for s in _SERVERLESS_DB_SKUS):
                return True
            supports = False

        if supports:
            return True

        return ScaleDownViolation(
            resource_name=resource.name,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            location=resource.location,
            sku=resource.sku,
            recommendation=entry["recommendation"],
            alternatives=list(entry.get("alternatives", [])),
        )
