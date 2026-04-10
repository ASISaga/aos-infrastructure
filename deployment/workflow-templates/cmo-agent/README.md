# Deploy Workflow — cmo-agent

Deploys the [`ASISaga/cmo-agent`](https://github.com/ASISaga/cmo-agent) agent definition
to Azure AI Foundry Agent Service using OIDC (passwordless) authentication.

**Deployment target:** Azure AI Foundry Agent Service (project-scoped)

---

## Quick Start

```bash
# Copy this workflow to the target repository
cp deploy.yml /path/to/cmo-agent/.github/workflows/deploy.yml
```

Then complete the [one-time setup](#one-time-setup) below and push to `main`.

---

## How It Works

This is a thin caller (~30 lines) that delegates all deployment logic to the reusable
workflow in `ASISaga/aos-infrastructure`:

```
cmo-agent               →  ASISaga/aos-infrastructure
.github/workflows/          .github/workflows/
deploy.yml (caller)         deploy-foundry-agent.yml (reusable)
                            • Install azure-ai-projects SDK
                            • OIDC login
                            • AIProjectClient.agents.create_or_update(agent.yaml)
```

If an agent named `cmo-agent` already exists in the Foundry project, it is updated
in-place. A fix in `deploy-foundry-agent.yml` propagates automatically on the next run.

---

## Prerequisites

Infrastructure must be provisioned first via `ASISaga/aos-infrastructure`
(`infrastructure-deploy.yml` workflow). The Bicep templates create:

- The Azure AI Foundry Hub and project
- A User-Assigned Managed Identity for OIDC
- The OIDC Workload Identity Federation federated credential for this repository
- The Key Vault secret `foundry-project-endpoint-{env}`

---

## Agent Definition File

The repository must contain an `agent.yaml` at the root defining the agent:

```yaml
name: "CMO Agent"
model: "gpt-4o"
instructions: |
  You are the CMO Agent responsible for marketing strategy and brand management...
tools:
  - type: code_interpreter
  - type: file_search
metadata:
  version: "1.0"
```

Supported `tools` types: `code_interpreter`, `file_search`.

---

## One-Time Setup

### 1. Retrieve the Foundry Project Endpoint

After infrastructure provisioning, retrieve the project endpoint from Key Vault:

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

cred = DefaultAzureCredential()
client = SecretClient(vault_url="https://<kv-name>.vault.azure.net", credential=cred)
# Replace <env> with dev, staging, or prod
print(client.get_secret("foundry-project-endpoint-<env>").value)
```

### 2. Create GitHub Environments

Go to **Settings → Environments** and create three environments:
`dev`, `staging`, `prod`.

For each environment, add these **secrets**:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Managed Identity client ID for OIDC authentication |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint URL (from Key Vault above) |

Optionally add this **variable** (not sensitive):

| Variable | Default if omitted |
|---|---|
| `AZURE_RESOURCE_GROUP` | `rg-aos-{env}` |

### 3. Copy the workflow file

```bash
cp deploy.yml /path/to/cmo-agent/.github/workflows/deploy.yml
```

---

## Triggers

| Event | Target Environment |
|---|---|
| Push to `main` | `dev` |
| GitHub Release published | `prod` |
| `workflow_dispatch` | Selected by user (`dev` / `staging` / `prod`) |
| `repository_dispatch` (`infra_provisioned`) | From infrastructure payload |

---

## Required Permissions

```yaml
permissions:
  id-token: write   # OIDC token exchange with Azure
  contents: read    # Repository checkout
```

---

## Full Setup Guide

For the complete setup guide, architecture diagrams, monitoring, and troubleshooting,
see [`deployment/workflow-templates/README.md`](https://github.com/ASISaga/aos-infrastructure/blob/main/deployment/workflow-templates/README.md)
in `ASISaga/aos-infrastructure`.
