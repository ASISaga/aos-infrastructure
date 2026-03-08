# Global Rules

> **MANDATORY** — These rules apply to ALL deployment operations. Violations are unacceptable.

## Rule 1: Destructive Actions Require User Confirmation

⛔ **ALWAYS ask the user** before ANY destructive action.

### What is Destructive?

| Category | Examples |
|----------|----------|
| **Delete** | `az group delete`, `az resource delete`, delete Bicep resource |
| **Overwrite** | Replace existing parameter files, overwrite config, reset settings |
| **Irreversible** | Purge Key Vault, delete storage account, drop Service Bus namespace |
| **Cost Impact** | Provision expensive resources (AI Hub, GPU compute), scale up significantly |
| **Security** | Expose secrets, change access policies, modify RBAC assignments |
| **Production** | ANY change to `prod` environment resources or parameter files |

### How to Confirm

Present the action clearly and wait for explicit approval:
```
"This will delete resource group 'rg-aos-prod' and all its resources. This is irreversible.
Proceed? [yes/no]"
```

### No Exceptions

- Do NOT assume the user wants to delete/overwrite
- Do NOT proceed based on "the user asked to deploy" (deploy ≠ delete old)
- Do NOT batch destructive actions without individual confirmation

---

## Rule 2: Never Assume Subscription or Environment

⛔ **ALWAYS confirm**:
- The active Azure subscription (`az account show`)
- Target environment (`dev` / `staging` / `prod`)
- Resource group name

See [Pre-Deploy Checklist](pre-deploy-checklist.md) for the confirmation steps.

---

## Rule 3: Never Skip Validation

⛔ **ALWAYS run the full pipeline** before deployment:

```
pylint deployment/orchestrator/    → zero errors required
az bicep build --file ...          → zero errors required  
az deployment group what-if ...    → review changes
az deployment group create ...     → only after what-if review
```

Skipping `what-if` or `pylint` is not permitted, even under time pressure.
