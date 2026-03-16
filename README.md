# aos-infrastructure

Complete Azure infrastructure lifecycle manager for the Agent Operating System, providing a robust, scalable, and resilient foundation for AgentOperatingSystem and the applications utilizing it.

The repository is organised around **three pillars**:

| Pillar | Responsibility |
|---|---|
| **Governance** | Policy enforcement, cost management, RBAC access review, tag compliance |
| **Automation** | Lint → validate → what-if → deploy pipeline; de-provisioning, region shift, in-place modification, SKU upgrade, scaling; SDK bridge to `aos-client-sdk`; kernel config sync |
| **Reliability** | Drift detection, SLA-aware health monitoring, DR readiness assessment |

## Overview

`aos-infrastructure` contains all deployment infrastructure for AOS:

- **Bicep Templates** — Modular Azure infrastructure definitions (13 modules, including `policy.bicep` and `budget.bicep`)
- **Python Orchestrator** — Smart deployment CLI with linting, validation, health checks, and lifecycle management
- **Governance Pillar** — Azure Policy assignments, cost/budget management, RBAC access review (`governance/`)
- **Automation Pillar** — Deployment pipeline, lifecycle operations (deprovision/shift/modify/upgrade/scale), integration with `aos-client-sdk` and `aos-kernel` (`automation/`, `integration/`)
- **Reliability Pillar** — Infrastructure drift detection, SLA compliance tracking, DR readiness (`reliability/`)
- **Regional Validation** — Automatic region selection and capability validation
- **CI/CD Workflows** — Deploy, governance, drift-detection, monitoring, and troubleshooting workflows

## Quick Start

```bash
# Deploy to dev environment
python deployment/deploy.py deploy --resource-group my-rg --location eastus --environment dev --template deployment/main-modular.bicep

# Plan deployment (dry run — lint, validate, what-if only)
python deployment/deploy.py plan --resource-group my-rg --location eastus --environment dev --template deployment/main-modular.bicep

# Run the Automation pillar (pipeline + optional SDK bridge + kernel sync)
python deployment/deploy.py automate --resource-group my-rg --location eastus --environment dev --template deployment/main-modular.bicep --deploy-function-apps

# Run the Governance pillar (policy compliance, cost, RBAC)
python deployment/deploy.py govern --resource-group my-rg --environment dev

# Run the Reliability pillar (health, SLA, drift, DR)
python deployment/deploy.py reliability --resource-group my-rg --environment dev

# Infrastructure lifecycle operations
python deployment/deploy.py deprovision --resource-group my-rg --resource-name st1 --resource-type Microsoft.Storage/storageAccounts
python deployment/deploy.py upgrade --resource-group my-rg --resource-name st1 --resource-type microsoft.storage/storageaccounts --new-sku Standard_ZRS
python deployment/deploy.py scale --resource-group my-rg --resource-name apim --resource-type microsoft.apimanagement/service --scale-settings '{"sku.capacity": 2}'
python deployment/deploy.py shift --resource-group my-rg --target-rg my-rg-dr --target-region westeurope
python deployment/deploy.py modify --resource-group my-rg --resource-name func1 --resource-type microsoft.web/sites --properties '{"properties.httpsOnly": true}'
```

## Repository Structure

```
deployment/                        # Bicep templates, orchestrator, validators
├── main-modular.bicep             # Primary Bicep template (13 modules)
├── modules/                       # Bicep modules
│   ├── policy.bicep               # Azure Policy assignments (Governance)
│   ├── budget.bicep               # Cost Management budget (Governance)
│   └── ...                        # monitoring, storage, servicebus, keyvault, ...
├── parameters/                    # Environment-specific parameters
├── orchestrator/                  # Python deployment orchestrator
│   ├── core/                      # DeploymentConfig (3 pillar sub-configs) + InfrastructureManager
│   ├── governance/                # PolicyManager, CostManager, RbacManager
│   ├── automation/                # PipelineManager, LifecycleManager (deprovision/shift/modify/upgrade/scale)
│   ├── reliability/               # DriftDetector, HealthMonitor
│   ├── integration/               # SDKBridge (aos-client-sdk), KernelBridge (aos-kernel)
│   ├── validators/                # RegionalValidator
│   └── cli/                       # regional_tool, workflow_helper
├── tests/                         # Deployment tests (123 tests)
└── deploy.py                      # Entry point (all pillar + lifecycle subcommands)
docs/                              # Deployment documentation
.github/                           # Workflows, skills, prompts
└── workflows/
    ├── infrastructure-deploy.yml          # Deployment pipeline
    ├── infrastructure-monitoring.yml      # Health monitoring
    ├── infrastructure-troubleshooting.yml # Troubleshooting
    ├── infrastructure-governance.yml      # Governance (daily)
    └── infrastructure-drift-detection.yml # Drift detection (every 6 h)
```

## Three-Pillar Lifecycle

### 🏛️ Governance

```python
from orchestrator.core.config import DeploymentConfig, GovernanceConfig
from orchestrator.core.manager import InfrastructureManager

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
mgr.govern()   # evaluate policy compliance, tags, budget, RBAC
```

Standalone governance components:

```python
from orchestrator.governance.policy_manager import PolicyManager
from orchestrator.governance.cost_manager   import CostManager
from orchestrator.governance.rbac_manager   import RbacManager

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

### ⚙️ Automation

The existing `InfrastructureManager.deploy()` and `plan()` pipelines are
supplemented by post-deploy governance and reliability lifecycle hooks that
activate automatically when the corresponding settings are enabled in
`DeploymentConfig`.

### 🔁 Reliability

```python
from orchestrator.core.config import DeploymentConfig, ReliabilityConfig
from orchestrator.core.manager import InfrastructureManager

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

Standalone reliability components:

```python
from orchestrator.reliability.drift_detector import DriftDetector
from orchestrator.reliability.health_monitor import HealthMonitor

dd = DriftDetector("rg-aos-prod")
dd.detect_drift("deployment/main-modular.bicep")          # template what-if
dd.detect_drift_from_manifest([{"name": "st1", ...}])     # manifest compare

hm = HealthMonitor("rg-aos-prod", "prod")
hm.check_all()
hm.check_sla_compliance()
hm.check_disaster_recovery_readiness()
```

## Key Features

- **Agentic Deployment** — GitHub Actions workflow with autonomous error fixing
- **Smart Retry** — Failure classification (logic vs environmental) with exponential backoff
- **Regional Validation** — Automatic region selection based on service availability
- **Deployment Audit** — Full audit trail of all deployment operations
- **Health Checks** — Post-deployment verification with SLA tracking
- **Governance Policies** — Azure Policy assignments for location, HTTPS, KV soft-delete
- **Cost Management** — Monthly budget with percentage-threshold alerts
- **RBAC Review** — Privileged-access review and least-privilege enforcement
- **Drift Detection** — Detect infrastructure drift via Bicep what-if or manifest comparison
- **DR Readiness** — Key Vault soft-delete, geo-replication, and purge-protection checks
- **Custom Domains** — 16 `*.asisaga.com` hostnames bound with free App Service Managed Certificates and SNI TLS

## No Runtime Dependency

This repository has **zero Python runtime dependency** on `aos-kernel` or any AOS package. The deployment orchestrator is a standalone CLI tool.

## DNS Prerequisites

All 16 production Function Apps use custom `*.asisaga.com` hostnames.  **CNAME records
must exist in your DNS provider before running the deployment with custom domains
enabled** — the hostname-binding step in `functionapp.bicep` fails if the CNAME is absent.

See [docs/dns-setup.md](docs/dns-setup.md) for the complete CNAME list, DNS provider
configuration, and the recommended two-phase deployment procedure.

## Related Repositories

### Platform services

| Repository | Role | Custom domain |
|-----------|------|---------------|
| [aos-kernel](https://github.com/ASISaga/aos-kernel) | OS kernel and agent runtime | `aos-kernel.asisaga.com` |
| [aos-dispatcher](https://github.com/ASISaga/aos-dispatcher) | Central orchestration hub | `aos-dispatcher.asisaga.com` |
| [aos-intelligence](https://github.com/ASISaga/aos-intelligence) | Intelligence layer | `aos-intelligence.asisaga.com` |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | Agent catalog and capability registry | `aos-realm-of-agents.asisaga.com` |
| [aos-mcp-servers](https://github.com/ASISaga/aos-mcp-servers) | MCP server framework | `aos-mcp-servers.asisaga.com` |
| [aos-client-sdk](https://github.com/ASISaga/aos-client-sdk) | Client SDK and Azure Functions scaffolding | `aos-client-sdk.asisaga.com` |
| [business-infinity](https://github.com/ASISaga/business-infinity) | Business application | `business-infinity.asisaga.com` |

### C-suite agents

| Repository | Role | Custom domain |
|-----------|------|---------------|
| [ceo-agent](https://github.com/ASISaga/ceo-agent) | CEO C-suite agent | `ceo-agent.asisaga.com` |
| [cfo-agent](https://github.com/ASISaga/cfo-agent) | CFO C-suite agent | `cfo-agent.asisaga.com` |
| [cto-agent](https://github.com/ASISaga/cto-agent) | CTO C-suite agent | `cto-agent.asisaga.com` |
| [cso-agent](https://github.com/ASISaga/cso-agent) | CSO C-suite agent | `cso-agent.asisaga.com` |
| [cmo-agent](https://github.com/ASISaga/cmo-agent) | CMO C-suite agent | `cmo-agent.asisaga.com` |

### Base agent classes _(not deployed directly)_

| Repository | Role |
|-----------|------|
| [purpose-driven-agent](https://github.com/ASISaga/purpose-driven-agent) | Base class for purpose-driven agents |
| [leadership-agent](https://github.com/ASISaga/leadership-agent) | Base class for leadership agents |

### MCP server submodules

Each MCP server repository name doubles as its custom domain — see [DNS Setup](docs/dns-setup.md#mcp-server-domains) for the rationale.

| Repository | Role | Custom domain |
|-----------|------|---------------|
| [erpnext.asisaga.com](https://github.com/ASISaga/erpnext.asisaga.com) | ERPNext MCP server | `erpnext.asisaga.com` |
| [linkedin.asisaga.com](https://github.com/ASISaga/linkedin.asisaga.com) | LinkedIn MCP server | `linkedin.asisaga.com` |
| [reddit.asisaga.com](https://github.com/ASISaga/reddit.asisaga.com) | Reddit MCP server | `reddit.asisaga.com` |
| [subconscious.asisaga.com](https://github.com/ASISaga/subconscious.asisaga.com) | Subconscious MCP server | `subconscious.asisaga.com` |

## License

Apache License 2.0 — see [LICENSE](LICENSE)
