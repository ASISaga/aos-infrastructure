using '../main-modular.bicep'

param environment = 'staging'
param location = 'eastus'
param locationML = 'eastus2'
param projectName = 'aos'
param tags = {
  project: 'AgentOperatingSystem'
  environment: 'staging'
  managedBy: 'bicep'
}
param enableGovernancePolicies = false
// Custom domain binding requires DNS CNAME records to be pre-configured.
// Leave empty for staging to allow Function Apps to provision without DNS setup.
param baseDomain = ''
