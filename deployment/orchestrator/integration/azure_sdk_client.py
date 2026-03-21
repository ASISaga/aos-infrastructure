"""Azure SDK client — typed, closed-loop wrapper around Azure Management SDKs.

``AzureSDKClient`` provides a **type-safe, SDK-native** interface to Azure
Resource Management, Cost Management, and resource health APIs.  It replaces
the CLI-subprocess calls (``az resource list``, ``az consumption …``, etc.)
with direct SDK calls that enable:

* **Structured results** — Python objects instead of parsed JSON stdout.
* **Graceful degradation** — when the Azure SDK packages are not installed
  the client falls back to ``az`` CLI subprocess calls automatically.
* **State awareness** — methods to observe current infrastructure state as a
  foundation for the OODA-loop orchestration pattern.

Usage
-----
>>> client = AzureSDKClient.create("sub-123", "rg-aos-dev")
>>> resources = client.list_resources()
>>> cost = client.get_current_cost(period_days=30)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

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
    def from_str(cls, value: str) -> "ProvisioningState":
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
        """Return ``True`` when the resource is in any terminal state."""
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
    """Cost data for a resource group or subscription scope."""

    currency: str = "USD"
    total_cost: float = 0.0
    period_start: str = ""
    period_end: str = ""
    by_service: list[dict[str, Any]] = field(default_factory=list)
    forecast_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "total_cost": self.total_cost,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "by_service": self.by_service,
            "forecast_cost": self.forecast_cost,
        }


@dataclass
class DeploymentState:
    """State of an ARM deployment."""

    name: str
    provisioning_state: ProvisioningState
    timestamp: str = ""
    duration: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    operations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provisioning_state": self.provisioning_state.value,
            "timestamp": self.timestamp,
            "duration": self.duration,
            "error": self.error,
            "operations_count": len(self.operations),
        }


@dataclass
class InfrastructureSnapshot:
    """Complete observed state of the infrastructure at a point in time."""

    resource_group: str
    timestamp: str
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
# Azure SDK availability check
# ---------------------------------------------------------------------------

def _is_azure_sdk_available() -> bool:
    """Return ``True`` if the Azure Management SDK packages are importable."""
    try:
        import importlib
        importlib.import_module("azure.identity")
        importlib.import_module("azure.mgmt.resource")
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Azure SDK Client
# ---------------------------------------------------------------------------

class AzureSDKClient:
    """Typed Azure SDK client with automatic CLI fallback.

    When the Azure Management SDK packages (``azure-identity``,
    ``azure-mgmt-resource``, ``azure-mgmt-costmanagement``) are installed,
    the client uses direct SDK calls.  Otherwise it degrades to ``az`` CLI
    subprocess invocations.

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
        self._sdk_available = _is_azure_sdk_available()
        self._resource_client: Any = None
        self._cost_client: Any = None

        if self._sdk_available:
            self._init_sdk_clients()
        else:
            logger.info(
                "Azure SDK packages not installed; using az CLI fallback. "
                "Install with: pip install azure-identity azure-mgmt-resource "
                "azure-mgmt-costmanagement"
            )

    @classmethod
    def create(cls, subscription_id: str, resource_group: str) -> "AzureSDKClient":
        """Factory method for creating an SDK client."""
        return cls(subscription_id=subscription_id, resource_group=resource_group)

    @property
    def sdk_available(self) -> bool:
        """Return ``True`` if the Azure SDK is available."""
        return self._sdk_available

    # ------------------------------------------------------------------
    # Resource Management
    # ------------------------------------------------------------------

    def list_resources(self) -> list[ResourceState]:
        """List all resources in the resource group with full state.

        Returns a list of :class:`ResourceState` objects with provisioning
        state, SKU, location, and tags.
        """
        if self._sdk_available:
            return self._list_resources_sdk()
        return self._list_resources_cli()

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
        if self._sdk_available:
            return self._list_deployments_sdk(top)
        return self._list_deployments_cli(top)

    def get_deployment_operations(self, deployment_name: str) -> list[dict[str, Any]]:
        """Get per-module operations for a specific deployment.

        Returns a list of dicts with keys: ``name``, ``type``, ``state``.
        """
        if self._sdk_available:
            return self._get_deployment_operations_sdk(deployment_name)
        return self._get_deployment_operations_cli(deployment_name)

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
        if self._sdk_available:
            return self._get_cost_sdk(period_days)
        return self._get_cost_cli(period_days)

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

    # ------------------------------------------------------------------
    # Private — SDK implementations
    # ------------------------------------------------------------------

    def _init_sdk_clients(self) -> None:
        """Initialise Azure SDK management clients lazily."""
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]
            from azure.mgmt.resource import ResourceManagementClient  # type: ignore[import]

            credential = DefaultAzureCredential()
            self._resource_client = ResourceManagementClient(
                credential, self.subscription_id
            )
            logger.info("Azure SDK clients initialised successfully")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to initialise Azure SDK clients: %s", exc)
            self._sdk_available = False

    def _list_resources_sdk(self) -> list[ResourceState]:
        """List resources using ResourceManagementClient."""
        results: list[ResourceState] = []
        try:
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
                        (r.provisioning_state or "Unknown")
                    ),
                    resource_id=r.id or "",
                    sku=sku_name,
                    kind=r.kind or "",
                    tags=dict(r.tags) if r.tags else {},
                ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("SDK list_resources failed, falling back to CLI: %s", exc)
            return self._list_resources_cli()
        return results

    def _list_deployments_sdk(self, top: int = 10) -> list[DeploymentState]:
        """List deployments using ResourceManagementClient."""
        results: list[DeploymentState] = []
        try:
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("SDK list_deployments failed, falling back to CLI: %s", exc)
            return self._list_deployments_cli(top)
        return results

    def _get_deployment_operations_sdk(
        self, deployment_name: str
    ) -> list[dict[str, Any]]:
        """Get deployment operations via SDK."""
        results: list[dict[str, Any]] = []
        try:
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SDK get_deployment_operations failed, falling back to CLI: %s", exc
            )
            return self._get_deployment_operations_cli(deployment_name)
        return results

    def _get_cost_sdk(self, period_days: int = 30) -> CostSummary:
        """Query cost data using the Cost Management SDK."""
        try:
            from azure.mgmt.costmanagement import CostManagementClient  # type: ignore[import]
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            credential = DefaultAzureCredential()
            cost_client = CostManagementClient(credential)

            end = date.today()
            start = end - timedelta(days=period_days)

            scope = (
                f"/subscriptions/{self.subscription_id}"
                f"/resourceGroups/{self.resource_group}"
            )

            # Use the query API to aggregate costs by ServiceName
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
                    # row format: [cost, service_name, currency]
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("SDK cost query failed, falling back to CLI: %s", exc)
            return self._get_cost_cli(period_days)

    # ------------------------------------------------------------------
    # Private — CLI fallback implementations
    # ------------------------------------------------------------------

    def _list_resources_cli(self) -> list[ResourceState]:
        """List resources using ``az resource list`` as fallback."""
        result = self._az_json([
            "resource", "list",
            "--resource-group", self.resource_group,
        ])
        if result is None:
            return []
        resources: list[ResourceState] = []
        for r in result:
            sku_name = ""
            sku_data = r.get("sku")
            if isinstance(sku_data, dict):
                sku_name = sku_data.get("name", "")
            resources.append(ResourceState(
                name=r.get("name", ""),
                resource_type=r.get("type", ""),
                location=r.get("location", ""),
                provisioning_state=ProvisioningState.from_str(
                    r.get("provisioningState", "Unknown")
                ),
                resource_id=r.get("id", ""),
                sku=sku_name,
                kind=r.get("kind", ""),
                tags=r.get("tags") or {},
            ))
        return resources

    def _list_deployments_cli(self, top: int = 10) -> list[DeploymentState]:
        """List deployments using ``az deployment group list`` as fallback."""
        result = self._az_json([
            "deployment", "group", "list",
            "--resource-group", self.resource_group,
        ])
        if result is None:
            return []
        deployments: list[DeploymentState] = []
        for dep in result[:top]:
            props = dep.get("properties", {})
            error_msg = ""
            error_data = props.get("error")
            if isinstance(error_data, dict):
                error_msg = error_data.get("message", "")
            deployments.append(DeploymentState(
                name=dep.get("name", ""),
                provisioning_state=ProvisioningState.from_str(
                    props.get("provisioningState", "Unknown")
                ),
                timestamp=props.get("timestamp", ""),
                duration=str(props.get("duration", "")),
                error=error_msg,
            ))
        return deployments

    def _get_deployment_operations_cli(
        self, deployment_name: str
    ) -> list[dict[str, Any]]:
        """Get deployment operations using ``az deployment operation group list``."""
        result = self._az_json([
            "deployment", "operation", "group", "list",
            "--resource-group", self.resource_group,
            "--name", deployment_name,
        ])
        if result is None:
            return []
        operations: list[dict[str, Any]] = []
        for op in result:
            props = op.get("properties", {})
            target = props.get("targetResource")
            if not target:
                continue
            operations.append({
                "name": target.get("resourceName", ""),
                "type": target.get("resourceType", ""),
                "state": props.get("provisioningState", "Unknown"),
            })
        return operations

    def _get_cost_cli(self, period_days: int = 30) -> CostSummary:
        """Get cost data using ``az consumption usage list`` as fallback."""
        end = date.today()
        start = end - timedelta(days=period_days)

        result = self._az_json([
            "consumption", "usage", "list",
            "--start-date", start.isoformat(),
            "--end-date", end.isoformat(),
        ])
        if result is None:
            return CostSummary(
                period_start=start.isoformat(),
                period_end=end.isoformat(),
            )

        rg_lower = self.resource_group.lower()
        rg_usage = [
            u for u in result
            if rg_lower in (u.get("instanceId") or "").lower()
        ]

        total = sum(float(u.get("pretaxCost", 0)) for u in rg_usage)
        currency = rg_usage[0].get("currency", "USD") if rg_usage else "USD"

        service_costs: dict[str, float] = {}
        for u in rg_usage:
            svc = u.get("meterCategory", "Other")
            service_costs[svc] = service_costs.get(svc, 0.0) + float(
                u.get("pretaxCost", 0)
            )
        by_service = [
            {"service": svc, "cost": round(cost, 4)}
            for svc, cost in sorted(service_costs.items(), key=lambda x: -x[1])
        ]

        return CostSummary(
            currency=currency,
            total_cost=round(total, 4),
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            by_service=by_service,
        )

    # ------------------------------------------------------------------
    # Private — az CLI helpers
    # ------------------------------------------------------------------

    def _az_json(self, args: list[str]) -> Any:
        """Run an ``az`` command with ``--output json`` and parse the result."""
        cmd = ["az"] + args + ["--output", "json"]
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "az command failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
