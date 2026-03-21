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

@description('Resource tags')
param tags object = {}

@description('AI Project workspace resource ID (parent for the endpoint). Must be a Project-kind workspace — Hub workspaces do not support online endpoint creation.')
param workspaceId string

@description('Application logical name (used to derive endpoint name)')
param appName string

@description('Model identifier (azureml://registries/... or other registry id)')
param modelId string

@description('VM instance type for the managed online deployment.')
param instanceType string = 'Standard_DS3_v2'

@description('SKU capacity (number of replicas for the managed online deployment; 1 = single replica)')
param skuCapacity int = 1

@description('When true, creates the online deployment resource (requires the model asset to exist in the registry). Set to false to create only the endpoint shell until the LoRA adapter model has been trained and registered.')
param deployModel bool = true

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
var deploymentName = '${appName}-deployment'

// ====================================================================
// Managed Online Endpoint
// ====================================================================

resource endpoint 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints@2024-10-01' = {
  name: '${split(workspaceId, '/')[8]}/${endpointName}'
  location: location
  tags: tags
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
// Deployment — uses a model reference (Model Registry) for predictable infra.
// Only deployed when deployModel=true (i.e., the LoRA adapter has been trained
// and registered in the model registry).
// ====================================================================

resource agentDeployment 'Microsoft.MachineLearningServices/workspaces/onlineEndpoints/deployments@2024-10-01' = if (deployModel) {
  parent: endpoint
  name: deploymentName
  location: location
  tags: tags
  sku: {
    // Default SKU — standard VM-backed managed online deployment
    name: 'Default'
    capacity: skuCapacity
  }
  properties: {
    endpointComputeType: 'Managed'
    model: modelId
    instanceType: instanceType
    description: 'Agent deployment for ${appName}'
    scaleSettings: {
      scaleType: 'Default'
    }
    requestSettings: {
      maxConcurrentRequestsPerInstance: 4
    }
  }
}

// ====================================================================
// Outputs
// ====================================================================

output endpointName string = endpointName
@description('The deployment resource name, or empty string when deployModel=false (no deployment created yet).')
output deploymentName string = deployModel ? deploymentName : ''
output scoringUri string = endpoint.properties.scoringUri
output endpointId string = endpoint.id

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
