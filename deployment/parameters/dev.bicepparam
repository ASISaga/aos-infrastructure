using '../main-modular.bicep'

param environment = 'dev'
param location = 'eastus'
param locationML = 'eastus'
param projectName = 'aos'
param tags = {
  project: 'AgentOperatingSystem'
  environment: 'dev'
  managedBy: 'bicep'
}
// Custom domain binding requires DNS CNAME records to be pre-configured.
// Leave empty for dev to allow Function Apps to provision without DNS setup.
param baseDomain = ''
