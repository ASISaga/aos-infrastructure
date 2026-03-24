// gitops-feedback.bicep — ARM Event-Driven Deployment Feedback Loop
//
// Deploys a "Zero-Footprint" GitOps feedback loop:
//   - Logic App (Consumption) that receives ARM deployment events via Event Grid and
//     updates GitHub Deployment statuses via the GitHub REST API.
//   - Event Grid System Topic scoped to Microsoft.Resources.ResourceGroups.
//   - Event Grid Event Subscription with an advancedFilter on data.operationName
//     (Microsoft.Resources/deployments/write) so the Logic App fires only when the
//     full Bicep deployment completes, not for every individual resource write.
//
// GitHub integration uses a PAT stored as a Logic App securestring parameter.
// The Logic App's system-assigned MSI is granted Reader on the resource group so it
// can call the ARM REST API to read the deployment's `github_deployment_id` tag.
//
// Linking GitHub deployments to ARM deployments:
//   GitHub Actions must tag the ARM deployment with `github_deployment_id: <id>` when
//   creating the deployment group via `az deployment group create --no-prompt true --tags github_deployment_id=...`
//   The Logic App reads this tag after the ARM deployment completes and uses the value
//   to call /repos/{owner}/{repo}/deployments/{id}/statuses on the GitHub REST API.

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

@description('GitHub repository name to update deployment statuses on (e.g. aos-infrastructure)')
param githubRepo string

@description('GitHub Personal Access Token with repo scope for the Deployment Status API. Stored as a Logic App securestring parameter and never written to ARM output.')
@secure()
param githubToken string

// ====================================================================
// Variables
// ====================================================================

var logicAppName          = 'la-gitops-feedback-${projectName}-${environment}'
var systemTopicName       = 'evgt-rg-${projectName}-${environment}'
var eventSubscriptionName = 'evgs-deployment-${projectName}-${environment}'

// Reader role — allows Logic App MSI to GET ARM deployment resources (read tags)
var readerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)

// ── Logic App expression strings (use triple-quote literals to allow embedded single quotes) ──

// Switch expression: evaluate the eventType field of the current foreach item
var exprEventType = '''@items('For_each_event')['eventType']'''

// ARM REST API URIs — the resourceUri from the event data is substituted at Logic App runtime
// management.azure.com is the ARM endpoint; these are Logic App runtime expressions, not Bicep deployment-time values
#disable-next-line no-hardcoded-env-urls
var exprGetSuccessDeploymentUri = '''https://management.azure.com@{items('For_each_event')['data']['resourceUri']}?api-version=2021-04-01'''
#disable-next-line no-hardcoded-env-urls
var exprGetFailureDeploymentUri = '''https://management.azure.com@{items('For_each_event')['data']['resourceUri']}?api-version=2021-04-01'''

// Condition expressions: coalesce the tag value and compare against empty string
var exprSuccessTagValue   = '''@coalesce(body('Get_ARM_Deployment_Success')?['tags']?['github_deployment_id'], '')'''
var exprFailureTagValue   = '''@coalesce(body('Get_ARM_Deployment_Failure')?['tags']?['github_deployment_id'], '')'''

// GitHub Deployment Status API URIs
var exprGitHubSuccessUri  = '''https://api.github.com/repos/@{parameters('githubOwner')}/@{parameters('githubRepository')}/deployments/@{body('Get_ARM_Deployment_Success')['tags']['github_deployment_id']}/statuses'''
var exprGitHubFailureUri  = '''https://api.github.com/repos/@{parameters('githubOwner')}/@{parameters('githubRepository')}/deployments/@{body('Get_ARM_Deployment_Failure')['tags']['github_deployment_id']}/statuses'''

// Authorization header value
var exprGitHubAuthHeader  = '''Bearer @{parameters('githubToken')}'''

// Status message for failure — extracted from the Event Grid data payload
var exprFailureStatusMsg  = '''@{coalesce(items('For_each_event')?['data']?['statusMessage'], 'Azure deployment failed')}'''

// Resource Graph / ARM MSI authentication audience
#disable-next-line no-hardcoded-env-urls
var armAudience = 'https://management.azure.com/'

// ====================================================================
// Logic App — Deployment Feedback Webhook
// ====================================================================
//
// Trigger : HTTP Request (Event Grid posts deployment events here as a JSON array)
// Actions :
//   For_each_event — iterate over the event array in the trigger body
//     Switch_on_event_type — route by eventType:
//       Case_ResourceWriteSuccess:
//         Get_ARM_Deployment_Success       — GET the ARM deployment resource (MSI auth)
//         Condition_Success_Has_Tag        — check if github_deployment_id tag exists
//           [True] Update_GitHub_Status_Success — POST success deployment status to GitHub
//       Case_ResourceWriteFailure:
//         Get_ARM_Deployment_Failure       — GET the ARM deployment resource (MSI auth)
//         Condition_Failure_Has_Tag        — check if github_deployment_id tag exists
//           [True] Update_GitHub_Status_Failure — POST failure status with statusMessage

resource feedbackLogicApp 'Microsoft.Logic/workflows@2019-05-01' = {
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
      }
      triggers: {
        When_a_HTTP_request_is_received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'array'
              items: {
                type: 'object'
                properties: {
                  id:              { type: 'string' }
                  eventType:       { type: 'string' }
                  subject:         { type: 'string' }
                  data:            { type: 'object' }
                  dataVersion:     { type: 'string' }
                  metadataVersion: { type: 'string' }
                  eventTime:       { type: 'string' }
                  topic:           { type: 'string' }
                }
              }
            }
          }
        }
      }
      actions: {
        For_each_event: {
          type: 'Foreach'
          foreach: '@triggerBody()'
          runAfter: {}
          actions: {
            Switch_on_event_type: {
              type: 'Switch'
              expression: exprEventType
              runAfter: {}
              default: {
                actions: {}
              }
              cases: {

                // ── Deployment succeeded ───────────────────────────────────────
                Case_ResourceWriteSuccess: {
                  case: 'Microsoft.Resources.ResourceWriteSuccess'
                  actions: {
                    Get_ARM_Deployment_Success: {
                      type: 'Http'
                      runAfter: {}
                      inputs: {
                        method: 'GET'
                        uri: exprGetSuccessDeploymentUri
                        authentication: {
                          type: 'ManagedServiceIdentity'
                          audience: armAudience
                        }
                      }
                    }
                    Condition_Success_Has_Tag: {
                      type: 'If'
                      runAfter: {
                        Get_ARM_Deployment_Success: [ 'Succeeded' ]
                      }
                      expression: {
                        and: [
                          {
                            not: {
                              equals: [
                                exprSuccessTagValue
                                ''
                              ]
                            }
                          }
                        ]
                      }
                      actions: {
                        Update_GitHub_Status_Success: {
                          type: 'Http'
                          inputs: {
                            method: 'POST'
                            uri: exprGitHubSuccessUri
                            headers: {
                              Authorization: exprGitHubAuthHeader
                              Accept: 'application/vnd.github+json'
                              'X-GitHub-Api-Version': '2022-11-28'
                              'User-Agent': 'AOS-GitOps-LogicApp/1.0'
                            }
                            body: {
                              state: 'success'
                              description: 'Azure deployment completed successfully'
                              environment: environment
                              auto_inactive: true
                            }
                          }
                        }
                      }
                      else: {
                        actions: {}
                      }
                    }
                  }
                }

                // ── Deployment failed ──────────────────────────────────────────
                Case_ResourceWriteFailure: {
                  case: 'Microsoft.Resources.ResourceWriteFailure'
                  actions: {
                    Get_ARM_Deployment_Failure: {
                      type: 'Http'
                      runAfter: {}
                      inputs: {
                        method: 'GET'
                        uri: exprGetFailureDeploymentUri
                        authentication: {
                          type: 'ManagedServiceIdentity'
                          audience: armAudience
                        }
                      }
                    }
                    Condition_Failure_Has_Tag: {
                      type: 'If'
                      runAfter: {
                        Get_ARM_Deployment_Failure: [ 'Succeeded' ]
                      }
                      expression: {
                        and: [
                          {
                            not: {
                              equals: [
                                exprFailureTagValue
                                ''
                              ]
                            }
                          }
                        ]
                      }
                      actions: {
                        // Mark the GitHub deployment failed; include the ARM statusMessage
                        // from the Event Grid data payload in the description field.
                        Update_GitHub_Status_Failure: {
                          type: 'Http'
                          inputs: {
                            method: 'POST'
                            uri: exprGitHubFailureUri
                            headers: {
                              Authorization: exprGitHubAuthHeader
                              Accept: 'application/vnd.github+json'
                              'X-GitHub-Api-Version': '2022-11-28'
                              'User-Agent': 'AOS-GitOps-LogicApp/1.0'
                            }
                            body: {
                              state: 'failure'
                              description: exprFailureStatusMsg
                              environment: environment
                              auto_inactive: false
                            }
                          }
                        }
                      }
                      else: {
                        actions: {}
                      }
                    }
                  }
                }

              }
            }
          }
        }
      }
    }
    parameters: {
      githubOwner:      { value: githubOrg   }
      githubRepository: { value: githubRepo  }
      githubToken:      { value: githubToken }
    }
  }
}

// ── Reader role: Logic App MSI reads ARM deployment resources in this RG ───────
// Uses feedbackLogicApp.id (deterministic at deployment start) for the name GUID,
// and feedbackLogicApp.identity.principalId (runtime) only in properties.
resource feedbackLogicAppReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: resourceGroup()
  name: guid(feedbackLogicApp.id, readerRoleId)
  properties: {
    roleDefinitionId: readerRoleId
    principalId: feedbackLogicApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Event Grid System Topic — scoped to this resource group ───────────────────
resource systemTopic 'Microsoft.EventGrid/systemTopics@2022-06-15' = {
  name: systemTopicName
  location: location
  tags: tags
  properties: {
    source: resourceGroup().id
    topicType: 'Microsoft.Resources.ResourceGroups'
  }
}

// ── Event Grid Event Subscription → Logic App webhook ─────────────────────────
//
// includedEventTypes: only ResourceWriteSuccess and ResourceWriteFailure
// advancedFilter: data.operationName = 'Microsoft.Resources/deployments/write'
//   → fires only when the TOP-LEVEL Bicep deployment completes, not for each
//     individual resource write within the deployment.
resource eventSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = {
  parent: systemTopic
  name: eventSubscriptionName
  properties: {
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: listCallbackUrl(
          '${feedbackLogicApp.id}/triggers/When_a_HTTP_request_is_received',
          feedbackLogicApp.apiVersion
        ).value
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    eventDeliverySchema: 'EventGridSchema'
    filter: {
      includedEventTypes: [
        'Microsoft.Resources.ResourceWriteSuccess'
        'Microsoft.Resources.ResourceWriteFailure'
      ]
      advancedFilters: [
        {
          operatorType: 'StringIn'
          key: 'data.operationName'
          values: [
            'Microsoft.Resources/deployments/write'
          ]
        }
      ]
    }
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}

// ====================================================================
// Outputs
// ====================================================================

output logicAppName          string = feedbackLogicApp.name
output logicAppPrincipalId   string = feedbackLogicApp.identity.principalId
output systemTopicName       string = systemTopic.name
output eventSubscriptionName string = eventSubscription.name
