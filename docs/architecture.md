# AOS Deployment Architecture

## Three-Tier Deployment Model

1. **Agent Layer** — GitHub Actions workflow interprets deployment intent and handles errors
2. **Python Layer** — Deployment orchestrator (deploy.py) manages the deployment lifecycle
3. **Bicep Layer** — Infrastructure-as-Code defines Azure resources

## Deployment Pipeline

```
Developer → GitHub PR → Agentic Workflow → Python Orchestrator → Bicep → Azure
                         ↑                  ↑                     ↑
                    Error fixing        Linting/Validation    Regional validation
```

## Key Components

- **Orchestrator** — Core deployment engine with state machine
- **Failure Classifier** — Categorizes errors as logic or environmental
- **Regional Validator** — Validates and selects optimal Azure regions
- **Health Checker** — Post-deployment verification
- **Audit Logger** — Deployment audit trail

## Modular Bicep Architecture

```
main-modular.bicep                  # Entry point — composes all modules
├── modules/monitoring.bicep        # Application Insights, Log Analytics
├── modules/storage.bicep           # Storage accounts
├── modules/servicebus.bicep        # Service Bus namespace and queues
├── modules/keyvault.bicep          # Key Vault and secrets
├── modules/ai-services.bicep       # Azure AI Services (Cognitive Services)
├── modules/ai-hub.bicep            # Azure AI Foundry Hub (ML Workspace)
├── modules/ai-project.bicep        # Azure AI Foundry Project (ML Workspace)
├── modules/model-registry.bicep    # Model registry for LoRA adapter assets
├── modules/lora-inference.bicep    # Llama-3.3-70B-Instruct managed endpoint
├── modules/ai-gateway.bicep        # API Management (rate limiting + JWT)
├── modules/a2a-connections.bicep   # Agent-to-Agent connections (C-suite)
├── modules/functionapp.bicep       # FC1 Flex Consumption plan + Function App
│                                   #   (one per app: 12 AOS modules + 4 MCP servers)
└── modules/functionapp-ssl.bicep   # SNI TLS re-binding sub-module (Phase 3)
```

## Custom Domain and DNS Architecture

Every Function App is bound to a `*.asisaga.com` custom hostname secured by a free
App Service Managed Certificate.  The binding follows a three-phase sequence per app:

```
DNS provider            Azure App Service
─────────────────────   ─────────────────────────────────────────────
CNAME record pre-created
  ceo-agent.asisaga.com
  → func-ceo-agent-        Phase 1 — hostnameBinding (sslState: Disabled)
    prod-<suffix>.          ↓
    azurewebsites.net      Phase 2 — managedCertificate (free, auto-renewing)
                            ↓
                           Phase 3 — sslBinding (SniEnabled + thumbprint)
                            ↓
                           https://ceo-agent.asisaga.com  ✅
```

**Domain derivation rules:**

| App category | Custom domain formula | Example |
|-------------|----------------------|---------|
| Standard AOS modules (12) | `<appName>.asisaga.com` | `aos-dispatcher.asisaga.com` |
| MCP servers (4) | `githubRepo` value (IS the full domain) | `erpnext.asisaga.com` |

**All 16 production custom domains:**

```
ceo-agent.asisaga.com        cfo-agent.asisaga.com
cto-agent.asisaga.com        cso-agent.asisaga.com
cmo-agent.asisaga.com        aos-kernel.asisaga.com
aos-intelligence.asisaga.com aos-realm-of-agents.asisaga.com
aos-mcp-servers.asisaga.com  aos-client-sdk.asisaga.com
business-infinity.asisaga.com aos-dispatcher.asisaga.com
erpnext.asisaga.com          linkedin.asisaga.com
reddit.asisaga.com           subconscious.asisaga.com
```

→ Full CNAME list, deployment procedure, and per-environment strategy: `docs/dns-setup.md`

## Python Orchestrator Architecture

```
deploy.py                           # CLI entry point
orchestrator/
├── core/
│   ├── config.py                   # DeploymentConfig (three pillar sub-configs)
│   └── manager.py                  # InfrastructureManager (deploy/plan/govern/reliability)
├── governance/
│   ├── policy_manager.py           # Azure Policy assignments & compliance
│   ├── cost_manager.py             # Budget management & alerts
│   └── rbac_manager.py             # Privileged access review
├── automation/
│   ├── pipeline.py                 # Lint → validate → what-if → deploy pipeline
│   └── lifecycle.py                # deprovision / shift / modify / upgrade / scale
├── reliability/
│   ├── drift_detector.py           # Infrastructure drift detection
│   └── health_monitor.py           # SLA-aware health checks & DR readiness
├── integration/
│   ├── sdk_bridge.py               # Bridge to aos-client-sdk (endpoint discovery)
│   └── kernel_bridge.py            # Bridge to aos-kernel config sync
└── validators/
    └── regional_validator.py       # Regional capability validation
```

## Environment Strategy

| Environment | Purpose              | Deployment Trigger      |
|------------|----------------------|-------------------------|
| dev        | Development/testing  | Push to `develop`       |
| staging    | Pre-production       | PR to `main`            |
| prod       | Production           | Merge to `main`         |

## Error Recovery Flow

```
Deployment Attempt
    ├── Success → Health Check → Done
    └── Failure → Classify Error
                    ├── Logic Error → Auto-fix (deployment-error-fixer) → Retry
                    └── Environmental Error → Backoff → Retry (max 3)
```
