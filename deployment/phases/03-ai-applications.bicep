// Phase 3 — AI Applications
//
// Deploys the AI inference and orchestration layer:
//   - LoRA Inference endpoint (single shared Llama-3.3-70B serverless endpoint)
//   - Foundry App endpoints (one per C-suite agent — CEO, CFO, CTO, CSO, CMO)
//   - AI Gateway (API Management for rate limiting and JWT validation)
//   - A2A Connections (Agent-to-Agent boardroom orchestration links)
//
// Prerequisites: Phase 1 (foundation) and Phase 2 (ai-services) must be deployed.
// Cross-phase references use `existing` to read aiProject and aiServices resources
// by their deterministic names without requiring ARM deployment outputs.

targetScope = 'resourceGroup'

// ====================================================================
// Parameters
// ====================================================================

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Primary Azure region')
param location string = resourceGroup().location

@description('Azure ML region (must match the region used in Phase 2)')
param locationML string = location

@description('Base project name used in resource naming')
param projectName string = 'aos'

@description('Resource tags applied to every resource')
param tags object = {
  project: 'AgentOperatingSystem'
  environment: environment
  managedBy: 'bicep'
}

@description('List of C-suite agent app names deployed to Foundry Agent Service')
param foundryAppNames array = [
  'ceo-agent'
  'cfo-agent'
  'cto-agent'
  'cso-agent'
  'cmo-agent'
]

@description('When true, creates online deployments for each foundry agent. Set to false to provision only the endpoint shells until LoRA adapters are registered.')
param deployFoundryModels bool = false

@description('When true, creates the shared LoRA inference serverless endpoint. Set to false (default) to skip endpoint creation until the model version is confirmed available in the target region.')
param deployLoraEndpoint bool = false

@description('Enable nested ARM deployment of the provided agentTemplate for each foundry app')
param useAgentNestedDeployment bool = false

@description('ARM template fragment deployed per foundry app to provision Agent Service resources')
param agentTemplate object = {
  '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
  contentVersion: '1.0.0.0'
  resources: []
}

@description('Parameters for the agentTemplate (object mapping parameterName -> { value: ... })')
param agentTemplateParameters object = {}

// ====================================================================
// Variables
// ====================================================================

var suffix = '${projectName}-${environment}'
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environment)

// Deterministic names for Phase-2 resources — mirrors naming in each module.
var aiProjectName = 'ai-project-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var aiServicesAccountName = 'ai-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var modelRegistryName = 'mlreg-${projectName}-${environment}-${take(uniqueSuffix, 6)}'

// Deterministic name for Phase-1 resource.
var foundationAppInsightsName = 'appi-${projectName}-${environment}'

// Compute AI Gateway URL using the same deterministic formula as main-modular.bicep.
var aiGatewayComputedName = 'ai-gw-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var aiGatewayComputedUrl = 'https://${aiGatewayComputedName}.azure-api.net'

// ====================================================================
// Existing Phase-1 and Phase-2 resources (cross-phase references)
// ====================================================================

resource existingAiProject 'Microsoft.MachineLearningServices/workspaces@2024-10-01' existing = {
  name: aiProjectName
}

resource existingAiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiServicesAccountName
}

resource existingAppInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: foundationAppInsightsName
}

// ====================================================================
// Modules
// ====================================================================

// Single shared Llama-3.3-70B-Instruct serverless endpoint for all C-suite agents.
module loraInference '../modules/lora-inference.bicep' = {
  name: 'lora-inference-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    tags: tags
    workspaceId: existingAiProject.id
    deployEndpoint: deployLoraEndpoint
  }
}

// Foundry-hosted C-suite agent endpoints (one per foundryAppNames entry).
module foundryApps '../modules/foundry-app.bicep' = [for (fa, i) in foundryAppNames: {
  name: 'foundry-${fa}-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    tags: tags
    workspaceId: existingAiProject.id
    appName: fa
    modelId: 'azureml://registries/${modelRegistryName}/models/${fa}-lora-adapter/versions/1'
    skuCapacity: 1
    deployModel: deployFoundryModels
    useNestedDeployment: useAgentNestedDeployment
    agentTemplate: agentTemplate
    agentTemplateParameters: agentTemplateParameters
  }
}]

// AI Gateway — API Management for rate limiting and JWT validation.
module aiGateway '../modules/ai-gateway.bicep' = {
  name: 'ai-gateway-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    aiServicesEndpoint: existingAiServices.properties.endpoint
    appInsightsInstrumentationKey: existingAppInsights.properties.InstrumentationKey
  }
}

// A2A Connections — Agent-to-Agent boardroom orchestration links.
// Uses computed AI project name and gateway URL (no hard ARM dependency chain).
module a2aConnections '../modules/a2a-connections.bicep' = {
  name: 'a2a-connections-${suffix}'
  params: {
    environment: environment
    projectName: projectName
    aiProjectName: aiProjectName
    aiGatewayUrl: aiGatewayComputedUrl
  }
}

// ====================================================================
// Outputs
// ====================================================================

output aiGatewayName string = aiGateway.outputs.gatewayName
output aiGatewayUrl string = aiGateway.outputs.gatewayUrl
output loraInferenceEndpointName string = loraInference.outputs.endpointName
output loraInferenceScoringUri string = loraInference.outputs.scoringUri
output a2aConnectionCount int = a2aConnections.outputs.connectionCount
