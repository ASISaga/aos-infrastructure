using '../main-modular.bicep'

param environment = 'prod'
param location = 'eastus'
param locationML = 'eastus2'
param projectName = 'aos'
param tags = {
  project: 'AgentOperatingSystem'
  environment: 'prod'
  managedBy: 'bicep'
  costCenter: 'infrastructure'
}
// Set baseDomain once DNS CNAME records for all AOS apps are configured at asisaga.com.
// Until then, leave empty to allow Function Apps to provision without DNS prerequisite.
param baseDomain = ''
