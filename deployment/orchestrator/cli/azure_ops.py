"""Azure SDK CLI helper — replaces ``az`` CLI calls in GitHub Actions workflows.

Provides subcommands for common Azure operations using the Azure Management
SDKs (azure-identity, azure-mgmt-resource, azure-mgmt-web, azure-mgmt-monitor,
azure-mgmt-servicebus, azure-keyvault-secrets).

All subcommands authenticate via ``DefaultAzureCredential`` which inherits
the OIDC token from ``azure/login@v3`` in GitHub Actions.

Usage (in workflow YAML)::

    python3 deployment/orchestrator/cli/azure_ops.py resource-group-exists \
        --resource-group rg-aos-dev \
        --subscription-id "${{ secrets.AZURE_SUBSCRIPTION_ID }}"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Azure SDK imports — imported at call-site to give clear errors
# ---------------------------------------------------------------------------


def _credential():  # noqa: ANN202
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential()


def _resource_client(subscription_id: str):  # noqa: ANN202
    from azure.mgmt.resource import ResourceManagementClient
    return ResourceManagementClient(_credential(), subscription_id)


def _web_client(subscription_id: str):  # noqa: ANN202
    from azure.mgmt.web import WebSiteManagementClient
    return WebSiteManagementClient(_credential(), subscription_id)


def _monitor_client(subscription_id: str):  # noqa: ANN202
    from azure.mgmt.monitor import MonitorManagementClient
    return MonitorManagementClient(_credential(), subscription_id)


def _servicebus_client(subscription_id: str):  # noqa: ANN202
    from azure.mgmt.servicebus import ServiceBusManagementClient
    return ServiceBusManagementClient(_credential(), subscription_id)


def _keyvault_mgmt_client(subscription_id: str):  # noqa: ANN202
    from azure.mgmt.resource import ResourceManagementClient
    return ResourceManagementClient(_credential(), subscription_id)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _resource_group_exists(args: argparse.Namespace) -> None:
    """Check if a resource group exists. Outputs 'true' or 'false'."""
    client = _resource_client(args.subscription_id)
    exists = client.resource_groups.check_existence(args.resource_group)
    result = "true" if exists else "false"
    print(result)
    _github_output("exists", result)


def _resource_group_show(args: argparse.Namespace) -> None:
    """Show resource group details as JSON."""
    client = _resource_client(args.subscription_id)
    try:
        rg = client.resource_groups.get(args.resource_group)
        info = {
            "name": rg.name,
            "location": rg.location,
            "provisioning_state": rg.properties.provisioning_state if rg.properties else "Unknown",
            "tags": dict(rg.tags) if rg.tags else {},
        }
        print(json.dumps(info, indent=2))
    except Exception as exc:
        print(f"Resource group '{args.resource_group}' not found: {exc}", file=sys.stderr)
        sys.exit(1)


def _list_resources(args: argparse.Namespace) -> None:
    """List all resources in a resource group."""
    client = _resource_client(args.subscription_id)
    resources = list(client.resources.list_by_resource_group(args.resource_group))

    if args.output == "json":
        result = [
            {"name": r.name, "type": r.type, "location": r.location,
             "provisioningState": r.properties.get("provisioningState", "Unknown") if isinstance(r.properties, dict) else "Unknown"}
            for r in resources
        ]
        print(json.dumps(result, indent=2))
    elif args.output == "table":
        print(f"{'Name':<40} {'Type':<50} {'Location':<15}")
        print("-" * 105)
        for r in resources:
            print(f"{r.name:<40} {r.type:<50} {r.location:<15}")
    elif args.output == "markdown":
        # Pipe-through to resource_mapper if available
        result = [
            {"name": r.name, "type": r.type, "location": r.location}
            for r in resources
        ]
        print(json.dumps(result))
    else:
        # Count only
        print(str(len(resources)))

    if args.query == "failed":
        failed = [r for r in resources
                  if isinstance(r.properties, dict) and r.properties.get("provisioningState") == "Failed"]
        if args.output == "table":
            print(f"\n{'Failed Resources':<40} {'Type':<50}")
            print("-" * 90)
            for r in failed:
                print(f"{r.name:<40} {r.type:<50}")
        else:
            result = [{"name": r.name, "type": r.type} for r in failed]
            print(json.dumps(result, indent=2))


def _list_deployments(args: argparse.Namespace) -> None:
    """List ARM deployments for a resource group."""
    client = _resource_client(args.subscription_id)
    deployments = list(client.deployments.list_by_resource_group(args.resource_group, top=args.top))

    if args.query == "failed":
        deployments = [d for d in deployments if d.properties and d.properties.provisioning_state != "Succeeded"]

    result = []
    for d in deployments:
        entry: dict[str, Any] = {
            "name": d.name,
            "state": d.properties.provisioning_state if d.properties else "Unknown",
            "timestamp": d.properties.timestamp.isoformat() if d.properties and d.properties.timestamp else "",
        }
        if d.properties and d.properties.error:
            entry["error"] = {
                "code": d.properties.error.code or "",
                "message": d.properties.error.message or "",
            }
        result.append(entry)

    if args.output == "table":
        print(f"{'Name':<40} {'State':<15} {'Timestamp':<25}")
        print("-" * 80)
        for d in result:
            print(f"{d['name']:<40} {d['state']:<15} {d['timestamp']:<25}")
    else:
        print(json.dumps(result, indent=2))


def _show_deployment(args: argparse.Namespace) -> None:
    """Show details of a specific deployment."""
    client = _resource_client(args.subscription_id)
    try:
        d = client.deployments.get(args.resource_group, args.name)
        result: dict[str, Any] = {
            "state": d.properties.provisioning_state if d.properties else "Unknown",
            "timestamp": d.properties.timestamp.isoformat() if d.properties and d.properties.timestamp else "",
        }
        if d.properties and d.properties.error:
            result["error"] = {
                "code": d.properties.error.code or "",
                "message": d.properties.error.message or "",
            }
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Deployment '{args.name}' not found: {exc}", file=sys.stderr)
        sys.exit(1)


def _list_function_apps(args: argparse.Namespace) -> None:
    """List Function Apps in a resource group."""
    client = _web_client(args.subscription_id)
    apps = list(client.web_apps.list_by_resource_group(args.resource_group))
    func_apps = [a for a in apps if a.kind and "functionapp" in a.kind.lower()]

    if args.output == "names":
        for app in func_apps:
            print(app.name)
    elif args.output == "hostnames":
        for app in func_apps:
            print(f"{app.name}\t{app.default_host_name or ''}")
    else:
        result = [
            {"name": a.name, "hostname": a.default_host_name or "", "state": a.state or "Unknown"}
            for a in func_apps
        ]
        print(json.dumps(result, indent=2))


def _show_source_control(args: argparse.Namespace) -> None:
    """Show source control configuration for a web/function app."""
    client = _web_client(args.subscription_id)
    try:
        sc = client.web_apps.get_source_control(args.resource_group, args.name)
        result = {
            "repoUrl": sc.repo_url or "",
            "branch": sc.branch or "",
            "isManualIntegration": sc.is_manual_integration if sc.is_manual_integration is not None else True,
        }
        print(json.dumps(result, indent=2))
    except Exception:
        # Source control not configured
        print(json.dumps({"repoUrl": "", "branch": "", "isManualIntegration": True}))


def _function_app_status(args: argparse.Namespace) -> None:
    """Get Function App deployment status (name, hostname, state, resource ID)."""
    client = _web_client(args.subscription_id)
    try:
        app = client.web_apps.get(args.resource_group, args.name)
        result = {
            "name": app.name,
            "hostname": app.default_host_name or "",
            "state": app.state or "Unknown",
            "resourceId": app.id or "",
        }
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Function App '{args.name}' not found: {exc}", file=sys.stderr)
        sys.exit(1)


def _list_activity_logs(args: argparse.Namespace) -> None:
    """List activity log entries (errors/warnings) for the last N hours."""
    from datetime import datetime, timedelta, timezone
    client = _monitor_client(args.subscription_id)

    hours = int(args.hours) if args.hours else 24
    start_time = datetime.now(timezone.utc) - timedelta(hours=hours)

    filter_str = (
        f"eventTimestamp ge '{start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
        f" and resourceGroupName eq '{args.resource_group}'"
    )

    logs = list(client.activity_logs.list(filter=filter_str))
    entries = []
    for log in logs:
        level = log.level.value if log.level else ""
        if level not in ("Error", "Warning", "Critical"):
            continue
        entries.append({
            "time": log.event_timestamp.isoformat() if log.event_timestamp else "",
            "level": level,
            "operation": log.operation_name.localized_value if log.operation_name else "",
            "status": log.status.localized_value if log.status else "",
        })

    if args.output == "table":
        print(f"{'Time':<25} {'Level':<10} {'Operation':<50} {'Status':<20}")
        print("-" * 105)
        for e in entries:
            print(f"{e['time']:<25} {e['level']:<10} {e['operation']:<50} {e['status']:<20}")
    else:
        print(json.dumps(entries, indent=2))


def _list_metrics(args: argparse.Namespace) -> None:
    """Get metrics for a resource."""
    from datetime import datetime, timedelta, timezone
    client = _monitor_client(args.subscription_id)

    hours = int(args.hours) if args.hours else 4
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)
    timespan = f"{start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    try:
        response = client.metrics.list(
            resource_uri=args.resource_id,
            metricnames=args.metric,
            timespan=timespan,
            aggregation=args.aggregation or "Total",
            interval=args.interval or "PT1H",
        )
        result = []
        for metric in response.value:
            for ts in (metric.timeseries or []):
                for dp in (ts.data or []):
                    entry: dict[str, Any] = {"timestamp": dp.time_stamp.isoformat() if dp.time_stamp else ""}
                    if dp.total is not None:
                        entry["total"] = dp.total
                    if dp.average is not None:
                        entry["average"] = dp.average
                    result.append(entry)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Failed to get metrics: {exc}", file=sys.stderr)
        print("[]")


def _list_servicebus_namespaces(args: argparse.Namespace) -> None:
    """List Service Bus namespaces in a resource group."""
    client = _servicebus_client(args.subscription_id)
    try:
        namespaces = list(client.namespaces.list_by_resource_group(args.resource_group))
        result = [
            {"name": ns.name, "status": ns.status or "Unknown", "location": ns.location}
            for ns in namespaces
        ]
        if args.output == "names":
            for ns in namespaces:
                print(ns.name)
        else:
            print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Failed to list Service Bus namespaces: {exc}", file=sys.stderr)
        print("[]")


def _show_servicebus_namespace(args: argparse.Namespace) -> None:
    """Show Service Bus namespace status."""
    client = _servicebus_client(args.subscription_id)
    try:
        ns = client.namespaces.get(args.resource_group, args.name)
        print(json.dumps({"name": ns.name, "status": ns.status or "Unknown"}, indent=2))
    except Exception as exc:
        print(f"Namespace '{args.name}' not found: {exc}", file=sys.stderr)
        sys.exit(1)


def _show_resource(args: argparse.Namespace) -> None:
    """Show a specific resource by name. Searches all resources in the RG."""
    client = _resource_client(args.subscription_id)
    resources = list(client.resources.list_by_resource_group(args.resource_group))
    matched = [r for r in resources if r.name == args.name]
    if not matched:
        print(f"Resource '{args.name}' not found in '{args.resource_group}'.", file=sys.stderr)
        sys.exit(1)
    r = matched[0]
    result = {
        "name": r.name,
        "type": r.type,
        "location": r.location,
        "id": r.id,
        "provisioningState": r.properties.get("provisioningState", "Unknown") if isinstance(r.properties, dict) else "Unknown",
        "tags": dict(r.tags) if r.tags else {},
    }
    print(json.dumps(result, indent=2))


def _list_keyvaults(args: argparse.Namespace) -> None:
    """List Key Vaults in a resource group and output the first vault URI."""
    client = _resource_client(args.subscription_id)
    resources = list(client.resources.list_by_resource_group(
        args.resource_group,
        filter="resourceType eq 'Microsoft.KeyVault/vaults'",
    ))
    vaults = []
    for r in resources:
        vault_uri = ""
        if isinstance(r.properties, dict):
            vault_uri = r.properties.get("vaultUri", "")
        vaults.append({"name": r.name, "vaultUri": vault_uri})

    if args.output == "uri":
        # Output just the first vault URI (for use in workflow outputs)
        if vaults and vaults[0]["vaultUri"]:
            print(vaults[0]["vaultUri"])
        else:
            print("")
    else:
        print(json.dumps(vaults, indent=2))


# ---------------------------------------------------------------------------
# GitHub Actions output helper
# ---------------------------------------------------------------------------


def _github_output(key: str, value: str) -> None:
    """Write a key=value pair to $GITHUB_OUTPUT if running in Actions."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Azure SDK CLI helper for GitHub Actions workflows",
    )
    parser.add_argument(
        "--subscription-id",
        default=os.environ.get("AZURE_SUBSCRIPTION_ID", ""),
        help="Azure subscription ID (default: $AZURE_SUBSCRIPTION_ID)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- resource-group-exists
    p = sub.add_parser("resource-group-exists", help="Check if resource group exists")
    p.add_argument("--resource-group", required=True)

    # -- resource-group-show
    p = sub.add_parser("resource-group-show", help="Show resource group details")
    p.add_argument("--resource-group", required=True)

    # -- list-resources
    p = sub.add_parser("list-resources", help="List resources in a resource group")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--output", choices=["json", "table", "markdown", "count"], default="json")
    p.add_argument("--query", choices=["all", "failed"], default="all")

    # -- list-deployments
    p = sub.add_parser("list-deployments", help="List ARM deployments")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--output", choices=["json", "table"], default="json")
    p.add_argument("--query", choices=["all", "failed"], default="all")
    p.add_argument("--top", type=int, default=10)

    # -- show-deployment
    p = sub.add_parser("show-deployment", help="Show deployment details")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--name", required=True)

    # -- list-function-apps
    p = sub.add_parser("list-function-apps", help="List Function Apps")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--output", choices=["json", "names", "hostnames"], default="json")

    # -- show-source-control
    p = sub.add_parser("show-source-control", help="Show source control for a web app")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--name", required=True)

    # -- function-app-status
    p = sub.add_parser("function-app-status", help="Get Function App status")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--name", required=True)

    # -- list-activity-logs
    p = sub.add_parser("list-activity-logs", help="List activity log entries")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--hours", default="24")
    p.add_argument("--output", choices=["json", "table"], default="table")

    # -- list-metrics
    p = sub.add_parser("list-metrics", help="Get resource metrics")
    p.add_argument("--resource-id", required=True)
    p.add_argument("--metric", required=True)
    p.add_argument("--hours", default="4")
    p.add_argument("--aggregation", default="Total")
    p.add_argument("--interval", default="PT1H")

    # -- list-servicebus-namespaces
    p = sub.add_parser("list-servicebus-namespaces", help="List Service Bus namespaces")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--output", choices=["json", "names"], default="json")

    # -- show-servicebus-namespace
    p = sub.add_parser("show-servicebus-namespace", help="Show Service Bus namespace status")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--name", required=True)

    # -- show-resource
    p = sub.add_parser("show-resource", help="Show a specific resource")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--name", required=True)

    # -- list-keyvaults
    p = sub.add_parser("list-keyvaults", help="List Key Vaults")
    p.add_argument("--resource-group", required=True)
    p.add_argument("--output", choices=["json", "uri"], default="json")

    args = parser.parse_args()

    dispatch = {
        "resource-group-exists": _resource_group_exists,
        "resource-group-show": _resource_group_show,
        "list-resources": _list_resources,
        "list-deployments": _list_deployments,
        "show-deployment": _show_deployment,
        "list-function-apps": _list_function_apps,
        "show-source-control": _show_source_control,
        "function-app-status": _function_app_status,
        "list-activity-logs": _list_activity_logs,
        "list-metrics": _list_metrics,
        "list-servicebus-namespaces": _list_servicebus_namespaces,
        "show-servicebus-namespace": _show_servicebus_namespace,
        "show-resource": _show_resource,
        "list-keyvaults": _list_keyvaults,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
