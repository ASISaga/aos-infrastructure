# aos-infrastructure

Azure infrastructure lifecycle manager for the Agent Operating System (AOS) — a multi-agent AI platform running C-suite autonomous agents and supporting services on Azure.

The repository is organised around **three pillars**:

| Pillar | Responsibility |
|---|---|
| **Governance** | Policy enforcement, cost management, RBAC access review, tag compliance, AI SKU governance |
| **Automation** | Lint → validate → what-if → deploy pipeline; OODA-loop smart deployment; lifecycle operations (deprovision, region shift, in-place modification, SKU upgrade, scaling); SDK bridge to `aos-client-sdk`; kernel config sync |
| **Reliability** | Drift detection, SLA-aware health monitoring, DR readiness assessment |

## Overview

`aos-infrastructure` contains all Azure infrastructure-as-code and deployment automation for the AOS platform:

- **Bicep Templates** — 20 modular Azure infrastructure modules (foundation, AI/ML, application, governance layers)
- **Python Orchestrator** — Smart deployment CLI with OODA-loop gating, linting, validation, health checks, and lifecycle management
- **Governance Pillar** — Azure Policy assignments, cost/budget management, RBAC access review, AI SKU governance (`governance/`)
- **Automation Pillar** — Deployment pipeline, lifecycle operations, integration with `aos-client-sdk` and `aos-kernel` (`automation/`, `integration/`)
- **Reliability Pillar** — Infrastructure drift detection, SLA compliance tracking, DR readiness (`reliability/`)
- **Regional Validation** — Automatic region selection and service capability validation
- **Reusable CI/CD Workflows** — Centralised GitHub Actions reusable workflows for deploying Function Apps and Foundry Agents, invoked from all 18 related repositories

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Deploy to dev environment (OODA-loop enabled when --cost-threshold is set)
python deployment/deploy.py deploy \
  --resource-group rg-aos-dev \
  --location eastus \
  --environment dev \
  --template deployment/main-modular.bicep

# Deploy with cost gating (OODA loop blocks if monthly spend > $500)
python deployment/deploy.py deploy \
  --resource-group rg-aos-prod \
  --location westeurope \
  --environment prod \
  --template deployment/main-modular.bicep \
  --cost-threshold 500 \
  --auto-approve

# Plan deployment (dry run — lint, validate, what-if only)
python deployment/deploy.py plan \
  --resource-group rg-aos-dev \
  --location eastus \
  --environment dev \
  --template deployment/main-modular.bicep

# Run the Governance pillar (policy compliance, cost, RBAC)
python deployment/deploy.py govern --resource-group rg-aos-prod --environment prod

# Run the Reliability pillar (health, SLA, drift, DR)
python deployment/deploy.py reliability --resource-group rg-aos-prod --environment prod

# Infrastructure lifecycle operations
python deployment/deploy.py deprovision \
  --resource-group rg-aos-dev \
  --resource-name st1 \
  --resource-type Microsoft.Storage/storageAccounts
python deployment/deploy.py upgrade \
  --resource-group rg-aos-dev \
  --resource-name st1 \
  --resource-type microsoft.storage/storageaccounts \
  --new-sku Standard_ZRS
python deployment/deploy.py scale \
  --resource-group rg-aos-dev \
  --resource-name apim \
  --resource-type microsoft.apimanagement/service \
  --scale-settings '{"sku.capacity": 2}'
python deployment/deploy.py shift \
  --resource-group rg-aos-dev \
  --target-rg rg-aos-dr \
  --target-region westeurope
python deployment/deploy.py modify \
  --resource-group rg-aos-dev \
  --resource-name func1 \
  --resource-type microsoft.web/sites \
  --properties '{"properties.httpsOnly": true}'
```

## Repository Structure

```
deployment/
├── deploy.py                      # CLI entry point — all pillar + lifecycle subcommands
├── main-modular.bicep             # Primary Bicep template (15 direct modules)
├── modules/                       # 20 Bicep modules (foundation, AI/ML, application, governance)
│   ├── monitoring.bicep           # Application Insights, Log Analytics
│   ├── storage.bicep              # Storage accounts
│   ├── servicebus.bicep           # Service Bus namespace + queues (async messaging)
│   ├── keyvault.bicep             # Key Vault (secrets, managed identity clientIds)
│   ├── identity.bicep             # User-Assigned Managed Identities (GitHub OIDC)
│   ├── rbac.bicep                 # RBAC role assignments
│   ├── ai-services.bicep          # Azure AI Services (Cognitive Services)
│   ├── ai-hub.bicep               # Azure AI Foundry Hub (ML Workspace Hub)
│   ├── ai-project.bicep           # Azure AI Foundry Project (ML Workspace Project)
│   ├── machinelearning.bicep      # ML compute and registry resources
│   ├── lora-inference.bicep       # Llama-3.3-70B Multi-LoRA shared endpoint
│   ├── foundry-app.bicep          # Foundry Agent Service endpoints (C-suite agents)
│   ├── ai-gateway.bicep           # API Management (rate limiting, JWT validation)
│   ├── a2a-connections.bicep      # Agent-to-Agent connections (C-suite boardroom)
│   ├── functionapp.bicep          # FC1 Flex Consumption + custom domain (3-phase binding)
│   ├── functionapp-ssl.bicep      # SNI TLS re-binding sub-module (Phase 3)
│   ├── compute.bicep              # Compute resources (container instances, etc.)
│   ├── policy.bicep               # Azure Policy assignments (Governance)
│   ├── ai-sku-policy-def.bicep    # Custom AI SKU governance policy definition
│   └── budget.bicep               # Cost Management budget (Governance)
├── parameters/                    # Environment-specific Bicep parameters
│   ├── dev.bicepparam
│   ├── staging.bicepparam
│   └── prod.bicepparam
├── orchestrator/                  # Python deployment orchestrator
│   ├── core/
│   │   ├── config.py              # DeploymentConfig + three pillar sub-configs
│   │   ├── manager.py             # InfrastructureManager — main orchestrator
│   │   └── ooda_loop.py           # OODA loop (Observe → Orient → Decide → Act)
│   ├── governance/
│   │   ├── policy_manager.py      # Azure Policy assignments & compliance
│   │   ├── cost_manager.py        # Budget management & alerts
│   │   ├── rbac_manager.py        # Privileged access review
│   │   └── scale_down_auditor.py  # Scale-to-zero compliance auditor
│   ├── automation/
│   │   ├── pipeline.py            # Lint → validate → what-if → deploy pipeline
│   │   └── lifecycle.py           # deprovision / shift / modify / upgrade / scale
│   ├── reliability/
│   │   ├── drift_detector.py      # Infrastructure drift detection
│   │   └── health_monitor.py      # SLA-aware health checks & DR readiness
│   ├── integration/
│   │   ├── azure_sdk_client.py    # Azure SDK wrapper (resources, cost, state)
│   │   ├── identity_client.py     # Managed identity client ID fetch & Key Vault store
│   │   ├── sdk_bridge.py          # Bridge to aos-client-sdk (Function App deployment)
│   │   └── kernel_bridge.py       # Bridge to aos-kernel config sync
│   ├── validators/
│   │   └── regional_validator.py  # Regional service capability validation
│   └── cli/
│       ├── azure_ops.py           # 14 Azure SDK subcommands (OIDC-compatible)
│       ├── regional_tool.py       # Region discovery & validation tool
│       ├── workflow_helper.py     # GitHub Actions workflow automation helpers
│       └── resource_mapper.py     # Resource ID parsing & mapping
├── workflow-templates/            # GitHub Actions templates for code repositories
│   ├── agent-operating-system/    # Calls deploy-function-app.yml reusable workflow
│   ├── aos-realm-of-agents/       # Calls deploy-function-app.yml reusable workflow
│   ├── business-infinity/         # Calls deploy-function-app.yml reusable workflow
│   ├── mcp/                       # 4 parallel Function App deployments
│   ├── erpnext.asisaga.com/       # Calls deploy-function-app.yml reusable workflow
│   ├── linkedin.asisaga.com/      # Calls deploy-function-app.yml reusable workflow
│   ├── reddit.asisaga.com/        # Calls deploy-function-app.yml reusable workflow
│   ├── subconscious.asisaga.com/  # Calls deploy-function-app.yml reusable workflow
│   ├── ceo-agent/                 # Calls deploy-foundry-agent.yml reusable workflow
│   ├── cfo-agent/                 # Calls deploy-foundry-agent.yml reusable workflow
│   ├── cto-agent/                 # Calls deploy-foundry-agent.yml reusable workflow
│   ├── cso-agent/                 # Calls deploy-foundry-agent.yml reusable workflow
│   └── cmo-agent/                 # Calls deploy-foundry-agent.yml reusable workflow
└── tests/                         # Unit tests (123+ tests)
docs/                              # Repository documentation
.github/
└── workflows/
    ├── infrastructure-deploy.yml          # 5-phase infrastructure deployment (OODA-gated)
    ├── infrastructure-governance.yml      # Governance (daily)
    ├── infrastructure-drift-detection.yml # Drift detection (every 6 h)
    ├── infrastructure-troubleshooting.yml # Autonomous error diagnosis & fixing
    ├── cost-management.yml                # Cost audit and scale-down violations
    ├── deploy-function-app.yml            # Reusable: deploy Python Function App via OIDC
    └── deploy-foundry-agent.yml           # Reusable: deploy agent to Azure AI Foundry
```

## Three-Pillar Lifecycle

### 🏛️ Governance

```python
from deployment.orchestrator.core.config import DeploymentConfig, GovernanceConfig
from deployment.orchestrator.core.manager import InfrastructureManager

cfg = DeploymentConfig(
    environment="prod",
    resource_group="rg-aos-prod",
    location="westeurope",
    governance=GovernanceConfig(
        enforce_policies=True,
        budget_amount=2000.0,
        required_tags={"environment": "prod", "team": "platform"},
        review_rbac=True,
    ),
)
mgr = InfrastructureManager(cfg)
mgr.govern()   # policy compliance, tags, budget, RBAC
```

Standalone governance components:

```python
from deployment.orchestrator.governance.policy_manager import PolicyManager
from deployment.orchestrator.governance.cost_manager   import CostManager
from deployment.orchestrator.governance.rbac_manager   import RbacManager

pm = PolicyManager("rg-aos-prod", subscription_id="...")
pm.evaluate_compliance()
pm.assign_aos_policies("prod")
pm.enforce_required_tags({"environment": "prod"})

cm = CostManager("rg-aos-prod")
cm.get_current_spend(period_days=30)
cm.check_budget_alerts()

rm = RbacManager("rg-aos-prod")
rm.review_privileged_access()
```

### ⚙️ Automation (OODA-loop Deployment)

When `--cost-threshold` is set, the `deploy` command activates the **OODA loop** to gate deployments on infrastructure state:

1. **Observe** — Snapshot live infrastructure via Azure SDK (resources, health, cost)
2. **Orient** — Compare desired vs. actual state; factor in cost and compliance constraints
3. **Decide** — Recommend action: `DEPLOY`, `INCREMENTAL_UPDATE`, `SKIP`, `SCALE_DOWN`, `ALERT`, or `BLOCK`
4. **Act** — Execute approved actions; verify outcome

```python
# Standard deployment (open-loop)
mgr.deploy()

# OODA-loop deployment (cost-gated, auto-approve safe actions)
mgr.smart_deploy(cost_threshold=500.0, auto_approve=True)
```

### 🔁 Reliability

```python
from deployment.orchestrator.core.config import DeploymentConfig, ReliabilityConfig
from deployment.orchestrator.core.manager import InfrastructureManager

cfg = DeploymentConfig(
    environment="prod",
    resource_group="rg-aos-prod",
    location="westeurope",
    template="deployment/main-modular.bicep",
    reliability=ReliabilityConfig(
        enable_drift_detection=True,
        check_dr_readiness=True,
    ),
)
mgr = InfrastructureManager(cfg)
mgr.reliability_check()  # health + SLA + drift + DR
```

## Reusable Deployment Workflows

The deployment logic for all code repositories is centralised in two reusable GitHub Actions workflows:

| Reusable Workflow | Called By |
|---|---|
| [`.github/workflows/deploy-function-app.yml`](.github/workflows/deploy-function-app.yml) | All 8 Azure Function App repositories |
| [`.github/workflows/deploy-foundry-agent.yml`](.github/workflows/deploy-foundry-agent.yml) | All 5 C-suite agent repositories |

After `infrastructure-deploy.yml` completes Phase 4, it dispatches an `infra_provisioned` event to all code repositories. Each code repository's thin caller delegates to the appropriate reusable workflow via:

```yaml
uses: ASISaga/aos-infrastructure/.github/workflows/deploy-function-app.yml@main
with:
  app-name: 'agent-operating-system'
  environment: ${{ needs.resolve-env.outputs.environment }}
secrets: inherit
```

See [`deployment/workflow-templates/README.md`](deployment/workflow-templates/README.md) for the full setup guide.

## Key Features

- **OODA-loop Deployment** — Observe → Orient → Decide → Act cycle gates deployments on cost and infrastructure health
- **Agentic Deployment** — GitHub Actions workflow with autonomous error fixing via `deployment-error-fixer` skill
- **Smart Retry** — Failure classification (logic vs environmental) with exponential backoff
- **Regional Validation** — Automatic region selection based on service availability
- **Deployment Audit** — Full JSON audit trail (90-day retention as GitHub Actions artifacts)
- **Health Checks** — Post-deployment verification with SLA compliance tracking
- **Governance Policies** — Azure Policy for location, HTTPS, KV soft-delete, AI SKU governance
- **Cost Management** — Monthly budget with percentage-threshold alerts; scale-to-zero auditing
- **RBAC Review** — Privileged-access review and least-privilege enforcement
- **Drift Detection** — Infrastructure drift via Bicep what-if or manifest comparison
- **DR Readiness** — Key Vault soft-delete, geo-replication, and purge-protection checks
- **Custom Domains** — 16 `*.asisaga.com` hostnames bound with free App Service Managed Certificates and SNI TLS
- **Reusable Workflows** — Centralised `workflow_call` reusable workflows called from all 13 code repositories

## No Runtime Dependency

This repository has **zero Python runtime dependency** on `aos-kernel` or any AOS package. The deployment orchestrator is a self-contained CLI tool.

## DNS Prerequisites

All Function Apps and Foundry Agent endpoints use custom `*.asisaga.com` hostnames. **CNAME records must exist in your DNS provider before running the deployment with custom domains enabled** — the hostname-binding step in `functionapp.bicep` fails if the CNAME is absent.

See [docs/dns-setup.md](docs/dns-setup.md) for the complete CNAME list, DNS provider configuration, and the recommended two-phase deployment procedure.

## Testing

```bash
pip install -e ".[dev]"
pytest deployment/tests/ -v         # run all 123+ unit tests
pylint deployment/orchestrator/     # lint (max 120 chars)
az bicep build --file deployment/main-modular.bicep --stdout  # Bicep validation
```

## Related Repositories

### Platform services

| Repository | Role | Custom domain |
|---|---|---|
| [aos-kernel](https://github.com/ASISaga/aos-kernel) | OS kernel and agent runtime | `aos-kernel.asisaga.com` |
| [aos-dispatcher](https://github.com/ASISaga/aos-dispatcher) | Central orchestration hub | `aos-dispatcher.asisaga.com` |
| [aos-intelligence](https://github.com/ASISaga/aos-intelligence) | Intelligence layer | `aos-intelligence.asisaga.com` |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | Agent catalog and capability registry | `aos-realm-of-agents.asisaga.com` |
| [aos-mcp-servers](https://github.com/ASISaga/aos-mcp-servers) | MCP server framework | `aos-mcp-servers.asisaga.com` |
| [aos-client-sdk](https://github.com/ASISaga/aos-client-sdk) | Client SDK and Azure Functions scaffolding | `aos-client-sdk.asisaga.com` |
| [agent-operating-system](https://github.com/ASISaga/agent-operating-system) | AOS runtime Function App | `agent-operating-system.asisaga.com` |
| [business-infinity](https://github.com/ASISaga/business-infinity) | Business application | `business-infinity.asisaga.com` |

### C-suite agents (Azure AI Foundry Agent Service)

| Repository | Role | Custom domain |
|---|---|---|
| [ceo-agent](https://github.com/ASISaga/ceo-agent) | CEO — strategic decision-making | `ceo-agent.asisaga.com` |
| [cfo-agent](https://github.com/ASISaga/cfo-agent) | CFO — financial management | `cfo-agent.asisaga.com` |
| [cto-agent](https://github.com/ASISaga/cto-agent) | CTO — technology strategy | `cto-agent.asisaga.com` |
| [cso-agent](https://github.com/ASISaga/cso-agent) | CSO — security oversight | `cso-agent.asisaga.com` |
| [cmo-agent](https://github.com/ASISaga/cmo-agent) | CMO — marketing strategy | `cmo-agent.asisaga.com` |

### Base agent classes _(not deployed directly)_

| Repository | Role |
|---|---|
| [purpose-driven-agent](https://github.com/ASISaga/purpose-driven-agent) | Base class for purpose-driven agents |
| [leadership-agent](https://github.com/ASISaga/leadership-agent) | Base class for leadership agents |

### MCP server submodules

| Repository | Role | Custom domain |
|---|---|---|
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | ERPNext MCP server | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | LinkedIn MCP server | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | Reddit MCP server | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | Subconscious MCP server | `subconscious.asisaga.com` |

## Repository Name Suggestions

The current name `aos-infrastructure` accurately describes its role as infrastructure manager but doesn't capture its broader scope as an AI agent platform operations system. Consider these alternatives:

| Name | Rationale |
|---|---|
| **`aos-platform`** | Clean, reflects that this is the platform layer for the Agent Operating System — the foundation everything else builds on |
| **`agentos-platform`** | Spells out "Agent Operating System Platform" — unambiguous and discoverable |
| **`aos-platform-ops`** | Emphasises the Governance + Automation + Reliability (operations) aspect |
| **`aos-azure-platform`** | Explicit about the Azure cloud target; useful if multi-cloud is planned |
| **`aos-devops`** | Short and punchy; captures the DevOps/GitOps character of the repository |

**Recommendation**: `aos-platform` — concise, accurate, and clearly communicates the role of this repository as the Azure platform layer for the Agent Operating System.

## License

Apache License 2.0 — see [LICENSE](LICENSE)
