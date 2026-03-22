// Phase 4 — Function Apps
//
// Deploys the Azure Function Apps that host AOS platform services and MCP servers:
//   - One FC1 Flex Consumption plan + Function App per AOS module (appNames array)
//   - One FC1 Flex Consumption plan + Function App per MCP server (mcpServerApps array)
//   - Each app includes: User-Assigned Managed Identity, GitHub OIDC, RBAC, and
//     optional custom domain + App Service Managed Certificate
//
// Prerequisites: Phase 1 (foundation) must be deployed first.
// Cross-phase references use `existing` to read storage, serviceBus, keyVault, and
// appInsights resources by their deterministic names.

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

@description('List of AOS application module names — one Function App per entry')
param appNames array = [
  'agent-operating-system'
  'aos-realm-of-agents'
  'business-infinity'
]

@description('List of MCP server modules — each entry has appName and githubRepo fields')
param mcpServerApps array = [
  { appName: 'mcp-erpnext', githubRepo: 'erpnext.asisaga.com' }
  { appName: 'mcp-linkedin', githubRepo: 'linkedin.asisaga.com' }
  { appName: 'mcp-reddit', githubRepo: 'reddit.asisaga.com' }
  { appName: 'mcp-subconscious', githubRepo: 'subconscious.asisaga.com' }
]

@description('GitHub organization or user name that owns the AOS repositories')
param githubOrg string = 'ASISaga'

@description('Base domain for custom hostnames (e.g. asisaga.com). Leave empty to skip custom domain binding.')
param baseDomain string = ''

// ====================================================================
// Variables
// ====================================================================

var suffix = '${projectName}-${environment}'
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environment)

// Deterministic names for Phase-1 resources — mirrors naming in each module.
var foundationStorageName = 'st${projectName}${environment}${take(uniqueSuffix, 8)}'
var foundationServiceBusName = 'sb-${projectName}-${environment}'
var foundationKeyVaultName = 'kv-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var foundationAppInsightsName = 'appi-${projectName}-${environment}'

// Core AOS orchestration hub URL — injected into every Function App for peer discovery.
var coreAppName = 'agent-operating-system'
var coreAppUrl = 'https://func-${coreAppName}-${environment}-${take(uniqueSuffix, 6)}.azurewebsites.net'

// ====================================================================
// Existing Phase-1 resources (cross-phase references via `existing`)
// ====================================================================

resource existingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: foundationStorageName
}

resource existingServiceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: foundationServiceBusName
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

// One dedicated FC1 Flex Consumption plan + Function App per AOS module.
module functionApps '../modules/functionapp.bicep' = [for appName in appNames: {
  name: 'functionapp-${appName}-${suffix}'
  params: {
    location: location
    environment: environment
    appName: appName
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountName: existingStorage.name
    storageAccountId: existingStorage.id
    appInsightsConnectionString: existingAppInsights.properties.ConnectionString
    serviceBusNamespace: existingServiceBus.name
    serviceBusId: existingServiceBus.id
    keyVaultName: existingKeyVault.name
    keyVaultId: existingKeyVault.id
    tableServiceUri: 'https://${existingStorage.name}.table.${az.environment().suffixes.storage}'
    coreAppUrl: coreAppUrl
    githubOrg: githubOrg
    githubEnvironment: environment
    customDomain: !empty(baseDomain) ? '${appName}.${baseDomain}' : ''
  }
}]

// One dedicated FC1 Flex Consumption plan + Function App per MCP server submodule.
// additionalGithubRepo 'mcp' allows the ASISaga/mcp monorepo to also deploy to each server's
// Function App using its own OIDC token, in addition to the primary domain repo.
module mcpServerFunctionApps '../modules/functionapp.bicep' = [for mcpApp in mcpServerApps: {
  name: 'functionapp-${mcpApp.appName}-${suffix}'
  params: {
    location: location
    environment: environment
    appName: mcpApp.appName
    githubRepo: mcpApp.githubRepo
    additionalGithubRepo: 'mcp'
    uniqueSuffix: uniqueSuffix
    tags: tags
    storageAccountName: existingStorage.name
    storageAccountId: existingStorage.id
    appInsightsConnectionString: existingAppInsights.properties.ConnectionString
    serviceBusNamespace: existingServiceBus.name
    serviceBusId: existingServiceBus.id
    keyVaultName: existingKeyVault.name
    keyVaultId: existingKeyVault.id
    tableServiceUri: 'https://${existingStorage.name}.table.${az.environment().suffixes.storage}'
    coreAppUrl: coreAppUrl
    githubOrg: githubOrg
    githubEnvironment: environment
    customDomain: !empty(baseDomain) ? mcpApp.githubRepo : ''
  }
}]

// ====================================================================
// Outputs
// ====================================================================

output functionAppNames array = [for (appName, i) in appNames: functionApps[i].outputs.functionAppName]
output functionAppClientIds array = [for (appName, i) in appNames: functionApps[i].outputs.clientId]
output functionAppCustomDomains array = [for (appName, i) in appNames: functionApps[i].outputs.customDomain]
output mcpServerFunctionAppNames array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.functionAppName]
output mcpServerFunctionAppClientIds array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.clientId]
output mcpServerCustomDomains array = [for (mcpApp, i) in mcpServerApps: mcpServerFunctionApps[i].outputs.customDomain]
