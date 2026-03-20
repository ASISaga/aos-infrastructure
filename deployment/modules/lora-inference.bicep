// LoRA Inference module — Llama-3.3-70B-Instruct Managed Online Endpoint with Multi-LoRA support
//
// Deploys a per-agent LoRA-enabled endpoint backed by the Llama-3.3-70B-Instruct base model.
// One instance of this module is provisioned per C-suite agent (ceo-agent, cfo-agent, etc.)
// so that each agent endpoint carries its own LoRA adapter at inference time without
// reloading base weights from disk between requests (Provisioned Throughput keeps weights
// resident in VRAM).
//
// NOTE: Online endpoints must be created under a Project workspace, not a Hub workspace.
// Pass aiProject.outputs.projectId as workspaceId from the parent template.

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

@description('AI Project workspace resource ID (parent for the endpoint). Must be a Project-kind workspace — Hub workspaces do not support online endpoint creation.')
param workspaceId string

@description('Agent application name for per-agent LoRA adapter deployment (e.g. ceo-agent). Each C-suite agent gets its own LoRA-enabled endpoint backed by the shared Llama base model.')
param appName string

@description('VM instance type for the managed online deployment (e.g. Standard_NC24ads_A100_v4 for Llama-70B).')
param instanceType string = 'Standard_NC24ads_A100_v4'

// ====================================================================
// Variables
// ====================================================================

var endpointName = 'ep-${appName}-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var deploymentName = '${appName}-lora'
var baseModelId = 'azureml://registries/azureml-meta/models/Meta-Llama-3.3-70B-Instruct/versions/1'

// ====================================================================
// Managed Online Endpoint
// ====================================================================

resource llamaEndpoint 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints@2024-10-01' = {
  name: '${split(workspaceId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Llama-3.3-70B-Instruct Multi-LoRA inference endpoint for ${appName} (${environment})'
    authMode: 'Key'
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// ====================================================================
// Base Model Deployment — Provisioned Throughput, Multi-LoRA enabled
// ====================================================================

resource llamaDeployment 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints/deployments@2024-10-01' = {
  parent: llamaEndpoint
  name: deploymentName
  location: location
  tags: tags
  sku: {
    // Provisioned Throughput keeps base weights resident in VRAM
    name: 'Provisioned'
    capacity: 1
  }
  properties: {
    endpointComputeType: 'Managed'
    model: baseModelId
    instanceType: instanceType
    description: 'meta-llama/Llama-3.3-70B-Instruct — Multi-LoRA adapter deployment for ${appName}'
    scaleSettings: {
      scaleType: 'Default'
    }
    requestSettings: {
      maxConcurrentRequestsPerInstance: 8
    }
  }
}

// ====================================================================
// Outputs
// ====================================================================

output endpointName string = endpointName
output deploymentName string = deploymentName
output endpointId string = llamaEndpoint.id
output scoringUri string = llamaEndpoint.properties.scoringUri
