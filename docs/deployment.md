# Deployment Guide

**Last Updated**: 2026-03-24

This guide covers end-to-end deployment of the AOS platform infrastructure using `aos-infrastructure`.

## Overview

Deployment is organised into **five phases**, each deployed independently via a Bicep `deployment group` operation. Later phases depend on earlier ones but can be re-deployed individually.

| Phase | Bicep call | Resources |
|---|---|---|
| 1 — Foundation | `deploy-bicep-foundation` | Monitoring, Storage, Service Bus, Key Vault |
| 2 — AI Services | `deploy-bicep-ai-services` | AI Services, AI Foundry Hub, AI Foundry Project |
| 3 — AI Applications | `deploy-bicep-ai-apps` | LoRA inference endpoint, Foundry agent endpoints, AI Gateway, A2A connections |
| 4 — Function Apps | `deploy-bicep-function-apps` | AOS Function Apps + MCP server Function Apps |
| 5 — Governance | `deploy-bicep-governance` | Azure Policy assignments, Cost Management budget |

## Prerequisites

1. **DNS**: CNAME records for all 16 `*.asisaga.com` domains must exist before deploying with `baseDomain` set — see [dns-setup.md](dns-setup.md).
2. **GitHub Secrets** in `ASISaga/aos-infrastructure`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
3. **`DEPLOY_DISPATCH_TOKEN`** (PAT with `repo` scope) for dispatching `infra_provisioned` events to code repositories.
4. **GitHub Environments** in each code repository (`dev`, `staging`, `prod`) with the secrets listed in [workflow-templates/README.md](../deployment/workflow-templates/README.md).

## Full Deployment via GitHub Actions

1. Go to `ASISaga/aos-infrastructure` → **Actions** → **Infrastructure Deployment**.
2. Click **Run workflow**, select environment, resource group, and region.
3. The workflow runs all five phases sequentially; phase gates ensure later phases only run when earlier ones succeed.
4. After Phase 4, `infra_provisioned` events are dispatched to all code repositories.

## Manual Deployment via CLI

```bash
pip install -e ".[dev]"

# Full deployment (all phases)
python deployment/deploy.py deploy \
  --resource-group rg-aos-dev \
  --location eastus \
  --environment dev \
  --template deployment/main-modular.bicep

# Dry run (lint + validate + what-if only)
python deployment/deploy.py plan \
  --resource-group rg-aos-dev \
  --location eastus \
  --environment dev \
  --template deployment/main-modular.bicep

# Deploy individual phase
python deployment/deploy.py deploy-bicep-foundation \
  --resource-group rg-aos-dev \
  --location eastus \
  --environment dev \
  --template deployment/main-modular.bicep
```

## OODA-Loop Cost Gating

Add `--cost-threshold` to block deployment when monthly costs exceed a threshold:

```bash
python deployment/deploy.py deploy \
  --resource-group rg-aos-prod \
  --location westeurope \
  --environment prod \
  --template deployment/main-modular.bicep \
  --cost-threshold 1000 \
  --auto-approve
```

The OODA loop observes current infrastructure state and cost before acting. If cost exceeds the threshold, the action is `BLOCK` and deployment does not proceed.

## Post-Deployment

After a successful deployment:
- **Health checks** run automatically (`HealthMonitor.check_all()`)
- **Function App clientIds** are stored in Key Vault (`clientid-{app}-{env}`)
- **Code repositories** receive `infra_provisioned` events and deploy their application code

## Environment Strategy

| Environment | Resource group | Trigger |
|---|---|---|
| `dev` | `rg-aos-dev` | Push to `main` in code repos; manual |
| `staging` | `rg-aos-staging` | Manual `workflow_dispatch`; PR approval |
| `prod` | `rg-aos-prod` | GitHub Release; manual with approval |

## Troubleshooting

See [workflows.md](workflows.md#4-infrastructure-troubleshooting) for the troubleshooting workflow.

For common errors:
- **RBAC/Authorization failures** — Expected on first deploy when policy assignments require elevated permissions. The workflow auto-detects and retries with appropriate flags.
- **DNS binding failures** — CNAME must exist before deploying with `baseDomain` set. Use two-phase deployment (first without `baseDomain`, then with).
- **Bicep what-if failures** — Non-fatal; the deploy workflow continues on what-if errors (informational only).

## References

→ **DNS setup**: `docs/dns-setup.md`  
→ **Architecture**: `docs/architecture.md`  
→ **Workflow templates**: `deployment/workflow-templates/README.md`  
→ **API reference**: `docs/api-reference.md`
