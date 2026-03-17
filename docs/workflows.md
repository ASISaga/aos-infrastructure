# GitHub Actions Workflows

**Last Updated**: 2026-03-17  
**Audience**: Platform engineers, DevOps, contributors

This document describes the five GitHub Actions workflows that automate the infrastructure lifecycle for `aos-infrastructure`, and the Python `workflow_helper` CLI that supports them.

---

## Workflow Overview

| Workflow file | Name | Trigger | Purpose |
|---------------|------|---------|---------|
| `infrastructure-deploy.yml` | Infrastructure Deployment Agent | `workflow_dispatch`, PR label, issue comment | Full deployment pipeline: lint → validate → what-if → deploy |
| `infrastructure-governance.yml` | Infrastructure Governance | Daily 06:00 UTC, `workflow_dispatch` | Policy compliance, cost/budget, RBAC review |
| `infrastructure-drift-detection.yml` | Infrastructure Drift Detection | Every 6 hours, `workflow_dispatch` | Drift vs. Bicep template, SLA compliance, DR readiness |
| `infrastructure-monitoring.yml` | Infrastructure Monitoring | Every 6 hours, `workflow_dispatch` | Health, performance, cost, security posture checks |
| `infrastructure-troubleshooting.yml` | Infrastructure Troubleshooting | `workflow_dispatch` | Diagnostics collection and failure analysis |

Required GitHub secrets for all workflows:

| Secret | Description |
|--------|-------------|
| `AZURE_CLIENT_ID` | OIDC application (client) ID |
| `AZURE_TENANT_ID` | Azure Active Directory tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Target Azure subscription ID |

---

## 1. Infrastructure Deployment Agent

**File**: `.github/workflows/infrastructure-deploy.yml`

### Triggers

| Event | Conditions | Behaviour |
|-------|-----------|-----------|
| `workflow_dispatch` | Manual | Full deploy to the selected environment |
| `pull_request` (labeled) | Paths: `deployment/**` | See label table below |
| `issue_comment` (created) | `/deploy` command in body | Deploy from a comment |

**PR label rules**:

| Label | Effect |
|-------|--------|
| `deploy:dev` | Dry-run plan to `dev` |
| `deploy:staging` + `status:approved` | Live deploy to `staging` |
| `action:deploy` | Live deploy using input defaults |

**Issue comment commands**:

```
/deploy           → deploy to dev (default)
/deploy prod      → deploy to prod
/deploy plan      → dry-run (plan only)
```

### Inputs (`workflow_dispatch`)

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `environment` | ✅ | — | `dev`, `staging`, or `prod` |
| `resource_group` | ❌ | auto (`rg-aos-<env>`) | Azure resource group |
| `location` | ❌ | auto-selected | Primary Azure region |
| `geography` | ❌ | `''` | `americas`, `europe`, or `asia` |
| `template` | ❌ | `deployment/main-modular.bicep` | Bicep template path |
| `skip_health_checks` | ❌ | `false` | Skip post-deployment health checks |

### Jobs

```
setup ──► deploy
```

**`setup`** — Evaluates the trigger (label, comment, or dispatch) and emits deployment parameters as step outputs.

**`deploy`** — Orchestrates the full deployment:

1. Posts a "started" comment on the PR/issue
2. Installs Python dependencies and Azure CLI
3. Authenticates to Azure via OIDC (`azure/login@v2`)
4. Calls `workflow_helper.py select-regions` to pick primary and ML regions
5. Validates regional capabilities via `regional_tool.py`
6. Runs `deployment/deploy.py plan` (dry-run) or `deploy` (live)
7. Calls `workflow_helper.py analyze-output` to classify success/failure
8. On **logic error**: invokes the `deployment-error-fixer` skill to auto-fix and retry
9. On **transient error**: calls `workflow_helper.py retry` (up to 3 attempts, exponential back-off)
10. Extracts deployment summary from audit JSON
11. Posts a result comment on the PR/issue
12. Uploads audit logs as artifacts (90-day retention)

### Permissions

```yaml
id-token: write   # OIDC login
contents: read
pull-requests: write
issues: write
```

---

## 2. Infrastructure Governance

**File**: `.github/workflows/infrastructure-governance.yml`

### Triggers

- **Scheduled**: daily at 06:00 UTC
- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `environment` | `dev` | Target environment |
| `resource_group` | `rg-aos-<env>` | Resource group |
| `enforce_policies` | `false` | Assign AOS governance policies |
| `check_budget` | `true` | Run budget/cost alert check |
| `review_rbac` | `true` | Run privileged-access review |
| `required_tags` | `''` | Comma-separated `key=value` tag pairs to enforce |

### Job: `governance`

Steps executed in order:

1. **Policy compliance evaluation** — `PolicyManager.evaluate_compliance()`: lists non-compliant resources and emits GitHub workflow warnings.
2. **Required tag enforcement** *(optional)* — `PolicyManager.enforce_required_tags()`: warns on resources missing required tags.
3. **Budget status check** *(optional)* — `CostManager.check_budget_alerts()`: warns when budget thresholds are breached.
4. **Privileged access review** *(optional)* — `RbacManager.review_privileged_access()`: lists over-privileged principals with recommendations.
5. **Assign AOS governance policies** *(optional)* — `PolicyManager.assign_aos_policies()`: assigns standard AOS policy set.
6. **Governance summary** — writes a Markdown table to `$GITHUB_STEP_SUMMARY`.

---

## 3. Infrastructure Drift Detection

**File**: `.github/workflows/infrastructure-drift-detection.yml`

### Triggers

- **Scheduled**: every 6 hours (`0 */6 * * *`)
- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `environment` | `dev` | Target environment |
| `resource_group` | `rg-aos-<env>` | Resource group |
| `template` | `deployment/main-modular.bicep` | Bicep template to compare against |
| `parameters_file` | `''` | Optional `.bicepparam` file |
| `check_dr_readiness` | `true` | Assess DR readiness (soft-delete, geo-replication) |
| `fail_on_drift` | `false` | Fail the workflow when drift is detected |

### Job: `drift-detection`

1. **Snapshot live state** — `DriftDetector.snapshot_state()`: records current Azure resource state.
2. **Detect infrastructure drift** — `DriftDetector.detect_drift()`: compares live state against the Bicep template; categorises findings as `missing`, `unexpected`, or `changed`.
3. **SLA compliance check** — `HealthMonitor.check_sla_compliance()`: warns when observed uptime falls below the environment SLA target.
4. **DR readiness assessment** *(optional)* — `HealthMonitor.check_disaster_recovery_readiness()`: checks Key Vault soft-delete and storage geo-replication.
5. **Upload drift findings** — saves `drift-findings.json` as a workflow artifact.
6. **Drift detection summary** — writes a Markdown table to `$GITHUB_STEP_SUMMARY`.

---

## 4. Infrastructure Monitoring

**File**: `.github/workflows/infrastructure-monitoring.yml`

### Triggers

- **Scheduled**: every 6 hours (`0 */6 * * *`)
- **Manual**: `workflow_dispatch`

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `environment` | — | `dev`, `staging`, `prod`, or `all` |
| `check_type` | `all` | `all`, `health`, `performance`, `cost`, or `security` |

### Jobs (matrix over environments)

```
setup ──► health-check
       ──► performance-metrics
       ──► cost-monitoring
       ──► security-posture
       └──► monitoring-summary (always)
```

| Job | Checks |
|-----|--------|
| **health-check** | Function Apps (state + HTTP reachability), Storage Accounts, Service Bus namespaces, Application Insights |
| **performance-metrics** | Function App request count, average response time (last 1 hour) |
| **cost-monitoring** | Lists resources; links to Azure Cost Management for detail |
| **security-posture** | Key Vault soft-delete + purge-protection; Storage Account HTTPS-only |
| **monitoring-summary** | Aggregates all results; creates a GitHub issue when health/performance/security checks fail |

Each job uploads an artifact report (90-day retention).

---

## 5. Infrastructure Troubleshooting

**File**: `.github/workflows/infrastructure-troubleshooting.yml`

### Triggers

- **Manual**: `workflow_dispatch` only

### Inputs

| Input | Required | Description |
|-------|----------|-------------|
| `environment` | ✅ | `dev`, `staging`, or `prod` |
| `issue_type` | ✅ | `deployment_failure`, `performance_degradation`, `connectivity_issue`, `resource_error`, `custom_diagnostic` |
| `resource_name` | ❌ | Specific resource to target |
| `description` | ❌ | Free-text description of the issue |

### Jobs

```
collect-diagnostics ──► analyze-deployment-failure   (if issue_type == deployment_failure)
                    ──► diagnose-performance          (if issue_type == performance_degradation)
                    ──► diagnose-connectivity         (if issue_type == connectivity_issue)
                    ──► diagnose-resource-error       (if issue_type == resource_error)
                    └──► generate-report              (always)
```

| Job | What it does |
|-----|-------------|
| **collect-diagnostics** | Resource group details, all resources, recent deployments, activity log (errors/warnings, last 24 h) |
| **analyze-deployment-failure** | Failed deployment details, failed operations, VM quota status, recommendations |
| **diagnose-performance** | Function App request count, response time, HTTP 5xx errors (last 4 h) |
| **diagnose-connectivity** | Function App and Service Bus reachability tests |
| **diagnose-resource-error** | Resource health status, specific resource details |
| **generate-report** | Combines all diagnostics into a single troubleshooting report artifact |

---

## workflow_helper.py CLI

**Path**: `deployment/orchestrator/cli/workflow_helper.py`

A pure-Python CLI tool used by the deployment workflow to perform complex logic that would be impractical in shell scripts. It writes results to `$GITHUB_OUTPUT` (GitHub Actions) or to stdout for local use.

### check-trigger

Determines whether a deployment should run and resolves deployment parameters.

```bash
# Reads environment variables set by the workflow
python3 deployment/orchestrator/cli/workflow_helper.py check-trigger
```

**Environment variables consumed**:

| Variable | Source |
|----------|--------|
| `GITHUB_EVENT_NAME` | `github.event_name` |
| `INPUT_ENVIRONMENT` | `inputs.environment` |
| `INPUT_RESOURCE_GROUP` | `inputs.resource_group` |
| `INPUT_LOCATION` | `inputs.location` |
| `INPUT_GEOGRAPHY` | `inputs.geography` |
| `INPUT_TEMPLATE` | `inputs.template` |
| `INPUT_SKIP_HEALTH_CHECKS` | `inputs.skip_health_checks` |
| `PR_LABEL_DEPLOY_DEV` | `contains(labels, 'deploy:dev')` |
| `PR_LABEL_DEPLOY_STAGING` | `contains(labels, 'deploy:staging')` |
| `PR_LABEL_STATUS_APPROVED` | `contains(labels, 'status:approved')` |
| `PR_LABEL_ACTION_DEPLOY` | `contains(labels, 'action:deploy')` |
| `COMMENT_BODY` | `github.event.comment.body` |

**Outputs**: `should_deploy`, `is_dry_run`, `environment`, `resource_group`, `location`, `geography`, `template`, `parameters_file`, `skip_health_checks`

### select-regions

Picks the optimal primary and Azure ML regions.

```bash
python3 deployment/orchestrator/cli/workflow_helper.py select-regions \
  --environment staging \
  --location "" \
  --geography americas
```

**Region selection logic**:

1. If `--location` is provided, use it as the primary region.
2. Else if `--geography` matches (`americas` → `eastus`, `europe` → `westeurope`, `asia` → `southeastasia`), use the mapped region.
3. Otherwise default to `eastus`.
4. For `staging` and `prod` environments whose primary region is `eastus`, the ML region is `eastus2` (to avoid capacity contention).

**Outputs**: `primary_region`, `ml_region`

### analyze-output

Classifies the orchestrator's exit code and log text as success, transient failure, or logic error.

```bash
python3 deployment/orchestrator/cli/workflow_helper.py analyze-output \
  --log-file orchestrator-output.log \
  --exit-code "$EXIT_CODE"
```

**Transient patterns** (trigger `should_retry=true`): `RetryableError`, `Timeout`, `ThrottlingException`, `ServiceUnavailable`, `InternalServerError`, `ECONNRESET`, `socket hang up`, `could not resolve host`.

**Outputs**: `status`, `failure_type`, `should_retry`, `is_transient`, `error_file`

### retry

Re-runs `deployment/deploy.py deploy` up to `--max-retries` times with **exponential back-off** (base delay: 10 s; doubles on each subsequent attempt).

```bash
python3 deployment/orchestrator/cli/workflow_helper.py retry \
  --resource-group rg-aos-prod \
  --location eastus \
  --location-ml eastus2 \
  --environment prod \
  --template deployment/main-modular.bicep \
  --git-sha "$GITHUB_SHA" \
  --max-retries 3
```

**Back-off schedule** (default 3 retries):

| Attempt | Delay before attempt |
|---------|----------------------|
| 1 | none |
| 2 | 10 s |
| 3 | 20 s |

**Outputs**: `retry_success`, `retry_count`

### extract-summary

Reads audit JSON files produced by the orchestrator and emits deployment statistics.

```bash
python3 deployment/orchestrator/cli/workflow_helper.py extract-summary \
  --audit-dir deployment/audit
```

Reads all `*.json` files in `--audit-dir` and extracts `deployed_resources` and `duration` from the most recent successful entry.

**Outputs**: `deployed_resources`, `duration`

---

## Testing

```bash
# Run workflow helper tests only
pytest deployment/tests/test_workflow_helper.py -v

# Run full test suite
pytest deployment/tests/ -v
```

## References

→ **Repository spec**: `.github/specs/repository.md`  
→ **Deployment guide**: `docs/deployment.md`  
→ **Architecture**: `docs/architecture.md`  
→ **Quick start**: `deployment/QUICKSTART.md`  
→ **Error fixer skill**: `.github/skills/deployment-error-fixer/`
