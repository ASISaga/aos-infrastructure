// policy.bicep — AOS governance policy assignments
//
// Assigns the standard AOS governance policy initiative to the deployment scope.
//
// All assignments use the native Microsoft.Authorization/policyAssignments resource
// (API version 2022-06-01) with per-environment enforcement mode.
//
// AI SKU governance (enableAiSkuGovernance=true) additionally:
//   1. Creates a custom policy definition at subscription scope (ai-sku-policy-def.bicep)
//      that denies Provisioned / PTU AI model deployment SKUs.
//   2. Assigns that definition so only GlobalStandard (serverless, scale-to-zero)
//      or Standard (regional) SKUs can be deployed — never Provisioned/PTU.

@description('Deployment environment (dev, staging, prod)')
param environment string

@description('List of allowed Azure locations for resources')
param allowedLocations array = [
  'eastus'
  'eastus2'
  'westus2'
  'westeurope'
  'northeurope'
]

@description('When true, deploy the custom "Deny Provisioned AI SKUs" policy definition (at subscription scope) and assign it. Enforces GlobalStandard / Standard-only AI model deployments — no PTU / Provisioned SKUs.')
param enableAiSkuGovernance bool = true

// ── Built-in policy definition resource IDs ───────────────────────────────────
var policyAllowedLocations    = tenantResourceId('Microsoft.Authorization/policyDefinitions', 'e56962a6-4747-49cd-b67b-bf8b01975c4c')
var policyRequireHttpsStorage = tenantResourceId('Microsoft.Authorization/policyDefinitions', '404c3081-a854-4457-ae30-26a93ef643f9')
var policyKeyVaultSoftDelete  = tenantResourceId('Microsoft.Authorization/policyDefinitions', '1e66c121-a66a-4b1f-9b83-0fd99bf0fc2d')

// ── Enforcement mode per environment ──────────────────────────────────────────
// prod → hard enforcement; dev/staging → audit-only (DoNotEnforce)
var enforceMode = environment == 'prod' ? 'Default' : 'DoNotEnforce'

// ── AI SKU custom policy definition (subscription scope) ─────────────────────
// Deployed at subscription scope so it is visible to all resource groups in the sub.
// The definition is always created (lightweight, idempotent) so its ID is available
// to the conditional assignment below without ambiguous output access.
module aiSkuPolicyDef 'ai-sku-policy-def.bicep' = {
  name: 'ai-sku-policy-def'
  scope: subscription()
}

// ── Native: Allowed Locations ────────────────────────────────────────────────
resource allowedLocationsAssignment 'Microsoft.Authorization/policyAssignments@2022-06-01' = {
  name: 'aos-allowed-locations-${environment}'
  properties: {
    displayName: '[AOS] Allowed locations — ${environment}'
    policyDefinitionId: policyAllowedLocations
    parameters: {
      listOfAllowedLocations: {
        value: allowedLocations
      }
    }
    enforcementMode: enforceMode
  }
}

// ── Native: Require HTTPS on Storage ─────────────────────────────────────────
resource httpsStorageAssignment 'Microsoft.Authorization/policyAssignments@2022-06-01' = {
  name: 'aos-https-storage-${environment}'
  properties: {
    displayName: '[AOS] Require HTTPS on Storage — ${environment}'
    policyDefinitionId: policyRequireHttpsStorage
    enforcementMode: enforceMode
  }
}

// ── Native: Key Vault soft-delete ────────────────────────────────────────────
resource kvSoftDeleteAssignment 'Microsoft.Authorization/policyAssignments@2022-06-01' = {
  name: 'aos-kv-softdelete-${environment}'
  properties: {
    displayName: '[AOS] Key Vault soft-delete — ${environment}'
    policyDefinitionId: policyKeyVaultSoftDelete
    // Always enforced regardless of environment — KV soft-delete is a data-protection baseline.
    enforcementMode: 'Default'
  }
}

// ── Native: Deny Provisioned / PTU AI model deployment SKUs ──────────────────
// Enforces Frugal-First governance: GlobalStandard (serverless, scale-to-zero) and
// Standard (regional) SKUs only. PTU / Provisioned capacity is never allowed.
resource aiSkuDenyAssignment 'Microsoft.Authorization/policyAssignments@2022-06-01' = if (enableAiSkuGovernance) {
  name: 'aos-deny-provisioned-ai-sku-${environment}'
  properties: {
    displayName: '[AOS] Deny Provisioned / PTU AI SKUs — ${environment}'
    policyDefinitionId: aiSkuPolicyDef.outputs.policyDefinitionId
    enforcementMode: enforceMode
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output allowedLocationsAssignmentId string = allowedLocationsAssignment.id
output httpsStorageAssignmentId string = httpsStorageAssignment.id
output kvSoftDeleteAssignmentId string = kvSoftDeleteAssignment.id
