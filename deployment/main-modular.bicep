// AOS Infrastructure — Modular Bicep Template
//
// Deploys the full Agent Operating System infrastructure:
//   - Log Analytics + Application Insights (monitoring)
//   - Storage Account (function app backing store + deployment packages)
//   - Service Bus namespace + queue (orchestration messaging)
//   - Key Vault (secrets management)
//   - Azure AI Services (Cognitive Services)
//   - Azure AI Foundry Hub (ML Workspace — Hub kind) with AI Services connection
//   - Azure AI Foundry Project (ML Workspace — Project kind)
//   - AI Gateway (API Management) for rate limiting and JWT validation
//   - A2A Connections (Agent-to-Agent) for C-suite boardroom orchestration
//   - One dedicated FC1 Flex Consumption Plan + Function App per AOS module (12 deployed)
//   - One dedicated FC1 Flex Consumption Plan + Function App per MCP server module (4 deployed)
//   - RBAC role assignments (identity-based connections, no secrets in env vars)

targetScope = 'resourceGroup'

// ====================================================================
// Parameters
// ====================================================================

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Primary Azure region for resource deployment')
param location string = resourceGroup().location

@description('Azure region for ML workloads (may differ from primary)')
param locationML string = location

@description('Base project name used in resource naming')
param projectName string = 'aos'

@description('Resource tags applied to every resource')
param tags object = {
  project: 'AgentOperatingSystem'
  environment: environment
  managedBy: 'bicep'
}

@description('GitHub organization or user name that owns the AOS repositories (e.g. ASISaga). Used to construct the OIDC subject for Workload Identity Federation.')
param githubOrg string = 'ASISaga'

@description('Enable AOS governance policy assignments (allowed locations, HTTPS storage, KV soft-delete).')
param enableGovernancePolicies bool = true

@description('List of allowed Azure locations enforced by the Governance policy module.')
param governanceAllowedLocations array = [
  'eastus'
  'eastus2'
  'westus2'
  'westeurope'
  'northeurope'
]

@description('Monthly budget limit in the subscription currency (0 = disabled).')
param monthlyBudgetAmount int = 0

@description('Email addresses notified when the budget crosses an alert threshold.')
param budgetAlertEmails array = []

@description('List of AOS application module names — one dedicated Flex Consumption plan and Function App is created per entry. Each entry must be the GitHub repository name of an actual deployable Azure Function App. Code-only libraries (aos-kernel, aos-intelligence, aos-client-sdk, aos-dispatcher) are NOT included — they are imported by agent-operating-system at runtime. C-suite agents are excluded as they run as Foundry Agent Service endpoints.')
param appNames array = [
  'agent-operating-system'
  'aos-realm-of-agents'
  'business-infinity'
]

@description('List of C-suite agent app names that should be deployed to Foundry rather than as Function Apps')
param foundryAppNames array = [
  'ceo-agent'
  'cfo-agent'
  'cto-agent'
  'cso-agent'
  'cmo-agent'
]

@description('ARM template fragment deployed per foundry app to provision Agent Service resources')
param agentTemplate object = {
  '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
  contentVersion: '1.0.0.0'
  resources: []
}

@description('Parameters for the `agentTemplate` (object mapping parameterName -> { value: ... })')
param agentTemplateParameters object = {}

@description('Enable nested deployment of the provided `agentTemplate` for each foundry app')
param useAgentNestedDeployment bool = false

@description('When true, creates online deployments for each foundry agent (requires the LoRA adapter models to be already registered in the model registry). Set to false to provision only the endpoint shells until adapters are trained. Workflow: 1) deploy with false (default) to create infra; 2) run fine-tuning jobs on the ft-cluster compute; 3) re-deploy with true once models are confirmed in the registry.')
param deployFoundryModels bool = false

@description('VM size for the fine-tuning compute cluster attached to the AI Project. Defaults to Standard_NC6s_v3 (V100 GPU). Choose a size with available quota in the target region.')
param fineTuningVmSize string = 'Standard_NC6s_v3'

@description('List of MCP server modules from the ASISaga/mcp repository and its submodules. Each entry specifies the Azure-safe app name and the actual GitHub repository name (which may contain dots) used for Workload Identity Federation.')
param mcpServerApps array = [
  { appName: 'mcp-erpnext', githubRepo: 'erpnext.asisaga.com' }
  { appName: 'mcp-linkedin', githubRepo: 'linkedin.asisaga.com' }
  { appName: 'mcp-reddit', githubRepo: 'reddit.asisaga.com' }
  { appName: 'mcp-subconscious', githubRepo: 'subconscious.asisaga.com' }
]

@description('Base domain used to construct custom hostnames for standard AOS module apps (e.g. asisaga.com produces aos-dispatcher.asisaga.com). MCP server apps use their githubRepo value as the custom domain directly. Set to empty string to disable custom domain setup for all apps. Custom domain binding requires DNS CNAME records to exist before deployment — leave empty until DNS is configured.')
param baseDomain string = ''

// ====================================================================
// Variables
// ====================================================================

var suffix = '${projectName}-${environment}'
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environment)

// Core AOS orchestration hub — its URL is injected into every module's env vars for peer discovery.
// The hostname follows the same naming formula used in functionapp.bicep:
//   func-{appName}-{environment}-{take(uniqueSuffix,6)}.azurewebsites.net
var coreAppName = 'agent-operating-system'
var coreAppUrl = 'https://func-${coreAppName}-${environment}-${take(uniqueSuffix, 6)}.azurewebsites.net'

// Computed URLs — derived from deterministic naming patterns that match the variables in their
// respective modules. Used by the A2A Connections module; not passed to Function Apps.
var aiGatewayComputedName = 'ai-gw-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var aiGatewayComputedUrl = 'https://${aiGatewayComputedName}.azure-api.net'
var aiProjectComputedName = 'ai-project-${projectName}-${environment}-${take(uniqueSuffix, 6)}'

// ====================================================================
// Modules
// ====================================================================

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

module serviceBus 'modules/servicebus.bicep' = {
  name: 'servicebus-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    tags: tags
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// Azure AI Services (Cognitive Services)
module aiServices 'modules/ai-services.bicep' = {
  name: 'ai-services-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// Azure AI Foundry Hub (ML Workspace — Hub kind)
module aiHub 'modules/ai-hub.bicep' = {
  name: 'ai-hub-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountId: storage.outputs.storageAccountId
    keyVaultId: keyVault.outputs.keyVaultId
    appInsightsId: monitoring.outputs.appInsightsId
    aiServicesAccountId: aiServices.outputs.accountId
    aiServicesAccountName: aiServices.outputs.accountName
  }
}

// Azure AI Foundry Project (ML Workspace — Project kind)
module aiProject 'modules/ai-project.bicep' = {
  name: 'ai-project-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    hubId: aiHub.outputs.hubId
    fineTuningVmSize: fineTuningVmSize
  }
}

// Model Registry — permanent storage for LoRA adapter assets
module modelRegistry 'modules/model-registry.bicep' = {
  name: 'model-registry-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// Single shared Llama-3.3-70B-Instruct Managed Online Endpoint with Multi-LoRA support.
// All C-suite agents share this one endpoint; each agent specifies its own LoRA adapter
// via the adapter_id field in the scoring request body at inference time.
// The endpoint is created under the AI Project workspace (not Hub) as required by Azure ML.
module loraInference 'modules/lora-inference.bicep' = {
  name: 'lora-inference-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    tags: tags
    workspaceId: aiProject.outputs.projectId
  }
}

// Create Foundry-hosted C-suite agent endpoints (one per foundryAppNames entry)
// All agents share the single LoRA inference endpoint; per-agent adapter selection
// happens at inference time via the adapter_id in the scoring request body.
// Endpoints are created under the AI Project workspace (not Hub) as required by Azure ML.
module foundryApps 'modules/foundry-app.bicep' = [for (fa, i) in foundryAppNames: {
  name: 'foundry-${fa}-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    tags: tags
    workspaceId: aiProject.outputs.projectId
    appName: fa
    // LoRA adapter model registered in the Model Registry for this agent
    modelId: 'azureml://registries/${modelRegistry.outputs.registryName}/models/${fa}-lora-adapter/versions/1'
    skuCapacity: 1
    deployModel: deployFoundryModels
    useNestedDeployment: useAgentNestedDeployment
    agentTemplate: agentTemplate
    agentTemplateParameters: agentTemplateParameters
  }
}]

// AI Gateway (API Management)
module aiGateway 'modules/ai-gateway.bicep' = {
  name: 'ai-gateway-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    aiServicesEndpoint: aiServices.outputs.endpoint
    appInsightsInstrumentationKey: monitoring.outputs.instrumentationKey
  }
}

// A2A Connections — Agent-to-Agent connections for C-suite boardroom orchestration
// Creates connections of type Agent2Agent for each specialist (CFO, CTO, CSO, CMO)
// All A2A traffic routes through the AI Gateway / Foundry Private Link substrate.
// Uses computed AI project name and gateway URL (no hard dependency on aiProject/aiGateway modules).
module a2aConnections 'modules/a2a-connections.bicep' = {
  name: 'a2a-connections-${suffix}'
  params: {
    environment: environment
    projectName: projectName
    aiProjectName: aiProjectComputedName
    aiGatewayUrl: aiGatewayComputedUrl
  }
}

// One dedicated FC1 Flex Consumption plan + Function App per AOS module
module functionApps 'modules/functionapp.bicep' = [for appName in appNames: {
  name: 'functionapp-${appName}-${suffix}'
  params: {
    location: location
    environment: environment
    appName: appName
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountName: storage.outputs.storageAccountName
    storageAccountId: storage.outputs.storageAccountId
    appInsightsConnectionString: monitoring.outputs.connectionString
    serviceBusNamespace: serviceBus.outputs.namespaceName
    serviceBusId: serviceBus.outputs.namespaceId
    keyVaultName: keyVault.outputs.keyVaultName
    keyVaultId: keyVault.outputs.keyVaultId
    tableServiceUri: storage.outputs.tableServiceUri
    coreAppUrl: coreAppUrl
    githubOrg: githubOrg
    githubEnvironment: environment
    // Custom domain: <appName>.<baseDomain> (e.g. agent-operating-system.asisaga.com)
    customDomain: !empty(baseDomain) ? '${appName}.${baseDomain}' : ''
  }
}]

// One dedicated FC1 Flex Consumption plan + Function App per MCP server submodule.
// The githubRepo param passes the actual GitHub repository name (which may contain dots)
// for Workload Identity Federation, while appName is an Azure-safe identifier.
module mcpServerFunctionApps 'modules/functionapp.bicep' = [for mcpApp in mcpServerApps: {
  name: 'functionapp-${mcpApp.appName}-${suffix}'
  params: {
    location: location
    environment: environment
    appName: mcpApp.appName
    githubRepo: mcpApp.githubRepo
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountName: storage.outputs.storageAccountName
    storageAccountId: storage.outputs.storageAccountId
    appInsightsConnectionString: monitoring.outputs.connectionString
    serviceBusNamespace: serviceBus.outputs.namespaceName
    serviceBusId: serviceBus.outputs.namespaceId
    keyVaultName: keyVault.outputs.keyVaultName
    keyVaultId: keyVault.outputs.keyVaultId
    tableServiceUri: storage.outputs.tableServiceUri
    coreAppUrl: coreAppUrl
    githubOrg: githubOrg
    githubEnvironment: environment
    // Custom domain: githubRepo IS the full domain for MCP servers (e.g. erpnext.asisaga.com).
    // Conditional on baseDomain being non-empty so that dev/staging environments that set
    // baseDomain='' skip custom domain binding for both standard AOS apps and MCP servers.
    customDomain: !empty(baseDomain) ? mcpApp.githubRepo : ''
  }
}]

// ── Governance: Policy assignments ──────────────────────────────────────────
module governancePolicy 'modules/policy.bicep' = if (enableGovernancePolicies) {
  name: 'governance-policy-${suffix}'
  params: {
    environment: environment
    allowedLocations: governanceAllowedLocations
  }
}

// ── Governance: Cost Management budget ──────────────────────────────────────
module governanceBudget 'modules/budget.bicep' = if (monthlyBudgetAmount > 0) {
  name: 'governance-budget-${suffix}'
  params: {
    environment: environment
    budgetAmount: monthlyBudgetAmount
    contactEmails: budgetAlertEmails
  }
}

// ====================================================================
// Outputs
// ====================================================================

output resourceGroupName string = resourceGroup().name
output functionAppNames array = [for (appName, i) in appNames: functionApps[i].outputs.functionAppName]
// clientId per app — use as the AZURE_CLIENT_ID GitHub Actions secret in each repository's deployment workflow
output functionAppClientIds array = [for (appName, i) in appNames: functionApps[i].outputs.clientId]
// Custom domain per standard AOS app (e.g. aos-dispatcher.asisaga.com)
output functionAppCustomDomains array = [for (appName, i) in appNames: functionApps[i].outputs.customDomain]
// MCP server function app outputs — clientId per MCP server for GitHub Actions OIDC deployment
output mcpServerFunctionAppNames array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.functionAppName]
output mcpServerFunctionAppClientIds array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.clientId]
// Custom domain per MCP server (equals githubRepo, e.g. erpnext.asisaga.com)
output mcpServerCustomDomains array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.customDomain]
output storageAccountName string = storage.outputs.storageAccountName
output serviceBusNamespace string = serviceBus.outputs.namespaceName
output keyVaultName string = keyVault.outputs.keyVaultName
output appInsightsName string = monitoring.outputs.appInsightsName
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
// Azure AI Foundry outputs
output aiServicesAccountName string = aiServices.outputs.accountName
output aiServicesEndpoint string = aiServices.outputs.endpoint
output aiHubName string = aiHub.outputs.hubName
output aiProjectName string = aiProject.outputs.projectName
output aiProjectDiscoveryUrl string = aiProject.outputs.projectDiscoveryUrl
output fineTuningComputeName string = aiProject.outputs.fineTuningComputeName
output aiGatewayName string = aiGateway.outputs.gatewayName
output aiGatewayUrl string = aiGateway.outputs.gatewayUrl
// Multi-LoRA inference outputs — single shared endpoint for all C-suite agents
output modelRegistryName string = modelRegistry.outputs.registryName
output loraInferenceEndpointName string = loraInference.outputs.endpointName
output loraInferenceScoringUri string = loraInference.outputs.scoringUri
// Foundry C-suite endpoints created by foundryApps module
output foundryEndpointNames array = [for (fa, i) in foundryAppNames: foundryApps[i].outputs.endpointName]
output foundryScoringUris array = [for (fa, i) in foundryAppNames: foundryApps[i].outputs.scoringUri]
// Governance outputs
output governancePoliciesEnabled bool = enableGovernancePolicies
output budgetEnabled bool = monthlyBudgetAmount > 0
// A2A Connection outputs
output a2aConnectionNames array = a2aConnections.outputs.connectionNames
output a2aConnectionCount int = a2aConnections.outputs.connectionCount
