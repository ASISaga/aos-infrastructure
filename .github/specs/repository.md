# aos-infrastructure Repository Specification

**Version**: 1.0.0  
**Status**: Active  
**Last Updated**: 2026-03-07

## Overview

`aos-infrastructure` is the Azure infrastructure lifecycle manager for the Agent Operating System. It provides a standalone Python orchestrator and modular Bicep templates that deploy, govern, and continuously verify the AOS platform. The orchestrator has **zero runtime dependency** on any AOS package — it is a self-contained CLI tool.

## Scope

- Repository role in the AOS ecosystem
- Technology stack and directory layout
- Three-pillar architecture patterns
- Testing and validation workflows
- Key design principles for agents and contributors

## Repository Role

| Concern | Owner |
|---------|-------|
| Azure infrastructure lifecycle (deploy, govern, drift-detect, health) | **aos-infrastructure** |
| Bicep module definitions for all AOS platform resources | **aos-infrastructure** |
| AOS application runtime, agent orchestration, messaging | `aos-kernel`, `aos-dispatcher` |
| Agent catalog and capabilities | `aos-realm-of-agents` |
| Client SDK and Azure Functions scaffolding | `aos-client-sdk` |

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.10+ |
| Configuration | `pydantic>=2.12.0` — type-safe `DeploymentConfig` |
| Infrastructure-as-Code | Azure Bicep (14 modules) |
| Deployment | Azure Resource Manager via `az deployment group` |
| Tests | `pytest>=8.0.0` |
| Linter | `pylint>=3.0.0` (max line length: 120) |
| Build/Package | `setuptools`, `wheel` |
| CI/CD | GitHub Actions (5 workflows) |

## Directory Structure

```
deployment/
├── deploy.py                      # CLI entry point — all subcommands
├── main-modular.bicep             # Primary Bicep template (14 modules)
├── modules/                       # Bicep modules
│   ├── policy.bicep               # Azure Policy (Governance pillar)
│   ├── budget.bicep               # Cost Management (Governance pillar)
│   ├── monitoring.bicep
│   ├── storage.bicep
│   ├── keyvault.bicep
│   ├── servicebus.bicep
│   ├── functionapp.bicep
│   └── ai-*.bicep                 # AI hub, gateway, project, services, model-registry, lora-inference
├── parameters/                    # Environment-specific Bicep parameters
│   ├── dev.bicepparam
│   ├── staging.bicepparam
│   └── prod.bicepparam
├── orchestrator/                  # Python orchestration engine
│   ├── core/
│   │   ├── config.py              # DeploymentConfig (GovernanceConfig, AutomationConfig, ReliabilityConfig)
│   │   └── manager.py             # InfrastructureManager (deploy/plan/govern/reliability_check)
│   ├── governance/
│   │   ├── policy_manager.py      # Azure Policy assignments & compliance
│   │   ├── cost_manager.py        # Budget management & alerts
│   │   └── rbac_manager.py        # Privileged access review
│   ├── automation/
│   │   ├── pipeline.py            # Lint → validate → what-if → deploy pipeline
│   │   └── lifecycle.py           # deprovision / shift / modify / upgrade / scale
│   ├── reliability/
│   │   ├── drift_detector.py      # Infrastructure drift detection
│   │   └── health_monitor.py      # SLA-aware health checks & DR readiness
│   ├── integration/
│   │   ├── sdk_bridge.py          # Bridge to aos-client-sdk
│   │   └── kernel_bridge.py       # Bridge to aos-kernel config sync
│   ├── validators/
│   │   └── regional_validator.py  # Regional capability validation
│   └── cli/
│       ├── regional_tool.py
│       └── workflow_helper.py
├── tests/                         # 123 unit tests
│   ├── test_manager.py
│   ├── test_automation.py
│   └── test_lifecycle.py
└── docs/                          # Deployment documentation
docs/                              # Repository-level documentation
.github/
├── workflows/                     # 5 CI/CD automation workflows
│   ├── infrastructure-deploy.yml
│   ├── infrastructure-governance.yml
│   ├── infrastructure-drift-detection.yml
│   ├── infrastructure-monitoring.yml
│   └── infrastructure-troubleshooting.yml
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

### Deploy / Plan

```python
from deployment.orchestrator.core.manager import InfrastructureManager

mgr = InfrastructureManager(cfg)
mgr.plan()    # lint → validate → what-if (no changes applied)
mgr.deploy()  # lint → validate → what-if → deploy → health checks
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

**CI Workflows**:
- `infrastructure-deploy.yml` — Triggered by PR labels or `/deploy` comments; lint → what-if → deploy
- `infrastructure-governance.yml` — Daily policy, cost, and RBAC compliance checks
- `infrastructure-drift-detection.yml` — Every 6 hours; detects infrastructure drift
- `infrastructure-monitoring.yml` — Continuous health and SLA monitoring
- `infrastructure-troubleshooting.yml` — Autonomous error fixing via `deployment-error-fixer` skill

## Related Repositories

| Repository | Role |
|-----------|------|
| [aos-kernel](https://github.com/ASISaga/aos-kernel) | OS kernel |
| [aos-dispatcher](https://github.com/ASISaga/aos-dispatcher) | Main Azure Functions app |
| [aos-realm-of-agents](https://github.com/ASISaga/aos-realm-of-agents) | RealmOfAgents function app |
| [aos-mcp-servers](https://github.com/ASISaga/aos-mcp-servers) | MCPServers function app |
| [aos-client-sdk](https://github.com/ASISaga/aos-client-sdk) | Client SDK & App Framework |

## Key Design Principles

1. **Zero runtime dependency** — The orchestrator has no dependency on `aos-kernel` or any AOS package at runtime
2. **Three-pillar architecture** — Governance, Automation, and Reliability are distinct, composable pillars
3. **Lint before deploy** — Every deployment path runs Bicep linting and what-if analysis first
4. **Agentic self-healing** — Logic errors are auto-fixed by the `deployment-error-fixer` skill; environmental errors use exponential-backoff retry
5. **Audit trail** — All deployments emit JSON audit records uploaded as GitHub Actions artifacts (90-day retention)

## References

→ **Agent framework**: `.github/specs/agent-intelligence-framework.md`  
→ **Conventional tools**: `.github/docs/conventional-tools.md`  
→ **Python coding standards**: `.github/instructions/python.instructions.md`  
→ **Deployment instructions**: `.github/instructions/deployment.instructions.md`  
→ **Infrastructure deploy agent**: `.github/agents/infrastructure-deploy.agent.md`  
→ **Deployment error fixer skill**: `.github/skills/deployment-error-fixer/SKILL.md`  
→ **Orchestrator user guide**: `deployment/ORCHESTRATOR_USER_GUIDE.md`
