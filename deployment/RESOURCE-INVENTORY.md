# Azure Resource Inventory

This document maps every Azure resource provisioned by `aos-infrastructure` to its source Bicep module, purpose, and related code repository.

> **Last Updated**: 2026-03-19
> **Source of truth**: `deployment/main-modular.bicep`
> **Current inventory snapshot**: `deployment/azure-status.csv`

## Resource Group Layout

All AOS resources are deployed into a single resource group per environment:

| Environment | Resource Group | Primary Region | ML Region |
|-------------|---------------|----------------|-----------|
| dev | `rg-aos-dev` | auto-selected | auto-selected |
| staging | `rg-aos-staging` | auto-selected | auto-selected |
| prod | `rg-aos-prod` | auto-selected | auto-selected |

## Shared Infrastructure

These resources have no separate code repository — they are created once per environment by the Bicep modules.

| Bicep Module | Azure Resource(s) Created | Purpose |
|-------------|--------------------------|---------|
| `modules/monitoring.bicep` | Log Analytics workspace (`log-{project}-{env}`), Application Insights (`appi-{project}-{env}`) | Centralized logging, telemetry, and performance monitoring for all services |
| `modules/storage.bicep` | Storage Account (`st{project}{env}{suffix}`) | Function App backing store, deployment packages, table storage for state |
| `modules/servicebus.bicep` | Service Bus namespace (`sb-{project}-{env}`) + `orchestration` queue | Agent-to-agent orchestration messaging |
| `modules/keyvault.bicep` | Key Vault (`kv-{project}-{env}-{suffix}`) | Secrets management for all services |
| `modules/ai-services.bicep` | Azure AI Services / Cognitive Services (`ai-{project}-{env}-{suffix}`) | LLM and cognitive API access for agents |
| `modules/ai-hub.bicep` | Azure AI Foundry Hub (`ai-hub-{project}-{env}-{suffix}`) | ML workspace hub hosting agent models and connections |
| `modules/ai-project.bicep` | Azure AI Foundry Project | Agent project workspace within the hub |
| `modules/ai-gateway.bicep` | API Management service (`ai-gw-{project}-{env}-{suffix}`) | AI gateway for rate limiting, JWT validation, routing |
| `modules/model-registry.bicep` | Model Registry | LoRA adapter model storage for C-suite agents |
| `modules/a2a-connections.bicep` | A2A (Agent-to-Agent) connections | Boardroom orchestration connections between C-suite agents |
| `modules/policy.bicep` | Azure Policy assignments | Governance: allowed locations, HTTPS-only storage, KV soft-delete |
| `modules/budget.bicep` | Cost Management budget | Monthly budget alerts at percentage thresholds |

## Platform Service Function Apps

Each AOS platform repository that is an Azure Function App gets a dedicated Flex Consumption (FC1) plan, Function App, and user-assigned managed identity for OIDC-based deployment.

> **Note on code-only repositories**: `aos-kernel`, `aos-intelligence`, `aos-client-sdk`, and `aos-dispatcher` are Python library packages — they are **not** Azure Function Apps. They are imported at runtime by `agent-operating-system`, which is the single deployable Function App. Similarly, `purpose-driven-agent` and `leadership-agent` are base-class packages with no Azure infrastructure.

| Code Repository | Azure Function App | Custom Domain | Deploy From |
|----------------|-------------------|---------------|-------------|
| [agent-operating-system](https://github.com/ASISaga/agent-operating-system) | `func-agent-operating-system-{env}-{suffix}` | `agent-operating-system.asisaga.com` | `agent-operating-system` repo → `az functionapp deploy` |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | `func-aos-realm-of-agents-{env}-{suffix}` | `aos-realm-of-agents.asisaga.com` | `aos-realm-of-agents` repo |
| [business-infinity](https://github.com/ASISaga/business-infinity) | `func-business-infinity-{env}-{suffix}` | `business-infinity.asisaga.com` | `business-infinity` repo |

### Code-Only Library Repositories (no Azure infrastructure)

These repositories are Python packages consumed by `agent-operating-system` at runtime. They have no dedicated Function Apps or Azure resources:

| Repository | Purpose |
|-----------|---------|
| [aos-kernel](https://github.com/ASISaga/aos-kernel) | Core AOS kernel — agent lifecycle, orchestration runtime |
| [aos-intelligence](https://github.com/ASISaga/aos-intelligence) | Intelligence layer — LLM integration, reasoning |
| [aos-client-sdk](https://github.com/ASISaga/aos-client-sdk) | AOS Client SDK — workflow, deployment helpers |
| [aos-dispatcher](https://github.com/ASISaga/aos-dispatcher) | Dispatcher logic — message routing between agents |

### How to Deploy Code to a Function App

Each code repository should have its own GitHub Actions workflow using OIDC:

```bash
# Example: deploy aos-dispatcher code to its Function App
az login --service-principal --federated-token <token> \
  --tenant-id <TENANT_ID> --client-id <CLIENT_ID>

az functionapp deployment source config-zip \
  -g rg-aos-<env> \
  -n func-aos-dispatcher-<env>-<suffix> \
  --src <package.zip>
```

The `AZURE_CLIENT_ID` for each Function App is output by the deployment and can be found in the deployment audit logs.

## MCP Server Function Apps

Each MCP server submodule deploys to its own Function App via `modules/functionapp.bicep`.

| Code Repository | Azure Function App | Custom Domain |
|----------------|-------------------|---------------|
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | `func-mcp-erpnext-{env}-{suffix}` | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | `func-mcp-linkedin-{env}-{suffix}` | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | `func-mcp-reddit-{env}-{suffix}` | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | `func-mcp-subconscious-{env}-{suffix}` | `subconscious.asisaga.com` |

## C-Suite Agent Foundry Endpoints

C-suite agents deploy to Azure AI Foundry (not Function Apps). Each gets a dedicated LoRA inference endpoint.

| Code Repository | Foundry Endpoint | Bicep Modules | Model |
|----------------|-----------------|---------------|-------|
| [ceo-agent](https://github.com/ASISaga/ceo-agent) | `ceo-agent` | `foundry-app.bicep` + `lora-inference.bicep` | `ceo-agent-lora-adapter` |
| [cfo-agent](https://github.com/ASISaga/cfo-agent) | `cfo-agent` | `foundry-app.bicep` + `lora-inference.bicep` | `cfo-agent-lora-adapter` |
| [cto-agent](https://github.com/ASISaga/cto-agent) | `cto-agent` | `foundry-app.bicep` + `lora-inference.bicep` | `cto-agent-lora-adapter` |
| [cso-agent](https://github.com/ASISaga/cso-agent) | `cso-agent` | `foundry-app.bicep` + `lora-inference.bicep` | `cso-agent-lora-adapter` |
| [cmo-agent](https://github.com/ASISaga/cmo-agent) | `cmo-agent` | `foundry-app.bicep` + `lora-inference.bicep` | `cmo-agent-lora-adapter` |

## Base Class Repositories (Not Deployed)

These repositories are Python packages, not Azure-deployed services:

| Repository | Purpose |
|-----------|---------|
| [purpose-driven-agent](https://github.com/ASISaga/purpose-driven-agent) | Base class for purpose-driven agents |
| [leadership-agent](https://github.com/ASISaga/leadership-agent) | Base class for C-suite leadership agents |

## DNS Prerequisites

Custom domains require CNAME records before deployment. See [docs/dns-setup.md](../docs/dns-setup.md).

## Azure Status CSV

The file `deployment/azure-status.csv` is a point-in-time snapshot of all resources in the Azure subscription (across all resource groups, not just AOS). It is exported from the Azure portal and may include resources from other projects.
