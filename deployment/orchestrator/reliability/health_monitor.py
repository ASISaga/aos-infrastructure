"""Reliability pillar — Enhanced health monitoring with SLA tracking.

``HealthMonitor`` extends basic health checks with SLA objective tracking,
multi-resource deep health probes, and an availability summary for AOS
infrastructure.  It uses the Azure Resource Management SDK via
:class:`AzureSDKClient` for type-safe, closed-loop state observation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from orchestrator.integration.azure_sdk_client import (
    AzureSDKClient,
    ProvisioningState,
)

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Overall health status of a resource or the environment."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ResourceHealth:
    """Health state of a single Azure resource."""

    name: str
    resource_type: str
    status: HealthStatus
    provisioning_state: str = ""
    availability_state: str = ""
    details: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY


# SLA targets per environment (uptime percentage)
_SLA_TARGETS: dict[str, float] = {
    "dev": 99.0,
    "staging": 99.5,
    "prod": 99.9,
}


class HealthMonitor:
    """Monitors AOS infrastructure health and tracks SLA compliance.

    Uses :class:`AzureSDKClient` for type-safe resource state queries
    (closed-loop observation).
    """

    def __init__(
        self,
        resource_group: str,
        environment: str = "dev",
        subscription_id: str = "",
    ) -> None:
        self.resource_group = resource_group
        self.environment = environment
        self.sla_target = _SLA_TARGETS.get(environment, 99.0)
        self._client = AzureSDKClient(subscription_id, resource_group)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(self) -> tuple[HealthStatus, list[ResourceHealth]]:
        """Run a full health check across all resources in the resource group.

        Uses the Azure SDK for closed-loop state observation.

        Returns ``(overall_status, resource_healths)``.
        """
        print(f"🏥 Running full health check for {self.resource_group} ({self.environment})")

        sdk_resources = self._client.list_resources()
        if not sdk_resources:
            print("  No resources found.")
            return HealthStatus.UNKNOWN, []

        healths: list[ResourceHealth] = []
        for r in sdk_resources:
            status = HealthStatus.HEALTHY if r.is_healthy else (
                HealthStatus.UNHEALTHY if r.provisioning_state in (
                    ProvisioningState.FAILED, ProvisioningState.CANCELED
                )
                else HealthStatus.DEGRADED
            )
            healths.append(ResourceHealth(
                name=r.name,
                resource_type=r.resource_type,
                status=status,
                provisioning_state=r.provisioning_state.value,
                details=f"provisioningState={r.provisioning_state.value}",
            ))

        overall = self._aggregate_status(healths)
        self._print_health_report(healths, overall)
        return overall, healths

    def check_sla_compliance(self, observed_uptime_pct: float | None = None) -> dict[str, Any]:
        """Report SLA compliance for the environment.

        Parameters
        ----------
        observed_uptime_pct:
            Optional uptime percentage from an external monitoring system.
            If not provided, derives it from the current resource health.

        Returns a dict with keys:
        - ``environment``: the environment name
        - ``sla_target``: target uptime percentage
        - ``observed_uptime``: measured uptime percentage
        - ``compliant``: whether SLA is met
        - ``gap``: how far above/below target (positive = above)
        """
        print(f"📈 SLA compliance check for {self.resource_group} "
              f"(target: {self.sla_target}%)")

        if observed_uptime_pct is None:
            _, healths = self.check_all()
            if not healths:
                observed_uptime_pct = 0.0
            else:
                healthy_count = sum(1 for h in healths if h.is_healthy())
                observed_uptime_pct = 100.0 * healthy_count / len(healths)

        compliant = observed_uptime_pct >= self.sla_target
        gap = round(observed_uptime_pct - self.sla_target, 3)

        result = {
            "environment": self.environment,
            "sla_target": self.sla_target,
            "observed_uptime": round(observed_uptime_pct, 3),
            "compliant": compliant,
            "gap": gap,
        }
        icon = "✅" if compliant else "❌"
        print(f"  {icon} Uptime: {observed_uptime_pct:.2f}% "
              f"(target: {self.sla_target}%, gap: {gap:+.3f}%)")
        return result

    def check_disaster_recovery_readiness(self) -> dict[str, Any]:
        """Assess DR readiness using SDK-based resource inspection.

        Returns a summary dict with a boolean ``ready`` flag and a list of
        ``findings``.
        """
        print(f"🆘 DR readiness check for {self.resource_group}")
        findings: list[str] = []

        resources = self._client.list_resources()

        # Check Key Vault soft-delete / purge protection
        kv_resources = [
            r for r in resources
            if r.resource_type.lower() == "microsoft.keyvault/vaults"
        ]
        if kv_resources:
            # Note: SDK list_resources doesn't include deep properties;
            # we can infer healthy state from provisioning state.
            all_healthy = all(r.is_healthy for r in kv_resources)
            if not all_healthy:
                findings.append("Key Vault: one or more vaults in unhealthy state")
        else:
            findings.append("Key Vault: no vaults found in resource group")

        # Check Storage geo-redundancy
        storage_resources = [
            r for r in resources
            if r.resource_type.lower() == "microsoft.storage/storageaccounts"
        ]
        if storage_resources:
            geo_redundant = any(
                r.sku and any(g in r.sku.upper() for g in ("GRS", "GZRS"))
                for r in storage_resources
            )
            if not geo_redundant:
                findings.append(
                    "Storage: no geo-redundant accounts found (consider GRS/GZRS for prod)"
                )

        ready = len(findings) == 0
        icon = "✅" if ready else "⚠️"
        print(f"  {icon} DR readiness: {'ready' if ready else 'action required'}")
        for f in findings:
            print(f"    • {f}")
        return {"ready": ready, "findings": findings}

    def get_resource_health(self, resource_name: str) -> ResourceHealth | None:
        """Return the health state of a specific named resource."""
        resource = self._client.get_resource(resource_name)
        if resource is None:
            print(f"  ⚠️  Resource '{resource_name}' not found in {self.resource_group}")
            return None

        status = HealthStatus.HEALTHY if resource.is_healthy else (
            HealthStatus.UNHEALTHY if resource.provisioning_state in (
                ProvisioningState.FAILED, ProvisioningState.CANCELED
            )
            else HealthStatus.DEGRADED
        )
        return ResourceHealth(
            name=resource.name,
            resource_type=resource.resource_type,
            status=status,
            provisioning_state=resource.provisioning_state.value,
            details=f"provisioningState={resource.provisioning_state.value}",
        )

    # ------------------------------------------------------------------
    # Private helpers — display
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_status(healths: list[ResourceHealth]) -> HealthStatus:
        if not healths:
            return HealthStatus.UNKNOWN
        statuses = {h.status for h in healths}
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @staticmethod
    def _print_health_report(
        healths: list[ResourceHealth], overall: HealthStatus
    ) -> None:
        icons = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "❌",
            HealthStatus.UNKNOWN: "❓",
        }
        print(f"\n  Overall: {icons[overall]} {overall.value.upper()}")
        for h in healths:
            avail = f" [{h.availability_state}]" if h.availability_state else ""
            print(f"  {icons[h.status]} {h.name} ({h.resource_type}): "
                  f"{h.provisioning_state}{avail}")
