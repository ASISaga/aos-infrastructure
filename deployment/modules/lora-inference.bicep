// LoRA Inference module — Llama-3.3-70B-Instruct Managed Online Endpoint with Multi-LoRA support
//
// Deploys a SINGLE shared endpoint backed by the Llama-3.3-70B-Instruct base model.
// All C-suite agents (ceo-agent, cfo-agent, etc.) share this one endpoint; each agent
// specifies its own LoRA adapter at inference time via the adapter_id field in the
// request body. Base model weights remain resident in VRAM across all agent requests,
// eliminating per-agent endpoint overhead while keeping adapter-level isolation.
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

@description('List of C-suite agent application names that will share this endpoint (e.g. [\'ceo-agent\', \'cfo-agent\']). Used in the deployment description to identify which agents route through this endpoint. All agents differentiate via their LoRA adapter ID at inference time.')
param appNames array

@description('VM instance type for the managed online deployment (e.g. Standard_NC24ads_A100_v4 for Llama-70B).')
param instanceType string = 'Standard_NC24ads_A100_v4'

@description('Base model asset ID from an Azure ML registry. Defaults to Llama-3.3-70B-Instruct version 9 from the azureml-meta registry (verified available in eastus2 for fine-tuning / chat-completion LoRA adapters).')
param baseModelId string = 'azureml://registries/azureml-meta/models/Llama-3.3-70B-Instruct/versions/9'

// ====================================================================
// Variables
// ====================================================================

// Shared endpoint suffix — derived from resource group, project name, and environment.
// No per-agent component is needed because there is only one endpoint for all agents.
// 8 characters provide adequate uniqueness while keeping the full name ≤ 32 chars.
var endpointSuffix = take(uniqueString(resourceGroup().id, projectName, environment), 8)
var endpointName = 'ep-lora-shared-${environment}-${endpointSuffix}'
var deploymentName = 'lora-base-deployment'

// ====================================================================
// Managed Online Endpoint (shared by all C-suite agents)
// ====================================================================

resource llamaEndpoint 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints@2024-10-01' = {
  name: '${split(workspaceId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Llama-3.3-70B-Instruct shared Multi-LoRA inference endpoint for all C-suite agents (${environment})'
    authMode: 'Key'
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// ====================================================================
// Base Model Deployment — single Managed Online Deployment, Multi-LoRA enabled.
// All C-suite agents share this deployment; per-agent adapter selection happens
// at inference time via the adapter_id field in the scoring request body.
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
    description: 'Llama-3.3-70B-Instruct v9 — shared Multi-LoRA base deployment (agents: ${join(appNames, \', \')})'
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
