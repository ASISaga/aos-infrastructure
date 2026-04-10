# Azure Function App & Foundry Agent Deployment Workflow Templates

This directory contained GitHub Actions workflow files for deploying each AOS platform
service, MCP server, and C-Suite agent. Moved the appropriate subdirectory's `deploy.yml`
to `.github/workflows/deploy.yml` in the corresponding repository.

Each template is a **thin caller** that delegates all deployment logic to reusable
workflows hosted in this repository (`ASISaga/aos-infrastructure`):

| Reusable Workflow | Purpose |
|---|---|
| [`deploy-function-app.yml`](../../.github/workflows/deploy-function-app.yml) | Deploy a Python Azure Function App via OIDC |
| [`deploy-foundry-agent.yml`](../../.github/workflows/deploy-foundry-agent.yml) | Deploy an agent definition to Azure AI Foundry Agent Service |

Centralising logic in reusable workflows means that all repositories automatically
pick up fixes and improvements when they reference `@main`.

---

## Repository → Deployment Target Mapping

### Azure Function Apps

| Repository | Template | Per-Workflow README | Azure Function App (pattern) | Custom Domain |
|---|---|---|---|---|
| [agent-operating-system](https://github.com/ASISaga/agent-operating-system) | `agent-operating-system/deploy.yml` | [README](agent-operating-system/README.md) | `func-agent-operating-system-{env}-*` | `agent-operating-system.asisaga.com` |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | `aos-realm-of-agents/deploy.yml` | [README](aos-realm-of-agents/README.md) | `func-aos-realm-of-agents-{env}-*` | `aos-realm-of-agents.asisaga.com` |
| [business-infinity](https://github.com/ASISaga/business-infinity) | `business-infinity/deploy.yml` | [README](business-infinity/README.md) | `func-business-infinity-{env}-*` | `business-infinity.asisaga.com` |
| [mcp](https://github.com/ASISaga/mcp) _(monorepo)_ | `mcp/deploy.yml` | [README](mcp/README.md) | all 4 MCP Function Apps in parallel | — |
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | `erpnext.asisaga.com/deploy.yml` | [README](erpnext.asisaga.com/README.md) | `func-mcp-erpnext-{env}-*` | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | `linkedin.asisaga.com/deploy.yml` | [README](linkedin.asisaga.com/README.md) | `func-mcp-linkedin-{env}-*` | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | `reddit.asisaga.com/deploy.yml` | [README](reddit.asisaga.com/README.md) | `func-mcp-reddit-{env}-*` | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | `subconscious.asisaga.com/deploy.yml` | [README](subconscious.asisaga.com/README.md) | `func-mcp-subconscious-{env}-*` | `subconscious.asisaga.com` |

### Azure AI Foundry Agents

| Repository | Template | Per-Workflow README | Deployed To |
|---|---|---|---|
| [ceo-agent](https://github.com/ASISaga/ceo-agent) | `ceo-agent/deploy.yml` | [README](ceo-agent/README.md) | Azure AI Foundry Agent Service |
| [cfo-agent](https://github.com/ASISaga/cfo-agent) | `cfo-agent/deploy.yml` | [README](cfo-agent/README.md) | Azure AI Foundry Agent Service |
| [cto-agent](https://github.com/ASISaga/cto-agent) | `cto-agent/deploy.yml` | [README](cto-agent/README.md) | Azure AI Foundry Agent Service |
| [cso-agent](https://github.com/ASISaga/cso-agent) | `cso-agent/deploy.yml` | [README](cso-agent/README.md) | Azure AI Foundry Agent Service |
| [cmo-agent](https://github.com/ASISaga/cmo-agent) | `cmo-agent/deploy.yml` | [README](cmo-agent/README.md) | Azure AI Foundry Agent Service |

---

## Prerequisites

Infrastructure must be provisioned via **`ASISaga/aos-infrastructure`** before running
these deployment workflows. The Bicep templates in phases `01-foundation`, `03-ai-applications`,
and `04-function-apps` create all required Azure resources and pre-configure GitHub OIDC
Workload Identity Federation.

---

## Architecture: Reusable Workflows

The deployment logic lives entirely in two reusable workflows in `ASISaga/aos-infrastructure`:

```
 ┌─────────────────────────────────┐
 │  agent-operating-system repo    │
 │  .github/workflows/deploy.yml   │
 │  (thin caller — 30 lines)       │
 └────────────────┬────────────────┘
                  │ uses: ASISaga/aos-infrastructure/
                  │       .github/workflows/deploy-function-app.yml@main
                  ▼
 ┌─────────────────────────────────────────────────────────┐
 │  ASISaga/aos-infrastructure                              │
 │  .github/workflows/deploy-function-app.yml (reusable)   │
 │  • Checkout caller's code                                │
 │  • Install Python deps                                   │
 │  • OIDC login (optionally via Key Vault client ID)       │
 │  • Resolve Function App name via azure-mgmt-web          │
 │  • azure/functions-action@v1 deploy                      │
 └─────────────────────────────────────────────────────────┘
```

**Benefits:**
- A single fix in `deploy-function-app.yml` or `deploy-foundry-agent.yml` propagates to all repositories.
- Calling workflows are small (~30 lines) and easy to understand.
- Environment protection rules (approvals, wait timers) remain in each calling repository.

---

## Decoupled Deployment Pipeline

The provisioning and code deployment workflows are **decoupled** across repositories:

1. **`ASISaga/aos-infrastructure`** — Bicep templates provision all Azure resources, including
   Function Apps and their User-Assigned Managed Identities.
2. After Phase 4 succeeds, the infrastructure workflow automatically:
   - Fetches each Function App's Managed Identity `clientId` via `ManagedServiceIdentityClient`.
   - Stores every `clientId` in Azure Key Vault as `clientid-{app_name}-{environment}`.
   - Dispatches an `infra_provisioned` `repository_dispatch` event to each code repository.
3. **Code repositories** trigger on `infra_provisioned` and delegate to the reusable workflow,
   which retrieves `AZURE_CLIENT_ID` from Key Vault automatically on first run.

---

## One-Time Setup per Repository

### Function App Repositories

#### 1. Retrieve the `AZURE_CLIENT_ID` for this Function App

After infrastructure provisioning, retrieve the client ID from Key Vault:

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

kv_url = "https://<kv-name>.vault.azure.net"
secret_name = "clientid-<APP_NAME>-<env>"  # e.g., clientid-agent-operating-system-dev

cred = DefaultAzureCredential()
client = SecretClient(vault_url=kv_url, credential=cred)
print(client.get_secret(secret_name).value)
```

> **First run**: The `infra_provisioned` event triggers this workflow automatically and
> passes the `key_vault_url` — no manual step required.

#### 2. Create GitHub Environments

Create three environments in the repository settings (`Settings → Environments`):
**`dev`**, **`staging`**, and **`prod`**.

For each environment, add these secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Managed identity client ID (from Key Vault) |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Optionally add this environment **variable** (not sensitive):

| Variable | Value | Default |
|---|---|---|
| `AZURE_RESOURCE_GROUP` | Azure resource group name | `rg-aos-<env>` |

#### 3. Copy the workflow file

```bash
cp deployment/workflow-templates/agent-operating-system/deploy.yml \
   /path/to/agent-operating-system/.github/workflows/deploy.yml
```

---

### Azure AI Foundry Agent Repositories

#### 1. Create an `agent.yaml` definition file

Each agent repository must contain an `agent.yaml` at the repository root:

```yaml
name: "CEO Agent"
model: "gpt-4o"
instructions: |
  You are the CEO agent responsible for strategic decision-making...
tools:
  - type: code_interpreter
  - type: file_search
metadata:
  version: "1.0"
```

Supported tool types: `code_interpreter`, `file_search`.

#### 2. Retrieve the Foundry Project Endpoint

After infrastructure provisioning, retrieve the project endpoint from the Azure portal
or Key Vault:

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

kv_url = "https://<kv-name>.vault.azure.net"
cred = DefaultAzureCredential()
client = SecretClient(vault_url=kv_url, credential=cred)
print(client.get_secret("foundry-project-endpoint-<env>").value)
```

#### 3. Create GitHub Environments

Create three environments: **`dev`**, **`staging`**, and **`prod`**. For each, add:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Managed identity client ID for OIDC |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint URL |

#### 4. Copy the workflow file

```bash
cp deployment/workflow-templates/ceo-agent/deploy.yml \
   /path/to/ceo-agent/.github/workflows/deploy.yml
```

---

## Special Setup: `mcp` Monorepo

The `mcp` repository deploys to all four MCP server Function Apps in parallel. Each
Function App has its own managed identity, so `mcp` needs **12 GitHub Environments**
(4 apps × 3 environments):

| GitHub Environment | Key Vault secret | Function App |
|---|---|---|
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

Each environment needs the same secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID` (and optionally `AZURE_RESOURCE_GROUP`).

---

## Deployment Trigger Summary

| Event | Target Environment |
|---|---|
| Push to `main` branch | `dev` |
| GitHub Release published | `prod` |
| `workflow_dispatch` | Selected by user |
| `repository_dispatch` (`infra_provisioned`) | Environment from payload |

---

## Required Permissions

Each calling workflow requires these permissions (already set in the templates):

```yaml
permissions:
  id-token: write   # Required for OIDC token exchange
  contents: read    # Required to checkout the repository
```

---

## Function App Name Discovery

Azure Function App names include a unique 6-character suffix. The reusable workflow
discovers the exact name at runtime using prefix matching via `azure-mgmt-web`:

```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient

cred = DefaultAzureCredential()
client = WebSiteManagementClient(cred, subscription_id)
for app in client.web_apps.list_by_resource_group("rg-aos-dev"):
    if (app.name or "").startswith("func-agent-operating-system-dev-"):
        print(app.name)
        break
```

---

## Deployment Method

All Function Apps use the **Azure Flex Consumption plan** (`FC1`). Code deployment uses
`azure/functions-action@v1` which uploads a zip package to the pre-configured blob
storage deployment container.

All Foundry agents use the `azure-ai-projects` Python SDK (`AIProjectClient.agents`)
to create or update agent definitions. If an agent with the same name already exists
in the project, it is updated in-place.

---

## Enabling Automatic Code Deployment on Infrastructure Provisioning

Add a GitHub PAT with `repo` scope as `DEPLOY_DISPATCH_TOKEN` in the
`ASISaga/aos-infrastructure` repository (`Settings → Secrets and variables → Actions`).
This token is used by the `Signal code repositories` step to dispatch `infra_provisioned`
events to each code repository after Phase 4 succeeds.

---

## Monitoring

- **Infrastructure deploys**: Track in `infrastructure-deploy.yml` workflow run. The Step Summary shows per-phase status and the result of the `infra_provisioned` dispatch.
- **Code deploys**: Each code repository's `deploy.yml` shows Function App or Foundry Agent deployment status.
- **Drift detection**: `infrastructure-drift-detection.yml` runs on a schedule to detect configuration drift.
- **Health monitoring**: `infrastructure-monitoring.yml` checks Function App health, costs, and performance on demand.
- **Governance**: `infrastructure-governance.yml` enforces Azure Policy and RBAC compliance.

All Function Apps connect to a shared **Application Insights** instance (provisioned by Phase 1 `modules/monitoring.bicep`). Useful KQL queries, Azure Portal alert recommendations, and detailed monitoring setup guidance are available in the full setup guide in `docs/` and in the `infrastructure-monitoring.yml` workflow comments.
