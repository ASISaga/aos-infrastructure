"""Tests for deployment/orchestrator/cli/resource_mapper.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the module is importable from the test runner's working directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator" / "cli"))

import resource_mapper  # noqa: E402


class TestMapResourceToModule:
    """Unit tests for map_resource_to_module."""

    def test_known_resource_type(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.web/sites") == "modules/functionapp.bicep"

    def test_case_insensitive(self) -> None:
        assert resource_mapper.map_resource_to_module("Microsoft.Web/Sites") == "modules/functionapp.bicep"

    def test_unknown_resource_type(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.unknown/resource") == "_not mapped_"

    def test_storage_account(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.storage/storageaccounts") == "modules/storage.bicep"

    def test_keyvault(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.keyvault/vaults") == "modules/keyvault.bicep"

    def test_service_bus(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.servicebus/namespaces") == "modules/servicebus.bicep"

    def test_api_management(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.apimanagement/service") == "modules/ai-gateway.bicep"

    def test_policy(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.authorization/policyassignments") == "modules/policy.bicep"

    def test_budget(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.consumption/budgets") == "modules/budget.bicep"

    def test_log_analytics(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.operationalinsights/workspaces") == "modules/monitoring.bicep"

    def test_source_controls(self) -> None:
        assert resource_mapper.map_resource_to_module("microsoft.web/sites/sourcecontrols") == "modules/functionapp.bicep"


class TestFormatInventoryTable:
    """Unit tests for format_inventory_table."""

    def test_empty_resources(self) -> None:
        result = resource_mapper.format_inventory_table([])
        assert "| Resource | Type | Location | Bicep Module |" in result
        # Only header rows, no data rows
        assert result.count("\n") == 1

    def test_single_resource(self) -> None:
        resources = [{"name": "my-kv", "type": "Microsoft.KeyVault/vaults", "location": "eastus"}]
        result = resource_mapper.format_inventory_table(resources)
        assert "my-kv" in result
        assert "modules/keyvault.bicep" in result
        assert "eastus" in result

    def test_multiple_resources_sorted_by_type(self) -> None:
        resources = [
            {"name": "my-sb", "type": "Microsoft.ServiceBus/namespaces", "location": "eastus"},
            {"name": "my-kv", "type": "Microsoft.KeyVault/vaults", "location": "eastus"},
        ]
        result = resource_mapper.format_inventory_table(resources)
        lines = result.split("\n")
        # Header (2 lines) + 2 data lines
        assert len(lines) == 4
        # KeyVault should come before ServiceBus alphabetically by type
        assert "KeyVault" in lines[2]
        assert "ServiceBus" in lines[3]

    def test_unknown_type_shows_not_mapped(self) -> None:
        resources = [{"name": "custom", "type": "Custom/Type", "location": "westus"}]
        result = resource_mapper.format_inventory_table(resources)
        assert "_not mapped_" in result


class TestModuleMapCompleteness:
    """Ensure all expected Azure resource types are in the mapping."""

    EXPECTED_TYPES = [
        "microsoft.operationalinsights/workspaces",
        "microsoft.insights/components",
        "microsoft.storage/storageaccounts",
        "microsoft.servicebus/namespaces",
        "microsoft.keyvault/vaults",
        "microsoft.cognitiveservices/accounts",
        "microsoft.web/sites",
        "microsoft.web/serverfarms",
        "microsoft.web/sites/sourcecontrols",
        "microsoft.apimanagement/service",
        "microsoft.machinelearningservices/workspaces",
        "microsoft.machinelearningservices/registries",
    ]

    @pytest.mark.parametrize("resource_type", EXPECTED_TYPES)
    def test_type_is_mapped(self, resource_type: str) -> None:
        result = resource_mapper.map_resource_to_module(resource_type)
        assert result != "_not mapped_", f"{resource_type} should be in MODULE_MAP"
