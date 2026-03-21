"""Azure SDK client — typed, closed-loop wrapper around Azure Management SDKs.

``AzureSDKClient`` provides a **type-safe, SDK-native** interface to Azure
Resource Management, Cost Management, and resource health APIs.  It requires
the Azure SDK packages (``azure-identity``, ``azure-mgmt-resource``,
``azure-mgmt-costmanagement``) to be installed.

This is the sole observation layer for the OODA-loop orchestration pattern.
All infrastructure state queries go through this client — there is no CLI
fallback.

Usage
-----
>>> client = AzureSDKClient("sub-123", "rg-aos-dev")
>>> resources = client.list_resources()
>>> cost = client.get_current_cost(period_days=30)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from azure.identity import DefaultAzureCredential  # type: ignore[import]
from azure.mgmt.resource import ResourceManagementClient  # type: ignore[import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for structured SDK results
# ---------------------------------------------------------------------------

class ProvisioningState(str, Enum):
    """ARM provisioning states for Azure resources."""

    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELED = "Canceled"
    CREATING = "Creating"
    UPDATING = "Updating"
    DELETING = "Deleting"
    ACCEPTED = "Accepted"
    RUNNING = "Running"
    UNKNOWN = "Unknown"

    @classmethod
    def from_str(cls, value: str | None) -> "ProvisioningState":
        """Parse a provisioning state string (case-insensitive)."""
        return cls._LOOKUP.get((value or "").lower(), cls.UNKNOWN)


# Build the lookup once at class definition time.
ProvisioningState._LOOKUP = {  # type: ignore[attr-defined]
    m.value.lower(): m for m in ProvisioningState
}


@dataclass
class ResourceState:
    """Observed state of a single Azure resource."""

    name: str
    resource_type: str
    location: str
    provisioning_state: ProvisioningState
    resource_id: str = ""
    sku: str = ""
    kind: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        """Return ``True`` when the resource is in a terminal-success state."""
        return self.provisioning_state == ProvisioningState.SUCCEEDED

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` when the provisioning state is terminal."""
        return self.provisioning_state in (
            ProvisioningState.SUCCEEDED,
            ProvisioningState.FAILED,
            ProvisioningState.CANCELED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "resource_type": self.resource_type,
            "location": self.location,
            "provisioning_state": self.provisioning_state.value,
            "resource_id": self.resource_id,
            "sku": self.sku,
            "kind": self.kind,
            "tags": self.tags,
        }


@dataclass
class CostSummary:
    """Aggregated cost data for a resource group."""

    currency: str = "USD"
    total_cost: float = 0.0
    period_start: str = ""
    period_end: str = ""
    by_service: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "total_cost": self.total_cost,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "by_service": self.by_service,
        }


@dataclass
class DeploymentState:
    """State of an ARM deployment."""

    name: str = ""
    provisioning_state: ProvisioningState = ProvisioningState.UNKNOWN
    timestamp: str = ""
    duration: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provisioning_state": self.provisioning_state.value,
            "timestamp": self.timestamp,
            "duration": self.duration,
            "error": self.error,
        }


@dataclass
class InfrastructureSnapshot:
    """Complete observed infrastructure state at a point in time."""

    resource_group: str = ""
    timestamp: str = ""
    resources: list[ResourceState] = field(default_factory=list)
    deployments: list[DeploymentState] = field(default_factory=list)
    cost: Optional[CostSummary] = None

    @property
    def total_resources(self) -> int:
        return len(self.resources)

    @property
    def healthy_resources(self) -> int:
        return sum(1 for r in self.resources if r.is_healthy)

    @property
    def unhealthy_resources(self) -> list[ResourceState]:
        return [r for r in self.resources if not r.is_healthy and r.is_terminal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_group": self.resource_group,
            "timestamp": self.timestamp,
            "total_resources": self.total_resources,
            "healthy_resources": self.healthy_resources,
            "unhealthy_count": len(self.unhealthy_resources),
            "cost": self.cost.to_dict() if self.cost else None,
        }


# ---------------------------------------------------------------------------
# Azure SDK Client
# ---------------------------------------------------------------------------

class AzureSDKClient:
    """Typed Azure SDK client for closed-loop infrastructure management.

    Uses the Azure Management SDKs (``azure-identity``,
    ``azure-mgmt-resource``, ``azure-mgmt-costmanagement``) directly.
    No CLI fallback — the SDK packages must be installed.

    Parameters
    ----------
    subscription_id:
        Azure subscription ID.
    resource_group:
        Target resource group name.
    """

    def __init__(
        self,
        subscription_id: str,
        resource_group: str,
    ) -> None:
        self.subscription_id = subscription_id
        self.resource_group = resource_group

        credential = DefaultAzureCredential()
        self._resource_client = ResourceManagementClient(
            credential, self.subscription_id
        )
        logger.info("AzureSDKClient initialised for %s/%s",
                     subscription_id, resource_group)

    @classmethod
    def create(cls, subscription_id: str, resource_group: str) -> "AzureSDKClient":
        """Factory method for creating an SDK client."""
        return cls(subscription_id=subscription_id, resource_group=resource_group)

    # ------------------------------------------------------------------
    # Resource Management
    # ------------------------------------------------------------------

    def list_resources(self) -> list[ResourceState]:
        """List all resources in the resource group with full state.

        Returns a list of :class:`ResourceState` objects with provisioning
        state, SKU, location, and tags.
        """
        results: list[ResourceState] = []
        for r in self._resource_client.resources.list_by_resource_group(
            self.resource_group
        ):
            sku_name = ""
            if r.sku:
                sku_name = r.sku.name or ""
            results.append(ResourceState(
                name=r.name or "",
                resource_type=r.type or "",
                location=r.location or "",
                provisioning_state=ProvisioningState.from_str(
                    r.provisioning_state or "Unknown"
                ),
                resource_id=r.id or "",
                sku=sku_name,
                kind=r.kind or "",
                tags=dict(r.tags) if r.tags else {},
            ))
        return results

    def get_resource(self, resource_name: str) -> Optional[ResourceState]:
        """Get the state of a specific resource by name."""
        resources = self.list_resources()
        for r in resources:
            if r.name.lower() == resource_name.lower():
                return r
        return None

    def list_deployments(self, top: int = 10) -> list[DeploymentState]:
        """List recent ARM deployments for the resource group.

        Parameters
        ----------
        top:
            Maximum number of deployments to return.
        """
        results: list[DeploymentState] = []
        for i, dep in enumerate(
            self._resource_client.deployments.list_by_resource_group(
                self.resource_group
            )
        ):
            if i >= top:
                break
            props = dep.properties
            error_msg = ""
            if props and props.error:
                error_msg = props.error.message or ""
            results.append(DeploymentState(
                name=dep.name or "",
                provisioning_state=ProvisioningState.from_str(
                    (props.provisioning_state or "Unknown") if props else "Unknown"
                ),
                timestamp=(
                    props.timestamp.isoformat() if props and props.timestamp else ""
                ),
                duration=str(props.duration) if props and props.duration else "",
                error=error_msg,
            ))
        return results

    def get_deployment_operations(self, deployment_name: str) -> list[dict[str, Any]]:
        """Get per-module operations for a specific deployment.

        Returns a list of dicts with keys: ``name``, ``type``, ``state``.
        """
        results: list[dict[str, Any]] = []
        for op in self._resource_client.deployment_operations.list(
            self.resource_group, deployment_name
        ):
            props = op.properties
            if not props or not props.target_resource:
                continue
            results.append({
                "name": props.target_resource.resource_name or "",
                "type": props.target_resource.resource_type or "",
                "state": props.provisioning_state or "Unknown",
            })
        return results

    # ------------------------------------------------------------------
    # Cost Management
    # ------------------------------------------------------------------

    def get_current_cost(self, period_days: int = 30) -> CostSummary:
        """Query Azure Cost Management for current resource group spend.

        Uses the Cost Management ``query`` API to aggregate costs by
        service for the specified period.

        Parameters
        ----------
        period_days:
            Number of days to look back.
        """
        from azure.mgmt.costmanagement import CostManagementClient  # type: ignore[import]

        credential = DefaultAzureCredential()
        cost_client = CostManagementClient(credential)

        end = date.today()
        start = end - timedelta(days=period_days)

        scope = (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
        )

        query_body = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start.isoformat() + "T00:00:00Z",
                "to": end.isoformat() + "T23:59:59Z",
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "grouping": [
                    {
                        "type": "Dimension",
                        "name": "ServiceName",
                    }
                ],
            },
        }

        result = cost_client.query.usage(scope, query_body)

        total_cost = 0.0
        by_service: list[dict[str, Any]] = []
        currency = "USD"

        if result.rows:
            for row in result.rows:
                cost_val = float(row[0])
                svc_name = str(row[1]) if len(row) > 1 else "Unknown"
                if len(row) > 2:
                    currency = str(row[2])
                total_cost += cost_val
                by_service.append({
                    "service": svc_name,
                    "cost": round(cost_val, 4),
                })

        by_service.sort(key=lambda x: -x["cost"])

        return CostSummary(
            currency=currency,
            total_cost=round(total_cost, 4),
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            by_service=by_service,
        )

    # ------------------------------------------------------------------
    # Infrastructure Snapshot
    # ------------------------------------------------------------------

    def observe(self, include_cost: bool = False, cost_period_days: int = 30) -> InfrastructureSnapshot:
        """Capture a complete infrastructure snapshot (OODA Observe phase).

        Returns an :class:`InfrastructureSnapshot` combining resource state,
        deployment history, and optionally cost data.

        Parameters
        ----------
        include_cost:
            When ``True``, also queries cost data (slower but enables
            cost-aware decision making).
        cost_period_days:
            Look-back period for cost data.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        resources = self.list_resources()
        deployments = self.list_deployments()
        cost = self.get_current_cost(cost_period_days) if include_cost else None

        return InfrastructureSnapshot(
            resource_group=self.resource_group,
            timestamp=timestamp,
            resources=resources,
            deployments=deployments,
            cost=cost,
        )
