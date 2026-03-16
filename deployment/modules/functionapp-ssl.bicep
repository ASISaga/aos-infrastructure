// SSL hostname-binding sub-module
//
// Re-binds a custom hostname with SNI TLS enabled, linking the thumbprint of the App Service
// Managed Certificate that was issued in the parent functionapp.bicep module.
//
// A separate module is required here because Bicep forbids two resource declarations that
// share the same parent + name combination within a single module scope.  ARM treats the
// deployment as an idempotent in-place update of the existing hostname binding.

@description('Name of the existing Azure Function App')
param functionAppName string

@description('Custom domain hostname already bound to the app (e.g. erpnext.asisaga.com)')
param customDomain string

@description('TLS certificate thumbprint from the App Service Managed Certificate')
param thumbprint string

resource existingFunctionApp 'Microsoft.Web/sites@2023-12-01' existing = {
  name: functionAppName
}

// Update the hostname binding in-place: enable SNI TLS and link the managed certificate.
resource hostnameBindingWithSsl 'Microsoft.Web/sites/hostNameBindings@2023-12-01' = {
  parent: existingFunctionApp
  name: customDomain
  properties: {
    sslState: 'SniEnabled'
    thumbprint: thumbprint
    hostNameType: 'Verified'
  }
}

output customDomainUrl string = 'https://${customDomain}'
