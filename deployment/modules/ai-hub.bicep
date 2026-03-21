// AI Hub module — Azure AI Foundry Hub (ML Workspace, Hub kind) with AI Services connection
// Central governance hub that links storage, Key Vault, App Insights, and AI Services into a
// unified workspace for AI project development and deployment.

@description('Azure region')
param location string

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Base project name')
param projectName string

@description('Unique suffix for globally unique names')
param uniqueSuffix string

@description('Resource tags')
param tags object

// Dependencies from other modules
@description('Storage account resource ID')
param storageAccountId string

@description('Key Vault resource ID')
param keyVaultId string

@description('Application Insights resource ID')
param appInsightsId string

@description('AI Services account resource ID for the hub connection')
param aiServicesAccountId string

@description('AI Services account name for the hub connection')
param aiServicesAccountName string

@description('VM size for the fine-tuning compute cluster. Choose a GPU VM with available quota; defaults to Standard_NC6s_v3 (V100, 16 GB VRAM) which suits QLoRA fine-tuning of 7B–13B models and is widely available. Latency-tolerant workloads can use spot pricing on any instance size.')
param fineTuningVmSize string = 'Standard_NC6s_v3'

// ====================================================================
// Variables
// ====================================================================

var hubName = 'ai-hub-${projectName}-${environment}-${take(uniqueSuffix, 6)}'

// ====================================================================
// Resources
// ====================================================================

resource aiHub 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: hubName
  location: location
  kind: 'Hub'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Foundry Hub (${environment})'
    description: 'Azure AI Foundry Hub for ${projectName} — ${environment}'
    storageAccount: storageAccountId
    keyVault: keyVaultId
    applicationInsights: appInsightsId
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// Connection to AI Services — enables AI Foundry projects to use OpenAI and other cognitive models
resource aiServicesConnection 'Microsoft.MachineLearningServices/workspaces/connections@2024-10-01' = {
  parent: aiHub
  name: 'aoai-${aiServicesAccountName}'
  properties: {
    category: 'AzureOpenAI'
    authType: 'AAD'
    isSharedToAll: true
    target: 'https://${aiServicesAccountName}.cognitiveservices.azure.com'
    metadata: {
      ApiType: 'Azure'
      ResourceId: aiServicesAccountId
    }
  }
}

// Fine-tuning compute cluster — spot/low-priority VMs for lowest cost LoRA adapter training.
// Latency-tolerant: Azure may evict nodes at any time (LowPriority), reducing cost by ~60-80%.
// Min node count of 0 means no cost when idle; scales up only when a training job is submitted.
resource fineTuningCompute 'Microsoft.MachineLearningServices/workspaces/computes@2024-10-01' = {
  parent: aiHub
  name: 'ft-cluster-${environment}'
  location: location
  tags: tags
  properties: {
    computeType: 'AmlCompute'
    computeLocation: location
    description: 'Fine-tuning compute cluster — spot pricing (LowPriority) for lowest-cost LoRA adapter training'
    properties: {
      vmSize: fineTuningVmSize
      vmPriority: 'LowPriority'
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: 4
        nodeIdleTimeBeforeScaleDown: 'PT120S'
      }
      enableNodePublicIp: false
      remoteLoginPortPublicAccess: 'Disabled'
    }
  }
}

// ====================================================================
// Outputs
// ====================================================================

output hubName string = aiHub.name
output hubId string = aiHub.id
output hubPrincipalId string = aiHub.identity.principalId
output fineTuningComputeName string = fineTuningCompute.name
