// LoRA Inference module — Llama-3.3-70B-Instruct Managed Online Endpoint with Multi-LoRA support
//
// Deploys a per-agent LoRA-enabled endpoint backed by the Llama-3.3-70B-Instruct base model.
// One instance of this module is provisioned per C-suite agent (ceo-agent, cfo-agent, etc.)
// so that each agent endpoint carries its own LoRA adapter at inference time without
// reloading base weights from disk between requests (the single VM replica keeps weights
// resident in VRAM while the endpoint is running).
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

@description('Base model asset ID from an Azure ML registry. Defaults to Llama-3.3-70B-Instruct version 9 from the azureml-meta registry (verified available in eastus2 for fine-tuning / chat-completion LoRA adapters).')
param baseModelId string = 'azureml://registries/azureml-meta/models/Llama-3.3-70B-Instruct/versions/9'

// ====================================================================
// Variables
// ====================================================================

// Azure ML online endpoint names are globally unique per region — a short 6-char suffix derived
// from the resource-group-level uniqueSuffix is insufficient to prevent cross-subscription
// collisions. We recompute a per-agent hash here (including appName) and take 8 characters to
// provide adequate uniqueness while staying within the 32-character Azure ML name limit.
// The projectName is still part of the hash input but is omitted from the visible name to keep
// the full name ≤ 32 chars.
var endpointSuffix = take(uniqueString(resourceGroup().id, projectName, environment, appName), 8)
var endpointName = 'ep-${appName}-${environment}-${endpointSuffix}'
var deploymentName = '${appName}-lora'

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
// Base Model Deployment — Managed Online Deployment, Multi-LoRA enabled
// ====================================================================

resource llamaDeployment 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints/deployments@2024-10-01' = {
  parent: llamaEndpoint
  name: deploymentName
  location: location
  tags: tags
  sku: {
    // Default SKU — standard VM-backed managed online deployment (1 replica)
    name: 'Default'
    capacity: 1
  }
  properties: {
    endpointComputeType: 'Managed'
    model: baseModelId
    instanceType: instanceType
    description: 'Llama-3.3-70B-Instruct v9 — Multi-LoRA adapter deployment for ${appName}'
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
