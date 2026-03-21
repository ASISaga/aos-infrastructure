// ai-sku-policy-def.bicep — Custom Azure Policy definition for AI deployment SKU governance
//
// Deployed at SUBSCRIPTION scope so the policy can be assigned to any resource group.
// Denies creation of Provisioned (PTU / GlobalProvisionedManaged) AI model deployments,
// enforcing the Frugal-First principle: only GlobalStandard (serverless, scale-to-zero)
// and Standard (regional, lowest-capacity) SKUs are permitted.
//
// Called by policy.bicep via `scope: subscription()`.

targetScope = 'subscription'

// ── Policy definition ─────────────────────────────────────────────────────────

resource aiSkuDenyPolicy 'Microsoft.Authorization/policyDefinitions@2024-04-01' = {
  name: 'aos-deny-provisioned-ai-sku'
  properties: {
    displayName: '[AOS] Deny Provisioned / PTU AI model deployment SKUs'
    policyType: 'Custom'
    mode: 'All'
    description: 'Denies creation of Azure AI model deployments that use a Provisioned or PTU SKU (ProvisionedManaged, GlobalProvisionedManaged). Only GlobalStandard (serverless, scale-to-zero) and Standard (regional) SKUs are permitted, ensuring cost control and elasticity.'
    metadata: {
      category: 'AI + Machine Learning'
      version: '1.0.0'
    }
    policyRule: {
      if: {
        allOf: [
          {
            field: 'type'
            equals: 'Microsoft.CognitiveServices/accounts/deployments'
          }
          {
            field: 'Microsoft.CognitiveServices/accounts/deployments/sku.name'
            in: [
              'ProvisionedManaged'
              'GlobalProvisionedManaged'
            ]
          }
        ]
      }
      then: {
        effect: 'Deny'
      }
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output policyDefinitionId string = aiSkuDenyPolicy.id
output policyDefinitionName string = aiSkuDenyPolicy.name
