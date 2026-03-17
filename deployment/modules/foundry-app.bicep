// Foundry App module — provisions a managed online endpoint + deployment for an agent
// Reuses patterns from lora-inference.bicep; expects a model registered in the Model Registry

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

@description('Unique suffix used to create globally-unique names')
param uniqueSuffix string

@description('Resource tags')
param tags object = {}

@description('AI Hub workspace resource ID (parent for the endpoint)')
param hubId string

@description('AI Services account resource ID (compute backbone)')
param aiServicesAccountId string

@description('Application logical name (used to derive endpoint name)')
param appName string

@description('Name of the LoRA inference endpoint provisioned for this agent (used to connect the Foundry Agent Service to its dedicated LoRA adapter deployment)')
param loraInferenceEndpointName string

@description('Model identifier (azureml://registries/... or other registry id)')
param modelId string

@description('Provisioned SKU capacity (1 = single replica for Provisioned sku)')
param skuCapacity int = 1

// ====================================================================
// Variables
// ====================================================================

var endpointName = 'ep-${appName}-${projectName}-${environment}-${take(uniqueSuffix, 6)}'
var deploymentName = '${appName}-deployment'

// ====================================================================
// Managed Online Endpoint
// ====================================================================

resource endpoint 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints@2024-10-01' = {
  name: '${split(hubId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
  kind: 'Managed'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Foundry endpoint for ${appName} (${environment})'
    authMode: 'Key'
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

// ====================================================================
// Deployment — uses a model reference (Model Registry) for predictable infra
// ====================================================================

resource agentDeployment 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints/deployments@2024-10-01' = {
  name: '${split(hubId, '/')[8]}/${endpointName}/${deploymentName}'
  location: location
  tags: tags
  sku: {
    name: 'Provisioned'
    capacity: skuCapacity
  }
  properties: {
    endpointComputeType: 'Managed'
    model: modelId
    description: 'Agent deployment for ${appName}'
    scaleSettings: {
      scaleType: 'Default'
    }
    requestSettings: {
      maxConcurrentRequestsPerInstance: 4
    }
    properties: {
      aiServicesAccountId: aiServicesAccountId
      loraInferenceEndpointName: loraInferenceEndpointName
    }
  }
  dependsOn: [
    endpoint
  ]
}

// ====================================================================
// Outputs
// ====================================================================

output endpointName string = endpointName
output deploymentName string = deploymentName
output scoringUri string = endpoint.properties.scoringUri
output endpointId string = endpoint.id

// Nested deployment for Agent Service resources (user-supplied template)
resource agentTemplateDeployment 'Microsoft.Resources/deployments@2021-04-01' = if (useNestedDeployment) {
  name: '${appName}-agent-template'
  properties: {
    mode: 'Incremental'
    template: agentTemplate
    parameters: agentTemplateParameters
  }
}

output agentTemplateDeploymentName string = useNestedDeployment ? '${appName}-agent-template' : ''
output agentTemplateDeploymentOutputs object = useNestedDeployment ? (agentTemplateDeployment.properties.outputs ?? {}) : {}
