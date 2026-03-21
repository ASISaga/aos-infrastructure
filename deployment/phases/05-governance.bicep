// Phase 5 — Governance
//
// Deploys the governance layer:
//   - Azure Policy assignments (allowed locations, HTTPS storage, KV soft-delete)
//   - Cost Management budget with email alerts
//
// Both resources are conditional — governed by enableGovernancePolicies and
// monthlyBudgetAmount parameters respectively.
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
