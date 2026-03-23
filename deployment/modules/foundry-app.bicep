// Foundry App module — provisions a serverless endpoint for an agent
// Uses azureml://registries/azureml-meta/models/Llama-3.3-70B-Instruct/versions/9 which supports
// Serverless APIs and fine-tuning. Azure manages the underlying GPU compute — no subscription
// quota is required.

@description('Azure region for ML workloads')
param location string

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('When true, deploys the provided ARM `agentTemplate` as a nested deployment for Agent Service resources')
param useNestedDeployment bool = false

@description('ARM template fragment for Agent Service / Foundry agents. When empty, a no-op template is used.')
param agentTemplate object = {
  '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
  contentVersion: '1.0.0.0'
  resources: []
}

@description('Parameters for the provided `agentTemplate` (object of parameterName -> { value: ... })')
param agentTemplateParameters object = {}

@description('Human-friendly project name (workspace name)')
param projectName string

@description('Resource tags')
param tags object = {}

@description('AI Project workspace resource ID (parent for the endpoint). Must be a Project-kind workspace — Hub workspaces do not support serverless endpoint creation.')
param workspaceId string

@description('Application logical name (used to derive endpoint name)')
param appName string

@description('Base model asset ID from an Azure ML registry. Defaults to Llama-3.3-70B-Instruct version 9 from the azureml-meta registry — supports Serverless APIs and fine-tuning.')
param modelId string = 'azureml://registries/azureml-meta/models/Llama-3.3-70B-Instruct/versions/9'

@description('When true, creates the serverless endpoint resource. Set to false to create only the endpoint name reservation until the model version is confirmed available in the target region.')
param deployEndpoint bool = false

// ====================================================================
// Variables
// ====================================================================

// Azure ML serverless endpoint names are globally unique per region — a short 6-char suffix
// derived from the resource-group-level uniqueSuffix is insufficient to prevent cross-subscription
// collisions. We recompute a per-agent hash here (including appName) and take 8 characters to
// provide adequate uniqueness while staying within the 32-character Azure ML name limit.
// The projectName is still part of the hash input but is omitted from the visible name to keep
// the full name ≤ 32 chars.
var endpointSuffix = take(uniqueString(resourceGroup().id, projectName, environment, appName), 8)
var endpointName = 'ep-${appName}-${environment}-${endpointSuffix}'

// ====================================================================
// Serverless Endpoint
// Azure manages the underlying GPU compute — no subscription quota required.
// Conditional on deployEndpoint=true to allow infrastructure provisioning before the
// specific model version is confirmed available in the target region.
// ====================================================================

resource agentEndpoint 'Microsoft.MachineLearningServices/workspaces/serverlessEndpoints@2024-10-01' = if (deployEndpoint) {
  name: '${split(workspaceId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
  }
  properties: {
    modelSettings: {
      modelId: modelId
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
@description('The endpoint resource name, or empty string when deployEndpoint=false (no endpoint created yet).')
output deploymentName string = deployEndpoint ? endpointName : ''
// Non-null assertion (!) is safe: the ternary condition mirrors the `if (deployEndpoint)` resource
// guard, so agentEndpoint is guaranteed to exist when deployEndpoint=true.
output endpointId string = deployEndpoint ? agentEndpoint!.id : ''
output scoringUri string = deployEndpoint ? agentEndpoint!.properties.inferenceEndpoint.uri : ''

// Nested deployment for Agent Service resources (user-supplied template)
#disable-next-line no-deployments-resources
resource agentTemplateDeployment 'Microsoft.Resources/deployments@2021-04-01' = if (useNestedDeployment) {
  name: '${appName}-agent-template'
  properties: {
    mode: 'Incremental'
    template: agentTemplate
    parameters: agentTemplateParameters
  }
}

output agentTemplateDeploymentName string = useNestedDeployment ? '${appName}-agent-template' : ''
output agentTemplateDeploymentOutputs object = useNestedDeployment ? (agentTemplateDeployment!.properties.outputs ?? {}) : {}
