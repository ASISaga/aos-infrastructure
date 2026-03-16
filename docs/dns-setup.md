# AOS DNS Setup Guide

## Overview

Each AOS module and MCP server is hosted as an Azure Function App with a custom
`*.asisaga.com` hostname secured by a free App Service Managed Certificate (SNI TLS).
Every Function App must have its CNAME record in place **before** the Bicep deployment
runs the hostname-binding step, or the deployment will fail.

This guide documents:

1. [All 16 CNAME records](#cname-records) that must exist in your DNS provider
2. [Additional DNS requirements](#additional-dns-requirements) (no TXT records needed)
3. [Two-phase deployment procedure](#deployment-procedure) — the recommended way to
   obtain exact CNAME targets without pre-computing uniqueString hashes
4. [Per-environment recommendations](#environment-strategy)
5. [Repository naming recommendations](#repository-naming-recommendations)

---

## CNAME Records

The production deployment binds **16 custom domains** — 12 for standard AOS modules
and 4 for MCP server submodules.  Each record is a standard DNS `CNAME` entry.

### Standard AOS Module Domains

Domain derivation rule: **`<appName>.asisaga.com`**

| # | Hostname (CNAME name)              | Target (CNAME value)                                           |
|---|------------------------------------|----------------------------------------------------------------|
| 1 | `ceo-agent.asisaga.com`            | `func-ceo-agent-prod-<suffix>.azurewebsites.net`              |
| 2 | `cfo-agent.asisaga.com`            | `func-cfo-agent-prod-<suffix>.azurewebsites.net`              |
| 3 | `cto-agent.asisaga.com`            | `func-cto-agent-prod-<suffix>.azurewebsites.net`              |
| 4 | `cso-agent.asisaga.com`            | `func-cso-agent-prod-<suffix>.azurewebsites.net`              |
| 5 | `cmo-agent.asisaga.com`            | `func-cmo-agent-prod-<suffix>.azurewebsites.net`              |
| 6 | `aos-kernel.asisaga.com`           | `func-aos-kernel-prod-<suffix>.azurewebsites.net`             |
| 7 | `aos-intelligence.asisaga.com`     | `func-aos-intelligence-prod-<suffix>.azurewebsites.net`       |
| 8 | `aos-realm-of-agents.asisaga.com`  | `func-aos-realm-of-agents-prod-<suffix>.azurewebsites.net`    |
| 9 | `aos-mcp-servers.asisaga.com`      | `func-aos-mcp-servers-prod-<suffix>.azurewebsites.net`        |
|10 | `aos-client-sdk.asisaga.com`       | `func-aos-client-sdk-prod-<suffix>.azurewebsites.net`         |
|11 | `business-infinity.asisaga.com`    | `func-business-infinity-prod-<suffix>.azurewebsites.net`      |
|12 | `aos-dispatcher.asisaga.com`       | `func-aos-dispatcher-prod-<suffix>.azurewebsites.net`         |

### MCP Server Domains

The MCP server GitHub repository names (`githubRepo`) double as their custom domain
hostnames.  This means a single value (`erpnext.asisaga.com`) serves as both the GitHub
repository identifier used in the OIDC Workload Identity Federation subject **and** the
custom domain that is bound to the Azure Function App.  Domain binding for MCP servers
is also controlled by `baseDomain` — set `baseDomain = ''` to disable all custom domains
including MCP servers (see [Environment Strategy](#environment-strategy)).

| # | Hostname (CNAME name)     | Azure app name   | Target (CNAME value)                                      |
|---|---------------------------|------------------|-----------------------------------------------------------|
|13 | `erpnext.asisaga.com`     | `mcp-erpnext`    | `func-mcp-erpnext-prod-<suffix>.azurewebsites.net`       |
|14 | `linkedin.asisaga.com`    | `mcp-linkedin`   | `func-mcp-linkedin-prod-<suffix>.azurewebsites.net`      |
|15 | `reddit.asisaga.com`      | `mcp-reddit`     | `func-mcp-reddit-prod-<suffix>.azurewebsites.net`        |
|16 | `subconscious.asisaga.com`| `mcp-subconscious`| `func-mcp-subconscious-prod-<suffix>.azurewebsites.net` |

> **`<suffix>`** is the first 6 characters of
> `uniqueString(resourceGroup().id, 'aos', 'prod')`, computed deterministically from
> the Azure resource group's resource ID.  Obtain the exact hostnames by running
> Phase 1 of the [deployment procedure](#deployment-procedure) below.

---

## Additional DNS Requirements

App Service custom domain verification uses the **CNAME record itself** as proof of
ownership when the domain is a subdomain (not an apex/root domain).  No additional TXT
records (`asuid.*`) are needed because:

- All 16 hostnames are **subdomains** of `asisaga.com`.
- The `hostNameType: 'Verified'` property in the Bicep template instructs App Service
  to verify via CNAME resolution rather than a separate TXT record.

### Summary of DNS changes in your provider

| Record type | Name                        | Value                                          | TTL      |
|-------------|------------------------------|------------------------------------------------|----------|
| `CNAME`     | `ceo-agent`                 | `func-ceo-agent-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `cfo-agent`                 | `func-cfo-agent-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `cto-agent`                 | `func-cto-agent-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `cso-agent`                 | `func-cso-agent-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `cmo-agent`                 | `func-cmo-agent-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-kernel`                | `func-aos-kernel-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-intelligence`          | `func-aos-intelligence-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-realm-of-agents`       | `func-aos-realm-of-agents-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-mcp-servers`           | `func-aos-mcp-servers-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-client-sdk`            | `func-aos-client-sdk-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `business-infinity`         | `func-business-infinity-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `aos-dispatcher`            | `func-aos-dispatcher-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `erpnext`                   | `func-mcp-erpnext-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `linkedin`                  | `func-mcp-linkedin-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `reddit`                    | `func-mcp-reddit-prod-<suffix>.azurewebsites.net` | 3600 s |
| `CNAME`     | `subconscious`              | `func-mcp-subconscious-prod-<suffix>.azurewebsites.net` | 3600 s |

No MX, SPF, DKIM, A, or AAAA records need to be changed.

---

## Deployment Procedure

Azure App Service requires the CNAME to exist **before** the hostname-binding resource
is deployed, but the exact `azurewebsites.net` hostname contains a uniqueString hash
that is only deterministic once the resource group ID is known.  The recommended
two-phase procedure avoids any manual hash computation.

### Phase 1 — Deploy infrastructure without custom domains

Deploy once with `baseDomain` left empty so that all Function Apps are created and
their default hostnames become visible in the deployment outputs.

```bash
az deployment group create \
  --resource-group rg-aos-prod \
  --template-file deployment/main-modular.bicep \
  --parameters deployment/parameters/prod.bicepparam \
               baseDomain=''
```

After the deployment succeeds, collect every Function App's default hostname:

```bash
az deployment group show \
  --resource-group rg-aos-prod \
  --name main-modular-prod \
  --query 'properties.outputs.functionAppNames.value' \
  --output tsv

az deployment group show \
  --resource-group rg-aos-prod \
  --name main-modular-prod \
  --query 'properties.outputs.mcpServerFunctionAppNames.value' \
  --output tsv
```

The Azure Function App naming formula is:

```
func-<appName>-<environment>-<take(uniqueString(rgId, 'aos', environment), 6)>
```

For example, after Phase 1 you might see:

```
func-ceo-agent-prod-a1b2c3.azurewebsites.net
func-cfo-agent-prod-a1b2c3.azurewebsites.net
...
```

The 6-character suffix is **identical across all apps in the same resource group**
because it is derived from the resource group ID, not the app name.

### Phase 2 — Create the 16 CNAME records

Using the suffix obtained in Phase 1 (e.g. `a1b2c3`), create these 16 CNAME records in
your DNS provider. Replace `<suffix>` with your actual 6-character uniqueString value.

```
ceo-agent.asisaga.com            CNAME  func-ceo-agent-prod-<suffix>.azurewebsites.net
cfo-agent.asisaga.com            CNAME  func-cfo-agent-prod-<suffix>.azurewebsites.net
cto-agent.asisaga.com            CNAME  func-cto-agent-prod-<suffix>.azurewebsites.net
cso-agent.asisaga.com            CNAME  func-cso-agent-prod-<suffix>.azurewebsites.net
cmo-agent.asisaga.com            CNAME  func-cmo-agent-prod-<suffix>.azurewebsites.net
aos-kernel.asisaga.com           CNAME  func-aos-kernel-prod-<suffix>.azurewebsites.net
aos-intelligence.asisaga.com     CNAME  func-aos-intelligence-prod-<suffix>.azurewebsites.net
aos-realm-of-agents.asisaga.com  CNAME  func-aos-realm-of-agents-prod-<suffix>.azurewebsites.net
aos-mcp-servers.asisaga.com      CNAME  func-aos-mcp-servers-prod-<suffix>.azurewebsites.net
aos-client-sdk.asisaga.com       CNAME  func-aos-client-sdk-prod-<suffix>.azurewebsites.net
business-infinity.asisaga.com    CNAME  func-business-infinity-prod-<suffix>.azurewebsites.net
aos-dispatcher.asisaga.com       CNAME  func-aos-dispatcher-prod-<suffix>.azurewebsites.net
erpnext.asisaga.com              CNAME  func-mcp-erpnext-prod-<suffix>.azurewebsites.net
linkedin.asisaga.com             CNAME  func-mcp-linkedin-prod-<suffix>.azurewebsites.net
reddit.asisaga.com               CNAME  func-mcp-reddit-prod-<suffix>.azurewebsites.net
subconscious.asisaga.com         CNAME  func-mcp-subconscious-prod-<suffix>.azurewebsites.net
```

Wait for DNS propagation before proceeding (typically under 5 minutes for most
providers; up to 1 hour if you previously published a different value for these names).

Verify propagation:

```bash
# Repeat for each of the 16 names
dig +short ceo-agent.asisaga.com CNAME
dig +short erpnext.asisaga.com CNAME
```

### Phase 3 — Re-deploy with custom domains enabled

Re-run the deployment with the default `baseDomain='asisaga.com'`.  Bicep now:
1. Binds each custom hostname (Phase 1 of the three-phase binding — `sslState: Disabled`)
2. Issues a free App Service Managed Certificate per app (Phase 2)
3. Re-binds with SNI TLS enabled using the certificate thumbprint (Phase 3, via the
   `functionapp-ssl.bicep` sub-module)

```bash
az deployment group create \
  --resource-group rg-aos-prod \
  --template-file deployment/main-modular.bicep \
  --parameters deployment/parameters/prod.bicepparam
```

After the deployment completes, all 16 URLs are live over HTTPS:

```
https://ceo-agent.asisaga.com
https://cfo-agent.asisaga.com
https://cto-agent.asisaga.com
https://cso-agent.asisaga.com
https://cmo-agent.asisaga.com
https://aos-kernel.asisaga.com
https://aos-intelligence.asisaga.com
https://aos-realm-of-agents.asisaga.com
https://aos-mcp-servers.asisaga.com
https://aos-client-sdk.asisaga.com
https://business-infinity.asisaga.com
https://aos-dispatcher.asisaga.com
https://erpnext.asisaga.com
https://linkedin.asisaga.com
https://reddit.asisaga.com
https://subconscious.asisaga.com
```

---

## Environment Strategy

Custom domains use bare `<appName>.asisaga.com` names — they do not embed the
environment name.  Because each custom hostname can only resolve to one CNAME target
at a time, the following per-environment policy applies:

| Environment | `baseDomain` value | Custom domains deployed? | Rationale |
|-------------|-------------------|--------------------------|-----------|
| **prod**    | `asisaga.com`     | ✅ Yes — all 16 CNAMEs   | Public-facing, canonical URLs |
| **staging** | _(empty)_         | ❌ No                    | Staging uses default `*.azurewebsites.net` hostnames |
| **dev**     | _(empty)_         | ❌ No                    | Dev uses default `*.azurewebsites.net` hostnames |

Set `baseDomain = ''` in your staging/dev parameter files to skip custom domain binding
for non-production deployments.  The default `*.azurewebsites.net` URLs remain fully
functional for internal testing.

---

## Repository Naming Recommendations

### Current naming conventions

| Category | Naming pattern | Examples |
|----------|---------------|---------|
| Platform services | `aos-<service>` | `aos-kernel`, `aos-dispatcher`, `aos-intelligence` |
| Agent services | `<role>-agent` | `ceo-agent`, `cfo-agent`, `cto-agent`, `cso-agent`, `cmo-agent` |
| Base agent classes | `<type>-agent` | `purpose-driven-agent`, `leadership-agent` |
| Business app | _(unique)_ | `business-infinity` |
| MCP servers | `<service>.asisaga.com` | `erpnext.asisaga.com`, `linkedin.asisaga.com` |
| Infrastructure | `aos-infrastructure` | this repository |

### Assessment

The current naming is **consistent and logical** for the AOS ecosystem.  Agent services
intentionally omit the `aos-` prefix so that their custom domains read naturally as
`ceo-agent.asisaga.com` rather than `aos-ceo-agent.asisaga.com`.

The MCP server repositories use full domain names (`erpnext.asisaga.com`) as the
GitHub repository name.  This is **unconventional** but intentional: the `githubRepo`
parameter of `main-modular.bicep` is passed both to the OIDC Workload Identity
Federation subject and (for MCP servers) directly as the `customDomain` value —
keeping the two in sync automatically.  A repository named `erpnext.asisaga.com`
produces the OIDC subject:

```
repo:ASISaga/erpnext.asisaga.com:environment:prod
```

which exactly matches the custom domain used for the hostname binding.

### Recommended names (no changes required)

No repository renames are required.  The existing names satisfy logical grouping:

| Repository | Role | Custom domain |
|-----------|------|---------------|
| `aos-infrastructure` | Infrastructure lifecycle manager (this repo) | _(no public domain)_ |
| `aos-kernel` | OS kernel and agent runtime | `aos-kernel.asisaga.com` |
| `aos-dispatcher` | Central orchestration hub | `aos-dispatcher.asisaga.com` |
| `aos-intelligence` | Intelligence layer | `aos-intelligence.asisaga.com` |
| `aos-realm-of-agents` | Agent catalog and capability registry | `aos-realm-of-agents.asisaga.com` |
| `aos-mcp-servers` | MCP server framework | `aos-mcp-servers.asisaga.com` |
| `aos-client-sdk` | Client SDK and Azure Functions scaffolding | `aos-client-sdk.asisaga.com` |
| `ceo-agent` | CEO C-suite agent | `ceo-agent.asisaga.com` |
| `cfo-agent` | CFO C-suite agent | `cfo-agent.asisaga.com` |
| `cto-agent` | CTO C-suite agent | `cto-agent.asisaga.com` |
| `cso-agent` | CSO C-suite agent | `cso-agent.asisaga.com` |
| `cmo-agent` | CMO C-suite agent | `cmo-agent.asisaga.com` |
| `purpose-driven-agent` | Base class for purpose-driven agents | _(not deployed directly)_ |
| `leadership-agent` | Base class for leadership agents | _(not deployed directly)_ |
| `business-infinity` | Business application | `business-infinity.asisaga.com` |
| `erpnext.asisaga.com` | ERPNext MCP server | `erpnext.asisaga.com` |
| `linkedin.asisaga.com` | LinkedIn MCP server | `linkedin.asisaga.com` |
| `reddit.asisaga.com` | Reddit MCP server | `reddit.asisaga.com` |
| `subconscious.asisaga.com` | Subconscious MCP server | `subconscious.asisaga.com` |

### Optional improvement — MCP server naming

If the dot-in-repository-name convention becomes a maintenance burden, rename the MCP
server repos to shorter identifiers and introduce an explicit `customDomain` field in
`main-modular.bicep`:

| Current repo name | Recommended rename | Custom domain (unchanged) |
|-------------------|-------------------|---------------------------|
| `erpnext.asisaga.com` | `mcp-erpnext` | `erpnext.asisaga.com` |
| `linkedin.asisaga.com` | `mcp-linkedin` | `linkedin.asisaga.com` |
| `reddit.asisaga.com` | `mcp-reddit` | `reddit.asisaga.com` |
| `subconscious.asisaga.com` | `mcp-subconscious` | `subconscious.asisaga.com` |

This rename would require updating the `githubRepo` field in the `mcpServerApps`
parameter of `main-modular.bicep` and the `_MCP_SERVER_APPS` dictionary in
`deployment/orchestrator/integration/sdk_bridge.py`.

---

## Troubleshooting

### Hostname binding fails during deployment

**Symptom**: Bicep deployment error on the `hostnameBinding` resource.

**Cause**: The CNAME record does not exist or has not propagated yet.

**Fix**: Verify the CNAME record with `dig +short <hostname> CNAME` and wait for
propagation.  You can deploy with `baseDomain=''` to skip custom domains and
add them in a subsequent deployment once DNS is confirmed.

### Certificate issuance fails

**Symptom**: `managedCertificate` resource fails with a validation error.

**Cause**: App Service Managed Certificates are only available on apps running on a
paid (non-free) App Service Plan.  All AOS apps use the FC1 Flex Consumption plan,
which supports managed certificates.

**Fix**: Ensure the CNAME is correctly pointing to the `azurewebsites.net` hostname and
that the hostname binding (Phase 1) completed successfully before retrying.

### SNI binding reports stale thumbprint

**Symptom**: `functionapp-ssl.bicep` fails on re-deploy with a thumbprint mismatch.

**Cause**: The managed certificate was renewed and has a new thumbprint.

**Fix**: Trigger a full redeployment; Bicep re-reads `managedCertificate.properties.thumbprint`
at deploy time and updates the SSL binding automatically.

---

## References

→ **Bicep module**: `deployment/modules/functionapp.bicep` — three-phase custom domain binding  
→ **SSL sub-module**: `deployment/modules/functionapp-ssl.bicep` — SNI TLS re-binding  
→ **Primary template**: `deployment/main-modular.bicep` — `baseDomain` parameter and `appNames`/`mcpServerApps` arrays  
→ **SDK bridge**: `deployment/orchestrator/integration/sdk_bridge.py` — `_BASE_DOMAIN` and `_MCP_SERVER_APPS` constants  
→ **Architecture**: `docs/architecture.md` — deployment pipeline and modular Bicep architecture  
→ **API reference**: `docs/api-reference.md` — Bicep template parameters reference
