// AI Project module — Azure AI Foundry Project (ML Workspace, Project kind)
// A child workspace of the AI Foundry Hub that inherits its connections and governance
// settings, providing an isolated project workspace for model experimentation and deployment.

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
@description('AI Foundry Hub resource ID (parent workspace)')
param hubId string

@description('VM size for the fine-tuning compute cluster. Choose a GPU VM with available quota; defaults to Standard_NC6s_v3 (V100, 16 GB VRAM) which suits QLoRA fine-tuning of 7B–13B models and is widely available. Latency-tolerant workloads can use spot pricing on any instance size.')
param fineTuningVmSize string = 'Standard_NC6s_v3'

// ====================================================================
// Variables
// ====================================================================

var aiProjectName = 'ai-project-${projectName}-${environment}-${take(uniqueSuffix, 6)}'

// ====================================================================
// Resources
// ====================================================================

resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: aiProjectName
  location: location
  kind: 'Project'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Foundry Project (${environment})'
    description: 'Azure AI Foundry Project for ${projectName} — ${environment}'
    hubResourceId: hubId
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// ====================================================================
// RBAC — Cognitive Services User role for the Project Managed Identity
// ====================================================================

// Allows the Project's managed identity to call the AI Services inference API
// and to fetch LoRA adapters from the Model Registry and inject them at runtime.
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource cogServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, aiProjectName, cognitiveServicesUserRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: aiProject.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Fine-tuning compute cluster — spot/low-priority VMs for lowest cost LoRA adapter training.
// Latency-tolerant: Azure may evict nodes at any time (LowPriority), reducing cost by ~60-80%.
// Min node count of 0 means no cost when idle; scales up only when a training job is submitted.
// Must be attached to a Project workspace (not Hub); Hub workspaces do not support AmlCompute.
resource fineTuningCompute 'Microsoft.MachineLearningServices/workspaces/computes@2024-10-01' = {
  parent: aiProject
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

output projectName string = aiProject.name
output projectId string = aiProject.id
output projectPrincipalId string = aiProject.identity.principalId
output projectDiscoveryUrl string = aiProject.properties.discoveryUrl
output fineTuningComputeName string = fineTuningCompute.name
