# Azure Resource Graph Queries for AOS Diagnostics

Azure Resource Graph (ARG) enables fast, cross-subscription resource querying using KQL via `az graph query`. Use it to check resource health, find failed or degraded AOS resources, and correlate incidents across environments.

## How to Query

```bash
# Prerequisite
az extension add --name resource-graph

# Basic query
az graph query -q "<KQL>" --query "data[].{name:name, type:type}" -o table
```

Or use the MCP tool:
```
mcp_azure_mcp_extension_cli_generate
  intent: "query Azure Resource Graph to <describe what you want to diagnose>"
  cli-type: "az"
```

---

## Key Tables

| Table | Contains |
|-------|----------|
| `Resources` | All ARM resources (name, type, location, properties, tags) |
| `HealthResources` | Resource health availability status |
| `ServiceHealthResources` | Azure service health events and incidents |
| `ResourceContainers` | Subscriptions, resource groups, management groups |

---

## Diagnostic Query Patterns

### Check Resource Health (AOS Resource Group)

```kql
HealthResources
| where type =~ 'microsoft.resourcehealth/availabilitystatuses'
| where resourceGroup =~ 'rg-aos-dev'
| project name, availabilityState=properties.availabilityState, reasonType=properties.reasonType
```

### Find Unhealthy or Degraded AOS Resources

```kql
HealthResources
| where type =~ 'microsoft.resourcehealth/availabilitystatuses'
| where properties.availabilityState != 'Available'
| project name, state=properties.availabilityState, reason=properties.reasonType, summary=properties.summary
```

### Query Active Service Health Incidents

```kql
ServiceHealthResources
| where type =~ 'microsoft.resourcehealth/events'
| where properties.Status == 'Active'
| project name, title=properties.Title, impact=properties.Impact, status=properties.Status
```

### Find Failed or Stuck Deployments

```kql
Resources
| where properties.provisioningState != 'Succeeded'
| project name, type, resourceGroup, provisioningState=properties.provisioningState
```

### Find AOS Function Apps in Stopped/Error State

```kql
Resources
| where type =~ 'microsoft.web/sites'
| where resourceGroup startswith 'rg-aos'
| where properties.state != 'Running'
| project name, state=properties.state, resourceGroup, location
```

### Find AOS Storage Accounts

```kql
Resources
| where type =~ 'microsoft.storage/storageaccounts'
| where resourceGroup startswith 'rg-aos'
| project name, kind=kind, sku=sku.name, resourceGroup, location
```

### Find AOS Service Bus Namespaces

```kql
Resources
| where type =~ 'microsoft.servicebus/namespaces'
| where resourceGroup startswith 'rg-aos'
| project name, sku=sku.name, provisioningState=properties.provisioningState, resourceGroup
```

---

## Tips

- Use `=~` for case-insensitive type matching (resource types are lowercase in ARG)
- Navigate nested properties with `properties.fieldName`
- Use `--first N` to limit result count: `az graph query -q "..." --first 50`
- Use `--subscriptions <sub-id>` to scope to a specific subscription
- Combine ARG health data with KQL logs (see [kql-queries.md](kql-queries.md)) for full picture
- Check `HealthResources` before deep-diving into application logs

## More Resources

- [Azure Resource Graph documentation](https://learn.microsoft.com/azure/governance/resource-graph/overview)
- [ARG table reference](https://learn.microsoft.com/azure/governance/resource-graph/reference/supported-tables-resources)
