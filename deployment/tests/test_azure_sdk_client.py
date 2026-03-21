"""Tests for the Azure SDK client wrapper.

Validates that ``AzureSDKClient`` correctly wraps Azure SDK operations.
All tests mock the Azure SDK — no CLI fallback paths.
"""

from __future__ import annotations

from unittest import mock

import pytest

from orchestrator.integration.azure_sdk_client import (
    AzureSDKClient,
    CostSummary,
    DeploymentState,
    InfrastructureSnapshot,
    ProvisioningState,
    ResourceState,
)


# ====================================================================
# ProvisioningState tests
# ====================================================================


class TestProvisioningState:
    """Tests for ProvisioningState enum parsing."""

    def test_from_str_succeeded(self) -> None:
        assert ProvisioningState.from_str("Succeeded") == ProvisioningState.SUCCEEDED

    def test_from_str_case_insensitive(self) -> None:
        assert ProvisioningState.from_str("succeeded") == ProvisioningState.SUCCEEDED
        assert ProvisioningState.from_str("SUCCEEDED") == ProvisioningState.SUCCEEDED

    def test_from_str_failed(self) -> None:
        assert ProvisioningState.from_str("Failed") == ProvisioningState.FAILED

    def test_from_str_creating(self) -> None:
        assert ProvisioningState.from_str("Creating") == ProvisioningState.CREATING

    def test_from_str_unknown_value(self) -> None:
        assert ProvisioningState.from_str("SomethingNew") == ProvisioningState.UNKNOWN

    def test_from_str_empty(self) -> None:
        assert ProvisioningState.from_str("") == ProvisioningState.UNKNOWN

    def test_from_str_none(self) -> None:
        assert ProvisioningState.from_str(None) == ProvisioningState.UNKNOWN

    def test_all_states_roundtrip(self) -> None:
        """Every ProvisioningState should parse back from its own value."""
        for state in ProvisioningState:
            assert ProvisioningState.from_str(state.value) == state


# ====================================================================
# ResourceState tests
# ====================================================================


class TestResourceState:
    """Tests for ResourceState dataclass."""

    def test_is_healthy(self) -> None:
        r = ResourceState(
            name="test", resource_type="Microsoft.Storage/storageAccounts",
            location="eastus", provisioning_state=ProvisioningState.SUCCEEDED,
        )
        assert r.is_healthy is True

    def test_not_healthy_when_failed(self) -> None:
        r = ResourceState(
            name="test", resource_type="Microsoft.Storage/storageAccounts",
            location="eastus", provisioning_state=ProvisioningState.FAILED,
        )
        assert r.is_healthy is False

    def test_is_terminal_succeeded(self) -> None:
        r = ResourceState(
            name="test", resource_type="t", location="l",
            provisioning_state=ProvisioningState.SUCCEEDED,
        )
        assert r.is_terminal is True

    def test_is_terminal_failed(self) -> None:
        r = ResourceState(
            name="test", resource_type="t", location="l",
            provisioning_state=ProvisioningState.FAILED,
        )
        assert r.is_terminal is True

    def test_not_terminal_creating(self) -> None:
        r = ResourceState(
            name="test", resource_type="t", location="l",
            provisioning_state=ProvisioningState.CREATING,
        )
        assert r.is_terminal is False

    def test_to_dict(self) -> None:
        r = ResourceState(
            name="myapp", resource_type="Microsoft.Web/sites",
            location="westus", provisioning_state=ProvisioningState.SUCCEEDED,
            tags={"env": "dev"},
        )
        d = r.to_dict()
        assert d["name"] == "myapp"
        assert d["provisioning_state"] == "Succeeded"
        assert d["tags"] == {"env": "dev"}

    def test_not_healthy_when_creating(self) -> None:
        r = ResourceState(
            name="test", resource_type="t", location="l",
            provisioning_state=ProvisioningState.CREATING,
        )
        assert r.is_healthy is False

    def test_is_terminal_canceled(self) -> None:
        r = ResourceState(
            name="test", resource_type="t", location="l",
            provisioning_state=ProvisioningState.CANCELED,
        )
        assert r.is_terminal is True


# ====================================================================
# CostSummary tests
# ====================================================================


class TestCostSummary:
    """Tests for CostSummary dataclass."""

    def test_defaults(self) -> None:
        c = CostSummary()
        assert c.currency == "USD"
        assert c.total_cost == 0.0
        assert c.by_service == []

    def test_to_dict(self) -> None:
        c = CostSummary(
            currency="EUR", total_cost=42.5, period_start="2026-01-01",
            period_end="2026-01-31", by_service=[{"service": "Storage", "cost": 10.0}],
        )
        d = c.to_dict()
        assert d["currency"] == "EUR"
        assert d["total_cost"] == 42.5
        assert len(d["by_service"]) == 1


# ====================================================================
# DeploymentState tests
# ====================================================================


class TestDeploymentState:
    """Tests for DeploymentState dataclass."""

    def test_defaults(self) -> None:
        d = DeploymentState()
        assert d.name == ""
        assert d.provisioning_state == ProvisioningState.UNKNOWN

    def test_to_dict(self) -> None:
        d = DeploymentState(
            name="phase-1",
            provisioning_state=ProvisioningState.SUCCEEDED,
            timestamp="2026-01-01T10:00:00Z",
        )
        result = d.to_dict()
        assert result["name"] == "phase-1"
        assert result["provisioning_state"] == "Succeeded"


# ====================================================================
# InfrastructureSnapshot tests
# ====================================================================


class TestInfrastructureSnapshot:
    """Tests for InfrastructureSnapshot aggregation."""

    @staticmethod
    def _make_resource(name: str, state: ProvisioningState) -> ResourceState:
        return ResourceState(
            name=name, resource_type="Microsoft.Storage/storageAccounts",
            location="eastus", provisioning_state=state,
        )

    def test_total_and_healthy_counts(self) -> None:
        snap = InfrastructureSnapshot(
            resource_group="rg-test", timestamp="2026-01-01T00:00:00Z",
            resources=[
                self._make_resource("a", ProvisioningState.SUCCEEDED),
                self._make_resource("b", ProvisioningState.SUCCEEDED),
                self._make_resource("c", ProvisioningState.FAILED),
            ],
        )
        assert snap.total_resources == 3
        assert snap.healthy_resources == 2
        assert len(snap.unhealthy_resources) == 1
        assert snap.unhealthy_resources[0].name == "c"

    def test_empty_snapshot(self) -> None:
        snap = InfrastructureSnapshot(
            resource_group="rg-test", timestamp="2026-01-01T00:00:00Z",
        )
        assert snap.total_resources == 0
        assert snap.healthy_resources == 0

    def test_to_dict(self) -> None:
        snap = InfrastructureSnapshot(
            resource_group="rg-test",
            timestamp="2026-01-01T00:00:00Z",
            resources=[self._make_resource("a", ProvisioningState.SUCCEEDED)],
            cost=CostSummary(total_cost=10.0),
        )
        d = snap.to_dict()
        assert d["total_resources"] == 1
        assert d["healthy_resources"] == 1
        assert d["cost"]["total_cost"] == 10.0


# ====================================================================
# AzureSDKClient tests (mocked SDK)
# ====================================================================


class TestAzureSDKClient:
    """Test AzureSDKClient using mocked Azure SDK."""

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_create(self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock) -> None:
        client = AzureSDKClient.create("sub-123", "rg-test")
        assert client.subscription_id == "sub-123"
        assert client.resource_group == "rg-test"
        mock_cred.assert_called_once()
        mock_rmc.assert_called_once()

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_list_resources(self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock) -> None:
        mock_resource = mock.MagicMock()
        mock_resource.name = "myapp"
        mock_resource.type = "Microsoft.Web/sites"
        mock_resource.location = "eastus"
        mock_resource.provisioning_state = "Succeeded"
        mock_resource.id = "/sub/rg/myapp"
        mock_resource.sku = mock.MagicMock(name="Standard")
        mock_resource.sku.name = "Standard"
        mock_resource.kind = "functionapp"
        mock_resource.tags = {"env": "dev"}

        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.resources.list_by_resource_group.return_value = [mock_resource]

        client = AzureSDKClient("sub-123", "rg-test")
        resources = client.list_resources()
        assert len(resources) == 1
        assert resources[0].name == "myapp"
        assert resources[0].provisioning_state == ProvisioningState.SUCCEEDED
        assert resources[0].sku == "Standard"

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_get_resource_found(self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock) -> None:
        mock_resource = mock.MagicMock()
        mock_resource.name = "myapp"
        mock_resource.type = "T"
        mock_resource.location = "l"
        mock_resource.provisioning_state = "Succeeded"
        mock_resource.id = "id1"
        mock_resource.sku = None
        mock_resource.kind = ""
        mock_resource.tags = None

        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.resources.list_by_resource_group.return_value = [mock_resource]

        client = AzureSDKClient("sub-123", "rg-test")
        r = client.get_resource("myapp")
        assert r is not None
        assert r.name == "myapp"

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_get_resource_not_found(self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock) -> None:
        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.resources.list_by_resource_group.return_value = []

        client = AzureSDKClient("sub-123", "rg-test")
        assert client.get_resource("nonexistent") is None

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_list_deployments(self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock) -> None:
        mock_dep = mock.MagicMock()
        mock_dep.name = "phase-1"
        mock_dep.properties.provisioning_state = "Succeeded"
        mock_dep.properties.timestamp.isoformat.return_value = "2026-01-01T10:00:00Z"
        mock_dep.properties.duration = "PT5M"
        mock_dep.properties.error = None

        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.deployments.list_by_resource_group.return_value = [mock_dep]

        client = AzureSDKClient("sub-123", "rg-test")
        deps = client.list_deployments()
        assert len(deps) == 1
        assert deps[0].name == "phase-1"
        assert deps[0].provisioning_state == ProvisioningState.SUCCEEDED

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_observe_combines_resources_and_deployments(
        self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock,
    ) -> None:
        """Test that observe() produces a complete snapshot."""
        mock_resource = mock.MagicMock()
        mock_resource.name = "r1"
        mock_resource.type = "T"
        mock_resource.location = "l"
        mock_resource.provisioning_state = "Succeeded"
        mock_resource.id = "id1"
        mock_resource.sku = None
        mock_resource.kind = ""
        mock_resource.tags = None

        mock_dep = mock.MagicMock()
        mock_dep.name = "d1"
        mock_dep.properties.provisioning_state = "Succeeded"
        mock_dep.properties.timestamp.isoformat.return_value = "2026-01-01"
        mock_dep.properties.duration = None
        mock_dep.properties.error = None

        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.resources.list_by_resource_group.return_value = [mock_resource]
        mock_rmc_instance.deployments.list_by_resource_group.return_value = [mock_dep]

        client = AzureSDKClient("sub-123", "rg-test")
        snapshot = client.observe(include_cost=False)
        assert snapshot.resource_group == "rg-test"
        assert snapshot.total_resources == 1
        assert len(snapshot.deployments) == 1

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_list_deployments_top_limit(
        self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock,
    ) -> None:
        """list_deployments(top=2) should return at most 2 results."""
        mock_deps = []
        for i in range(5):
            d = mock.MagicMock()
            d.name = f"dep-{i}"
            d.properties.provisioning_state = "Succeeded"
            d.properties.timestamp.isoformat.return_value = f"2026-01-0{i+1}"
            d.properties.duration = None
            d.properties.error = None
            mock_deps.append(d)

        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.deployments.list_by_resource_group.return_value = mock_deps

        client = AzureSDKClient("sub-123", "rg-test")
        deps = client.list_deployments(top=2)
        assert len(deps) == 2

    @mock.patch("orchestrator.integration.azure_sdk_client.ResourceManagementClient")
    @mock.patch("orchestrator.integration.azure_sdk_client.DefaultAzureCredential")
    def test_list_resources_empty(
        self, mock_cred: mock.MagicMock, mock_rmc: mock.MagicMock,
    ) -> None:
        mock_rmc_instance = mock_rmc.return_value
        mock_rmc_instance.resources.list_by_resource_group.return_value = []

        client = AzureSDKClient("sub-123", "rg-test")
        resources = client.list_resources()
        assert resources == []
