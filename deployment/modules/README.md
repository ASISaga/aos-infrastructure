# Bicep Modules

Reusable Azure infrastructure modules:

- `monitoring.bicep` — Log Analytics + Application Insights
- `storage.bicep` — Storage Account
- `servicebus.bicep` — Service Bus namespace + queue
- `keyvault.bicep` — Key Vault (RBAC, soft-delete, purge protection)
- `ai-services.bicep` — Azure AI Services (Cognitive Services)
- `ai-hub.bicep` — Azure AI Foundry Hub (ML Workspace — Hub kind)
- `ai-project.bicep` — Azure AI Foundry Project (ML Workspace — Project kind)
- `model-registry.bicep` — ML Registry for LoRA adapter assets (one shared registry)
- `lora-inference.bicep` — Llama-3.3-70B-Instruct Managed Online Endpoint with Multi-LoRA (one per C-suite agent)
- `foundry-app.bicep` — Foundry Agent Service endpoint connected to its LoRA endpoint (one per C-suite agent)
- `ai-gateway.bicep` — API Management (rate limiting + JWT validation)
- `a2a-connections.bicep` — Agent-to-Agent connections for C-suite boardroom orchestration
- `functionapp.bicep` — App Service Plan + Function App + managed identity + RBAC (one per AOS module or MCP server)
- `functionapp-ssl.bicep` — SNI TLS re-binding sub-module (Phase 3 of custom domain binding)
- `policy.bicep` — Azure Policy assignments (Governance pillar)
- `budget.bicep` — Cost Management budget (Governance pillar)
