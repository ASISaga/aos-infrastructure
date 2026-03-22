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

### One-time Setup per Repository

After running the infrastructure deployment from `aos-infrastructure`, follow these steps for each repository:

#### 1. Retrieve the `AZURE_CLIENT_ID` for this Function App

The `clientId` Bicep output for each Function App is the User-Assigned Managed Identity client ID. Retrieve it from the Azure portal or CLI:

```bash
# Replace <env> with dev/staging/prod and <APP_NAME> with the Azure-safe app name (e.g. agent-operating-system)
az identity show \
  --resource-group "rg-aos-<env>" \
  --name "id-<APP_NAME>-<env>" \
  --query "clientId" \
  --output tsv
```

#### 2. Create GitHub Environments in the target repository

Create three environments in the repository settings (`Settings → Environments`): **`dev`**, **`staging`**, and **`prod`**.

For each environment, add the following secrets:

| Secret | Value | Where to find |
|--------|-------|---------------|
| `AZURE_CLIENT_ID` | Managed identity client ID for this app | Output of step 1 above |
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

| GitHub Environment | `AZURE_CLIENT_ID` | Function App |
|--------------------|-------------------|-------------|
| `mcp-erpnext-dev` | `az identity show --resource-group rg-aos-dev --name id-mcp-erpnext-dev --query clientId -o tsv` | `func-mcp-erpnext-dev-*` |
| `mcp-erpnext-staging` | `az identity show --resource-group rg-aos-staging --name id-mcp-erpnext-staging --query clientId -o tsv` | `func-mcp-erpnext-staging-*` |
| `mcp-erpnext-prod` | `az identity show --resource-group rg-aos-prod --name id-mcp-erpnext-prod --query clientId -o tsv` | `func-mcp-erpnext-prod-*` |
| `mcp-linkedin-dev` | `az identity show --resource-group rg-aos-dev --name id-mcp-linkedin-dev --query clientId -o tsv` | `func-mcp-linkedin-dev-*` |
| `mcp-linkedin-staging` | `az identity show --resource-group rg-aos-staging --name id-mcp-linkedin-staging --query clientId -o tsv` | `func-mcp-linkedin-staging-*` |
| `mcp-linkedin-prod` | `az identity show --resource-group rg-aos-prod --name id-mcp-linkedin-prod --query clientId -o tsv` | `func-mcp-linkedin-prod-*` |
| `mcp-reddit-dev` | `az identity show --resource-group rg-aos-dev --name id-mcp-reddit-dev --query clientId -o tsv` | `func-mcp-reddit-dev-*` |
| `mcp-reddit-staging` | `az identity show --resource-group rg-aos-staging --name id-mcp-reddit-staging --query clientId -o tsv` | `func-mcp-reddit-staging-*` |
| `mcp-reddit-prod` | `az identity show --resource-group rg-aos-prod --name id-mcp-reddit-prod --query clientId -o tsv` | `func-mcp-reddit-prod-*` |
| `mcp-subconscious-dev` | `az identity show --resource-group rg-aos-dev --name id-mcp-subconscious-dev --query clientId -o tsv` | `func-mcp-subconscious-dev-*` |
| `mcp-subconscious-staging` | `az identity show --resource-group rg-aos-staging --name id-mcp-subconscious-staging --query clientId -o tsv` | `func-mcp-subconscious-staging-*` |
| `mcp-subconscious-prod` | `az identity show --resource-group rg-aos-prod --name id-mcp-subconscious-prod --query clientId -o tsv` | `func-mcp-subconscious-prod-*` |

The OIDC federated credentials for `mcp` repo are provisioned by `deployment/phases/04-function-apps.bicep` via the `additionalGithubRepo: 'mcp'` parameter. Run infrastructure deployment first.

## Deployment Trigger Summary

| Event | Target Environment |
|-------|--------------------|
| Push to `main` branch | `dev` |
| GitHub Release published | `prod` |
| `workflow_dispatch` | Selected by user |

## Required Permissions

Each repository's GitHub Actions workflow requires these permissions (already set in the template):

```yaml
permissions:
  id-token: write   # Required for OIDC token exchange
  contents: read    # Required to checkout the repository
```

## Function App Name Discovery

Azure Function App names include a unique 6-character suffix derived from the resource group ID at deployment time (`uniqueString(resourceGroup().id, projectName, environment)`). The deployment workflows discover the exact name at runtime:

```bash
az functionapp list \
  --resource-group "rg-aos-dev" \
  --query "[?starts_with(name, 'func-agent-operating-system-dev-')].name | [0]" \
  --output tsv
```

## Deployment Method

All Function Apps use the **Azure Flex Consumption plan** (`FC1`). Code deployment uses `azure/functions-action@v1` which:
1. Creates a zip package of the application code.
2. Uploads it to the pre-configured blob storage deployment container (`deploy-{appName}`).
3. The Function App automatically loads the new package from blob storage.

No `WEBSITE_RUN_FROM_PACKAGE` URL is needed — the blob container reference is baked into the Function App configuration by the Bicep template.
