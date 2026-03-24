// gitops-compliance-rbac.bicep — Subscription-scoped Reader role for GitOps Compliance Logic App
//
// Deployed at subscription scope so the compliance Logic App's managed identity can call
// the Azure Resource Graph API (which requires read access across the subscription).
//
// Called from gitops-compliance.bicep with `scope: subscription()`.

targetScope = 'subscription'

@description('Principal ID of the compliance Logic App system-assigned managed identity')
param complianceLogicAppPrincipalId string

// ── Reader role definition ID ─────────────────────────────────────────────────
var readerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)

// ── Subscription-level Reader role assignment for the Logic App MSI ───────────
resource complianceReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, complianceLogicAppPrincipalId, readerRoleId)
  properties: {
    roleDefinitionId: readerRoleId
    principalId: complianceLogicAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output roleAssignmentId string = complianceReaderRole.id
