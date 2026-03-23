// budget.bicep — Azure Cost Management budget
//
// Deploys a monthly cost budget using the native Microsoft.Consumption/budgets
// resource (API version 2021-10-01):
//   - Derives sensible default start/end dates via utcNow() when none are provided.
//   - Creates alert notifications for each configured threshold when contact emails
//     are supplied.
//   - Scale-to-zero is implicit: the budget tracks actual spend against serverless
//     (GlobalStandard / Standard) resources that cost nothing when idle.

@description('Deployment environment (dev, staging, prod)')
param environment string

@description('Monthly budget limit in the subscription currency')
param budgetAmount int = 500

@description('Budget alert threshold percentages (0–100). Values must be integers (e.g. [50, 80, 100]).')
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

// ── Budget notifications ─────────────────────────────────────────────────────
// Build one notification entry per threshold when contact emails are provided.
// Notification keys are derived from the threshold value (e.g. 'actual-80pct').
var hasEmails = length(contactEmails) > 0
var notifications = reduce(alertThresholds, {}, (prev, threshold) =>
  union(prev, hasEmails ? {
    'actual-${threshold}pct': {
      enabled: true
      operator: 'GreaterThanOrEqualTo'
      threshold: threshold
      contactEmails: contactEmails
      thresholdType: 'Actual'
    }
  } : {})
)

// ── Native: Cost Management budget ──────────────────────────────────────────
resource budgetResource 'Microsoft.Consumption/budgets@2021-10-01' = {
  name: budgetName
  properties: {
    amount: budgetAmount
    category: 'Cost'
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: resolvedStartDate
      endDate: resolvedEndDate
    }
    notifications: notifications
  }
}

// ── Outputs ──────────────────────────────────────────────────────────────────
output budgetId string = budgetResource.id
output budgetName string = budgetResource.name
output budgetAmount int = budgetAmount
