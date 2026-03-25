// Function App module — FC1 Flex Consumption plan + Function App + User-Assigned Managed Identity
//                       + GitHub OIDC + Custom Domain + App Service Managed Certificate + Scoped RBAC
// One-app-per-plan pattern required by Flex Consumption.
// Each app has its own User-Assigned Identity used both as the runtime identity and as the GitHub Actions
// deployment agent. Workload Identity Federation (OIDC) restricts each identity to exactly one
// GitHub repository + environment, creating a hard security boundary between the AOS modules.
//
// Custom domain setup (three-phase, requires prior DNS configuration):
//   Phase 1 — hostnameBinding:      bind customDomain without TLS (Disabled)
//   Phase 2 — managedCertificate:   issue free App Service Managed Certificate for the domain
//   Phase 3 — sslBinding (module):  re-bind with SNI TLS linked to the certificate thumbprint
//
// DNS prerequisite: a CNAME record  `<customDomain>` → `<functionAppName>.azurewebsites.net`
// must exist before deployment.  Skip by leaving customDomain empty.

@description('Azure region')
param location string

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Application module name (used in resource naming)')
param appName string

@description('Unique suffix for globally unique names')
param uniqueSuffix string

@description('Resource tags')
param tags object

// Dependencies from other modules
@description('Storage account name for function app backing store and deployment packages')
param storageAccountName string

@description('Storage account resource ID')
param storageAccountId string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Service Bus namespace name')
param serviceBusNamespace string

@description('Service Bus namespace resource ID')
param serviceBusId string

@description('Key Vault name')
param keyVaultName string

@description('Key Vault resource ID')
param keyVaultId string

@description('Table service URI for the shared storage account (for AOSStateStore connection)')
param tableServiceUri string

@description('URL of the core AOS Dispatcher (aos-dispatcher) injected into all modules for peer discovery.')
param coreAppUrl string

@description('GitHub organization or user name that owns the AOS repositories (e.g. ASISaga)')
param githubOrg string

@description('GitHub Actions environment name used as the OIDC subject bound to this app. Defaults to the deployment environment value.')
param githubEnvironment string = environment

@description('GitHub repository name for the OIDC Workload Identity Federation subject. Defaults to appName when the repo name matches the app name. Override when the GitHub repo name differs from appName (e.g. MCP servers whose repo names contain dots).')
param githubRepo string = appName

@description('Optional additional GitHub repository that is also permitted to deploy to this Function App via OIDC Workload Identity Federation. Used when a monorepo (e.g. ASISaga/mcp) owns the implementation for an app whose primary OIDC subject is tied to a domain-named repo. Leave empty to allow only the primary githubRepo.')
param additionalGithubRepo string = ''

@description('Custom domain hostname to bind to this Function App and secure with a free App Service Managed Certificate (e.g. erpnext.asisaga.com or aos-dispatcher.asisaga.com). Requires a DNS CNAME record pointing this domain to the app\'s default azurewebsites.net hostname before deployment — deployment will fail at the hostname binding step if the CNAME is absent. Leave empty to skip custom domain setup entirely.')
param customDomain string = ''

// ====================================================================
// Variables
// ====================================================================

// One dedicated plan per app (required by Flex Consumption)
var planName = 'plan-${appName}-${environment}'
var functionAppName = 'func-${appName}-${environment}-${take(uniqueSuffix, 6)}'
var identityName = 'id-${appName}-${environment}'
// Blob container holding the deployment package for this app
var deploymentContainerName = 'deploy-${appName}'

// RBAC role definition IDs
var storageBlobDataOwnerRole = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageBlobDataContributorRole = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageTableDataContributorRole = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var websiteContributorRole = 'de139f84-1756-47ae-9be6-808fbbe84772'
var keyVaultSecretsUserRole = '4633458b-17de-408a-b874-0445c86b69e6'
var serviceBusDataSenderRole = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var serviceBusDataReceiverRole = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'

// ====================================================================
// Resources
// ====================================================================

// User-Assigned Managed Identity — one per app, acts as both the runtime identity and the
// GitHub Actions deployment agent for the corresponding AOS repository.
resource userAssignedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// Workload Identity Federation — binds the identity strictly to one GitHub repository + environment.
// Only a GitHub Actions workflow running in that exact repo/environment can exchange an OIDC token
// for an Azure credential, preventing cross-module deployments.
// NOTE: The GitHub OIDC subject for environment-based deployments is always
//   repo:{org}/{repo}:environment:{env}
// The 'ref' sub-claim is not part of the subject when an environment is set; the environment
// constraint itself is the security boundary (GitHub docs: "Configuring OpenID Connect in Azure").
resource federatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: userAssignedIdentity
  name: 'github-${appName}-${environment}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrg}/${githubRepo}:environment:${githubEnvironment}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

// Optional additional Workload Identity Federation — allows a second GitHub repository (e.g. a monorepo)
// to also exchange OIDC tokens for Azure credentials for this app's managed identity.
// The identity and RBAC scope remain the same; only the allowed OIDC issuer subject is broadened.
// NOTE: dependsOn federatedCredential is required to serialise writes to the same managed identity.
//   Azure does not support concurrent FederatedIdentityCredentials writes for a single identity
//   (error: ConcurrentFederatedIdentityCredentialsWritesForSingleManagedIdentity), so the
//   additional credential must wait for the primary one to complete before being created.
resource additionalFederatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = if (!empty(additionalGithubRepo)) {
  parent: userAssignedIdentity
  name: 'github-${appName}-${environment}-${additionalGithubRepo}'
  dependsOn: [federatedCredential]
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrg}/${additionalGithubRepo}:environment:${githubEnvironment}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

// Dedicated Flex Consumption plan — one per app
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true // Linux
  }
}

// Reference existing storage account and its blob service to create per-app deployment container
resource existingStorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: existingStorageAccount
  name: 'default'
}

// References to shared resources used for RBAC scoping
resource existingKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// The Service Bus namespace name is <projectName>-sb-<environment>; derive it from the namespace FQDN
// parameter by using the namespace name directly.
resource existingServiceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespace
}

// Per-app blob container for deployment packages (Managed Identity access)
resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    // Flex Consumption configuration block replaces legacy siteConfig scaling
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}/${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: userAssignedIdentity.id
          }
        }
      }
      scaleAndConcurrency: {
        // Target-based scaling: scale out to match Service Bus queue depth
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
        triggers: {
          http: {
            perInstanceConcurrency: 16
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      // Identity-based connections only — no connection strings or secrets
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storageAccountName }
        { name: 'AzureWebJobsStorage__blobServiceUri', value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}' }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: userAssignedIdentity.properties.clientId }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'ServiceBusConnection__fullyQualifiedNamespace', value: '${serviceBusNamespace}.servicebus.windows.net' }
        { name: 'ServiceBusConnection__credential', value: 'managedidentity' }
        { name: 'ServiceBusConnection__clientId', value: userAssignedIdentity.properties.clientId }
        // AOSStateStore — identity-based Table Storage connection for cross-module shared state
        { name: 'AosStateStore__accountName', value: storageAccountName }
        { name: 'AosStateStore__tableServiceUri', value: tableServiceUri }
        { name: 'AosStateStore__credential', value: 'managedidentity' }
        { name: 'AosStateStore__clientId', value: userAssignedIdentity.properties.clientId }
        { name: 'KEY_VAULT_URI', value: 'https://${keyVaultName}.${az.environment().suffixes.keyvaultDns}/' }
        { name: 'ENVIRONMENT', value: environment }
        { name: 'AZURE_CLIENT_ID', value: userAssignedIdentity.properties.clientId }
        // Peer discovery — URL of the core AOS orchestration hub
        { name: 'AOS_FUNCTION_APP_URL', value: coreAppUrl }
        // A2A connections — default connection ID for agent-to-agent communication
        { name: 'A2A_CONNECTION_ID_DEFAULT', value: 'a2a-connection-${appName}' }
        // Custom domain — the public hostname bound to this Function App (empty if not configured)
        { name: 'CUSTOM_DOMAIN', value: customDomain }
      ]
    }
  }
  dependsOn: [deploymentContainer]
}

// RBAC — Storage Blob Data Owner on the storage account (runtime: AzureWebJobsStorage identity-based connection)
resource storageBlobOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, userAssignedIdentity.id, storageBlobDataOwnerRole)
  scope: existingStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Storage Table Data Contributor on the storage account (AOSStateStore cross-module shared state)
resource storageTableContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, userAssignedIdentity.id, storageTableDataContributorRole)
  scope: existingStorageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Storage Blob Data Contributor on the per-app deployment container (deployment package upload/read)
// Scoped to the individual container — no cross-app storage access.
resource deploymentContainerContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deploymentContainer.id, userAssignedIdentity.id, storageBlobDataContributorRole)
  scope: deploymentContainer
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Website Contributor on this Function App only (GitHub Actions deployment)
// Scoped to the specific Function App resource — a credential for one repo cannot touch another app.
resource websiteContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionApp.id, userAssignedIdentity.id, websiteContributorRole)
  scope: functionApp
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', websiteContributorRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Key Vault Secrets User (scoped to this Key Vault only)
resource keyVaultSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, userAssignedIdentity.id, keyVaultSecretsUserRole)
  scope: existingKeyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Service Bus Data Sender (scoped to this Service Bus namespace only)
resource serviceBusSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusId, userAssignedIdentity.id, serviceBusDataSenderRole)
  scope: existingServiceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataSenderRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC — Service Bus Data Receiver (scoped to this Service Bus namespace only; needed for trigger-based scaling)
resource serviceBusReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusId, userAssignedIdentity.id, serviceBusDataReceiverRole)
  scope: existingServiceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataReceiverRole)
    principalId: userAssignedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ====================================================================
// Custom Domain, App Service Managed Certificate and HTTPS Binding
// ====================================================================
// DNS prerequisite: CNAME  <customDomain>  →  <functionAppName>.azurewebsites.net
// must exist before deployment.  All three resources are conditional on customDomain being set.

// Phase 1 — Bind the custom hostname without TLS so Azure can verify domain ownership.
resource hostnameBinding 'Microsoft.Web/sites/hostNameBindings@2023-12-01' = if (!empty(customDomain)) {
  parent: functionApp
  name: customDomain
  properties: {
    sslState: 'Disabled'
  }
}

// Phase 2 — Issue a free App Service Managed Certificate for the custom domain.
// Azure validates ownership via the CNAME record created in the DNS prerequisite step.
resource managedCertificate 'Microsoft.Web/certificates@2023-12-01' = if (!empty(customDomain)) {
  name: 'cert-${appName}-${environment}'
  location: location
  tags: tags
  properties: {
    serverFarmId: appServicePlan.id
    canonicalName: customDomain
  }
  dependsOn: [hostnameBinding]
}

// Phase 3 — Re-bind the hostname with SNI TLS linked to the managed certificate thumbprint.
// A sub-module is used to avoid a duplicate resource name in this Bicep scope.
module sslBinding 'functionapp-ssl.bicep' = if (!empty(customDomain)) {
  name: 'ssl-${appName}-${environment}'
  params: {
    functionAppName: functionApp.name
    customDomain: customDomain
    thumbprint: managedCertificate!.properties.thumbprint
  }
}

// ====================================================================
// Outputs
// ====================================================================

output functionAppName string = functionApp.name
output defaultHostName string = functionApp.properties.defaultHostName
output principalId string = userAssignedIdentity.properties.principalId
// clientId is used as the AZURE_CLIENT_ID GitHub Actions secret for this repository's deployment workflow
output clientId string = userAssignedIdentity.properties.clientId
output customDomain string = customDomain
output customDomainUrl string = !empty(customDomain) ? 'https://${customDomain}' : ''
// GitHub repository URL — computed from parameters (source control via the sourcecontrols ARM resource
// is not supported for Flex Consumption plans; code is deployed via blob storage).
output sourceControlRepoUrl string = 'https://github.com/${githubOrg}/${githubRepo}'
