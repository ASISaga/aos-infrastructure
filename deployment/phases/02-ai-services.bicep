// Phase 2 — AI Services
//
// Deploys the Azure AI / ML backend resources:
//   - Azure AI Services (Cognitive Services — OpenAI, language, vision)
//   - Azure AI Foundry Hub (ML workspace, Hub kind)
//   - Azure AI Foundry Project (ML workspace, Project kind)
//   - Model Registry (stores LoRA adapter assets for C-suite agents)
//
// Prerequisites: Phase 1 (foundation) must be deployed first.
// Cross-phase references use `existing` to read storage, keyVault, and appInsights
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

@description('Azure ML region (may differ from primary; some ML features only available in select regions)')
param locationML string = location

@description('Base project name used in resource naming')
param projectName string = 'aos'

@description('Resource tags applied to every resource')
param tags object = {
  project: 'AgentOperatingSystem'
  environment: environment
  managedBy: 'bicep'
}

// ====================================================================
// Variables
// ====================================================================

var suffix = '${projectName}-${environment}'
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environment)

// Deterministic names for Phase-1 resources — mirrors the naming in each module.
// These names are computable from first principles; no ARM query required.
var foundationStorageName = 'st${projectName}${environment}${take(uniqueSuffix, 8)}'
var foundationKeyVaultName = 'kv-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var foundationAppInsightsName = 'appi-${projectName}-${environment}'

// ====================================================================
// Existing Phase-1 resources (cross-phase references via `existing`)
// ====================================================================

resource existingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: foundationStorageName
}

resource existingKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: foundationKeyVaultName
}

resource existingAppInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: foundationAppInsightsName
}

// ====================================================================
// Modules
// ====================================================================

module aiServices '../modules/ai-services.bicep' = {
  name: 'ai-services-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

module aiHub '../modules/ai-hub.bicep' = {
  name: 'ai-hub-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountId: existingStorage.id
    keyVaultId: existingKeyVault.id
    appInsightsId: existingAppInsights.id
    aiServicesAccountId: aiServices.outputs.accountId
    aiServicesAccountName: aiServices.outputs.accountName
  }
}

module aiProject '../modules/ai-project.bicep' = {
  name: 'ai-project-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
    hubId: aiHub.outputs.hubId
  }
}

module modelRegistry '../modules/model-registry.bicep' = {
  name: 'model-registry-${suffix}'
  params: {
    location: locationML
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// ====================================================================
// Outputs
// ====================================================================

output aiServicesAccountName string = aiServices.outputs.accountName
output aiServicesAccountId string = aiServices.outputs.accountId
output aiServicesEndpoint string = aiServices.outputs.endpoint
output aiHubName string = aiHub.outputs.hubName
output aiHubId string = aiHub.outputs.hubId
output aiProjectName string = aiProject.outputs.projectName
output aiProjectId string = aiProject.outputs.projectId
output aiProjectDiscoveryUrl string = aiProject.outputs.projectDiscoveryUrl
output modelRegistryName string = modelRegistry.outputs.registryName
