"""Tests for the Azure SDK client wrapper.

Validates that ``AzureSDKClient`` correctly wraps Azure SDK operations
and degrades gracefully to CLI when the SDK is not installed.
"""

from __future__ import annotations

import json
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


# ====================================================================
# AzureSDKClient CLI fallback tests
# ====================================================================


class TestAzureSDKClientCliFallback:
    """Test the CLI-fallback paths (SDK not installed)."""

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    def test_sdk_not_available(self, _mock_sdk: mock.MagicMock) -> None:
        client = AzureSDKClient("sub-123", "rg-test")
        assert client.sdk_available is False

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    @mock.patch("subprocess.run")
    def test_list_resources_cli_fallback(
        self, mock_run: mock.MagicMock, _mock_sdk: mock.MagicMock,
    ) -> None:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([
                {
                    "name": "myapp",
                    "type": "Microsoft.Web/sites",
                    "location": "eastus",
                    "provisioningState": "Succeeded",
                    "id": "/sub/rg/myapp",
                    "sku": {"name": "Standard"},
                    "kind": "functionapp",
                    "tags": {"env": "dev"},
                },
            ]),
        )
        client = AzureSDKClient("sub-123", "rg-test")
        resources = client.list_resources()
        assert len(resources) == 1
        assert resources[0].name == "myapp"
        assert resources[0].provisioning_state == ProvisioningState.SUCCEEDED
        assert resources[0].sku == "Standard"

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    @mock.patch("subprocess.run")
    def test_list_resources_cli_failure(
        self, mock_run: mock.MagicMock, _mock_sdk: mock.MagicMock,
    ) -> None:
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="error")
        client = AzureSDKClient("sub-123", "rg-test")
        resources = client.list_resources()
        assert resources == []

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    @mock.patch("subprocess.run")
    def test_list_deployments_cli_fallback(
        self, mock_run: mock.MagicMock, _mock_sdk: mock.MagicMock,
    ) -> None:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([
                {
                    "name": "phase-foundation-dev",
                    "properties": {
                        "provisioningState": "Succeeded",
                        "timestamp": "2026-01-01T10:00:00Z",
                        "duration": "PT5M",
                    },
                },
            ]),
        )
        client = AzureSDKClient("sub-123", "rg-test")
        deployments = client.list_deployments()
        assert len(deployments) == 1
        assert deployments[0].name == "phase-foundation-dev"
        assert deployments[0].provisioning_state == ProvisioningState.SUCCEEDED

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    @mock.patch("subprocess.run")
    def test_get_cost_cli_fallback(
        self, mock_run: mock.MagicMock, _mock_sdk: mock.MagicMock,
    ) -> None:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([
                {
                    "instanceId": "/subscriptions/sub-123/resourceGroups/rg-test/providers/Microsoft.Storage/storageAccounts/sa",
                    "pretaxCost": 15.5,
                    "currency": "USD",
                    "meterCategory": "Storage",
                },
                {
                    "instanceId": "/subscriptions/sub-123/resourceGroups/rg-other/providers/Microsoft.Compute/vm",
                    "pretaxCost": 100.0,
                    "currency": "USD",
                    "meterCategory": "Compute",
                },
            ]),
        )
        client = AzureSDKClient("sub-123", "rg-test")
        cost = client.get_current_cost(30)
        assert cost.total_cost == 15.5  # Only the matching RG
        assert len(cost.by_service) == 1
        assert cost.by_service[0]["service"] == "Storage"

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    @mock.patch("subprocess.run")
    def test_observe_combines_resources_and_deployments(
        self, mock_run: mock.MagicMock, _mock_sdk: mock.MagicMock,
    ) -> None:
        """Test that observe() produces a complete snapshot."""
        def side_effect(cmd, **kwargs):
            # Distinguish between resource list and deployment list
            if "resource" in cmd and "list" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps([
                        {"name": "r1", "type": "T", "location": "l",
                         "provisioningState": "Succeeded", "id": "id1"},
                    ]),
                )
            if "deployment" in cmd and "group" in cmd and "list" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps([
                        {"name": "d1", "properties": {"provisioningState": "Succeeded",
                                                       "timestamp": "2026-01-01"}},
                    ]),
                )
            return mock.Mock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        client = AzureSDKClient("sub-123", "rg-test")
        snapshot = client.observe(include_cost=False)
        assert snapshot.resource_group == "rg-test"
        assert snapshot.total_resources == 1
        assert len(snapshot.deployments) == 1

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    def test_get_resource_by_name(self, _mock_sdk: mock.MagicMock) -> None:
        client = AzureSDKClient("sub-123", "rg-test")
        with mock.patch.object(client, "list_resources", return_value=[
            ResourceState(name="myapp", resource_type="T", location="l",
                          provisioning_state=ProvisioningState.SUCCEEDED),
        ]):
            r = client.get_resource("myapp")
            assert r is not None
            assert r.name == "myapp"

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    def test_get_resource_not_found(self, _mock_sdk: mock.MagicMock) -> None:
        client = AzureSDKClient("sub-123", "rg-test")
        with mock.patch.object(client, "list_resources", return_value=[]):
            assert client.get_resource("nonexistent") is None

    @mock.patch("orchestrator.integration.azure_sdk_client._is_azure_sdk_available", return_value=False)
    def test_create_factory(self, _mock_sdk: mock.MagicMock) -> None:
        client = AzureSDKClient.create("sub-123", "rg-test")
        assert client.subscription_id == "sub-123"
        assert client.resource_group == "rg-test"
