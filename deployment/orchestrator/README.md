# AOS Deployment Orchestrator

Python orchestration logic for Azure infrastructure management.

## Prerequisites

Azure SDK packages are **required**:
```bash
pip install azure-identity azure-mgmt-resource azure-mgmt-costmanagement
```

Or install from the project root:
```bash
pip install -e "."
```

## Structure

- `core/config.py` — `DeploymentConfig` pydantic model
- `core/manager.py` — `InfrastructureManager` (state-verified deploy, smart-deploy, plan, status, monitor, troubleshoot, delete)
- `core/ooda_loop.py` — OODA loop orchestration (Observe → Orient → Decide → Act)
- `integration/azure_sdk_client.py` — `AzureSDKClient` typed wrapper around Azure Management SDKs
- `governance/cost_manager.py` — Cost Management and budget enforcement
- `reliability/health_monitor.py` — Health monitoring with SLA tracking
- `reliability/drift_detector.py` — Infrastructure drift detection
- `automation/pipeline.py` — IaC deployment pipeline (lint → validate → what-if → deploy)
- `cli/workflow_helper.py` — GitHub Actions workflow helper
- `cli/regional_tool.py` — Regional validation CLI
- `validators/regional_validator.py` — `RegionalValidator` for Azure region/service availability
