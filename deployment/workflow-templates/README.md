# Azure Function App Deployment Workflow Templates

This directory contains GitHub Actions workflow files for deploying each AOS platform service and MCP server Function App to Azure. Copy the appropriate subdirectory's `deploy.yml` to `.github/workflows/deploy.yml` in the corresponding repository.

## Repository → Azure Function App Mapping

| Repository | Azure Function App (pattern) | Custom Domain |
|------------|------------------------------|---------------|
| [agent-operating-system](https://github.com/ASISaga/agent-operating-system) | `func-agent-operating-system-{env}-*` | `agent-operating-system.asisaga.com` |
| [business-infinity](https://github.com/ASISaga/business-infinity) | `func-business-infinity-{env}-*` | `business-infinity.asisaga.com` |
| [mcp](https://github.com/ASISaga/mcp) | deploys to all 4 MCP Function Apps | — |
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | `func-mcp-erpnext-{env}-*` | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | `func-mcp-linkedin-{env}-*` | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | `func-mcp-reddit-{env}-*` | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | `func-mcp-subconscious-{env}-*` | `subconscious.asisaga.com` |

> **`aos-realm-of-agents`** deploys from [ASISaga/aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) to `func-aos-realm-of-agents-{env}-*`. Copy and adapt `agent-operating-system/deploy.yml`, changing `APP_NAME` to `aos-realm-of-agents`.

## Prerequisites

Infrastructure must be provisioned via **`ASISaga/aos-infrastructure`** before running these deployment workflows. The Bicep templates in phases `01-foundation` and `04-function-apps` create all the required Azure resources and pre-configure GitHub OIDC Workload Identity Federation.

## Decoupled Deployment Pipeline

The provisioning and code deployment workflows are **decoupled** across repositories:

1. **`ASISaga/aos-infrastructure`** (this repo) — Bicep templates provision all Azure resources, including Function Apps and their User-Assigned Managed Identities.
2. After Phase 4 succeeds, the infrastructure workflow automatically:
   - Fetches each Function App's Managed Identity `clientId` via `ManagedServiceIdentityClient` (`azure.mgmt.msi`) — no Azure portal or CLI needed.
   - Stores every `clientId` in Azure Key Vault as `clientid-{app_name}-{environment}`.
   - Dispatches an `infra_provisioned` `repository_dispatch` event to each code repository.
3. **Code repositories** (agent-operating-system, mcp, etc.) trigger on `infra_provisioned` and retrieve `AZURE_CLIENT_ID` from Key Vault, then deploy their Function App code using GitHub OIDC (passwordless).

### One-time Setup per Repository

After running the infrastructure deployment from `aos-infrastructure`, follow these steps for each repository:

#### 1. Retrieve the `AZURE_CLIENT_ID` for this Function App

After infrastructure provisioning, each Function App's Managed Identity `clientId` is stored automatically in Azure Key Vault by the `fetch-identity-client-ids` pipeline step (uses `ManagedServiceIdentityClient` from `azure.mgmt.msi`). Retrieve it:

```python
# Ensure azure-identity and azure-keyvault-secrets are installed:
#   pip install azure-identity azure-keyvault-secrets

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Replace <kv-name>, <APP_NAME>, and <env> with your values
kv_url = "https://<kv-name>.vault.azure.net"
secret_name = "clientid-<APP_NAME>-<env>"

cred = DefaultAzureCredential()
client = SecretClient(vault_url=kv_url, credential=cred)
print(client.get_secret(secret_name).value)
```

> **First run**: When infrastructure is provisioned, the `infra_provisioned` `repository_dispatch` event automatically triggers the code repository's deploy workflow with the `key_vault_url` and `environment` in the payload. The workflow retrieves `AZURE_CLIENT_ID` from Key Vault automatically — no manual step required.

> **Subsequent runs** (push to `main`, releases): The workflow reads `AZURE_CLIENT_ID` from the GitHub environment secret (set up once using the command above).

#### 2. Create GitHub Environments in the target repository

Create three environments in the repository settings (`Settings → Environments`): **`dev`**, **`staging`**, and **`prod`**.

For each environment, add the following secrets:

| Secret | Value | Where to find |
|--------|-------|---------------|
| `AZURE_CLIENT_ID` | Managed identity client ID for this app | Key Vault: `clientid-<APP_NAME>-<env>` (see step 1) |
| `AZURE_TENANT_ID` | Azure AD tenant ID | Azure portal → Microsoft Entra ID → Overview |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | Azure portal → Subscriptions |

Optionally add this environment **variable** (not secret — it is not sensitive):

| Variable | Value | Default |
|----------|-------|---------|
| `AZURE_RESOURCE_GROUP` | Azure resource group name | `rg-aos-<env>` |

#### 3. Copy the workflow file

Copy the `deploy.yml` from the matching subdirectory here to `.github/workflows/deploy.yml` in the target repository.

```bash
# Example for agent-operating-system
cp deployment/workflow-templates/agent-operating-system/deploy.yml \
   /path/to/agent-operating-system/.github/workflows/deploy.yml
```

#### 4. Special setup for the `mcp` monorepo

The `mcp` repository deploys to all four MCP server Function Apps. Each Function App has its **own** managed identity, so `mcp` needs four `AZURE_CLIENT_ID` values — one per app. These are stored in separate GitHub Environments:

| GitHub Environment | Key Vault secret | Function App |
|--------------------|------------------|-------------|
| `mcp-erpnext-dev` | `clientid-mcp-erpnext-dev` | `func-mcp-erpnext-dev-*` |
| `mcp-erpnext-staging` | `clientid-mcp-erpnext-staging` | `func-mcp-erpnext-staging-*` |
| `mcp-erpnext-prod` | `clientid-mcp-erpnext-prod` | `func-mcp-erpnext-prod-*` |
| `mcp-linkedin-dev` | `clientid-mcp-linkedin-dev` | `func-mcp-linkedin-dev-*` |
| `mcp-linkedin-staging` | `clientid-mcp-linkedin-staging` | `func-mcp-linkedin-staging-*` |
| `mcp-linkedin-prod` | `clientid-mcp-linkedin-prod` | `func-mcp-linkedin-prod-*` |
| `mcp-reddit-dev` | `clientid-mcp-reddit-dev` | `func-mcp-reddit-dev-*` |
| `mcp-reddit-staging` | `clientid-mcp-reddit-staging` | `func-mcp-reddit-staging-*` |
| `mcp-reddit-prod` | `clientid-mcp-reddit-prod` | `func-mcp-reddit-prod-*` |
| `mcp-subconscious-dev` | `clientid-mcp-subconscious-dev` | `func-mcp-subconscious-dev-*` |
| `mcp-subconscious-staging` | `clientid-mcp-subconscious-staging` | `func-mcp-subconscious-staging-*` |
| `mcp-subconscious-prod` | `clientid-mcp-subconscious-prod` | `func-mcp-subconscious-prod-*` |

Retrieve each `AZURE_CLIENT_ID` from Key Vault:
```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

kv_url = "https://<kv-name>.vault.azure.net"
cred = DefaultAzureCredential()
client = SecretClient(vault_url=kv_url, credential=cred)
print(client.get_secret("clientid-mcp-<app>-<env>").value)
```

The OIDC federated credentials for `mcp` repo are provisioned by `deployment/phases/04-function-apps.bicep` via the `additionalGithubRepo: 'mcp'` parameter. Run infrastructure deployment first.

#### 5. Add `DEPLOY_DISPATCH_TOKEN` to the infrastructure repository

To enable automatic code deployment on infrastructure provisioning, add a GitHub PAT with `repo` scope as `DEPLOY_DISPATCH_TOKEN` in the `ASISaga/aos-infrastructure` repository (`Settings → Secrets and variables → Actions`). This token is used by the `Signal code repositories` step to dispatch `infra_provisioned` events to each code repository.

## Deployment Trigger Summary

| Event | Target Environment |
|-------|--------------------|
| Push to `main` branch | `dev` |
| GitHub Release published | `prod` |
| `workflow_dispatch` | Selected by user |
| `repository_dispatch` (`infra_provisioned`) | Environment from payload |

## Required Permissions

Each repository's GitHub Actions workflow requires these permissions (already set in the template):

```yaml
permissions:
  id-token: write   # Required for OIDC token exchange
  contents: read    # Required to checkout the repository
```

## Function App Name Discovery

Azure Function App names include a unique 6-character suffix derived from the resource group ID at deployment time (`uniqueString(resourceGroup().id, projectName, environment)`). The deployment workflows discover the exact name at runtime:

```python
# Ensure azure-identity and azure-mgmt-web are installed:
#   pip install azure-identity azure-mgmt-web

from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient

subscription_id = "<your-subscription-id>"
resource_group = "rg-aos-dev"
prefix = "func-agent-operating-system-dev-"

cred = DefaultAzureCredential()
client = WebSiteManagementClient(cred, subscription_id)
for app in client.web_apps.list_by_resource_group(resource_group):
    if (app.name or "").startswith(prefix):
        print(app.name)
        break
```

## Deployment Method

All Function Apps use the **Azure Flex Consumption plan** (`FC1`). Code deployment uses `azure/functions-action@v1` which:
1. Creates a zip package of the application code.
2. Uploads it to the pre-configured blob storage deployment container (`deploy-{appName}`).
3. The Function App automatically loads the new package from blob storage.

No `WEBSITE_RUN_FROM_PACKAGE` URL is needed — the blob container reference is baked into the Function App configuration by the Bicep template.

## Monitoring

### GitHub Actions Monitoring

- **Infrastructure deploys**: Track provisioning status in the `infrastructure-deploy.yml` workflow run. The Step Summary shows per-phase status, resource change counts, and the result of the `infra_provisioned` dispatch.
- **Code deploys**: Each code repository's `deploy.yml` shows Function App deployment status.
- **Drift detection**: `infrastructure-drift-detection.yml` runs on a schedule to detect configuration drift.
- **Health monitoring**: `infrastructure-monitoring.yml` checks Function App health, costs, and performance on demand.
- **Governance**: `infrastructure-governance.yml` enforces Azure Policy and RBAC compliance.

### Azure Monitoring

All Function Apps are connected to a shared **Application Insights** instance (provisioned by Phase 1 `modules/monitoring.bicep`). The following monitoring resources are available:

| Resource | Type | Purpose |
|----------|------|---------|
| Application Insights | `Microsoft.Insights/components` | Live metrics, request traces, exceptions, dependencies |
| Log Analytics Workspace | `Microsoft.OperationalInsights/workspaces` | Centralized log aggregation for all Function Apps |

**Useful KQL queries (Application Insights / Log Analytics):**

```kusto
-- Function App execution summary (last 24h)
requests
| where timestamp > ago(24h)
| summarize count(), avg(duration), countif(success == false) by cloud_RoleName
| order by count_ desc

-- Failed function executions
exceptions
| where timestamp > ago(24h)
| summarize count() by cloud_RoleName, type
| order by count_ desc

-- Slow requests (> 5s)
requests
| where timestamp > ago(24h) and duration > 5000
| project timestamp, cloud_RoleName, name, duration, resultCode
| order by duration desc
```

**Azure Portal alerts** (recommended):
- Set an **Availability alert** on each Function App's health endpoint.
- Set a **Failed requests alert** threshold of > 5% for production.
- Use the **Cost Management budget** provisioned by Phase 5 (`modules/budget.bicep`) to receive email alerts when monthly spend exceeds the configured threshold.

### Deployment Audit Trail

Every infrastructure deploy run uploads audit JSON files as GitHub Actions artifacts (`deployment-audit-<run_id>`) with 90-day retention. These can be used to reconstruct the deployment history and troubleshoot failures.
