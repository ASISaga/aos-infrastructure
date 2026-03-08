# KQL Query Reference

Essential Kusto Query Language (KQL) queries for diagnosing AOS Azure infrastructure issues.

## Prerequisites

- Application Insights or Log Analytics workspace configured for AOS resources
- Diagnostic settings enabled on Function Apps, Service Bus, and Key Vault

---

## Recent Errors

```kql
// Recent exceptions in AOS Function App
AppExceptions
| where TimeGenerated > ago(1h)
| project TimeGenerated, Message, StackTrace
| order by TimeGenerated desc
```

## Failed Requests

```kql
// Failed HTTP requests
AppRequests
| where Success == false
| where TimeGenerated > ago(1h)
| summarize count() by Name, ResultCode
| order by count_ desc
```

## Slow Requests

```kql
// Slow requests (>5 seconds) — identifies severe performance degradation or timeout issues
AppRequests
| where TimeGenerated > ago(1h)
| where DurationMs > 5000
| project TimeGenerated, Name, DurationMs
| order by DurationMs desc
```

## Dependency Failures

```kql
// Dependency failures (Service Bus, Storage, Key Vault)
AppDependencies
| where Success == false
| where TimeGenerated > ago(1h)
| summarize count() by Name, ResultCode, Target
```

## AOS Service Bus Errors

```kql
// Service Bus delivery failures
AzureDiagnostics
| where ResourceType == "NAMESPACES" and Category == "OperationalLogs"
| where TimeGenerated > ago(1h)
| where Level == "Error" or Level == "Warning"
| project TimeGenerated, OperationName, ResultDescription, Level
| order by TimeGenerated desc
```

## AOS Key Vault Access Audit

```kql
// Key Vault access denied events
AzureDiagnostics
| where ResourceProvider == "MICROSOFT.KEYVAULT"
| where TimeGenerated > ago(1h)
| where ResultType != "Success"
| project TimeGenerated, OperationName, ResultType, CallerIPAddress, identity_claim_oid_g
| order by TimeGenerated desc
```

## Deployment Activity

```kql
// Recent deployment operations
AzureActivity
| where TimeGenerated > ago(24h)
| where OperationNameValue has "deployments/write"
| project TimeGenerated, Caller, ActivityStatusValue, Properties
| order by TimeGenerated desc
```

---

## Tips

- Always include time filter: `TimeGenerated > ago(Xh)`
- Limit results with `take 50` for large datasets
- Use `summarize` to aggregate data before analyzing
- Combine with Azure Resource Graph for cross-resource correlation (see [azure-resource-graph.md](azure-resource-graph.md))

## More Resources

- [KQL Quick Reference](https://learn.microsoft.com/azure/data-explorer/kql-quick-reference)
- [Application Insights Queries](https://learn.microsoft.com/azure/azure-monitor/logs/queries)
- [Azure Monitor Log Analytics](https://learn.microsoft.com/azure/azure-monitor/logs/log-query-overview)
