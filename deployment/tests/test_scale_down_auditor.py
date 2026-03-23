"""Tests for the ScaleDownAuditor governance module.

Validates classification logic, audit report generation, and GitHub issue
body formatting.  All Azure SDK calls are mocked.
"""

from __future__ import annotations

from unittest import mock

import pytest

from orchestrator.governance.scale_down_auditor import (
    ScaleDownAuditReport,
    ScaleDownAuditor,
    ScaleDownViolation,
)
from orchestrator.integration.azure_sdk_client import ProvisioningState, ResourceState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resource(
    name: str,
    resource_type: str,
    sku: str = "",
    location: str = "eastus",
) -> ResourceState:
    return ResourceState(
        name=name,
        resource_type=resource_type,
        location=location,
        provisioning_state=ProvisioningState.SUCCEEDED,
        resource_id=f"/subscriptions/sub-123/resourceGroups/rg-test/providers/{resource_type}/{name}",
        sku=sku,
    )


def _make_auditor(resources: list[ResourceState]) -> ScaleDownAuditor:
    """Create a ScaleDownAuditor with a mocked AzureSDKClient."""
    with (
        mock.patch("orchestrator.governance.scale_down_auditor.AzureSDKClient") as mock_sdk,
    ):
        instance = mock_sdk.return_value
        instance.list_resources.return_value = resources
        auditor = ScaleDownAuditor.__new__(ScaleDownAuditor)
        auditor.resource_group = "rg-test"
        auditor.subscription_id = "sub-123"
        auditor._client = instance
        return auditor


# ====================================================================
# ScaleDownViolation
# ====================================================================

class TestScaleDownViolation:
    def test_to_dict_contains_required_keys(self) -> None:
        v = ScaleDownViolation(
            resource_name="sb-prod",
            resource_type="Microsoft.ServiceBus/namespaces",
            resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ServiceBus/namespaces/sb-prod",
            location="eastus",
            sku="Standard",
            recommendation="Use Azure Storage Queue.",
            alternatives=["Azure Storage Queue"],
        )
        d = v.to_dict()
        assert d["resource_name"] == "sb-prod"
        assert d["resource_type"] == "Microsoft.ServiceBus/namespaces"
        assert d["sku"] == "Standard"
        assert "Azure Storage Queue" in d["alternatives"]


# ====================================================================
# ScaleDownAuditReport
# ====================================================================

class TestScaleDownAuditReport:
    def test_has_violations_true(self) -> None:
        v = ScaleDownViolation(
            resource_name="r", resource_type="t", resource_id="id",
            location="eastus", sku="", recommendation="rec",
        )
        report = ScaleDownAuditReport(
            resource_group="rg", subscription_id="sub", violations=[v],
        )
        assert report.has_violations is True

    def test_has_violations_false(self) -> None:
        report = ScaleDownAuditReport(resource_group="rg", subscription_id="sub")
        assert report.has_violations is False

    def test_to_dict_counts(self) -> None:
        v = ScaleDownViolation(
            resource_name="r", resource_type="t", resource_id="id",
            location="eastus", sku="S1", recommendation="rec",
        )
        report = ScaleDownAuditReport(
            resource_group="rg",
            subscription_id="sub",
            violations=[v],
            compliant=["a", "b"],
            skipped=["c"],
            total_resources=4,
        )
        d = report.to_dict()
        assert d["violation_count"] == 1
        assert d["compliant_count"] == 2
        assert d["skipped_count"] == 1
        assert d["total_resources"] == 4

    def test_format_issue_body_contains_resource_name(self) -> None:
        v = ScaleDownViolation(
            resource_name="redis-dev",
            resource_type="Microsoft.Cache/Redis",
            resource_id="/subs/sub/rg/rg/providers/Microsoft.Cache/Redis/redis-dev",
            location="eastus",
            sku="C1",
            recommendation="Use in-process caching.",
            alternatives=["functools.lru_cache"],
        )
        report = ScaleDownAuditReport(
            resource_group="rg-test",
            subscription_id="sub-123",
            violations=[v],
            total_resources=5,
        )
        body = report.format_issue_body(environment="dev")
        assert "redis-dev" in body
        assert "Microsoft.Cache/Redis" in body
        assert "functools.lru_cache" in body
        assert "rg-test" in body
        assert "dev" in body

    def test_format_issue_body_no_violations(self) -> None:
        report = ScaleDownAuditReport(
            resource_group="rg-clean", subscription_id="sub-123", total_resources=3,
        )
        body = report.format_issue_body()
        assert "rg-clean" in body
        assert "Violations found:** 0" in body


# ====================================================================
# ScaleDownAuditor._classify
# ====================================================================

class TestScaleDownAuditorClassify:
    """Unit tests for classification logic — no AzureSDKClient calls."""

    def _auditor(self) -> ScaleDownAuditor:
        auditor = ScaleDownAuditor.__new__(ScaleDownAuditor)
        auditor.resource_group = "rg-test"
        auditor.subscription_id = "sub-123"
        return auditor

    # ---- Function Apps (Microsoft.Web/sites) ----

    def test_function_app_compliant(self) -> None:
        r = _resource("func-app", "Microsoft.Web/sites")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- App Service Plans ----

    def test_app_service_plan_consumption_compliant(self) -> None:
        r = _resource("plan-y1", "Microsoft.Web/serverFarms", sku="Y1")
        result = self._auditor()._classify(r)
        assert result is True

    def test_app_service_plan_premium_violation(self) -> None:
        r = _resource("plan-ep1", "Microsoft.Web/serverFarms", sku="EP1")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert result.resource_name == "plan-ep1"
        assert "Consumption" in result.recommendation

    def test_app_service_plan_standard_violation(self) -> None:
        r = _resource("plan-s1", "Microsoft.Web/serverFarms", sku="S1")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)

    def test_app_service_plan_empty_sku_violation(self) -> None:
        r = _resource("plan-unknown", "Microsoft.Web/serverFarms", sku="")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)

    # ---- VMs ----

    def test_vm_always_violation(self) -> None:
        r = _resource("vm-dev", "Microsoft.Compute/virtualMachines")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert "auto-shutdown" in result.recommendation or "deallocate" in result.recommendation

    def test_vmss_compliant(self) -> None:
        r = _resource("vmss-dev", "Microsoft.Compute/virtualMachineScaleSets")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- Databases ----

    def test_sql_serverless_compliant(self) -> None:
        r = _resource("sql-db", "Microsoft.Sql/servers/databases", sku="ServerlessGP")
        result = self._auditor()._classify(r)
        assert result is True

    def test_sql_standard_violation(self) -> None:
        r = _resource("sql-db", "Microsoft.Sql/servers/databases", sku="S2")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert "Serverless" in result.recommendation

    def test_cosmosdb_provisioned_violation(self) -> None:
        r = _resource("cosmos-db", "Microsoft.DocumentDB/databaseAccounts", sku="Standard")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert "Serverless" in result.recommendation

    # ---- Service Bus ----

    def test_service_bus_violation(self) -> None:
        r = _resource("sb-ns", "Microsoft.ServiceBus/namespaces", sku="Standard")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert len(result.alternatives) > 0

    # ---- APIM Consumption ----

    def test_apim_consumption_compliant(self) -> None:
        r = _resource("apim-dev", "Microsoft.ApiManagement/service", sku="Consumption")
        result = self._auditor()._classify(r)
        assert result is True

    def test_apim_developer_violation(self) -> None:
        r = _resource("apim-dev", "Microsoft.ApiManagement/service", sku="Developer")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert "Consumption" in result.recommendation

    # ---- Container Registry ----

    def test_container_registry_violation(self) -> None:
        r = _resource("acr-dev", "Microsoft.ContainerRegistry/registries", sku="Basic")
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)

    # ---- Cognitive Services (AI) ----

    def test_cognitive_services_compliant(self) -> None:
        r = _resource("oai-dev", "Microsoft.CognitiveServices/accounts", sku="S0")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- Storage Account ----

    def test_storage_account_compliant(self) -> None:
        r = _resource("staosfuncdev", "Microsoft.Storage/storageAccounts", sku="Standard_LRS")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- Key Vault ----

    def test_keyvault_compliant(self) -> None:
        r = _resource("kv-aos-dev", "Microsoft.KeyVault/vaults")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- Managed Identity ----

    def test_managed_identity_compliant(self) -> None:
        r = _resource("id-func-dev", "Microsoft.ManagedIdentity/userAssignedIdentities")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- Log Analytics / Application Insights ----

    def test_log_analytics_compliant(self) -> None:
        r = _resource("law-aos-dev", "Microsoft.OperationalInsights/workspaces")
        result = self._auditor()._classify(r)
        assert result is True

    def test_app_insights_compliant(self) -> None:
        r = _resource("appi-aos-dev", "Microsoft.Insights/components")
        result = self._auditor()._classify(r)
        assert result is True

    # ---- AML Serverless Endpoint ----

    def test_aml_serverless_endpoint_compliant(self) -> None:
        r = _resource(
            "ep-lora-dev",
            "Microsoft.MachineLearningServices/workspaces/serverlessEndpoints",
        )
        result = self._auditor()._classify(r)
        assert result is True

    # ---- AML Online Endpoint (violation) ----

    def test_aml_online_endpoint_violation(self) -> None:
        r = _resource(
            "ep-ceo-agent",
            "Microsoft.MachineLearningServices/workspaces/onlineEndpoints",
        )
        result = self._auditor()._classify(r)
        assert isinstance(result, ScaleDownViolation)
        assert "minimumReplicaCount" in result.recommendation or "serverless" in result.recommendation.lower()
        assert len(result.alternatives) > 0

    # ---- Unknown resource type ----

    def test_unknown_resource_type_skipped(self) -> None:
        r = _resource("mystery", "Microsoft.Unknown/resources")
        result = self._auditor()._classify(r)
        assert result is None


# ====================================================================
# ScaleDownAuditor.audit (integration with mocked SDK)
# ====================================================================

class TestScaleDownAuditorAudit:
    """End-to-end audit tests with mocked AzureSDKClient."""

    def test_audit_all_compliant(self) -> None:
        resources = [
            _resource("func-app", "Microsoft.Web/sites"),
            _resource("aci-group", "Microsoft.ContainerInstance/containerGroups"),
        ]
        auditor = _make_auditor(resources)
        report = auditor.audit()
        assert report.has_violations is False
        assert len(report.compliant) == 2
        assert report.total_resources == 2

    def test_audit_detects_violation(self) -> None:
        resources = [
            _resource("func-app", "Microsoft.Web/sites"),
            _resource("sb-ns", "Microsoft.ServiceBus/namespaces", sku="Standard"),
        ]
        auditor = _make_auditor(resources)
        report = auditor.audit()
        assert report.has_violations is True
        assert len(report.violations) == 1
        assert report.violations[0].resource_name == "sb-ns"

    def test_audit_skips_unknown_types(self) -> None:
        resources = [
            _resource("mystery", "Microsoft.Unknown/resources"),
        ]
        auditor = _make_auditor(resources)
        report = auditor.audit()
        assert len(report.skipped) == 1
        assert not report.has_violations

    def test_audit_mixed_results(self) -> None:
        resources = [
            _resource("func-app", "Microsoft.Web/sites"),              # compliant
            _resource("sb-ns", "Microsoft.ServiceBus/namespaces"),    # violation
            _resource("mystery", "Microsoft.Unknown/resources"),       # skipped
            _resource("vm-01", "Microsoft.Compute/virtualMachines"),   # violation
        ]
        auditor = _make_auditor(resources)
        report = auditor.audit()
        assert len(report.compliant) == 1
        assert len(report.violations) == 2
        assert len(report.skipped) == 1
        assert report.total_resources == 4

    def test_audit_report_to_dict_structure(self) -> None:
        resources = [
            _resource("redis-dev", "Microsoft.Cache/Redis", sku="C1"),
        ]
        auditor = _make_auditor(resources)
        report = auditor.audit()
        d = report.to_dict()
        assert d["resource_group"] == "rg-test"
        assert d["subscription_id"] == "sub-123"
        assert d["violation_count"] == 1
        assert d["violations"][0]["resource_name"] == "redis-dev"
