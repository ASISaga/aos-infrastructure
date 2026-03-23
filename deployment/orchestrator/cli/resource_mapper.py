"""Map Azure resource types to their source Bicep modules.

Used by the deployment workflow to generate a resource-to-module inventory
in the GitHub Actions step summary.

Usage (standalone):
    python deployment/orchestrator/cli/resource_mapper.py < resources.json

Usage (library):
    from orchestrator.cli.resource_mapper import map_resource_to_module
    module = map_resource_to_module("microsoft.web/sites")
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

# Azure resource type → Bicep module file (relative to deployment/)
MODULE_MAP: Dict[str, str] = {
    "microsoft.operationalinsights/workspaces": "modules/monitoring.bicep",
    "microsoft.insights/components": "modules/monitoring.bicep",
    "microsoft.insights/actiongroups": "modules/monitoring.bicep",
    "microsoft.storage/storageaccounts": "modules/storage.bicep",
    "microsoft.servicebus/namespaces": "modules/servicebus.bicep",
    "microsoft.keyvault/vaults": "modules/keyvault.bicep",
    "microsoft.cognitiveservices/accounts": "modules/ai-services.bicep",
    "microsoft.machinelearningservices/workspaces": "modules/ai-hub.bicep or ai-project.bicep",
    "microsoft.apimanagement/service": "modules/ai-gateway.bicep",
    "microsoft.web/sites": "modules/functionapp.bicep",
    "microsoft.web/serverfarms": "modules/functionapp.bicep",
    "microsoft.web/sites/sourcecontrols": "modules/functionapp.bicep",
    "microsoft.managedidentity/userassignedidentities": "modules/functionapp.bicep (identity)",
    "microsoft.authorization/policyassignments": "modules/policy.bicep",
    "microsoft.consumption/budgets": "modules/budget.bicep",
}


def map_resource_to_module(resource_type: str) -> str:
    """Return the Bicep module name for a given Azure resource type."""
    return MODULE_MAP.get(resource_type.lower(), "_not mapped_")


def format_inventory_table(resources: List[Dict[str, Any]]) -> str:
    """Format a list of Azure resources as a Markdown table with module mapping.

    Each resource dict should have keys: name, type, location.
    """
    lines = [
        "| Resource | Type | Location | Bicep Module |",
        "|----------|------|----------|-------------|",
    ]
    for r in sorted(resources, key=lambda x: x.get("type", "")):
        module = map_resource_to_module(r.get("type", ""))
        name = r.get("name", "?")
        rtype = r.get("type", "?")
        location = r.get("location", "?")
        lines.append(f"| `{name}` | {rtype} | {location} | `{module}` |")
    return "\n".join(lines)


def main() -> None:
    """Read a JSON array of resources from stdin and print a Markdown table."""
    resources = json.load(sys.stdin)
    print(format_inventory_table(resources))


if __name__ == "__main__":
    main()
