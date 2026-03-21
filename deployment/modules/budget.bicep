// budget.bicep — Azure Cost Management budget using Azure Verified Modules (AVM)
//
// Wraps the AVM consumption/budget module to:
//   - Derive sensible default start/end dates via utcNow() when none are provided.
//   - Forward alert-threshold notifications in the compact array form supported by AVM.
//   - Expose only the parameters callers need (amount, emails, optional date overrides).

@description('Deployment environment (dev, staging, prod)')
param environment string

@description('Monthly budget limit in the subscription currency')
param budgetAmount int = 500

@description('Budget alert threshold percentages (0–100)')
param alertThresholds array = [50, 80, 100]

@description('Email addresses to notify when a threshold is crossed')
param contactEmails array = []

@description('Start date for the budget (YYYY-MM-DD). Defaults to first day of current month.')
param startDate string = ''

@description('End date for the budget (YYYY-MM-DD). Defaults to last day of current year.')
param endDate string = ''

@description('Current UTC year used to compute default start/end dates (do not override).')
param currentYear string = utcNow('yyyy')

@description('Current UTC month used to compute default start date (do not override).')
param currentMonth string = utcNow('MM')

// ── Resolve default dates ────────────────────────────────────────────────────
var resolvedStartDate = empty(startDate) ? '${currentYear}-${currentMonth}-01' : startDate
var resolvedEndDate   = empty(endDate)   ? '${currentYear}-12-31' : endDate

// ── Budget name ──────────────────────────────────────────────────────────────
var budgetName = 'aos-budget-${environment}'

// ── AVM: Cost Management budget ─────────────────────────────────────────────
// Uses the AVM consumption/budget module (non-deprecated schema).
// Scale-to-zero is implicit: the budget tracks actual spend against serverless
// (GlobalStandard / Standard) resources that cost nothing when idle.
module budget 'br/public:avm/res/consumption/budget:0.1.1' = {
  name: budgetName
  params: {
    name: budgetName
    amount: budgetAmount
    startDate: resolvedStartDate
    endDate: resolvedEndDate
    contactEmails: contactEmails
    thresholds: alertThresholds
  }
}

// ── Outputs ──────────────────────────────────────────────────────────────────
output budgetId string = budget.outputs.resourceId
output budgetName string = budget.outputs.name
output budgetAmount int = budgetAmount
