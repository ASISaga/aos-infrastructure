// Phase 1 — Foundation Infrastructure
//
// Deploys the core platform resources that all other phases depend on:
//   - Log Analytics workspace + Application Insights (monitoring)
//   - Storage Account (function-app backing store & deployment packages)
//   - Service Bus namespace + orchestration queue
//   - Key Vault (secrets management)
//
// No dependencies on other phases — safe to deploy first in every environment.

targetScope = 'resourceGroup'

// ====================================================================
// Parameters
// ====================================================================

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Primary Azure region')
param location string = resourceGroup().location

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

// ====================================================================
// Modules
// ====================================================================

module monitoring '../modules/monitoring.bicep' = {
  name: 'monitoring-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    tags: tags
  }
}

module storage '../modules/storage.bicep' = {
  name: 'storage-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

module serviceBus '../modules/servicebus.bicep' = {
  name: 'servicebus-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    tags: tags
  }
}

module keyVault '../modules/keyvault.bicep' = {
  name: 'keyvault-${suffix}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// ====================================================================
// Outputs
// ====================================================================

output storageAccountName string = storage.outputs.storageAccountName
output storageAccountId string = storage.outputs.storageAccountId
output tableServiceUri string = storage.outputs.tableServiceUri
output serviceBusNamespaceName string = serviceBus.outputs.namespaceName
output serviceBusId string = serviceBus.outputs.namespaceId
output keyVaultName string = keyVault.outputs.keyVaultName
output keyVaultId string = keyVault.outputs.keyVaultId
output appInsightsName string = monitoring.outputs.appInsightsName
output appInsightsId string = monitoring.outputs.appInsightsId
output appInsightsConnectionString string = monitoring.outputs.connectionString
output instrumentationKey string = monitoring.outputs.instrumentationKey
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
