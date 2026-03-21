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

@description('When true, creates the serverless endpoint resource. Set to false (default) to skip endpoint creation until the model version is confirmed available in the target region. Deploy infrastructure first, then re-deploy with deployEndpoint=true once model availability is verified.')
param deployEndpoint bool = false

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
// Conditional on deployEndpoint=true to allow infrastructure provisioning before the
// specific model version is confirmed available in the target region.
// ====================================================================

resource llamaEndpoint 'Microsoft.MachineLearningServices/workspaces/serverlessEndpoints@2024-10-01' = if (deployEndpoint) {
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
@description('The deployment/endpoint name, or empty string when deployEndpoint=false (no endpoint created yet).')
output deploymentName string = deployEndpoint ? endpointName : ''
// Non-null assertion (!) is safe: the ternary condition mirrors the `if (deployEndpoint)` resource
// guard, so llamaEndpoint is guaranteed to exist when deployEndpoint=true.
output endpointId string = deployEndpoint ? llamaEndpoint!.id : ''
output scoringUri string = deployEndpoint ? llamaEndpoint!.properties.inferenceEndpoint.uri : ''
