// gitops-compliance.bicep — Policy Compliance Aggregator Logic App
//
// Deploys a "Zero-Footprint" GitOps compliance aggregator:
//   - Logic App (Consumption) with system-assigned Managed Identity
//   - Recurrence trigger (default: every 24 hours) — also supports manual runs
//     via the Azure portal or the Logic App run trigger REST API
//   - Queries Azure Resource Graph for non-compliant resources under the
//     ISO 27001 and Microsoft Cloud Security Benchmark (MCSB v2) initiatives
//   - Formats findings as a Markdown table: Policy Requirement → Affected Resource IDs
//   - Creates or updates a GitHub Issue titled
//     "[Compliance] Infrastructure Remediation Required"
//
// Identity:
//   System-assigned MSI is granted Reader at subscription scope via the companion
//   gitops-compliance-rbac.bicep module (called from this file with scope: subscription()).

@description('Azure region')
param location string

@description('Deployment environment')
@allowed(['dev', 'staging', 'prod'])
param environment string

@description('Base project name')
param projectName string

@description('Resource tags')
param tags object

@description('GitHub organization name (owner of the target repository)')
param githubOrg string

@description('GitHub repository name to create/update the compliance issue in (e.g. aos-infrastructure)')
param githubRepo string

@description('GitHub Personal Access Token with repo scope (issues:write). Stored as a Logic App securestring parameter and never written to ARM output.')
@secure()
param githubToken string

@description('Recurrence interval in hours between compliance scans (default: 24)')
param recurrenceIntervalHours int = 24

// ====================================================================
// Variables
// ====================================================================

var logicAppName = 'la-gitops-compliance-${projectName}-${environment}'

// KQL query: find non-compliant resources under ISO 27001 and MCSB v2 initiatives.
// Joins policy states with the Resources table to produce one row per violation.
// Results are ordered by PolicyRequirement so related rows are grouped in the output.
var complianceKqlQuery = '''PolicyResources
| where type == 'microsoft.policyinsights/policystates'
| where properties.complianceState == 'NonCompliant'
| where properties.policySetDefinitionName contains 'ISO 27001'
    or properties.policySetDefinitionName contains 'MCSB'
    or properties.policySetDefinitionName contains 'Microsoft Cloud Security Benchmark'
| join kind=inner (
    Resources
    | project resourceId = id, resourceType = type, resourceName = name
  ) on $left.properties.resourceId == $right.resourceId
| project
    PolicyRequirement = tostring(properties.policyDefinitionName),
    ResourceId        = tostring(properties.resourceId)
| order by PolicyRequirement asc'''

// GitHub issue title — used both for creation and for the search query
var complianceIssueTitle = '[Compliance] Infrastructure Remediation Required'

// ── Logic App expression strings (triple-quote literals allow embedded single quotes) ──

// Resource Graph REST API query body — kqlQuery parameter substituted at runtime
var exprKqlQuery = '''@{parameters('kqlQuery')}'''

// Build one Markdown table row per [PolicyRequirement, ResourceId] result row
var exprTableRowSelect = '''@concat('| ', item()[0], ' | ', item()[1], ' |')'''

// Source array for the Select action (rows from Resource Graph response)
var exprGraphRows = '''@body('Parse_Resource_Graph_Response')['data']['rows']'''

// Source array for the Join action
var exprBuildTableRowsBody = '''@body('Build_Table_Rows')'''

// Compose the full Markdown table: header + joined rows
var exprFullTable = '''@concat('| Policy Requirement | Affected Resource ID |\n|---|---|\n', body('Join_Table_Rows'))'''

// GitHub Search Issues API — URL-encoded query string
// Searches for open issues with the compliance title in the target repository
var exprSearchIssueUri = '''https://api.github.com/search/issues?q=%5BCompliance%5D+Infrastructure+Remediation+Required+in%3Atitle+repo%3A@{parameters('githubOwner')}%2F@{parameters('githubRepository')}+is%3Aissue+is%3Aopen'''

// Condition: total_count > 0 means an existing issue was found
var exprIssueCount = '''@body('Parse_Issue_Search_Response')['total_count']'''

// Update existing issue: PATCH with new body
var exprUpdateIssueUri = '''https://api.github.com/repos/@{parameters('githubOwner')}/@{parameters('githubRepository')}/issues/@{body('Parse_Issue_Search_Response')['items'][0]['number']}'''

// Create new issue: POST to issues endpoint
var exprCreateIssueUri = '''https://api.github.com/repos/@{parameters('githubOwner')}/@{parameters('githubRepository')}/issues'''

// Authorization header value
var exprAuthHeader = '''Bearer @{parameters('githubToken')}'''

// Compose output reference for the Markdown table body
var exprTableOutput = '''@{outputs('Compose_Full_Table')}'''

// ARM Management Service endpoint (Logic App MSI authentication audience)
#disable-next-line no-hardcoded-env-urls
var armAudience = 'https://management.azure.com/'

// Azure Resource Graph REST API endpoint
#disable-next-line no-hardcoded-env-urls
var resourceGraphUri = 'https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01'

// ====================================================================
// Logic App — Policy Compliance Aggregator
// ====================================================================
//
// Trigger : Recurrence (every `recurrenceIntervalHours` hours)
// Actions :
//   1. Query_Resource_Graph          — POST to Azure Resource Graph REST API (MSI auth)
//   2. Parse_Resource_Graph_Response — Parse the JSON response schema
//   3. Build_Table_Rows              — Select: transform each [Policy, ResourceId] row
//                                      into a Markdown table row string
//   4. Join_Table_Rows               — Join row strings with \n separator
//   5. Compose_Full_Table            — Prepend Markdown header to joined rows
//   6. Search_Compliance_Issue       — GET GitHub search/issues to find existing open issue
//   7. Parse_Issue_Search_Response   — Parse the search response JSON
//   8. Condition_Issue_Exists        — Branch on total_count > 0:
//        True  → Update_Compliance_Issue (PATCH /repos/.../issues/{number})
//        False → Create_Compliance_Issue (POST /repos/.../issues)

resource complianceLogicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        githubOwner: {
          type: 'string'
          defaultValue: githubOrg
        }
        githubRepository: {
          type: 'string'
          defaultValue: githubRepo
        }
        githubToken: {
          type: 'securestring'
          defaultValue: ''
        }
        kqlQuery: {
          type: 'string'
          defaultValue: complianceKqlQuery
        }
      }
      triggers: {
        Recurrence: {
          type: 'Recurrence'
          recurrence: {
            frequency: 'Hour'
            interval: recurrenceIntervalHours
          }
        }
      }
      actions: {

        // ── Step 1: Query Azure Resource Graph ─────────────────────────────────
        // Uses system-assigned MSI — requires Reader at subscription scope (provisioned
        // via gitops-compliance-rbac.bicep called below with scope: subscription()).
        Query_Resource_Graph: {
          type: 'Http'
          runAfter: {}
          inputs: {
            method: 'POST'
            uri: resourceGraphUri
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              query: exprKqlQuery
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: armAudience
            }
          }
        }

        // ── Step 2: Parse the Resource Graph JSON response ─────────────────────
        Parse_Resource_Graph_Response: {
          type: 'ParseJson'
          runAfter: {
            Query_Resource_Graph: [ 'Succeeded' ]
          }
          inputs: {
            content: '@body("Query_Resource_Graph")'
            schema: {
              type: 'object'
              properties: {
                totalRecords: { type: 'integer' }
                count:        { type: 'integer' }
                data: {
                  type: 'object'
                  properties: {
                    columns: {
                      type: 'array'
                      items: {
                        type: 'object'
                        properties: {
                          name: { type: 'string' }
                          type: { type: 'string' }
                        }
                      }
                    }
                    rows: {
                      type: 'array'
                      items: {
                        type: 'array'
                      }
                    }
                  }
                }
              }
            }
          }
        }

        // ── Step 3: Build Markdown table rows (one per policy violation) ───────
        // Resource Graph row format: index 0 = PolicyRequirement, index 1 = ResourceId
        Build_Table_Rows: {
          type: 'Select'
          runAfter: {
            Parse_Resource_Graph_Response: [ 'Succeeded' ]
          }
          inputs: {
            from: exprGraphRows
            select: exprTableRowSelect
          }
        }

        // ── Step 4: Join row strings with \n separator ─────────────────────────
        Join_Table_Rows: {
          type: 'Join'
          runAfter: {
            Build_Table_Rows: [ 'Succeeded' ]
          }
          inputs: {
            from: exprBuildTableRowsBody
            joinWith: '\n'
          }
        }

        // ── Step 5: Prepend Markdown header to produce the full compliance table ─
        Compose_Full_Table: {
          type: 'Compose'
          runAfter: {
            Join_Table_Rows: [ 'Succeeded' ]
          }
          inputs: exprFullTable
        }

        // ── Step 6: Search GitHub for the existing open compliance issue ────────
        Search_Compliance_Issue: {
          type: 'Http'
          runAfter: {
            Compose_Full_Table: [ 'Succeeded' ]
          }
          inputs: {
            method: 'GET'
            uri: exprSearchIssueUri
            headers: {
              Authorization: exprAuthHeader
              Accept: 'application/vnd.github+json'
              'X-GitHub-Api-Version': '2022-11-28'
              'User-Agent': 'AOS-GitOps-LogicApp/1.0'
            }
          }
        }

        // ── Step 7: Parse the GitHub issue search response ─────────────────────
        Parse_Issue_Search_Response: {
          type: 'ParseJson'
          runAfter: {
            Search_Compliance_Issue: [ 'Succeeded' ]
          }
          inputs: {
            content: '@body("Search_Compliance_Issue")'
            schema: {
              type: 'object'
              properties: {
                total_count: { type: 'integer' }
                items: {
                  type: 'array'
                  items: {
                    type: 'object'
                    properties: {
                      number:   { type: 'integer' }
                      title:    { type: 'string'  }
                      state:    { type: 'string'  }
                      html_url: { type: 'string'  }
                    }
                  }
                }
              }
            }
          }
        }

        // ── Step 8: Create or update the compliance issue ──────────────────────
        // total_count > 0  → existing open issue found → PATCH to update body
        // total_count == 0 → no open issue found       → POST to create new issue
        Condition_Issue_Exists: {
          type: 'If'
          runAfter: {
            Parse_Issue_Search_Response: [ 'Succeeded' ]
          }
          expression: {
            and: [
              {
                greater: [
                  exprIssueCount
                  0
                ]
              }
            ]
          }
          actions: {
            // Issue already exists — update its body with the latest compliance table
            Update_Compliance_Issue: {
              type: 'Http'
              inputs: {
                method: 'PATCH'
                uri: exprUpdateIssueUri
                headers: {
                  Authorization: exprAuthHeader
                  Accept: 'application/vnd.github+json'
                  'X-GitHub-Api-Version': '2022-11-28'
                  'User-Agent': 'AOS-GitOps-LogicApp/1.0'
                }
                body: {
                  body: exprTableOutput
                }
              }
            }
          }
          else: {
            actions: {
              // No open issue — create one with the compliance table as the body
              Create_Compliance_Issue: {
                type: 'Http'
                inputs: {
                  method: 'POST'
                  uri: exprCreateIssueUri
                  headers: {
                    Authorization: exprAuthHeader
                    Accept: 'application/vnd.github+json'
                    'X-GitHub-Api-Version': '2022-11-28'
                    'User-Agent': 'AOS-GitOps-LogicApp/1.0'
                  }
                  body: {
                    title: complianceIssueTitle
                    body: exprTableOutput
                    labels: [ 'compliance', 'infrastructure' ]
                  }
                }
              }
            }
          }
        }

      }
    }
    parameters: {
      githubOwner:      { value: githubOrg          }
      githubRepository: { value: githubRepo          }
      githubToken:      { value: githubToken         }
      kqlQuery:         { value: complianceKqlQuery  }
    }
  }
}

// ── Subscription-level Reader role for the Logic App MSI ──────────────────────
// Grants Reader at subscription scope so the MSI can call the Resource Graph API.
// Follows the same pattern as policy.bicep calling ai-sku-policy-def.bicep with
// scope: subscription().
module complianceSubscriptionRbac 'gitops-compliance-rbac.bicep' = {
  name: 'compliance-subscription-reader'
  scope: subscription()
  params: {
    complianceLogicAppPrincipalId: complianceLogicApp.identity.principalId
  }
}

// ====================================================================
// Outputs
// ====================================================================

output logicAppName                       string = complianceLogicApp.name
output logicAppPrincipalId                string = complianceLogicApp.identity.principalId
output subscriptionReaderRoleAssignmentId string = complianceSubscriptionRbac.outputs.roleAssignmentId
