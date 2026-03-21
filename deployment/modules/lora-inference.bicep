// LoRA Inference module — Llama-3.3-70B-Instruct Serverless Endpoint
//
// Deploys a SINGLE shared serverless endpoint backed by the Llama-3.3-70B-Instruct base model.
// Azure manages the underlying GPU compute — no subscription quota is required.
// All C-suite agents share this one endpoint; per-agent LoRA adapter selection happens
// at inference time via the adapter_id field in the scoring request body.
//
// NOTE: Serverless endpoints must be created under a Project workspace, not a Hub workspace.
// Pass aiProject.outputs.projectId as workspaceId from the parent template.

@description('Azure region')
param location string

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Base project name')
param projectName string

@description('Resource tags')
param tags object

@description('AI Project workspace resource ID (parent for the endpoint). Must be a Project-kind workspace — Hub workspaces do not support online endpoint creation.')
param workspaceId string

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

// ====================================================================
// Serverless Endpoint (shared by all C-suite agents)
// Azure manages the underlying GPU compute — no subscription quota required.
// This is the default inference experience selected by ml.azure.com for catalog models.
// ====================================================================

resource llamaEndpoint 'Microsoft.MachineLearningServices/workspaces/serverlessEndpoints@2024-10-01' = {
  name: '${split(workspaceId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
  }
  properties: {
    modelSettings: {
      modelId: baseModelId
    }
    authMode: 'Key'
    contentSafety: {
      contentSafetyStatus: 'Disabled'
    }
  }
}

// ====================================================================
// Outputs
// ====================================================================

output endpointName string = endpointName
output deploymentName string = endpointName
output endpointId string = llamaEndpoint.id
output scoringUri string = llamaEndpoint.properties.inferenceEndpoint.uri
