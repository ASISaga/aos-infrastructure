---
name: Sovereign Provisioning Agent
on:
  workflow_dispatch:
    inputs:
      target_branch:
        description: 'Branch to deploy and monitor'
        required: true
        default: 'main'
permissions:
  contents: read
  actions: read
  pull-requests: read
  issues: read
tools:
  gh-workflow-run: {}
  bash: true
  edit: {}
safe-outputs:
  create-pull-request:
    auto-merge: true
    allowed-labels: ["sovereign-fix", "automated"]
  create-issue:
    allowed-labels: ["sovereign-escalation", "manual-review-required"]
---

# Sovereign Provisioning Agent

... (Phase 1 remains the same) ...

## Phase 2: Error & Warning Resolution (The Loop)
... (Steps 1-5 remain the same) ...

6. **Patch flow and commit strategy:**
   - Instead of pushing directly to the target branch, use the `create-pull-request` safe-output.
   - The agent will bundle the Bicep/Python fixes into a PR. 
   - Once the PR is created, the agent can trigger the `infrastructure-deploy.yml` workflow against that PR branch to verify the fix.

... (Steps 7-10 remain the same) ...

## Phase 3: Validation
Once the deployment is 'Success':
- Provide a summary of the fixes.
- If errors persist, use the `create-issue` safe-output to escalate.