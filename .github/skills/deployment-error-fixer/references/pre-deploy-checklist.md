# AOS Pre-Deployment Checklist

> **CRITICAL**: Before running ANY `az deployment group create` command, complete this checklist IN ORDER.
>
> ⛔ **DO NOT** deploy until ALL steps are complete. Skipping steps causes failures and orphaned resources.

## Step 1: Confirm Active Subscription

```bash
az account show --query "{name:name, id:id}" -o json
```

Verify you are targeting the correct subscription. If incorrect, run:
```bash
az account set --subscription <subscription-id-or-name>
```

## Step 2: Confirm Target Environment

Verify the deployment parameters file for the target environment:

```bash
# Check what parameters will be used
cat deployment/parameters/<environment>.bicepparam
```

Valid environments: `dev`, `staging`, `prod`

⛔ For `prod`: get explicit user confirmation before proceeding.

## Step 3: Lint Python Orchestrator

```bash
pylint deployment/orchestrator/ --max-line-length=120
```

**Required**: Zero errors. Fix all issues using this skill before continuing.

## Step 4: Validate Bicep Templates

```bash
az bicep build --file deployment/main-modular.bicep --stdout > /dev/null
```

**Required**: Zero errors. Fix all BCP codes using this skill before continuing.

## Step 5: Run What-If Analysis

```bash
python deployment/deploy.py plan \
  --resource-group rg-aos-<environment> \
  --location <location> \
  --environment <environment> \
  --template deployment/main-modular.bicep
```

Or directly with Azure CLI:
```bash
az deployment group what-if \
  --resource-group rg-aos-<environment> \
  --template-file deployment/main-modular.bicep \
  --parameters deployment/parameters/<environment>.bicepparam
```

**Required**: Review all changes. No unexpected deletions or modifications.

## Step 6: Check Resource Group Exists

```bash
az group show --name rg-aos-<environment> --query "{location:location, provisioningState:properties.provisioningState}" -o json
```

If the resource group doesn't exist, it will be created by the deployment.
If it exists in the wrong location, resolve before proceeding.

## Step 7: Only NOW Run Deployment

```bash
python deployment/deploy.py deploy \
  --resource-group rg-aos-<environment> \
  --location <location> \
  --environment <environment>
```

---

## Quick Reference: Correct AOS Deployment Sequence

```bash
# 1. Confirm subscription
az account show

# 2. Lint Python code
pylint deployment/orchestrator/ --max-line-length=120

# 3. Validate Bicep
az bicep build --file deployment/main-modular.bicep --stdout > /dev/null

# 4. What-if analysis
python deployment/deploy.py plan --resource-group rg-aos-dev --location eastus --environment dev

# 5. Deploy (after reviewing what-if output)
python deployment/deploy.py deploy --resource-group rg-aos-dev --location eastus --environment dev
```

## Common Mistakes to Avoid

| ❌ Wrong | ✅ Correct |
|----------|-----------|
| Deploy without linting Python | Run `pylint` first, fix all errors |
| Deploy without Bicep validation | Run `az bicep build` first |
| Skip what-if for "quick fixes" | Always run `plan` before `deploy` |
| Deploy to prod without review | Use `--plan-only` flag, get approval |
| Modify production parameter files without confirmation | Follow [Global Rules](global-rules.md) |

---

## Non-CLI Deployments (GitHub Actions)

The `infrastructure-deploy.yml` workflow enforces this checklist automatically:
1. `pylint` step — lint orchestrator
2. `az bicep build` step — validate Bicep
3. `az deployment group what-if` step — preview changes
4. `az deployment group create` step — deploy

If the workflow fails at any step, use this skill to fix the error and re-trigger.
