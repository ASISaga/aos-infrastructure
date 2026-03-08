---
name: azure-troubleshooting
description: |
  Debug and troubleshoot production Azure infrastructure issues in the Agent Operating System. Covers systematic diagnostics, log analysis with KQL, resource health checks, and resolution of deployment failures, performance problems, and connectivity issues.
  USE FOR: debug Azure issues, troubleshoot deployment failures, analyze logs with KQL, fix connectivity issues, resolve resource provisioning errors, investigate health probe failures, check resource health, view application logs, find root cause of errors, diagnose AOS Function App issues, fix Service Bus connection failures, resolve Key Vault access problems.
  DO NOT USE FOR: deploying infrastructure (use deployment-error-fixer), creating new resources (use azure-prepare), setting up monitoring alerts (use azure-observability), cost analysis (use azure-cost-optimization).
license: MIT
metadata:
  author: ASISaga
  version: "2.0"
  category: azure-infrastructure
  role: troubleshooting-specialist
allowed-tools: Bash(az:*) Bash(gh:*) Read
---

# Azure Troubleshooting Skill

## Description
Expert skill for diagnosing and resolving Azure infrastructure issues in the Agent Operating System (AOS). This skill provides systematic troubleshooting procedures, common error patterns, MCP tool integrations, and resolution strategies for the most frequent issues encountered in Azure deployments.

## Triggers

Activate this skill when user wants to:
- Debug or troubleshoot production issues
- Diagnose errors in Azure services
- Analyze application logs or metrics with KQL
- Fix image pull, cold start, or health probe issues
- Investigate why Azure resources are failing
- Find root cause of application errors
- Diagnose AOS-specific failures (Function App, Service Bus, Key Vault)

## When to Use This Skill
- Deployment failures in Azure
- Performance degradation of deployed resources
- Connectivity issues between Azure services
- Resource provisioning errors
- Configuration problems
- After automated diagnostics detect issues

## Quick Diagnosis Flow

1. **Identify symptoms** — What's failing? Error code? Service?
2. **Check resource health** — Is Azure healthy? Use AppLens or Resource Health API.
3. **Review logs** — What do logs show? Use KQL queries in Log Analytics.
4. **Analyze metrics** — Performance patterns with Azure Monitor.
5. **Investigate recent changes** — What changed? Check Activity Log.

## MCP Tools

When Azure MCP is enabled, prefer these tools over CLI for faster diagnostics:

### AppLens (AI-Powered Diagnostics)
```
mcp_azure_mcp_applens
  intent: "diagnose issues with <resource-name>"
  command: "diagnose"
  parameters:
    resourceId: "<resource-id>"

Provides:
- Automated issue detection
- Root cause analysis
- Remediation recommendations
```

### Azure Monitor (Logs & Metrics)
```
mcp_azure_mcp_monitor
  intent: "query logs for <resource-name>"
  command: "logs_query"
  parameters:
    workspaceId: "<log-analytics-workspace-id>"
    query: "<KQL-query>"
```

See [KQL Query Reference](references/kql-queries.md) for common diagnostic queries.

### Resource Health
```
mcp_azure_mcp_resourcehealth
  intent: "check health status of <resource-name>"
  command: "get"
  parameters:
    resourceId: "<resource-id>"
```

### Azure Resource Graph (Cross-Resource Diagnostics)
Use the Resource Graph for fast cross-subscription queries to find failed or unhealthy resources:
```bash
# Requires: az extension add --name resource-graph
az graph query -q "HealthResources | where properties.availabilityState != 'Available' | project name, state=properties.availabilityState"
```
See [Azure Resource Graph Queries](references/azure-resource-graph.md) for diagnostic query patterns.

---

## Common Azure Issues and Resolutions

### 1. Deployment Failures

#### Issue: Resource Name Already Exists
**Symptoms:**
```
Error: A resource with the name 'aos-storage' already exists
Code: ResourceExists
```

**Resolution:**
```bash
# Check if resource exists
az resource list --name aos-storage

# Delete if not needed, or use different name in template
az resource delete --ids $(az resource list --name aos-storage --query "[0].id" -o tsv)
```

#### Issue: Quota Exceeded
**Symptoms:**
```
Error: Operation could not be completed as it results in exceeding approved quota
Code: QuotaExceeded
```

**Diagnosis:**
```bash
# Check current quota usage
az vm list-usage --location eastus --query "[?currentValue >= maximumValue]" -o table
```

**Resolution:**
1. Request quota increase through Azure portal
2. Use different region with available capacity
3. Delete unused resources to free up quota
4. Use smaller SKUs if applicable

#### Issue: Invalid Template (BCP Errors)
**Symptoms:**
```
Error BCP029: The resource type is not valid
Error BCP033: Expected value type mismatch
```

**Resolution:**
- Use deployment-error-fixer skill for automatic fixes
- Validate Bicep templates: `az bicep build --file template.bicep`
- Check API version compatibility
- Verify parameter types

### 2. Performance Issues

#### Issue: High Response Time in Function Apps
**Symptoms:**
- Slow API responses (>5 seconds)
- Timeout errors

**Diagnosis:**
```bash
# Check Function App metrics
RESOURCE_ID=$(az functionapp show -g rg-aos-dev -n aos-func --query id -o tsv)

az monitor metrics list \
  --resource $RESOURCE_ID \
  --metric "AverageResponseTime" \
  --aggregation Average
```

**Common Causes:**
1. Cold starts (function went idle)
2. Under-provisioned resources
3. Inefficient code
4. Slow external dependencies
5. Throttling

**Resolution:**
```bash
# Enable Always On (Premium/Dedicated plans)
az functionapp config set -g rg-aos-dev -n aos-func --always-on true

# Scale up (increase resources)
az functionapp plan update -g rg-aos-dev -n aos-plan --sku P1V2

# Scale out (more instances)
az functionapp plan update -g rg-aos-dev -n aos-plan --number-of-workers 3
```

### 3. Connectivity Issues

#### Issue: Service Bus Connection Failed
**Symptoms:**
```
Error: ServiceUnavailable
Error: Unauthorized
```

**Resolution:**
1. Verify Service Bus is running
2. Check connection string in Function App settings
3. Verify network rules
4. Check Managed Identity permissions

#### Issue: Storage Account Access Denied
**Symptoms:**
```
Error: AuthorizationPermissionMismatch
```

**Resolution:**
```bash
# Assign required role
PRINCIPAL_ID=$(az functionapp identity show -g rg-aos-dev -n aos-func --query principalId -o tsv)
STORAGE_ID=$(az storage account show -g rg-aos-dev -n aosstoragedev --query id -o tsv)

az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role "Storage Blob Data Contributor" \
  --scope $STORAGE_ID
```

### 4. Key Vault Access Issues

**Symptoms:**
```
Error: Forbidden - No secrets get permission
```

**Resolution:**
```bash
# Add access policy
PRINCIPAL_ID=$(az functionapp identity show -g rg-aos-dev -n aos-func --query principalId -o tsv)

az keyvault set-policy \
  -g rg-aos-dev \
  -n aos-keyvault \
  --object-id $PRINCIPAL_ID \
  --secret-permissions get list
```

## Troubleshooting Workflow

### Step 1: Identify the Problem
- What is failing?
- When did it start?
- What changed recently?
- Is it intermittent or consistent?

### Step 2: Collect Diagnostics
```bash
# Use automated workflow
gh workflow run infrastructure-troubleshooting.yml \
  -f environment=dev \
  -f issue_type=deployment_failure

# Or manual collection
az monitor activity-log list \
  -g rg-aos-dev \
  --query "[?level=='Error']" \
  -o table
```

### Step 3: Analyze Root Cause
- Check error codes and messages
- Review recent changes
- Check for known issues
- Correlate with other events

### Step 4: Implement Fix
- Start with least disruptive fix
- Test in dev first
- Document the fix
- Monitor after applying

### Step 5: Verify Resolution
- Confirm issue resolved
- Check for side effects
- Update monitoring if needed

## Common Error Codes

| Code | Meaning | Resolution |
|------|---------|------------|
| ResourceNotFound | Resource doesn't exist | Verify name, create resource |
| ResourceExists | Already exists | Use different name or delete |
| QuotaExceeded | Limit reached | Request increase or use different region |
| InvalidTemplate | Syntax error | Fix template, use linter |
| Unauthorized | Auth failed | Check credentials, add RBAC |
| Forbidden | Permission denied | Add required permissions |
| Conflict | Conflicting operation | Wait and retry |
| Throttled | Rate limited | Implement backoff |
| ServiceUnavailable | Transient failure | Retry, check Azure status |

## Diagnostic Commands

```bash
# Resource health
az resource list -g rg-aos-dev -o table

# Activity logs
az monitor activity-log list -g rg-aos-dev --max-events 50

# Deployment history
az deployment group list -g rg-aos-dev

# Network connectivity
curl -v https://<endpoint>

# AOS Function App logs
az functionapp logs tail --name <function-app-name> -g rg-aos-dev
az monitor log-analytics query \
  --workspace <workspace-id> \
  --analytics-query "AppRequests | where Success == false | take 20"

# Resource Graph: find all failed resources
az graph query -q "Resources | where properties.provisioningState != 'Succeeded' | project name, type, resourceGroup, provisioningState=properties.provisioningState"
```

### KQL Queries

See [KQL Query Reference](references/kql-queries.md) for commonly used queries:
- Recent errors and exceptions
- Failed and slow requests
- Dependency failures

### Azure Resource Graph

See [Azure Resource Graph Queries](references/azure-resource-graph.md) for:
- Cross-subscription health status queries
- Failed or stuck deployment detection
- Active service health incidents

## Best Practices

**Prevention:**
1. Use Infrastructure as Code
2. Implement monitoring
3. Use health checks
4. Follow naming conventions
5. Test in dev first
6. Document changes
7. Use Managed Identities
8. Enable diagnostic logging

**During Incidents:**
1. Stay calm
2. Collect data first
3. Document steps
4. Communicate updates
5. Use systematic approach
6. Escalate when needed

**After Resolution:**
1. Document root cause
2. Implement preventive measures
3. Update monitoring
4. Share learnings
5. Update runbooks

## Integration with Workflows

Use the infrastructure-troubleshooting workflow for automated diagnostics and comprehensive reports.

## Related Documentation

- [KQL Query Reference](references/kql-queries.md) — Common KQL queries for AOS diagnostics
- [Azure Resource Graph Queries](references/azure-resource-graph.md) — Cross-resource diagnostic patterns
- [Infrastructure Monitoring Workflow](../../workflows/infrastructure-monitoring.yml)
- [Infrastructure Troubleshooting Workflow](../../workflows/infrastructure-troubleshooting.yml)
- [Deployment Error Fixer Skill](../deployment-error-fixer/SKILL.md)

## Summary

This skill provides:
- ✅ Systematic troubleshooting procedures
- ✅ Common Azure error patterns
- ✅ Diagnostic command reference
- ✅ Best practices for prevention
- ✅ Integration with automated workflows
