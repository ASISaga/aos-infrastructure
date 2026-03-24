# aos-infrastructure Repository Specification

**Version**: 1.2.0  
**Status**: Active  
**Last Updated**: 2026-03-24

## Overview

`aos-infrastructure` is the Azure platform layer for the Agent Operating System (AOS) — a multi-agent AI system running autonomous C-suite agents and supporting services on Azure. It provides a standalone Python orchestrator and 20 modular Bicep templates that deploy, govern, and continuously verify the AOS platform. The orchestrator has **zero runtime dependency** on any AOS package.

## Scope

- Repository role in the AOS ecosystem
- Technology stack and directory layout
- Three-pillar architecture patterns (Governance, Automation, Reliability)
- OODA-loop deployment model
- Reusable workflow architecture for code repositories
- Testing and validation workflows
- Key design principles for agents and contributors

## Repository Role

| Concern | Owner |
|---------|-------|
| Azure infrastructure lifecycle (deploy, govern, drift-detect, health) | **aos-infrastructure** |
| Bicep module definitions for all AOS platform resources | **aos-infrastructure** |
| Reusable GitHub Actions workflows for Function App and Foundry Agent deployment | **aos-infrastructure** |
| AOS application runtime, agent orchestration, messaging | `aos-kernel`, `aos-dispatcher` |
| Agent catalog and capabilities | `aos-realm-of-agents` |
| Client SDK and Azure Functions scaffolding | `aos-client-sdk` |

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.10+ |
| Configuration | `pydantic>=2.12.0` — type-safe `DeploymentConfig` |
| Infrastructure-as-Code | Azure Bicep (20 modules, 15 directly composed in `main-modular.bicep`) |
| Deployment | Azure Resource Manager via `az deployment group` |
| Azure SDK | `azure-identity`, `azure-mgmt-resource`, `azure-mgmt-costmanagement`, `azure-mgmt-msi`, `azure-mgmt-web`, `azure-keyvault-secrets`, `azure-mgmt-monitor`, `azure-mgmt-servicebus` |
| Tests | `pytest>=8.0.0` (123+ unit tests) |
| Linter | `pylint>=3.0.0` (max line length: 120) |
| Build/Package | `setuptools`, `wheel` |
| CI/CD | GitHub Actions (8 workflows: 5 infrastructure + 2 reusable + 1 cost audit) |

## Directory Structure

```
deployment/
├── deploy.py                      # CLI entry point — all subcommands
├── main-modular.bicep             # Primary Bicep template (15 direct module calls)
├── modules/                       # 20 Bicep modules
│   ├── monitoring.bicep           # Application Insights, Log Analytics
│   ├── storage.bicep              # Storage accounts
│   ├── servicebus.bicep           # Service Bus namespace + queues
│   ├── keyvault.bicep             # Key Vault + secrets management
│   ├── identity.bicep             # User-Assigned Managed Identities (GitHub OIDC)
│   ├── rbac.bicep                 # RBAC role assignments
│   ├── ai-services.bicep          # Azure AI Services (Cognitive Services)
│   ├── ai-hub.bicep               # Azure AI Foundry Hub (ML Workspace Hub)
│   ├── ai-project.bicep           # Azure AI Foundry Project (ML Workspace Project)
│   ├── machinelearning.bicep      # ML compute and registry resources
│   ├── lora-inference.bicep       # Llama-3.3-70B Multi-LoRA shared inference endpoint
│   ├── foundry-app.bicep          # Foundry Agent Service endpoints (C-suite agents; looped)
│   ├── ai-gateway.bicep           # API Management (rate limiting, JWT validation)
│   ├── a2a-connections.bicep      # Agent-to-Agent connections (C-suite boardroom)
│   ├── functionapp.bicep          # FC1 Flex Consumption + custom domain (3-phase binding)
│   ├── functionapp-ssl.bicep      # SNI TLS re-binding sub-module (Phase 3)
│   ├── compute.bicep              # Compute resources (container instances, etc.)
│   ├── policy.bicep               # Azure Policy assignments (Governance pillar)
│   ├── ai-sku-policy-def.bicep    # Custom policy: deny Provisioned/PTU AI SKUs
│   └── budget.bicep               # Cost Management budget (Governance pillar)
├── parameters/                    # Environment-specific Bicep parameters
│   ├── dev.bicepparam
│   ├── staging.bicepparam
│   └── prod.bicepparam
├── orchestrator/                  # Python orchestration engine
│   ├── core/
│   │   ├── config.py              # DeploymentConfig (GovernanceConfig, AutomationConfig, ReliabilityConfig)
│   │   ├── manager.py             # InfrastructureManager (all pillar + lifecycle operations)
│   │   └── ooda_loop.py           # OODA loop: Observe → Orient → Decide → Act
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
│   │   ├── azure_sdk_client.py    # Azure SDK wrapper (resources, cost, state snapshots)
│   │   ├── identity_client.py     # Managed identity client ID fetch & Key Vault store
│   │   ├── sdk_bridge.py          # Bridge to aos-client-sdk (Function App deployment)
│   │   └── kernel_bridge.py       # Bridge to aos-kernel config sync
│   ├── validators/
│   │   └── regional_validator.py  # Regional service capability validation
│   └── cli/
│       ├── azure_ops.py           # 14 Azure SDK subcommands (OIDC-compatible; no az CLI)
│       ├── regional_tool.py       # Region discovery & validation
│       ├── workflow_helper.py     # GitHub Actions workflow automation helpers
│       └── resource_mapper.py     # Resource ID parsing & mapping
├── workflow-templates/            # Deployment workflow templates for code repositories
│   ├── README.md                  # Full setup guide for calling the reusable workflows
│   ├── agent-operating-system/    # deploy.yml — calls deploy-function-app.yml
│   ├── aos-realm-of-agents/       # deploy.yml — calls deploy-function-app.yml
│   ├── business-infinity/         # deploy.yml — calls deploy-function-app.yml
│   ├── mcp/                       # deploy.yml — 4 parallel calls to deploy-function-app.yml
│   ├── erpnext.asisaga.com/       # deploy.yml — calls deploy-function-app.yml
│   ├── linkedin.asisaga.com/      # deploy.yml — calls deploy-function-app.yml
│   ├── reddit.asisaga.com/        # deploy.yml — calls deploy-function-app.yml
│   ├── subconscious.asisaga.com/  # deploy.yml — calls deploy-function-app.yml
│   ├── ceo-agent/                 # deploy.yml — calls deploy-foundry-agent.yml
│   ├── cfo-agent/                 # deploy.yml — calls deploy-foundry-agent.yml
│   ├── cto-agent/                 # deploy.yml — calls deploy-foundry-agent.yml
│   ├── cso-agent/                 # deploy.yml — calls deploy-foundry-agent.yml
│   └── cmo-agent/                 # deploy.yml — calls deploy-foundry-agent.yml
├── tests/                         # 123+ unit tests
│   ├── test_manager.py
│   ├── test_automation.py
│   ├── test_lifecycle.py
│   ├── test_ooda_loop.py
│   ├── test_azure_sdk_client.py
│   ├── test_azure_ops.py
│   ├── test_identity_client.py
│   ├── test_scale_down_auditor.py
│   ├── test_orchestrator.py
│   ├── test_resource_mapper.py
│   └── test_workflow_helper.py
└── docs/                          # Deployment documentation
docs/                              # Repository-level documentation
.github/
├── workflows/
│   ├── infrastructure-deploy.yml          # 5-phase infra deployment (OODA-gated)
│   ├── infrastructure-governance.yml      # Governance (daily 06:00 UTC)
│   ├── infrastructure-drift-detection.yml # Drift detection (every 6 h)
│   ├── infrastructure-troubleshooting.yml # Autonomous diagnostics & error fixing
│   ├── cost-management.yml                # Cost audit + scale-down violations
│   ├── deploy-function-app.yml            # Reusable: deploy Python Function App via OIDC
│   ├── deploy-foundry-agent.yml           # Reusable: deploy agent to Azure AI Foundry
│   └── copilot-setup-steps.yml            # Copilot agent environment setup
└── specs/repository.md            # This file
```

## Core Patterns

### Three-Pillar Configuration

```python
from deployment.orchestrator.core.config import (
    DeploymentConfig, GovernanceConfig, AutomationConfig, ReliabilityConfig,
)

cfg = DeploymentConfig(
    environment="prod",
    resource_group="rg-aos-prod",
    location="westeurope",
    template="deployment/main-modular.bicep",
    governance=GovernanceConfig(
        enforce_policies=True,
        budget_amount=2000.0,
        required_tags={"environment": "prod", "team": "platform"},
        review_rbac=True,
    ),
    reliability=ReliabilityConfig(
        enable_drift_detection=True,
        check_dr_readiness=True,
    ),
)
```

### Deploy / Plan / Smart Deploy (OODA loop)

```python
from deployment.orchestrator.core.manager import InfrastructureManager

mgr = InfrastructureManager(cfg)
mgr.plan()    # lint → validate → what-if (no changes applied)
mgr.deploy()  # lint → validate → what-if → deploy → health checks (open-loop)
mgr.smart_deploy(cost_threshold=500.0, auto_approve=True)
# OODA loop: observe state → orient vs desired → decide action → act
```

CLI equivalents:

```bash
python deployment/deploy.py plan --resource-group rg-aos-dev --location eastus --environment dev --template deployment/main-modular.bicep
python deployment/deploy.py deploy --resource-group rg-aos-prod --location westeurope --environment prod --template deployment/main-modular.bicep
python deployment/deploy.py deploy --resource-group rg-aos-prod --location westeurope --environment prod --template deployment/main-modular.bicep --cost-threshold 500 --auto-approve
```

### Governance Pillar

```python
from deployment.orchestrator.governance.policy_manager import PolicyManager
from deployment.orchestrator.governance.cost_manager   import CostManager
from deployment.orchestrator.governance.rbac_manager   import RbacManager

pm = PolicyManager("rg-aos-prod", subscription_id="<sub-id>")
pm.evaluate_compliance()
pm.assign_aos_policies("prod")
pm.enforce_required_tags({"environment": "prod"})

cm = CostManager("rg-aos-prod")
cm.check_budget_alerts()

rm = RbacManager("rg-aos-prod")
rm.review_privileged_access()
```

### Reliability Pillar

```python
from deployment.orchestrator.reliability.drift_detector import DriftDetector
from deployment.orchestrator.reliability.health_monitor import HealthMonitor

dd = DriftDetector("rg-aos-prod")
dd.detect_drift("deployment/main-modular.bicep")       # template what-if
dd.detect_drift_from_manifest([{"name": "st1", ...}])  # manifest compare

hm = HealthMonitor("rg-aos-prod", "prod")
hm.check_all()
hm.check_sla_compliance()
hm.check_disaster_recovery_readiness()
```

### Lifecycle Operations (CLI)

```bash
python deployment/deploy.py deprovision --resource-group rg --resource-name st1 --resource-type Microsoft.Storage/storageAccounts
python deployment/deploy.py upgrade     --resource-group rg --resource-name st1 --resource-type microsoft.storage/storageaccounts --new-sku Standard_ZRS
python deployment/deploy.py scale       --resource-group rg --resource-name apim --resource-type microsoft.apimanagement/service --scale-settings '{"sku.capacity": 2}'
python deployment/deploy.py shift       --resource-group rg --target-rg rg-dr --target-region westeurope
python deployment/deploy.py modify      --resource-group rg --resource-name func1 --resource-type microsoft.web/sites --properties '{"properties.httpsOnly": true}'
```

## Reusable Workflow Architecture

All code repositories call centralised `workflow_call` reusable workflows hosted here:

| Reusable Workflow | Called By | What It Does |
|---|---|---|
| `deploy-function-app.yml` | 8 Function App repos | OIDC login → resolve Function App name → `azure/functions-action@v1` deploy |
| `deploy-foundry-agent.yml` | 5 C-suite agent repos | OIDC login → read `agent.yaml` → create/update agent in AI Foundry via `azure-ai-projects` |

Calling template pattern (all templates are identical thin callers, ~80 lines):

```yaml
jobs:
  resolve-env:
    ...
  deploy:
    needs: resolve-env
    environment: ${{ needs.resolve-env.outputs.environment }}
    uses: ASISaga/aos-infrastructure/.github/workflows/deploy-function-app.yml@main
    with:
      app-name: 'agent-operating-system'
      environment: ${{ needs.resolve-env.outputs.environment }}
      key-vault-url: ${{ needs.resolve-env.outputs.key_vault_url }}
    secrets: inherit
```

After `infrastructure-deploy.yml` Phase 4 completes, it dispatches `infra_provisioned` to all 8 code repositories. Each repository triggers and delegates to the reusable workflow.

## Testing Workflow

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest deployment/tests/ -v

# Lint (max line length 120)
pylint deployment/orchestrator/

# Bicep compilation check
az bicep build --file deployment/main-modular.bicep --stdout

# Dry-run deployment (plan only)
python deployment/deploy.py plan --resource-group my-rg --location eastus --environment dev --template deployment/main-modular.bicep
```

**CI Workflows** (8 total):
- `infrastructure-deploy.yml` — 5-phase Bicep deployment with OODA gating and cost threshold enforcement
- `infrastructure-governance.yml` — Daily policy, cost, and RBAC compliance checks
- `infrastructure-drift-detection.yml` — Every 6 hours; detects infrastructure drift
- `infrastructure-troubleshooting.yml` — Autonomous error diagnosis and fixing via `deployment-error-fixer` skill
- `cost-management.yml` — Azure Cost Management audit; scale-down violations; GitHub issue creation
- `deploy-function-app.yml` — Reusable workflow called by Function App code repositories
- `deploy-foundry-agent.yml` — Reusable workflow called by C-suite agent repositories
- `copilot-setup-steps.yml` — Configures GitHub Copilot Agent environment

## Related Repositories

| Repository | Role | Custom domain |
|-----------|------|---------------|
| [aos-kernel](https://github.com/ASISaga/aos-kernel) | OS kernel | `aos-kernel.asisaga.com` |
| [aos-dispatcher](https://github.com/ASISaga/aos-dispatcher) | Central orchestration hub | `aos-dispatcher.asisaga.com` |
| [aos-intelligence](https://github.com/ASISaga/aos-intelligence) | Intelligence layer | `aos-intelligence.asisaga.com` |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | Agent catalog | `aos-realm-of-agents.asisaga.com` |
| [aos-mcp-servers](https://github.com/ASISaga/aos-mcp-servers) | MCP server framework | `aos-mcp-servers.asisaga.com` |
| [aos-client-sdk](https://github.com/ASISaga/aos-client-sdk) | Client SDK & App Framework | `aos-client-sdk.asisaga.com` |
| [agent-operating-system](https://github.com/ASISaga/agent-operating-system) | AOS runtime Function App | `agent-operating-system.asisaga.com` |
| [business-infinity](https://github.com/ASISaga/business-infinity) | Business application | `business-infinity.asisaga.com` |
| [ceo-agent](https://github.com/ASISaga/ceo-agent) | CEO C-suite agent (Foundry) | `ceo-agent.asisaga.com` |
| [cfo-agent](https://github.com/ASISaga/cfo-agent) | CFO C-suite agent (Foundry) | `cfo-agent.asisaga.com` |
| [cto-agent](https://github.com/ASISaga/cto-agent) | CTO C-suite agent (Foundry) | `cto-agent.asisaga.com` |
| [cso-agent](https://github.com/ASISaga/cso-agent) | CSO C-suite agent (Foundry) | `cso-agent.asisaga.com` |
| [cmo-agent](https://github.com/ASISaga/cmo-agent) | CMO C-suite agent (Foundry) | `cmo-agent.asisaga.com` |
| [purpose-driven-agent](https://github.com/ASISaga/purpose-driven-agent) | Base agent class | _(not deployed directly)_ |
| [leadership-agent](https://github.com/ASISaga/leadership-agent) | Base leadership class | _(not deployed directly)_ |
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | ERPNext MCP server | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | LinkedIn MCP server | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | Reddit MCP server | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | Subconscious MCP server | `subconscious.asisaga.com` |

## Key Design Principles

1. **Zero runtime dependency** — The orchestrator has no dependency on `aos-kernel` or any AOS package at runtime
2. **Three-pillar architecture** — Governance, Automation, and Reliability are distinct, composable pillars
3. **Lint before deploy** — Every deployment path runs Bicep linting and what-if analysis first
4. **OODA-loop deployment** — Smart deployment gates on infrastructure state (cost, health) before acting
5. **Reusable workflows** — All Function App and Foundry Agent deployment logic lives here; code repos are thin callers
6. **Agentic self-healing** — Logic errors are auto-fixed by the `deployment-error-fixer` skill; environmental errors use exponential-backoff retry
7. **Audit trail** — All deployments emit JSON audit records uploaded as GitHub Actions artifacts (90-day retention)
8. **Custom domains** — 16 `*.asisaga.com` CNAME records backed by free App Service Managed Certificates; CNAMEs must exist before deploying with `baseDomain` set

## References

→ **DNS setup guide**: `docs/dns-setup.md` — full CNAME list, DNS requirements, two-phase deployment procedure  
→ **Deployment architecture**: `docs/architecture.md` — three-tier model, OODA loop, reusable workflow architecture  
→ **Workflow guide**: `docs/workflows.md` — GitHub Actions workflow reference  
→ **Workflow templates guide**: `deployment/workflow-templates/README.md` — code repo setup guide  
→ **Agent framework**: `.github/specs/agent-intelligence-framework.md`  
→ **Conventional tools**: `.github/docs/conventional-tools.md`  
→ **Python coding standards**: `.github/instructions/python.instructions.md`  
→ **Deployment instructions**: `.github/instructions/deployment.instructions.md`  
→ **Infrastructure deploy agent**: `.github/agents/infrastructure-deploy.agent.md`  
→ **Deployment error fixer skill**: `.github/skills/deployment-error-fixer/SKILL.md`  
→ **Orchestrator user guide**: `deployment/ORCHESTRATOR_USER_GUIDE.md`
