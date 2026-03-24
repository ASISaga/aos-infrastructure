# AOS Deployment Architecture

**Last Updated**: 2026-03-24  
**Audience**: Platform engineers, contributors

## Three-Tier Deployment Model

1. **Agent Layer** — GitHub Actions workflows interpret deployment intent, gate on OODA decisions, and handle errors autonomously
2. **Python Layer** — Deployment orchestrator (`deploy.py`) manages the full infrastructure lifecycle
3. **Bicep Layer** — Infrastructure-as-Code defines and provisions all Azure resources

```
Developer → GitHub PR → GitHub Actions → Python Orchestrator → Bicep → Azure
                         ↑                  ↑                     ↑
                    OODA gating         Lint/Validate         Regional validation
                    Error fixing        Cost threshold        What-if preview
                    Retry logic         State snapshot
```

## OODA-Loop Deployment Model

When `--cost-threshold` is set (or invoked via `smart_deploy()`), the deployment pipeline runs a closed-loop **Observe → Orient → Decide → Act** cycle before executing any changes:

```
┌─────────────────────────────────────────────────────────────────────┐
│                           OODA Loop                                  │
│                                                                       │
│  1. OBSERVE — Snapshot live infrastructure via Azure SDK             │
│     (resources, health status, current monthly cost, tags)           │
│                  ↓                                                    │
│  2. ORIENT  — Analyse gap (desired vs actual); apply constraints     │
│     (cost budget, compliance, health assessment, required tags)       │
│                  ↓                                                    │
│  3. DECIDE  — Recommend action:                                      │
│     DEPLOY | INCREMENTAL_UPDATE | SKIP | SCALE_DOWN | ALERT | BLOCK  │
│                  ↓                                                    │
│  4. ACT     — Execute approved action; verify outcome                │
│     (auto-approve safe actions; require human approval for           │
│      destructive actions when --auto-approve is NOT set)             │
└─────────────────────────────────────────────────────────────────────┘
```

**Key classes**:
- `OODALoop` (`orchestrator/core/ooda_loop.py`) — runs one cycle and returns an `OODACycle`
- `InfrastructureManager.smart_deploy()` — invokes OODA loop before `_run_pipeline()`
- CLI: `python deploy.py deploy --cost-threshold 500 --auto-approve`

## Modular Bicep Architecture

`main-modular.bicep` composes 15 direct module calls (from 20 modules in `modules/`):

```
main-modular.bicep                    # Entry point — composes all modules
│
├── Phase 1 — Foundation
│   ├── modules/monitoring.bicep      # Application Insights, Log Analytics Workspace
│   ├── modules/storage.bicep         # Storage accounts (deployment packages, blobs)
│   ├── modules/servicebus.bicep      # Service Bus namespace + queues (async messaging)
│   └── modules/keyvault.bicep        # Key Vault (secrets, managed identity clientIds)
│
├── Phase 2 — AI Services
│   ├── modules/ai-services.bicep     # Azure AI Services (Cognitive Services)
│   ├── modules/ai-hub.bicep          # Azure AI Foundry Hub (ML Workspace Hub)
│   └── modules/ai-project.bicep      # Azure AI Foundry Project (ML Workspace Project)
│
├── Phase 3 — AI Applications
│   ├── modules/lora-inference.bicep  # Llama-3.3-70B Multi-LoRA shared inference endpoint
│   ├── modules/foundry-app.bicep     # Foundry Agent Service endpoints (one per C-suite agent)
│   ├── modules/ai-gateway.bicep      # API Management (rate limiting, JWT validation, routing)
│   └── modules/a2a-connections.bicep # Agent-to-Agent connections (C-suite boardroom)
│
├── Phase 4 — Function Apps
│   ├── modules/functionapp.bicep     # FC1 Flex Consumption + custom domain (3-phase binding)
│   │                                 #   [looped] AOS apps: agent-operating-system,
│   │                                 #            aos-realm-of-agents, business-infinity
│   └── modules/functionapp.bicep     # [looped] MCP servers: mcp-erpnext, mcp-linkedin,
│                                     #           mcp-reddit, mcp-subconscious
│
└── Phase 5 — Governance (conditional)
    ├── modules/policy.bicep          # Azure Policy assignments (if enableGovernancePolicies)
    └── modules/budget.bicep          # Cost Management budget (if monthlyBudgetAmount > 0)

Support modules (not directly in main template):
    modules/identity.bicep            # User-Assigned Managed Identities
    modules/rbac.bicep                # RBAC role assignments
    modules/machinelearning.bicep     # ML compute and registry resources
    modules/ai-sku-policy-def.bicep   # Custom AI SKU policy definition (subscription scope)
    modules/functionapp-ssl.bicep     # SNI TLS re-binding sub-module (called by functionapp.bicep)
    modules/compute.bicep             # Compute resources (container instances, etc.)
```

## Reusable Workflow Deployment Architecture

Code deployment is decoupled from infrastructure provisioning via centralised reusable workflows:

```
 ┌─────────────────────────────────────┐
 │  agent-operating-system repo         │
 │  .github/workflows/deploy.yml        │
 │  (thin caller — ~80 lines)           │
 │                                       │
 │  on: push, release, workflow_dispatch,│
 │      repository_dispatch             │
 └─────────────────┬───────────────────┘
                   │ uses: ASISaga/aos-infrastructure/
                   │       .github/workflows/deploy-function-app.yml@main
                   │ secrets: inherit
                   ▼
 ┌─────────────────────────────────────────────────────────┐
 │  ASISaga/aos-infrastructure                              │
 │  .github/workflows/deploy-function-app.yml (reusable)   │
 │                                                           │
 │  1. Checkout caller's code                               │
 │  2. Install Python deps (azure-identity, azure-mgmt-web) │
 │  3. OIDC login (with optional Key Vault bootstrap)       │
 │  4. Resolve exact Function App name via azure-mgmt-web   │
 │  5. Deploy via azure/functions-action@v1                 │
 └─────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────┐
 │  ceo-agent repo                      │
 │  .github/workflows/deploy.yml        │
 │  (thin caller — ~80 lines)           │
 └─────────────────┬───────────────────┘
                   │ uses: ASISaga/aos-infrastructure/
                   │       .github/workflows/deploy-foundry-agent.yml@main
                   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  ASISaga/aos-infrastructure                                  │
 │  .github/workflows/deploy-foundry-agent.yml (reusable)      │
 │                                                               │
 │  1. Checkout caller's code                                   │
 │  2. Install azure-ai-projects, pyyaml                       │
 │  3. OIDC login                                              │
 │  4. Resolve Foundry project endpoint                        │
 │  5. Read agent.yaml from caller's repo root                 │
 │  6. Create or update agent via AIProjectClient.agents API   │
 └─────────────────────────────────────────────────────────────┘
```

**Decoupled provisioning flow:**

```
infrastructure-deploy.yml (aos-infrastructure)
    │
    ├── Phase 1-3: Foundation, AI Services, AI Applications
    ├── Phase 4: Function Apps
    │     └── fetch-identity-client-ids
    │           → Store clientid-{app}-{env} secrets in Key Vault
    │
    └── Signal code repositories (repository_dispatch: infra_provisioned)
          │  payload: { environment, resource_group, key_vault_url, infra_sha }
          │
          ├── → agent-operating-system/deploy.yml → deploy-function-app.yml
          ├── → aos-realm-of-agents/deploy.yml    → deploy-function-app.yml
          ├── → business-infinity/deploy.yml      → deploy-function-app.yml
          ├── → mcp/deploy.yml                   → deploy-function-app.yml × 4
          ├── → erpnext.asisaga.com/deploy.yml   → deploy-function-app.yml
          ├── → linkedin.asisaga.com/deploy.yml  → deploy-function-app.yml
          ├── → reddit.asisaga.com/deploy.yml    → deploy-function-app.yml
          └── → subconscious.asisaga.com/deploy.yml → deploy-function-app.yml
```

## Custom Domain and DNS Architecture

Function Apps are bound to `*.asisaga.com` custom hostnames secured by free App Service Managed Certificates. The binding follows a three-phase sequence:

```
DNS provider                Azure App Service
────────────────────────    ─────────────────────────────────────────────
CNAME record pre-created
  agent-operating-system.   Phase 1 — hostnameBinding (sslState: Disabled)
  asisaga.com                ↓
  → func-agent-operating-   Phase 2 — managedCertificate (free, auto-renewing)
    system-prod-<suffix>.    ↓
    azurewebsites.net       Phase 3 — sslBinding (SniEnabled + thumbprint)
                             ↓
                            https://agent-operating-system.asisaga.com  ✅
```

C-suite agents (`ceo-agent`, `cfo-agent`, `cto-agent`, `cso-agent`, `cmo-agent`) are hosted as **Foundry Agent Service** endpoints — not Function Apps. Each uses the shared `lora-inference.bicep` Llama-3.3-70B Multi-LoRA endpoint with per-agent adapters.

**Domain assignment by app category:**

| App category | Deployment target | Custom domain formula | Count |
|---|---|---|---|
| AOS Function Apps | Azure Function App (FC1 Flex) | `<appName>.asisaga.com` | 3 |
| MCP server Function Apps | Azure Function App (FC1 Flex) | `<repoName>` (IS the full domain) | 4 |
| C-suite agents | Foundry Agent Service endpoint | `<agentName>.asisaga.com` | 5 |
| Platform services | External / not deployed here | `<serviceName>.asisaga.com` | 4+ |

**All 16 production custom domains:**

```
agent-operating-system.asisaga.com   aos-realm-of-agents.asisaga.com
business-infinity.asisaga.com        ceo-agent.asisaga.com
cfo-agent.asisaga.com                cto-agent.asisaga.com
cso-agent.asisaga.com                cmo-agent.asisaga.com
erpnext.asisaga.com                  linkedin.asisaga.com
reddit.asisaga.com                   subconscious.asisaga.com
aos-kernel.asisaga.com               aos-intelligence.asisaga.com
aos-mcp-servers.asisaga.com          aos-client-sdk.asisaga.com
```

→ Full CNAME list, deployment procedure, and per-environment strategy: `docs/dns-setup.md`

## Python Orchestrator Architecture

```
deploy.py                             # CLI entry point (28 subcommands)
orchestrator/
├── core/
│   ├── config.py                     # DeploymentConfig (three pillar sub-configs)
│   ├── manager.py                    # InfrastructureManager (all operations)
│   └── ooda_loop.py                  # OODA loop (Observe/Orient/Decide/Act)
├── governance/
│   ├── policy_manager.py             # Azure Policy assignments & compliance
│   ├── cost_manager.py               # Budget management & alerts
│   ├── rbac_manager.py               # Privileged access review
│   └── scale_down_auditor.py         # Scale-to-zero compliance auditing
├── automation/
│   ├── pipeline.py                   # Lint → validate → what-if → deploy pipeline
│   └── lifecycle.py                  # deprovision / shift / modify / upgrade / scale
├── reliability/
│   ├── drift_detector.py             # Infrastructure drift detection (what-if + manifest)
│   └── health_monitor.py             # SLA-aware health checks & DR readiness
├── integration/
│   ├── azure_sdk_client.py           # Azure SDK wrapper (resource state, cost, health)
│   ├── identity_client.py            # Managed identity client ID fetch + Key Vault store
│   ├── sdk_bridge.py                 # Bridge to aos-client-sdk (Function App deployment)
│   └── kernel_bridge.py             # Bridge to aos-kernel (config sync)
└── validators/
    └── regional_validator.py         # Azure region capability validation & auto-selection
```

## Environment Strategy

| Environment | Purpose | Default region | Deployment trigger |
|---|---|---|---|
| `dev` | Development / testing | `eastus` | Push to `main` in code repos |
| `staging` | Pre-production validation | `eastus` (ML: `eastus2`) | Manual `workflow_dispatch` |
| `prod` | Production | `westeurope` | GitHub Release published |

## Error Recovery Flow

```
Deployment Attempt
    ├── Success → Health Check → Audit Log → Done
    └── Failure → Classify Error
                    ├── Logic Error (BCP code, syntax, parameter)
                    │     → deployment-error-fixer skill → auto-fix → Retry
                    └── Environmental Error (timeout, throttling, transient)
                          → Exponential back-off → Retry (max 3)
                                └── Still failing → Alert + human review
```

## Azure SDK Integration

All Azure operations use the Azure SDK directly via `DefaultAzureCredential` (OIDC-compatible). There are no `az` CLI calls in Python code — only `az bicep install` and `az bicep lint` (no SDK equivalent) remain in workflow scripts.

Key SDK packages used:
- `azure-identity` — DefaultAzureCredential (OIDC, managed identity, CLI)
- `azure-mgmt-resource` — Resource group operations, deployments, what-if
- `azure-mgmt-costmanagement` — Cost queries, budget management
- `azure-mgmt-msi` — Managed identity operations (fetch clientId)
- `azure-mgmt-web` — Function App discovery and status queries
- `azure-mgmt-monitor` — Activity logs, metrics
- `azure-mgmt-servicebus` — Service Bus namespace queries
- `azure-keyvault-secrets` — Key Vault secret storage and retrieval
