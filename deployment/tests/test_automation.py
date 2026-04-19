"""Tests for the Automation pillar and integration bridges.

Covers:
- AutomationConfig defaults and custom values
- PipelineManager stage invocations
- LifecycleManager operations (deprovision, modify, upgrade, scale)
- SDKBridge availability and status queries
- KernelBridge output translation and config validation
- InfrastructureManager.automate() integration
- Updated deploy.py CLI subcommands (govern, reliability, automate, lifecycle)
"""

from __future__ import annotations

import argparse
import json
import subprocess
from unittest import mock

import pytest

from orchestrator.automation.lifecycle import (
    LifecycleManager,
    LifecycleOperation,
    LifecycleResult,
    _SKU_PROPERTY_MAP,
    _PATCHABLE_TYPES,
)
from orchestrator.automation.pipeline import PipelineManager
from orchestrator.core.config import (
    AutomationConfig,
    DeploymentConfig,
    GovernanceConfig,
    ReliabilityConfig,
)
from orchestrator.core.manager import InfrastructureManager
from orchestrator.integration.kernel_bridge import KernelBridge, _OUTPUT_TO_ENV_MAP
from orchestrator.integration.sdk_bridge import SDKBridge, AppDeploymentStatus


# ====================================================================
# AutomationConfig — unit tests
# ====================================================================


class TestAutomationConfig:
    def test_defaults(self) -> None:
        ac = AutomationConfig()
        assert ac.deploy_function_apps is False
        assert ac.app_names == []
        assert ac.sync_kernel_config is False
        assert ac.enable_lifecycle_ops is False
        assert ac.target_version == ""
        assert ac.scale_overrides == {}
        assert ac.region_shift_target == ""

    def test_custom_values(self) -> None:
        ac = AutomationConfig(
            deploy_function_apps=True,
            app_names=["aos-dispatcher", "aos-realm-of-agents"],
            sync_kernel_config=True,
            enable_lifecycle_ops=True,
            target_version="5.0.0",
            scale_overrides={"func-aos-dispatcher-dev": "EP2"},
            region_shift_target="westeurope",
        )
        assert ac.deploy_function_apps is True
        assert len(ac.app_names) == 2
        assert ac.sync_kernel_config is True
        assert ac.target_version == "5.0.0"
        assert ac.region_shift_target == "westeurope"


class TestDeploymentConfigAllPillars:
    """Verify that all three pillar configs are present in DeploymentConfig."""

    def test_all_three_pillars_default(self) -> None:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
        )
        assert isinstance(cfg.governance, GovernanceConfig)
        assert isinstance(cfg.automation, AutomationConfig)
        assert isinstance(cfg.reliability, ReliabilityConfig)

    def test_automation_embedded(self) -> None:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            automation=AutomationConfig(deploy_function_apps=True),
        )
        assert cfg.automation.deploy_function_apps is True

    def test_from_args_includes_automation(self) -> None:
        ns = argparse.Namespace(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            location_ml="",
            template="t.bicep",
            parameters="",
            subscription_id="",
            git_sha="",
            allow_warnings=False,
            skip_health=False,
            no_confirm_deletes=False,
            # governance
            enforce_policies=False,
            budget_amount=0,
            required_tags={},
            review_rbac=False,
            # automation
            deploy_function_apps=True,
            sync_kernel_config=True,
            enable_lifecycle_ops=False,
            region_shift_target="",
            # reliability
            enable_drift_detection=False,
            check_dr_readiness=False,
        )
        cfg = DeploymentConfig.from_args(ns)
        assert cfg.automation.deploy_function_apps is True
        assert cfg.automation.sync_kernel_config is True


# ====================================================================
# PipelineManager — unit tests
# ====================================================================


class TestPipelineManager:
    @pytest.fixture()
    def pm(self) -> PipelineManager:
        return PipelineManager(
            resource_group="rg-test",
            environment="dev",
            location="eastus",
            template="deployment/main-modular.bicep",
        )

    @mock.patch.object(PipelineManager, "_run")
    def test_lint_success(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert pm.lint() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_lint_failure(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "lint error")
        assert pm.lint() is False

    def test_lint_no_template(self) -> None:
        pm = PipelineManager(
            resource_group="rg-test",
            environment="dev",
            location="eastus",
            template="",
        )
        assert pm.lint() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_validate_success(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        assert pm.validate() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_what_if_success(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        assert pm.what_if() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_what_if_changes_detected_exit_code_2(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        # Azure CLI 2.57+ returns exit code 2 when changes are detected — treat as success.
        mock_run.return_value = subprocess.CompletedProcess([], 2, "~ Modify resource", "")
        assert pm.what_if() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_what_if_genuine_failure(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "ResourceGroupNotFound")
        assert pm.what_if() is False

    @mock.patch.object(PipelineManager, "_run")
    def test_what_if_rbac_permission_error_treated_as_warning(
        self, mock_run: mock.Mock, pm: PipelineManager
    ) -> None:
        # RBAC write permission errors during what-if must be treated as a warning,
        # not a hard failure, so deployment can still proceed.
        rbac_error = (
            "ERROR: InvalidTemplateDeployment - Authorization failed for template resource "
            "'4ba364ab-8231-5eeb-8556-704ba8b5ad9c' of type "
            "'Microsoft.Authorization/roleAssignments'. The client does not have permission "
            "to perform action 'Microsoft.Authorization/roleAssignments/write'."
        )
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", rbac_error)
        assert pm.what_if() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_what_if_rbac_authorization_failed_template_resource(
        self, mock_run: mock.Mock, pm: PipelineManager
    ) -> None:
        # "Authorization failed for template resource" pattern (stderr)
        rbac_error = "Authorization failed for template resource of type roleAssignments."
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", rbac_error)
        assert pm.what_if() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_deploy_success(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        assert pm.deploy() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_health_check_all_succeeded(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        resources = json.dumps([
            {"name": "r1", "state": "Succeeded"},
            {"name": "r2", "state": "Succeeded"},
        ])
        mock_run.return_value = subprocess.CompletedProcess([], 0, resources, "")
        assert pm.health_check() is True

    @mock.patch.object(PipelineManager, "_run")
    def test_health_check_one_failed(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        resources = json.dumps([
            {"name": "r1", "state": "Succeeded"},
            {"name": "r2", "state": "Failed"},
        ])
        mock_run.return_value = subprocess.CompletedProcess([], 0, resources, "")
        assert pm.health_check() is False

    @mock.patch.object(PipelineManager, "_run")
    def test_plan_runs_three_stages(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        assert pm.plan() is True
        assert mock_run.call_count == 3  # lint + validate + what-if

    @mock.patch.object(PipelineManager, "_run")
    def test_full_deploy_runs_all_stages(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps([{"name": "r1", "state": "Succeeded"}]), ""
        )
        assert pm.full_deploy() is True
        assert mock_run.call_count == 5  # lint + validate + what-if + deploy + health-check

    @mock.patch.object(PipelineManager, "_run")
    def test_full_deploy_skip_health(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        assert pm.full_deploy(skip_health=True) is True
        assert mock_run.call_count == 4  # no health-check

    @mock.patch.object(PipelineManager, "_run")
    def test_full_deploy_stops_on_failure(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "lint error")
        assert pm.full_deploy() is False
        assert mock_run.call_count == 1  # stopped after lint

    @mock.patch.object(PipelineManager, "_run")
    def test_full_deploy_allow_warnings(self, mock_run: mock.Mock, pm: PipelineManager) -> None:
        # lint fails but allow_warnings=True — should continue
        results = [
            subprocess.CompletedProcess([], 1, "", "lint warning"),  # lint
            subprocess.CompletedProcess([], 0, "{}", ""),  # validate
            subprocess.CompletedProcess([], 0, "{}", ""),  # what-if
            subprocess.CompletedProcess([], 0, "{}", ""),  # deploy
            subprocess.CompletedProcess([], 0, json.dumps([{"name": "r1", "state": "Succeeded"}]), ""),
        ]
        mock_run.side_effect = results
        assert pm.full_deploy(allow_warnings=True) is True
        assert mock_run.call_count == 5

    def test_deployment_cmd_includes_env(self, pm: PipelineManager) -> None:
        cmd = pm._deployment_cmd("create")
        cmd_str = " ".join(cmd)
        assert "environment=dev" in cmd_str
        assert "location=eastus" in cmd_str

    def test_deployment_cmd_with_valid_sha(self) -> None:
        pm = PipelineManager(
            resource_group="rg",
            environment="prod",
            location="westeurope",
            template="t.bicep",
            git_sha="abc1234def",
        )
        cmd = pm._deployment_cmd("create")
        assert any("gitSha" in p for p in cmd)

    def test_deployment_cmd_with_invalid_sha(self) -> None:
        pm = PipelineManager(
            resource_group="rg",
            environment="prod",
            location="westeurope",
            template="t.bicep",
            git_sha="not-a-sha!",
        )
        cmd = pm._deployment_cmd("create")
        assert not any("gitSha" in p for p in cmd)


# ====================================================================
# LifecycleManager — unit tests
# ====================================================================


class TestLifecycleManager:
    @pytest.fixture()
    def lm(self) -> LifecycleManager:
        return LifecycleManager("rg-test", "sub-123")

    def test_lifecycle_result_to_dict(self) -> None:
        r = LifecycleResult(
            operation=LifecycleOperation.DEPROVISION,
            resource_name="st1",
            resource_type="Microsoft.Storage/storageAccounts",
            success=True,
            message="deleted",
        )
        d = r.to_dict()
        assert d["operation"] == "deprovision"
        assert d["success"] is True
        assert d["resource_name"] == "st1"

    @mock.patch("subprocess.run")
    def test_deprovision_success(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        result = lm.deprovision("st1", "Microsoft.Storage/storageAccounts", confirm=False)
        assert result.success is True
        assert result.operation == LifecycleOperation.DEPROVISION

    @mock.patch("subprocess.run")
    def test_deprovision_failure(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "Resource not found")
        result = lm.deprovision("missing-st", "Microsoft.Storage/storageAccounts", confirm=False)
        assert result.success is False
        assert "Resource not found" in result.message

    @mock.patch("subprocess.run")
    def test_modify_success(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        result = lm.modify(
            "func-dispatcher",
            "microsoft.web/sites",
            {"properties.httpsOnly": True},
        )
        assert result.success is True
        assert result.operation == LifecycleOperation.MODIFY
        assert result.details == {"properties.httpsOnly": True}

    @mock.patch("subprocess.run")
    def test_upgrade_success(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        result = lm.upgrade(
            "storage-account-1",
            "microsoft.storage/storageaccounts",
            "Standard_ZRS",
        )
        assert result.success is True
        assert result.operation == LifecycleOperation.UPGRADE
        assert result.details["new_sku"] == "Standard_ZRS"

    @mock.patch("subprocess.run")
    def test_upgrade_failure(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "Invalid SKU")
        result = lm.upgrade("st1", "microsoft.storage/storageaccounts", "Premium_ZRS")
        assert result.success is False

    @mock.patch("subprocess.run")
    def test_scale_success_all_settings(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        result = lm.scale(
            "apim-aos-dev",
            "microsoft.apimanagement/service",
            {"sku.capacity": 2},
        )
        assert result.success is True
        assert result.operation == LifecycleOperation.SCALE

    @mock.patch("subprocess.run")
    def test_scale_partial_failure(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0, "{}", ""),   # first setting ok
            subprocess.CompletedProcess([], 1, "", "error"),  # second setting fails
        ]
        result = lm.scale(
            "sb-aos-dev",
            "microsoft.servicebus/namespaces",
            {"sku.capacity": 2, "properties.messagingUnits": 4},
        )
        assert result.success is False

    @mock.patch("subprocess.run")
    def test_list_lifecycle_candidates(self, mock_run: mock.Mock, lm: LifecycleManager) -> None:
        resources = [
            {"name": "st1", "type": "Microsoft.Storage/storageAccounts",
             "location": "eastus", "provisioningState": "Succeeded"},
            {"name": "sb1", "type": "Microsoft.ServiceBus/namespaces",
             "location": "eastus", "provisioningState": "Failed"},
        ]
        mock_run.return_value = subprocess.CompletedProcess([], 0, json.dumps(resources), "")
        candidates = lm.list_lifecycle_candidates()
        assert len(candidates) == 2
        # Storage supports upgrade
        st = next(c for c in candidates if c["name"] == "st1")
        assert st["supports_upgrade"] is True

    def test_sku_property_map_coverage(self) -> None:
        assert "microsoft.storage/storageaccounts" in _SKU_PROPERTY_MAP
        assert "microsoft.servicebus/namespaces" in _SKU_PROPERTY_MAP
        assert "microsoft.apimanagement/service" in _SKU_PROPERTY_MAP

    def test_patchable_types_coverage(self) -> None:
        assert "microsoft.web/sites" in _PATCHABLE_TYPES
        assert "microsoft.keyvault/vaults" in _PATCHABLE_TYPES


# ====================================================================
# SDKBridge — unit tests
# ====================================================================


class TestSDKBridge:
    @pytest.fixture()
    def bridge(self) -> SDKBridge:
        return SDKBridge(
            resource_group="rg-test",
            environment="dev",
            subscription_id="sub-123",
            location="eastus",
        )

    def test_is_sdk_available_when_absent(self, bridge: SDKBridge) -> None:
        # In the test environment the SDK may or may not be installed.
        # The method should return a bool without raising.
        result = SDKBridge.is_sdk_available()
        assert isinstance(result, bool)

    def test_deploy_function_apps_skipped_when_sdk_unavailable(
        self, bridge: SDKBridge
    ) -> None:
        with mock.patch.object(SDKBridge, "is_sdk_available", return_value=False):
            statuses = bridge.deploy_function_apps(["aos-dispatcher"])
        assert len(statuses) == 1
        assert statuses[0].status == "skipped"
        assert statuses[0].error is not None

    def test_app_deployment_status_defaults(self) -> None:
        s = AppDeploymentStatus(app_name="test")
        assert s.status == "unknown"
        assert s.url is None
        assert s.error is None

    @mock.patch("orchestrator.integration.sdk_bridge.DefaultAzureCredential")
    @mock.patch("orchestrator.integration.sdk_bridge.WebSiteManagementClient")
    def test_get_function_app_status_not_found(
        self, mock_web_client: mock.Mock, mock_cred: mock.Mock, bridge: SDKBridge
    ) -> None:
        mock_web_client.return_value.web_apps.get.side_effect = Exception("Not found")
        status = bridge.get_function_app_status("nonexistent-app")
        assert status.status == "unknown"

    @mock.patch("orchestrator.integration.sdk_bridge.DefaultAzureCredential")
    @mock.patch("orchestrator.integration.sdk_bridge.WebSiteManagementClient")
    def test_get_function_app_status_running(
        self, mock_web_client: mock.Mock, mock_cred: mock.Mock, bridge: SDKBridge
    ) -> None:
        mock_app = mock.MagicMock()
        mock_app.state = "Running"
        mock_app.default_host_name = "my-app.azurewebsites.net"
        mock_web_client.return_value.web_apps.get.return_value = mock_app
        status = bridge.get_function_app_status("my-app")
        assert status.status == "running"
        assert status.url == "https://my-app.azurewebsites.net"

    @mock.patch("orchestrator.integration.sdk_bridge.DefaultAzureCredential")
    @mock.patch("orchestrator.integration.sdk_bridge.WebSiteManagementClient")
    def test_sync_app_settings_success(
        self, mock_web_client: mock.Mock, mock_cred: mock.Mock, bridge: SDKBridge
    ) -> None:
        mock_web_client.return_value.web_apps.update_application_settings.return_value = None
        ok = bridge.sync_app_settings("my-app", {"KEY": "value"})
        assert ok is True

    def test_sync_app_settings_empty_is_noop(
        self, bridge: SDKBridge
    ) -> None:
        ok = bridge.sync_app_settings("my-app", {})
        assert ok is True

    @mock.patch("orchestrator.integration.sdk_bridge.DefaultAzureCredential")
    @mock.patch("orchestrator.integration.sdk_bridge.WebSiteManagementClient")
    def test_get_aos_endpoint(
        self, mock_web_client: mock.Mock, mock_cred: mock.Mock, bridge: SDKBridge
    ) -> None:
        mock_app = mock.MagicMock()
        mock_app.name = "fa-agent-operating-dev-abc123"
        mock_app.default_host_name = "fa-agent-operating-dev-abc123.azurewebsites.net"
        mock_web_client.return_value.web_apps.list_by_resource_group.return_value = [mock_app]
        endpoint = bridge.get_aos_endpoint()
        assert endpoint == "https://fa-agent-operating-dev-abc123.azurewebsites.net"
        # Verify the SDK was called with the right resource group
        mock_web_client.return_value.web_apps.list_by_resource_group.assert_called_once_with(
            bridge.resource_group
        )

    def test_default_app_names_includes_mcp_servers(self) -> None:
        """All four MCP server submodules must be present in the default app names list."""
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES, _MCP_SERVER_APPS
        for app in _MCP_SERVER_APPS:
            assert app in _DEFAULT_APP_NAMES, f"'{app}' missing from _DEFAULT_APP_NAMES"

    def test_default_app_names_includes_agent_operating_system(self) -> None:
        """agent-operating-system is the single AOS Function App and must appear in _DEFAULT_APP_NAMES."""
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES
        assert "agent-operating-system" in _DEFAULT_APP_NAMES, (
            "'agent-operating-system' missing from _DEFAULT_APP_NAMES — "
            "it is the deployable Azure Function App that utilizes aos-kernel, "
            "aos-intelligence, aos-client-sdk, and aos-dispatcher as libraries"
        )

    def test_code_only_repos_not_in_default_app_names(self) -> None:
        """Code-only library repos must NOT appear as Function Apps in _DEFAULT_APP_NAMES.

        aos-kernel, aos-intelligence, aos-client-sdk, and aos-dispatcher are Python
        packages imported by agent-operating-system at runtime — they have no Azure
        infrastructure of their own.
        """
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES
        code_only_repos = ["aos-kernel", "aos-intelligence", "aos-client-sdk", "aos-dispatcher"]
        for repo in code_only_repos:
            assert repo not in _DEFAULT_APP_NAMES, (
                f"'{repo}' is a code-only library repo and must not appear in _DEFAULT_APP_NAMES — "
                "it is consumed by agent-operating-system, not deployed as a standalone Function App"
            )

    def test_default_app_names_mcp_names_are_azure_safe(self) -> None:
        """MCP server app names must not contain dots (Azure resource naming constraint)."""
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES
        mcp_server_apps = [a for a in _DEFAULT_APP_NAMES if a.startswith("mcp-")]
        assert len(mcp_server_apps) == 4
        for app in mcp_server_apps:
            assert "." not in app, f"'{app}' contains a dot — invalid Azure resource name"

    def test_mcp_server_github_repos_are_full_domains(self) -> None:
        """MCP server githubRepo values are full domain names (used directly as custom domains).

        _MCP_SERVER_APPS is the single Python-side source of truth and mirrors
        the mcpServerApps default in main-modular.bicep.
        """
        from orchestrator.integration.sdk_bridge import _MCP_SERVER_APPS, _BASE_DOMAIN
        for app_name, github_repo in _MCP_SERVER_APPS.items():
            assert "." in github_repo, (
                f"{app_name}: githubRepo '{github_repo}' is not a full domain — "
                "MCP server repos must be full domain names so they can be used as custom domains"
            )
            assert github_repo.endswith(f".{_BASE_DOMAIN}"), (
                f"{app_name}: githubRepo '{github_repo}' does not match expected *.{_BASE_DOMAIN} pattern"
            )

    def test_standard_app_custom_domain_convention(self) -> None:
        """Standard AOS apps get a custom domain of <appName>.<_BASE_DOMAIN>.

        _BASE_DOMAIN mirrors the baseDomain default in main-modular.bicep.
        """
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES, _MCP_SERVER_APPS, _BASE_DOMAIN
        standard_apps = [a for a in _DEFAULT_APP_NAMES if a not in _MCP_SERVER_APPS]
        assert len(standard_apps) > 0, "Expected at least one non-MCP app in _DEFAULT_APP_NAMES"
        for app in standard_apps:
            # appName must be dot-free so the derived domain is well-formed
            assert "." not in app, f"Standard app '{app}' must not contain dots"
            derived_domain = f"{app}.{_BASE_DOMAIN}"
            assert derived_domain.endswith(f".{_BASE_DOMAIN}")
            # domain must be a valid-looking hostname (at least two dots for a subdomain)
            assert derived_domain.count(".") >= 2, f"Derived domain '{derived_domain}' has too few labels"

    def test_foundry_app_names_are_csuite_agents(self) -> None:
        """_FOUNDRY_APP_NAMES must list all five C-suite agents — mirrors foundryAppNames in main-modular.bicep."""
        from orchestrator.integration.sdk_bridge import _FOUNDRY_APP_NAMES
        expected = {"ceo-agent", "cfo-agent", "cto-agent", "cso-agent", "cmo-agent"}
        assert set(_FOUNDRY_APP_NAMES) == expected, (
            f"_FOUNDRY_APP_NAMES {_FOUNDRY_APP_NAMES} does not match expected C-suite agents {expected}"
        )

    def test_foundry_apps_not_in_default_app_names(self) -> None:
        """C-suite agents are Foundry-hosted, not Function Apps — must not appear in _DEFAULT_APP_NAMES."""
        from orchestrator.integration.sdk_bridge import _DEFAULT_APP_NAMES, _FOUNDRY_APP_NAMES
        for agent in _FOUNDRY_APP_NAMES:
            assert agent not in _DEFAULT_APP_NAMES, (
                f"'{agent}' is in _DEFAULT_APP_NAMES but should be in _FOUNDRY_APP_NAMES only — "
                "C-suite agents are Foundry Agent Service endpoints, not Function Apps"
            )

    def test_function_app_hostname_bindings_do_not_set_host_name_type(self) -> None:
        """Azure hostname binding resources must not set unsupported hostNameType properties."""
        from pathlib import Path

        deployment_root = Path(__file__).resolve().parent.parent
        for relative_path in (
            "modules/functionapp.bicep",
            "modules/functionapp-ssl.bicep",
        ):
            template = (deployment_root / relative_path).read_text(encoding="utf-8")
            assert "hostNameType:" not in template, (
                f"{relative_path} must not set hostNameType on Microsoft.Web/sites/hostNameBindings "
                "resources because Azure rejects that property during Function App provisioning"
            )

    def test_online_deployment_sku_is_default_not_provisioned(self) -> None:
        """ManagedOnlineDeployment resources must use sku.name 'Default', not 'Provisioned'.

        'Provisioned' is the SKU for Azure OpenAI / serverless Provisioned Throughput endpoints.
        When set on a ManagedOnlineDeployment (endpointComputeType: 'Managed'), Azure ML's
        management frontend returns BadRequest: The request is invalid.
        The correct SKU for VM-backed managed online deployments is 'Default'.
        """
        from pathlib import Path

        deployment_root = Path(__file__).resolve().parent.parent
        for relative_path in (
            "modules/lora-inference.bicep",
            "modules/foundry-app.bicep",
        ):
            template = (deployment_root / relative_path).read_text(encoding="utf-8")
            assert "name: 'Provisioned'" not in template, (
                f"{relative_path} must not set sku.name to 'Provisioned' on "
                "ManagedOnlineDeployment resources — use 'Default' instead. "
                "'Provisioned' is only valid for serverless/PT endpoints and causes "
                "BadRequest from Azure ML management frontend when used with endpointComputeType: 'Managed'."
            )

    def test_online_endpoint_name_uses_per_agent_hash_for_global_uniqueness(self) -> None:
        """Azure ML online endpoint names must be globally unique within a region.

        foundry-app.bicep uses a per-agent hash (includes appName) for its endpoint name.
        lora-inference.bicep creates a single shared endpoint — its suffix is derived from
        project/environment only (no appName), which is correct because there is only one
        endpoint and no collision risk with other per-agent names.
        """
        import re
        from pathlib import Path

        deployment_root = Path(__file__).resolve().parent.parent

        # foundry-app.bicep creates one endpoint per agent — must include appName in the hash.
        template = (deployment_root / "modules/foundry-app.bicep").read_text(encoding="utf-8")

        assert "uniqueString(resourceGroup().id, projectName, environment, appName)" in template, (
            "modules/foundry-app.bicep must derive the endpoint suffix from a per-agent hash that "
            "includes appName: uniqueString(resourceGroup().id, projectName, environment, appName). "
            "Azure ML endpoint names are globally unique per region — a hash that omits appName "
            "produces identical suffixes for all agents and is not unique enough to avoid collisions."
        )

        # Must take at least 8 characters for adequate entropy (≥8 base-36 chars ≈ 4×10¹²).
        match = re.search(r"take\(uniqueString\(resourceGroup\(\)\.id.*?appName\),\s*(\d+)\)", template)
        assert match is not None, (
            "modules/foundry-app.bicep: could not find take(uniqueString(...appName...), N) pattern"
        )
        suffix_len = int(match.group(1))
        assert suffix_len >= 8, (
            f"modules/foundry-app.bicep takes only {suffix_len} chars from the endpoint suffix — "
            "use at least 8 to reduce cross-region name collision probability."
        )

        # Endpoint name must not embed projectName in the visible portion.
        assert "ep-${appName}-${projectName}-${environment}" not in template, (
            "modules/foundry-app.bicep must not include projectName in the visible endpoint name — "
            "it inflates the name length unnecessarily (projectName is already in the hash)."
        )

        # lora-inference.bicep creates a SHARED endpoint — suffix must NOT include appName.
        lora_template = (deployment_root / "modules/lora-inference.bicep").read_text(encoding="utf-8")
        assert "uniqueString(resourceGroup().id, projectName, environment)" in lora_template, (
            "modules/lora-inference.bicep must derive the shared endpoint suffix from "
            "uniqueString(resourceGroup().id, projectName, environment) — no appName — "
            "because there is exactly one shared endpoint for all C-suite agents."
        )
        # Shared endpoint name must embed 'lora-shared' to make its purpose clear.
        assert "ep-lora-shared-" in lora_template, (
            "modules/lora-inference.bicep shared endpoint name must contain 'ep-lora-shared-' "
            "to distinguish it from per-agent foundry-app endpoints."
        )
        # Shared endpoint suffix must take at least 8 characters for adequate uniqueness.
        lora_match = re.search(
            r"take\(uniqueString\(resourceGroup\(\)\.id,\s*projectName,\s*environment\),\s*(\d+)\)",
            lora_template,
        )
        assert lora_match is not None, (
            "modules/lora-inference.bicep: could not find "
            "take(uniqueString(resourceGroup().id, projectName, environment), N) pattern"
        )
        lora_suffix_len = int(lora_match.group(1))
        assert lora_suffix_len >= 8, (
            f"modules/lora-inference.bicep takes only {lora_suffix_len} chars from the shared "
            "endpoint suffix — use at least 8 to reduce cross-region name collision probability."
        )

        # Verify that the longest possible foundry-app endpoint name
        # (cso-agent / cmo-agent + 'staging' + 8-char suffix) stays within the 32-char limit.
        longest_agent = "cso-agent"   # 9 chars — same as ceo/cfo/cto/cmo/cso
        longest_env = "staging"        # 7 chars
        suffix_chars = 8
        # Formula: 'ep-' + appName + '-' + environment + '-' + suffix
        max_len = len(f"ep-{longest_agent}-{longest_env}-{'x' * suffix_chars}")
        assert max_len <= 32, (
            f"Endpoint name with longest agent/env would be {max_len} chars — "
            "Azure ML requires names ≤ 32 characters."
        )
        # Shared LoRA endpoint: 'ep-lora-shared-' + environment + '-' + 8-char suffix
        lora_shared_max_len = len(f"ep-lora-shared-{longest_env}-{'x' * suffix_chars}")
        assert lora_shared_max_len <= 32, (
            f"Shared LoRA endpoint name with longest env would be {lora_shared_max_len} chars — "
            "Azure ML requires names ≤ 32 characters."
        )

    def test_lora_base_model_id_uses_correct_registry_version(self) -> None:
        """lora-inference.bicep must reference the Llama-3.3-70B-Instruct model (versions/9)
        from the azureml-meta registry — verified available in eastus2 for the
        fine-tuning / chat-completion LoRA adapter task.

        The model name is 'Llama-3.3-70B-Instruct' (no 'Meta-' prefix) and the
        verified available version is 9.
        """
        import re
        from pathlib import Path

        deployment_root = Path(__file__).resolve().parent.parent
        template = (deployment_root / "modules/lora-inference.bicep").read_text(encoding="utf-8")

        # Must NOT use the old 'Meta-' prefixed name — that asset is not available.
        assert "Meta-Llama-3.3-70B-Instruct" not in template, (
            "lora-inference.bicep must not reference 'Meta-Llama-3.3-70B-Instruct' — "
            "the correct model name in the azureml-meta registry is 'Llama-3.3-70B-Instruct' "
            "(without the 'Meta-' prefix)."
        )

        # Must reference the verified-available version 9.
        assert re.search(r"Llama-3\.3-70B-Instruct/versions/9\b", template), (
            "lora-inference.bicep must reference Llama-3.3-70B-Instruct/versions/9 "
            "from the azureml-meta registry — this is the version verified available "
            "in eastus2 for fine-tuning / chat-completion LoRA adapter creation."
        )

        # Must still use the azureml-meta registry.
        assert "azureml://registries/azureml-meta/models/Llama-3.3-70B-Instruct" in template, (
            "lora-inference.bicep must reference Llama-3.3-70B-Instruct from the "
            "azureml-meta registry."
        )


# ====================================================================
# KernelBridge — unit tests
# ====================================================================


class TestKernelBridge:
    @pytest.fixture()
    def kb(self) -> KernelBridge:
        return KernelBridge("rg-test", "main-deploy-dev", "sub-123")

    def test_output_to_env_map_coverage(self) -> None:
        assert "aiServicesEndpoint" in _OUTPUT_TO_ENV_MAP
        assert "aiProjectDiscoveryUrl" in _OUTPUT_TO_ENV_MAP
        assert "keyVaultName" in _OUTPUT_TO_ENV_MAP
        assert "serviceBusNamespace" in _OUTPUT_TO_ENV_MAP
        # Shared LoRA endpoint outputs are now scalar strings (single endpoint for all agents).
        assert "loraInferenceEndpointName" in _OUTPUT_TO_ENV_MAP
        assert "loraInferenceScoringUri" in _OUTPUT_TO_ENV_MAP
        # Old per-agent array keys must NOT be present — they were replaced by scalar outputs.
        assert "loraInferenceEndpointNames" not in _OUTPUT_TO_ENV_MAP
        assert "loraInferenceScoringUris" not in _OUTPUT_TO_ENV_MAP
        assert "modelRegistryName" not in _OUTPUT_TO_ENV_MAP

    def test_translate_outputs_standard(self, kb: KernelBridge) -> None:
        outputs = {
            "aiServicesEndpoint": {"value": "https://ai.example.com"},
            "keyVaultName": {"value": "kv-aos-dev"},
            "serviceBusNamespace": {"value": "sb-aos-dev"},
            "storageAccountName": {"value": "staoasdev"},
            "aiProjectDiscoveryUrl": {"value": "https://proj.foundry.com"},
        }
        env_vars = KernelBridge._translate_outputs(outputs)
        assert env_vars["AZURE_AI_SERVICES_ENDPOINT"] == "https://ai.example.com"
        assert env_vars["KEY_VAULT_NAME"] == "kv-aos-dev"
        assert env_vars["SERVICE_BUS_NAMESPACE"] == "sb-aos-dev"
        assert env_vars["AOS_STORAGE_ACCOUNT"] == "staoasdev"
        assert env_vars["FOUNDRY_PROJECT_ENDPOINT"] == "https://proj.foundry.com"

    def test_translate_outputs_empty(self, kb: KernelBridge) -> None:
        env_vars = KernelBridge._translate_outputs({})
        assert env_vars == {}

    def test_translate_outputs_lora_shared_endpoint(self, kb: KernelBridge) -> None:
        """Shared LoRA endpoint outputs are translated as scalar strings.

        All C-suite agents share a single endpoint; per-agent adapter selection
        happens via adapter_id in the scoring request body at inference time.
        """
        outputs = {
            "loraInferenceEndpointName": {
                "value": "ep-lora-shared-prod-abcd1234",
            },
            "loraInferenceScoringUri": {
                "value": "https://ep-lora-shared-prod-abcd1234.eastus2.inference.ml.azure.com/score",
            },
        }
        env_vars = KernelBridge._translate_outputs(outputs)
        assert env_vars["LORA_INFERENCE_ENDPOINT_NAME"] == "ep-lora-shared-prod-abcd1234"
        assert env_vars["LORA_INFERENCE_SCORING_URI"] == (
            "https://ep-lora-shared-prod-abcd1234.eastus2.inference.ml.azure.com/score"
        )

    def test_translate_outputs_old_per_agent_lora_arrays_are_ignored(self, kb: KernelBridge) -> None:
        """Old per-agent array LoRA keys must not produce any env vars.

        The previous architecture emitted loraInferenceEndpointNames (array) and
        loraInferenceScoringUris (array); these were replaced by scalar outputs.
        If old-format data is present (e.g. from a stale deployment), it must be
        silently ignored — no LORA_INFERENCE_ENDPOINT_NAMES or LORA_INFERENCE_SCORING_URIS
        keys should appear in the translated env vars.
        """
        outputs = {
            "loraInferenceEndpointNames": {
                "value": ["ep-ceo-agent-prod-xyz", "ep-cfo-agent-prod-xyz"],
            },
            "loraInferenceScoringUris": {
                "value": [
                    "https://ep-ceo-agent-prod-xyz.eastus2.inference.ml.azure.com/score",
                    "https://ep-cfo-agent-prod-xyz.eastus2.inference.ml.azure.com/score",
                ],
            },
        }
        env_vars = KernelBridge._translate_outputs(outputs)
        assert "LORA_INFERENCE_ENDPOINT_NAMES" not in env_vars
        assert "LORA_INFERENCE_SCORING_URIS" not in env_vars

    def test_validate_kernel_config_all_present(self, kb: KernelBridge) -> None:
        env_vars = {
            "AZURE_AI_SERVICES_ENDPOINT": "https://ai.example.com",
            "FOUNDRY_PROJECT_ENDPOINT": "https://proj.foundry.com",
            "KEY_VAULT_NAME": "kv-aos-dev",
            "SERVICE_BUS_NAMESPACE": "sb-aos-dev",
            "AOS_STORAGE_ACCOUNT": "staoasdev",
        }
        result = kb.validate_kernel_config(env_vars)
        assert result["missing"] == []
        assert len(result["present"]) == 5

    def test_validate_kernel_config_missing(self, kb: KernelBridge) -> None:
        env_vars = {
            "AZURE_AI_SERVICES_ENDPOINT": "https://ai.example.com",
        }
        result = kb.validate_kernel_config(env_vars)
        assert len(result["missing"]) > 0

    @mock.patch.object(KernelBridge, "_get_deployment_outputs")
    def test_extract_kernel_env_with_outputs(
        self, mock_outputs: mock.Mock, kb: KernelBridge
    ) -> None:
        mock_outputs.return_value = {
            "keyVaultName": {"value": "kv-test"},
            "serviceBusNamespace": {"value": "sb-test"},
        }
        env = kb.extract_kernel_env()
        assert env["KEY_VAULT_NAME"] == "kv-test"
        assert env["SERVICE_BUS_NAMESPACE"] == "sb-test"

    @mock.patch.object(KernelBridge, "_get_deployment_outputs")
    def test_extract_kernel_env_no_outputs(
        self, mock_outputs: mock.Mock, kb: KernelBridge
    ) -> None:
        mock_outputs.return_value = None
        env = kb.extract_kernel_env()
        assert env == {}

    def test_write_env_file(self, tmp_path, kb: KernelBridge) -> None:
        env_path = str(tmp_path / ".env")
        env_vars = {"KEY_VAULT_NAME": "kv-test", "SERVICE_BUS_NAMESPACE": "sb-test"}
        ok = kb.write_env_file(env_vars, env_path)
        assert ok is True
        content = (tmp_path / ".env").read_text()
        assert "KEY_VAULT_NAME=kv-test" in content
        assert "SERVICE_BUS_NAMESPACE=sb-test" in content


# ====================================================================
# InfrastructureManager.automate() — integration tests
# ====================================================================


class TestInfrastructureManagerAutomate:
    @pytest.fixture()
    def manager(self) -> InfrastructureManager:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
            automation=AutomationConfig(
                deploy_function_apps=False,
                sync_kernel_config=False,
            ),
        )
        return InfrastructureManager(cfg)

    def test_automate_method_exists(self, manager: InfrastructureManager) -> None:
        assert callable(manager.automate)

    @mock.patch("orchestrator.automation.pipeline.PipelineManager.full_deploy")
    def test_automate_pipeline_success(
        self, mock_pipeline: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_pipeline.return_value = True
        result = manager.automate()
        assert result is True

    @mock.patch("orchestrator.automation.pipeline.PipelineManager.full_deploy")
    def test_automate_pipeline_failure(
        self, mock_pipeline: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_pipeline.return_value = False
        result = manager.automate()
        assert result is False

    @mock.patch("orchestrator.integration.sdk_bridge.SDKBridge.deploy_function_apps")
    @mock.patch("orchestrator.automation.pipeline.PipelineManager.full_deploy")
    def test_automate_with_sdk_bridge(
        self,
        mock_pipeline: mock.Mock,
        mock_sdk: mock.Mock,
    ) -> None:
        mock_pipeline.return_value = True
        mock_sdk.return_value = [
            AppDeploymentStatus(app_name="aos-dispatcher", status="succeeded"),
        ]
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
            automation=AutomationConfig(deploy_function_apps=True),
        )
        mgr = InfrastructureManager(cfg)
        result = mgr.automate()
        assert result is True
        mock_sdk.assert_called_once()

    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.extract_kernel_env")
    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.validate_kernel_config")
    @mock.patch("orchestrator.automation.pipeline.PipelineManager.full_deploy")
    def test_automate_with_kernel_sync(
        self,
        mock_pipeline: mock.Mock,
        mock_validate: mock.Mock,
        mock_extract: mock.Mock,
    ) -> None:
        mock_pipeline.return_value = True
        mock_extract.return_value = {"KEY_VAULT_NAME": "kv-test"}
        mock_validate.return_value = {"present": ["KEY_VAULT_NAME"], "missing": []}
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
            automation=AutomationConfig(sync_kernel_config=True),
        )
        mgr = InfrastructureManager(cfg)
        result = mgr.automate()
        assert result is True
        mock_extract.assert_called_once()


# ====================================================================
# deploy.py CLI — subcommand parsing tests
# ====================================================================


class TestDeployCLI:
    """Verify the CLI parser accepts the new subcommands without error."""

    def test_import_deploy_py(self) -> None:
        """Ensure deploy.py can be imported without errors."""
        import importlib.util, sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "deploy",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert hasattr(mod, "_build_parser")
        assert hasattr(mod, "main")

    def test_parser_govern_subcommand(self) -> None:
        from pathlib import Path
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "deploy2",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod._build_parser()
        args = parser.parse_args([
            "govern",
            "--resource-group", "rg-test",
            "--environment", "dev",
            "--review-rbac",
        ])
        assert args.command == "govern"
        assert args.review_rbac is True

    def test_parser_reliability_subcommand(self) -> None:
        from pathlib import Path
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "deploy3",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod._build_parser()
        args = parser.parse_args([
            "reliability",
            "--resource-group", "rg-test",
            "--environment", "prod",
            "--enable-drift-detection",
            "--check-dr-readiness",
        ])
        assert args.command == "reliability"
        assert args.enable_drift_detection is True
        assert args.check_dr_readiness is True

    def test_parser_deprovision_subcommand(self) -> None:
        from pathlib import Path
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "deploy4",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod._build_parser()
        args = parser.parse_args([
            "deprovision",
            "--resource-group", "rg-test",
            "--resource-name", "st1",
            "--resource-type", "Microsoft.Storage/storageAccounts",
            "--yes",
        ])
        assert args.command == "deprovision"
        assert args.resource_name == "st1"
        assert args.yes is True

    def test_parser_upgrade_subcommand(self) -> None:
        from pathlib import Path
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "deploy5",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod._build_parser()
        args = parser.parse_args([
            "upgrade",
            "--resource-group", "rg-test",
            "--resource-name", "st1",
            "--resource-type", "microsoft.storage/storageaccounts",
            "--new-sku", "Standard_ZRS",
        ])
        assert args.command == "upgrade"
        assert args.new_sku == "Standard_ZRS"

    def test_parser_scale_subcommand(self) -> None:
        from pathlib import Path
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "deploy6",
            Path(__file__).resolve().parent.parent / "deploy.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod._build_parser()
        args = parser.parse_args([
            "scale",
            "--resource-group", "rg-test",
            "--resource-name", "apim-aos-dev",
            "--resource-type", "microsoft.apimanagement/service",
            "--scale-settings", '{"sku.capacity": 2}',
        ])
        assert args.command == "scale"
        assert args.scale_settings == {"sku.capacity": 2}
