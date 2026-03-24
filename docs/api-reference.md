# API Reference — aos-infrastructure

**Last Updated**: 2026-03-24

## CLI Entry Point

### `deployment/deploy.py`

```bash
python deployment/deploy.py [COMMAND] [OPTIONS]
```

### Pillar Commands

| Command | Description |
|---|---|
| `deploy` | Full pipeline: lint → validate → what-if → deploy → health checks |
| `plan` | Dry run: lint → validate → what-if (no changes applied) |
| `automate` | Automation pillar (pipeline + optional SDK bridge + kernel sync) |
| `govern` | Governance pillar (policy, cost, RBAC) |
| `reliability` | Reliability pillar (drift, health, SLA, DR readiness) |

### OODA-Loop Flags (`deploy` command)

| Flag | Type | Description |
|---|---|---|
| `--cost-threshold FLOAT` | optional | Block deploy if monthly cost exceeds this (USD). Activates OODA loop. |
| `--auto-approve` | flag | Auto-approve safe OODA actions (DEPLOY, INCREMENTAL_UPDATE, SKIP) |

### Lifecycle Commands

| Command | Required options | Description |
|---|---|---|
| `deprovision` | `--resource-name`, `--resource-type` | Remove a single resource |
| `shift` | `--target-rg`, `--target-region` | Move resource group to new region |
| `modify` | `--resource-name`, `--resource-type`, `--properties` (JSON) | Update resource properties |
| `upgrade` | `--resource-name`, `--resource-type`, `--new-sku` | Upgrade resource SKU |
| `scale` | `--resource-name`, `--resource-type`, `--scale-settings` (JSON) | Scale resource capacity |
| `delete` | `--confirm` | Delete entire resource group |
| `list-resources` | — | List all resources in resource group |

### Observability Commands

| Command | Description |
|---|---|
| `status` | Show deployment status and resource state |
| `monitor` | Show resource health and metrics |
| `troubleshoot` | Diagnose deployment issues |

### Step-Level Commands (used by CI workflows)

| Command | Description |
|---|---|
| `ensure-rg` | Create resource group if it does not exist |
| `lint` | Run Bicep linter |
| `validate` | Run ARM template validation |
| `what-if` | Run what-if analysis |
| `deploy-bicep` | Deploy Bicep template |
| `health-check` | Run post-deployment health checks |
| `deploy-function-apps` | Deploy Function App code via SDK bridge |
| `sync-kernel-config` | Sync aos-kernel config to Function Apps |
| `fetch-identity-client-ids` | Fetch managed identity clientIds; store in Key Vault |
| `deploy-bicep-foundation` | Phase 1: monitoring, storage, servicebus, keyvault |
| `deploy-bicep-ai-services` | Phase 2: aiServices, aiHub, aiProject |
| `deploy-bicep-ai-apps` | Phase 3: loraInference, foundryApps, aiGateway, a2aConnections |
| `deploy-bicep-function-apps` | Phase 4: functionApps, mcpServerFunctionApps |
| `deploy-bicep-governance` | Phase 5: policy, budget |

### Common Options

| Option | Type | Required | Description |
|---|---|---|---|
| `--resource-group` | str | Yes | Azure resource group name |
| `--location` | str | No | Azure region (auto-selected if omitted) |
| `--location-ml` | str | No | Azure ML region override |
| `--environment` | str | No | `dev`, `staging`, or `prod` |
| `--template` | str | No | Bicep template path (default: `deployment/main-modular.bicep`) |
| `--parameters` | str | No | `.bicepparam` parameter file |
| `--subscription-id` | str | No | Azure subscription ID (auto-detected if omitted) |
| `--git-sha` | str | No | Git SHA for deployment tagging |

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Deployment or operation failed |
| `2` | OODA loop blocked deployment (cost exceeded) |

---

## Bicep Templates

### `deployment/main-modular.bicep`

Primary template that composes all modules.

#### Key Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `environment` | `string` | required | `dev`, `staging`, or `prod` |
| `location` | `string` | resource group location | Primary Azure region |
| `locationML` | `string` | `location` | Azure ML region (may differ for capacity) |
| `projectName` | `string` | `'aos'` | Naming prefix for resources |
| `githubOrg` | `string` | `'ASISaga'` | GitHub org for OIDC federated credentials |
| `enableGovernancePolicies` | `bool` | `true` | Assign Azure Policy assignments |
| `enableAiSkuGovernance` | `bool` | `true` | Assign custom AI SKU policy |
| `monthlyBudgetAmount` | `int` | `0` | Cost budget amount (0 = disabled) |
| `budgetAlertEmails` | `array` | `[]` | Email addresses for budget alerts |
| `appNames` | `array` | AOS standard apps | Standard Function App names |
| `foundryAppNames` | `array` | C-suite agent names | Foundry Agent Service app names |
| `mcpServerApps` | `array` | MCP server names | MCP server Function App names |
| `baseDomain` | `string` | `''` | Custom domain (e.g. `asisaga.com`; empty = no custom domain) |
| `deployFoundryModels` | `bool` | `false` | Deploy LoRA inference models |

### `deployment/modules/`

| Module | Resource Types |
|---|---|
| `monitoring.bicep` | `Microsoft.Insights/components`, `Microsoft.OperationalInsights/workspaces` |
| `storage.bicep` | `Microsoft.Storage/storageAccounts` |
| `servicebus.bicep` | `Microsoft.ServiceBus/namespaces`, `Microsoft.ServiceBus/namespaces/queues` |
| `keyvault.bicep` | `Microsoft.KeyVault/vaults` |
| `identity.bicep` | `Microsoft.ManagedIdentity/userAssignedIdentities` |
| `rbac.bicep` | `Microsoft.Authorization/roleAssignments` |
| `ai-services.bicep` | `Microsoft.CognitiveServices/accounts` |
| `ai-hub.bicep` | `Microsoft.MachineLearningServices/workspaces` (Hub) |
| `ai-project.bicep` | `Microsoft.MachineLearningServices/workspaces` (Project) |
| `machinelearning.bicep` | ML compute and registry resources |
| `lora-inference.bicep` | `Microsoft.MachineLearningServices/workspaces/onlineEndpoints` |
| `foundry-app.bicep` | Foundry Agent Service serverless endpoints |
| `ai-gateway.bicep` | `Microsoft.ApiManagement/service` |
| `a2a-connections.bicep` | Agent-to-Agent connection resources |
| `functionapp.bicep` | `Microsoft.Web/serverfarms`, `Microsoft.Web/sites`, hostname + cert binding |
| `functionapp-ssl.bicep` | SNI TLS binding sub-module |
| `compute.bicep` | Compute resources |
| `policy.bicep` | `Microsoft.Authorization/policyAssignments` |
| `ai-sku-policy-def.bicep` | `Microsoft.Authorization/policyDefinitions` (subscription scope) |
| `budget.bicep` | `Microsoft.Consumption/budgets` |

---

## Python Orchestrator

### `orchestrator/core/config.py`

**`DeploymentConfig`** — top-level configuration model:

| Field | Type | Description |
|---|---|---|
| `environment` | `str` | `dev`, `staging`, or `prod` |
| `resource_group` | `str` | Azure resource group |
| `location` | `str` | Azure region |
| `template` | `str` | Bicep template path |
| `parameters` | `str` | Parameters file path |
| `subscription_id` | `str` | Azure subscription ID |
| `governance` | `GovernanceConfig` | Governance pillar settings |
| `automation` | `AutomationConfig` | Automation pillar settings |
| `reliability` | `ReliabilityConfig` | Reliability pillar settings |

**`GovernanceConfig`**: `enforce_policies`, `budget_amount`, `required_tags`, `review_rbac`  
**`AutomationConfig`**: `deploy_function_apps`, `sdk_bridge`, `kernel_sync`  
**`ReliabilityConfig`**: `enable_drift_detection`, `check_dr_readiness`

### `orchestrator/core/manager.py`

**`InfrastructureManager`** — main orchestrator class:

| Method | Description |
|---|---|
| `deploy()` | Open-loop deployment pipeline (lint → validate → what-if → deploy → health) |
| `smart_deploy(cost_threshold, auto_approve)` | OODA-loop deployment (observe → orient → decide → act → pipeline) |
| `plan()` | Dry-run (no deployment) |
| `govern()` | Governance pillar |
| `reliability_check()` | Reliability pillar |
| `automate()` | Automation pillar |
| `status()` / `monitor()` / `troubleshoot()` | Observability |
| `deprovision()` / `shift()` / `modify()` / `upgrade()` / `scale()` | Lifecycle |
| Phase methods: `deploy_bicep_foundation()`, etc. | Phase-specific deployments |

### `orchestrator/core/ooda_loop.py`

**`OODALoop`** — closed-loop deployment decision engine:

| Method | Description |
|---|---|
| `run_cycle(include_cost=True)` | Run one full OODA cycle; returns `OODACycle` |
| `format_cycle_report(cycle)` | Human-readable cycle report |

**`RecommendedAction`** enum: `DEPLOY`, `INCREMENTAL_UPDATE`, `SKIP`, `REMEDIATE`, `SCALE_DOWN`, `ALERT`, `BLOCK`

### `orchestrator/governance/`

| Class | Key Methods |
|---|---|
| `PolicyManager` | `evaluate_compliance()`, `assign_aos_policies(env)`, `enforce_required_tags(tags)` |
| `CostManager` | `get_current_spend(period_days)`, `check_budget_alerts()` |
| `RbacManager` | `review_privileged_access()`, `enforce_least_privilege()` |
| `ScaleDownAuditor` | `audit()` → `ScaleDownAuditReport` with violations and recommendations |

### `orchestrator/reliability/`

| Class | Key Methods |
|---|---|
| `DriftDetector` | `detect_drift(template_path)`, `detect_drift_from_manifest(manifest)` |
| `HealthMonitor` | `check_all()`, `check_sla_compliance()`, `check_disaster_recovery_readiness()` |

### `orchestrator/integration/`

| Class | Description |
|---|---|
| `AzureSDKClient` | Azure SDK wrapper: resource listing, cost queries, state snapshots |
| `ManagedIdentityClient` | Fetch Function App managed identity clientIds via `azure.mgmt.msi` |
| `KeyVaultIdentityStore` | Store/retrieve clientIds in Key Vault as `clientid-{app_name}-{env}` |
| `SDKBridge` | Bridge to aos-client-sdk for Function App deployment and discovery |
| `KernelBridge` | Sync configuration to aos-kernel at runtime |

---

## Parameters Files

| File | Environment |
|---|---|
| `deployment/parameters/dev.bicepparam` | Development |
| `deployment/parameters/staging.bicepparam` | Staging / pre-production |
| `deployment/parameters/prod.bicepparam` | Production |
