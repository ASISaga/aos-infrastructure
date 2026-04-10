# Deploy Workflow — mcp (monorepo)

Deploys all four MCP server Function Apps from the [`ASISaga/mcp`](https://github.com/ASISaga/mcp)
monorepo to Azure **in parallel**, using OIDC (passwordless) authentication.

**Deployment targets:**

| Function App | Custom Domain |
|---|---|
| `func-mcp-erpnext-{env}-*` | `erpnext.asisaga.com` |
| `func-mcp-linkedin-{env}-*` | `linkedin.asisaga.com` |
| `func-mcp-reddit-{env}-*` | `reddit.asisaga.com` |
| `func-mcp-subconscious-{env}-*` | `subconscious.asisaga.com` |

All apps run on the Azure Flex Consumption plan (`FC1`).

---

## Quick Start

```bash
# Copy this workflow to the target repository
cp deploy.yml /path/to/mcp/.github/workflows/deploy.yml
```

Then complete the [one-time setup](#one-time-setup) below and push to `main`.

---

## How It Works

This workflow resolves the target environment once and then fans out to four parallel
deploy jobs — one per MCP Function App:

```
mcp (monorepo)          →  ASISaga/aos-infrastructure
.github/workflows/          .github/workflows/
deploy.yml (caller)         deploy-function-app.yml (reusable)
  ├── deploy-erpnext          (called 4× in parallel)
  ├── deploy-linkedin
  ├── deploy-reddit
  └── deploy-subconscious
```

Each deploy job uses its own GitHub Environment so that every Function App authenticates
with its own Managed Identity.

---

## Prerequisites

Infrastructure must be provisioned first via `ASISaga/aos-infrastructure`
(`infrastructure-deploy.yml` workflow). The Bicep templates (Phases 1, 3, 4) create all
four Function Apps, their User-Assigned Managed Identities, OIDC federated credentials,
and Key Vault secrets.

---

## One-Time Setup

### 1. Create GitHub Environments (12 total)

Go to **Settings → Environments** and create **12 environments** (4 apps × 3 envs):

| Environment | App |
|---|---|
| `mcp-erpnext-dev` | `func-mcp-erpnext-dev-*` |
| `mcp-erpnext-staging` | `func-mcp-erpnext-staging-*` |
| `mcp-erpnext-prod` | `func-mcp-erpnext-prod-*` |
| `mcp-linkedin-dev` | `func-mcp-linkedin-dev-*` |
| `mcp-linkedin-staging` | `func-mcp-linkedin-staging-*` |
| `mcp-linkedin-prod` | `func-mcp-linkedin-prod-*` |
| `mcp-reddit-dev` | `func-mcp-reddit-dev-*` |
| `mcp-reddit-staging` | `func-mcp-reddit-staging-*` |
| `mcp-reddit-prod` | `func-mcp-reddit-prod-*` |
| `mcp-subconscious-dev` | `func-mcp-subconscious-dev-*` |
| `mcp-subconscious-staging` | `func-mcp-subconscious-staging-*` |
| `mcp-subconscious-prod` | `func-mcp-subconscious-prod-*` |

### 2. Retrieve `AZURE_CLIENT_ID` for each environment

After infrastructure provisioning, retrieve each Function App's client ID from Key Vault:

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

cred = DefaultAzureCredential()
client = SecretClient(vault_url="https://<kv-name>.vault.azure.net", credential=cred)

# Retrieve for each app and environment
for app in ["mcp-erpnext", "mcp-linkedin", "mcp-reddit", "mcp-subconscious"]:
    for env in ["dev", "staging", "prod"]:
        secret_name = f"clientid-{app}-{env}"
        print(f"{app}-{env}: {client.get_secret(secret_name).value}")
```

> **First run:** The `infra_provisioned` `repository_dispatch` event triggers this
> workflow automatically after Phase 4 succeeds and supplies the Key Vault URL —
> no manual retrieval needed on the initial deploy.

### 3. Add secrets to each environment

For each of the 12 environments, add these **secrets**:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Managed Identity client ID for **that specific** Function App and environment |
| `AZURE_TENANT_ID` | Azure AD tenant ID (same for all environments) |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID (same for all environments) |

Optionally add this **variable** (not sensitive):

| Variable | Default if omitted |
|---|---|
| `AZURE_RESOURCE_GROUP` | `rg-aos-{env}` |

### 4. Copy the workflow file

```bash
cp deploy.yml /path/to/mcp/.github/workflows/deploy.yml
```

---

## Triggers

| Event | Target Environments |
|---|---|
| Push to `main` | All `*-dev` environments |
| GitHub Release published | All `*-prod` environments |
| `workflow_dispatch` | Selected by user (`dev` / `staging` / `prod`) → all 4 apps |
| `repository_dispatch` (`infra_provisioned`) | From infrastructure payload |

---

## Required Permissions

```yaml
permissions:
  id-token: write   # OIDC token exchange with Azure
  contents: read    # Repository checkout
```

---

## Function App Name Discovery

Each Function App name includes a unique 6-character suffix generated at provision time
(e.g. `func-mcp-erpnext-dev-a1b2c3`). The reusable workflow discovers the exact name
at runtime using prefix matching — no hardcoded names needed.

---

## Full Setup Guide

For the complete setup guide, architecture diagrams, monitoring, and troubleshooting,
see [`deployment/workflow-templates/README.md`](https://github.com/ASISaga/aos-infrastructure/blob/main/deployment/workflow-templates/README.md)
in `ASISaga/aos-infrastructure`.
