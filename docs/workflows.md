# GitHub Actions Workflows

**Last Updated**: 2026-03-24  
**Audience**: Platform engineers, DevOps, contributors

This document describes the eight GitHub Actions workflows that automate the infrastructure and code deployment lifecycle for the AOS platform.

---

## Workflow Overview

| Workflow file | Name | Trigger | Purpose |
|---|---|---|---|
| `infrastructure-deploy.yml` | Infrastructure Deployment | `workflow_dispatch`, PR label, issue comment | 5-phase Bicep deployment with OODA gating |
| `infrastructure-governance.yml` | Infrastructure Governance | Daily 06:00 UTC, `workflow_dispatch` | Policy compliance, cost/budget, RBAC review |
| `infrastructure-drift-detection.yml` | Infrastructure Drift Detection | Every 6 hours, `workflow_dispatch` | Drift vs. Bicep template, DR readiness |
| `infrastructure-troubleshooting.yml` | Infrastructure Troubleshooting | `workflow_dispatch` | Diagnostics and autonomous error fixing |
| `cost-management.yml` | Cost Management | `workflow_dispatch` | Cost audit, scale-down violations, GitHub issue creation |
| `deploy-function-app.yml` | Deploy Function App _(reusable)_ | `workflow_call` from code repos | Deploy Python Function App via OIDC |
| `deploy-foundry-agent.yml` | Deploy Foundry Agent _(reusable)_ | `workflow_call` from agent repos | Deploy agent to Azure AI Foundry Agent Service |
| `copilot-setup-steps.yml` | Copilot Setup Steps | `workflow_dispatch`, push | Configure GitHub Copilot Agent environment |

Required GitHub secrets (all infrastructure workflows):

| Secret | Description |
|---|---|
| `AZURE_CLIENT_ID` | OIDC application (client) ID |
| `AZURE_TENANT_ID` | Azure Active Directory tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Target Azure subscription ID |

---

## 1. Infrastructure Deployment

**File**: `.github/workflows/infrastructure-deploy.yml`

The primary infrastructure deployment workflow. Runs five sequential Bicep phases with OODA-loop cost gating and automatic dispatch to code repositories.

### Triggers

| Event | Conditions | Behaviour |
|---|---|---|
| `workflow_dispatch` | Manual | Full deploy to selected environment |
| `pull_request` (labeled) | Paths: `deployment/**` | See label table below |
| `issue_comment` (created) | `/deploy` in body | Deploy from comment |

**PR label rules:**

| Label | Effect |
|---|---|
| `deploy:dev` | Dry-run plan to `dev` |
| `deploy:staging` + `status:approved` | Live deploy to `staging` |
| `action:deploy` | Live deploy using input defaults |

### Inputs (`workflow_dispatch`)

| Input | Required | Default | Description |
|---|---|---|---|
| `environment` | ✅ | — | `dev`, `staging`, or `prod` |
| `resource_group` | ❌ | `rg-aos-<env>` | Azure resource group |
| `location` | ❌ | auto-selected | Primary Azure region |
| `geography` | ❌ | `''` | `americas`, `europe`, or `asia` |
| `template` | ❌ | `deployment/main-modular.bicep` | Bicep template path |
| `dry_run` | ❌ | `false` | Plan only (lint → validate → what-if, no deploy) |
| `skip_health_checks` | ❌ | `false` | Skip post-deployment health checks |
| `deploy_function_apps` | ❌ | `false` | Also deploy Function App code via SDK bridge |
| `sync_kernel_config` | ❌ | `false` | Sync aos-kernel env vars to all Function Apps |

### Jobs

```
validate ──► deploy
```

**`validate`** — Lints Bicep template; emits environment, resource_group, parameters_file, is_dry_run as outputs.

**`deploy`** — Full deployment pipeline:

1. Auto-select Azure regions via `workflow_helper.py select-regions`
2. Ensure resource group exists
3. Lint Bicep template
4. Validate ARM template
5. What-if analysis (informational; continue-on-error)
6. **Phase 1**: Foundation — monitoring, storage, serviceBus, keyVault
7. **Phase 2**: AI Services — aiServices, aiHub, aiProject (requires Phase 1)
8. **Phase 3**: AI Applications — loraInference, foundryApps, aiGateway, a2aConnections (requires Phase 2)
9. **Phase 4**: Function Apps — functionApps, mcpServerFunctionApps (requires Phase 1)
10. **Store client IDs**: Fetch managed identity clientIds via `ManagedIdentityClient`; store in Key Vault
11. **Phase 5**: Governance — policy assignments, cost budget (independent)
12. **Signal code repositories**: Dispatch `infra_provisioned` to 8 code repos (requires `DEPLOY_DISPATCH_TOKEN`)
13. Post-deploy health check
14. Upload audit logs (90-day retention)

**Bicep phase gating**: Each phase gates on `steps.arm_validate.outcome == 'success'` (NOT what-if outcome — what-if is informational only).

### Post-Deployment Dispatch

After Phase 4, an `infra_provisioned` `repository_dispatch` event is sent to:
`agent-operating-system`, `aos-realm-of-agents`, `business-infinity`, `mcp`, `erpnext.asisaga.com`, `linkedin.asisaga.com`, `reddit.asisaga.com`, `subconscious.asisaga.com`

**Payload**: `{ environment, resource_group, key_vault_url, infra_sha, infra_run_id }`

Requires `DEPLOY_DISPATCH_TOKEN` secret (GitHub PAT with `repo` scope).

---

## 2. Infrastructure Governance

**File**: `.github/workflows/infrastructure-governance.yml`

### Triggers

- **Scheduled**: daily at 06:00 UTC
- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|---|---|---|
| `environment` | `dev` | Target environment |
| `resource_group` | `rg-aos-<env>` | Resource group |
| `enforce_policies` | `false` | Assign AOS governance policies |
| `check_budget` | `true` | Run budget/cost alert check |
| `review_rbac` | `true` | Run privileged-access review |
| `required_tags` | `''` | Comma-separated `key=value` tag pairs to enforce |

### Steps

1. **Policy compliance evaluation** — `PolicyManager.evaluate_compliance()`: warns on non-compliant resources
2. **Required tag enforcement** *(optional)* — warns on resources missing required tags
3. **Budget status check** *(optional)* — `CostManager.check_budget_alerts()`: warns on threshold breaches
4. **Privileged access review** *(optional)* — `RbacManager.review_privileged_access()`: lists over-privileged principals
5. **Assign governance policies** *(optional)* — `PolicyManager.assign_aos_policies()`: assigns standard AOS policy set
6. **Governance summary** — Markdown table to `$GITHUB_STEP_SUMMARY`

---

## 3. Infrastructure Drift Detection

**File**: `.github/workflows/infrastructure-drift-detection.yml`

### Triggers

- **Scheduled**: every 6 hours (`0 */6 * * *`)
- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|---|---|---|
| `environment` | `dev` | Target environment |
| `resource_group` | `rg-aos-<env>` | Resource group |
| `template` | `deployment/main-modular.bicep` | Bicep template to compare against |
| `parameters_file` | `''` | Optional `.bicepparam` file |
| `check_dr_readiness` | `true` | Assess DR readiness |

### Steps

1. Detect drift — `DriftDetector.detect_drift()`: categorises findings as `missing`, `unexpected`, or `changed`
2. SLA compliance check — `HealthMonitor.check_sla_compliance()`
3. DR readiness assessment *(optional)* — checks Key Vault soft-delete and storage geo-replication
4. Upload `drift-findings.json` artifact
5. Drift detection summary to `$GITHUB_STEP_SUMMARY`

---

## 4. Infrastructure Troubleshooting

**File**: `.github/workflows/infrastructure-troubleshooting.yml`

### Triggers

- **Manual**: `workflow_dispatch` only

### Inputs

| Input | Required | Description |
|---|---|---|
| `environment` | ✅ | `dev`, `staging`, or `prod` |
| `issue_type` | ✅ | `deployment_failure`, `performance_degradation`, `connectivity_issue`, `resource_error`, `custom_diagnostic` |
| `resource_name` | ❌ | Specific resource to target |
| `description` | ❌ | Free-text description |

### Jobs

```
collect-diagnostics ──► analyze-deployment-failure   (deployment_failure)
                    ──► diagnose-performance          (performance_degradation)
                    ──► diagnose-connectivity         (connectivity_issue)
                    ──► diagnose-resource-error       (resource_error)
                    └──► generate-report              (always)
```

Uses `azure_ops.py` subcommands (Azure SDK; no `az` CLI calls) for all diagnostics. On logic errors, invokes the `deployment-error-fixer` skill to auto-fix and retry.

---

## 5. Cost Management

**File**: `.github/workflows/cost-management.yml`

### Triggers

- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|---|---|---|
| `environment` | required | `dev`, `staging`, or `prod` |
| `resource_group` | `rg-aos-<env>` | Resource group |
| `create_issue` | `true` | Create GitHub issue on findings |
| `cost_period_days` | `30` | Look-back period in days |

### Steps

1. **Cost audit** — `CostManager.get_current_spend(period_days)`: queries 30-day spend
2. **Scale-down audit** — `ScaleDownAuditor`: identifies resources not scaled to zero in non-prod
3. **GitHub issue creation** *(optional)* — creates issue with cost findings and recommendations
4. **Cost summary** — Markdown report to `$GITHUB_STEP_SUMMARY`

---

## 6. Deploy Function App _(Reusable)_

**File**: `.github/workflows/deploy-function-app.yml`  
**Trigger**: `workflow_call` (called by code repositories via `uses:`)

This centralised reusable workflow deploys any Python Azure Function App via OIDC Workload Identity Federation. All 8 Function App repositories call this workflow instead of duplicating deployment logic.

### Inputs

| Input | Required | Description |
|---|---|---|
| `app-name` | ✅ | Azure-safe app name (e.g. `agent-operating-system`) |
| `environment` | ✅ | `dev`, `staging`, or `prod` |
| `key-vault-url` | ❌ | Key Vault URL for first-run AZURE_CLIENT_ID retrieval |
| `python-version` | ❌ | Python version (default: `3.11`) |
| `azure-resource-group` | ❌ | Resource group override (default: `rg-aos-<env>`) |

### Secrets (via `secrets: inherit`)

| Secret | Source |
|---|---|
| `AZURE_CLIENT_ID` | Calling repo's GitHub Environment |
| `AZURE_TENANT_ID` | Calling repo's GitHub Environment |
| `AZURE_SUBSCRIPTION_ID` | Calling repo's GitHub Environment |

### Steps

1. Checkout caller's code
2. Install Python + Azure SDK deps (`azure-identity`, `azure-keyvault-secrets`, `azure-mgmt-web`)
3. *(Optional)* OIDC login with bootstrap credential to retrieve `AZURE_CLIENT_ID` from Key Vault
4. OIDC login with app-specific managed identity
5. Resolve exact Function App name by prefix via `azure-mgmt-web` (`WebSiteManagementClient`)
6. Deploy via `azure/functions-action@v1` (zip upload to pre-configured blob storage container)

**Called from** (all use `secrets: inherit`):
- `agent-operating-system/deploy.yml`
- `aos-realm-of-agents/deploy.yml`
- `business-infinity/deploy.yml`
- `mcp/deploy.yml` (×4 in parallel for each MCP server)
- `erpnext.asisaga.com/deploy.yml`
- `linkedin.asisaga.com/deploy.yml`
- `reddit.asisaga.com/deploy.yml`
- `subconscious.asisaga.com/deploy.yml`

---

## 7. Deploy Foundry Agent _(Reusable)_

**File**: `.github/workflows/deploy-foundry-agent.yml`  
**Trigger**: `workflow_call` (called by agent repositories via `uses:`)

This centralised reusable workflow deploys an agent definition to Azure AI Foundry Agent Service. All 5 C-suite agent repositories call this workflow.

The calling repository must contain an `agent.yaml` at the root:

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

### Inputs

| Input | Required | Description |
|---|---|---|
| `agent-name` | ✅ | Agent identifier (e.g. `ceo-agent`) |
| `environment` | ✅ | `dev`, `staging`, or `prod` |
| `foundry-project-endpoint` | ❌ | Foundry project endpoint URL (overrides `FOUNDRY_PROJECT_ENDPOINT` secret) |
| `azure-resource-group` | ❌ | Resource group override |
| `python-version` | ❌ | Python version (default: `3.11`) |

### Secrets (via `secrets: inherit`)

| Secret | Source |
|---|---|
| `AZURE_CLIENT_ID` | Calling repo's GitHub Environment |
| `AZURE_TENANT_ID` | Calling repo's GitHub Environment |
| `AZURE_SUBSCRIPTION_ID` | Calling repo's GitHub Environment |
| `FOUNDRY_PROJECT_ENDPOINT` | Calling repo's GitHub Environment _(optional if input provided)_ |

### Steps

1. Checkout caller's code
2. Install `azure-ai-projects>=1.0.0b4`, `pyyaml`
3. OIDC login
4. Resolve Foundry project endpoint (input → secret → auto-discovery from resource group)
5. Parse `agent.yaml` (or `agent.yml` / `agent.json`)
6. `AIProjectClient.agents.list_agents()` — check for existing agent by name
7. `create_agent()` or `update_agent()` depending on whether it already exists

**Called from** (all use `secrets: inherit`):
- `ceo-agent/deploy.yml`
- `cfo-agent/deploy.yml`
- `cto-agent/deploy.yml`
- `cso-agent/deploy.yml`
- `cmo-agent/deploy.yml`

---

## 8. Copilot Setup Steps

**File**: `.github/workflows/copilot-setup-steps.yml`  
**Trigger**: `workflow_dispatch`, push

Configures the GitHub Copilot Agent environment with the `gh-aw` MCP server extension for infrastructure-aware agent assistance.

---

## workflow_helper.py CLI

**Path**: `deployment/orchestrator/cli/workflow_helper.py`

A pure-Python CLI tool used by the deployment workflow for logic that would be impractical in shell scripts.

### Subcommands

| Subcommand | Purpose |
|---|---|
| `select-regions` | Pick optimal primary and ML Azure regions based on geography + environment |
| `analyze-output` | Classify orchestrator exit code/log as success, transient, or logic error |
| `retry` | Re-run `deploy` up to N times with exponential back-off (base: 10 s) |
| `extract-summary` | Read audit JSON files; emit `deployed_resources` and `duration` |

### Back-off schedule (default 3 retries)

| Attempt | Delay before attempt |
|---|---|
| 1 | none |
| 2 | 10 s |
| 3 | 20 s |

---

## Testing

```bash
# Run workflow helper tests only
pytest deployment/tests/test_workflow_helper.py -v

# Run full test suite (123+ tests)
pytest deployment/tests/ -v
```

## References

→ **Repository spec**: `.github/specs/repository.md`  
→ **Architecture**: `docs/architecture.md`  
→ **Workflow templates guide**: `deployment/workflow-templates/README.md`  
→ **Error fixer skill**: `.github/skills/deployment-error-fixer/`
