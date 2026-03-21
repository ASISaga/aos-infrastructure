// Phase 5 — Governance (Frugal-First, Sovereign Architecture)
//
// Deploys the governance layer using Azure Verified Modules (AVM):
//   - Azure Policy assignments (allowed locations, HTTPS storage, KV soft-delete)
//     via AVM authorization/policy-assignment — non-deprecated schema, per-env enforcement
//   - AI SKU governance policy (GlobalStandard / serverless only, no PTU/Provisioned)
//     — custom policy definition at subscription scope + AVM assignment
//   - Cost Management budget with email alerts
//     via AVM consumption/budget — non-deprecated schema
//
// Frugal-First principles enforced:
//   • All AI model deployments must use GlobalStandard (serverless, scale-to-zero) SKU.
//   • If GlobalStandard is unavailable, fallback to the lowest-capacity Standard (Regional) SKU.
//   • Provisioned (PTU / GlobalProvisionedManaged) SKUs are explicitly denied.
//
// Both the policy module and the budget module are conditional — governed by
// enableGovernancePolicies and monthlyBudgetAmount parameters respectively.
//
// No dependencies on other phases — can be deployed independently.

targetScope = 'resourceGroup'

// ====================================================================
// Parameters
// ====================================================================

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Enable AOS governance policy assignments')
param enableGovernancePolicies bool = true

@description('List of allowed Azure locations enforced by the Governance policy assignment')
param governanceAllowedLocations array = [
  'eastus'
  'eastus2'
  'westus2'
  'westeurope'
  'northeurope'
]

@description('When true, deploy and assign the custom AI SKU deny policy. Enforces GlobalStandard (serverless, scale-to-zero) and Standard (regional) SKUs. Denies Provisioned / PTU SKUs for all Azure AI model deployments in the resource group.')
param enableAiSkuGovernance bool = true

@description('Monthly budget limit in the subscription currency (0 = disabled)')
param monthlyBudgetAmount int = 0

@description('Email addresses notified when the budget crosses an alert threshold')
param budgetAlertEmails array = []

// ====================================================================
// Modules
// ====================================================================

module governancePolicy '../modules/policy.bicep' = if (enableGovernancePolicies) {
  name: 'governance-policy-${environment}'
  params: {
    environment: environment
    allowedLocations: governanceAllowedLocations
    enableAiSkuGovernance: enableAiSkuGovernance
  }
}

module governanceBudget '../modules/budget.bicep' = if (monthlyBudgetAmount > 0) {
  name: 'governance-budget-${environment}'
  params: {
    environment: environment
    budgetAmount: monthlyBudgetAmount
    contactEmails: budgetAlertEmails
  }
}

// ====================================================================
// Outputs
// ====================================================================

output aiSkuGovernanceEnabled bool = enableGovernancePolicies && enableAiSkuGovernance
