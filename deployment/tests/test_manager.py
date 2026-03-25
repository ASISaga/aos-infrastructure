"""Tests for AOS deployment orchestrator.

Covers configuration creation, validation, regional validation, and
InfrastructureManager method signatures using mocked subprocess calls.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from orchestrator.core.config import DeploymentConfig
from orchestrator.core.manager import InfrastructureManager
from orchestrator.validators.regional_validator import RegionalValidator


# ====================================================================
# DeploymentConfig tests
# ====================================================================


class TestDeploymentConfig:
    """Tests for the DeploymentConfig model."""

    def test_create_minimal(self) -> None:
        cfg = DeploymentConfig(environment="dev", resource_group="rg-test", location="eastus", template="t.bicep")
        assert cfg.environment == "dev"
        assert cfg.location_ml == "eastus"  # auto-filled

    def test_create_full(self) -> None:
        cfg = DeploymentConfig(
            environment="prod",
            resource_group="rg-prod",
            location="westeurope",
            location_ml="northeurope",
            template="main.bicep",
            parameters_file="params.bicepparam",
            subscription_id="sub-123",
            git_sha="abc1234",
            allow_warnings=True,
            skip_health=True,
            dry_run=True,
        )
        assert cfg.location_ml == "northeurope"
        assert cfg.allow_warnings is True

    def test_invalid_environment(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentConfig(environment="beta", resource_group="rg", location="eastus", template="t.bicep")

    def test_empty_resource_group(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentConfig(environment="dev", resource_group="", location="eastus", template="t.bicep")

    def test_from_args(self) -> None:
        ns = argparse.Namespace(
            environment="staging",
            resource_group="rg-stg",
            location="westus2",
            location_ml="westus2",
            template="main.bicep",
            parameters="params.bicepparam",
            subscription_id="",
            git_sha="abc",
            allow_warnings=False,
            skip_health=False,
            no_confirm_deletes=True,
        )
        cfg = DeploymentConfig.from_args(ns)
        assert cfg.environment == "staging"
        assert cfg.dry_run is True


# ====================================================================
# RegionalValidator tests
# ====================================================================


class TestRegionalValidator:
    """Tests for regional validation logic."""

    def test_known_good_region(self) -> None:
        v = RegionalValidator()
        result = v.validate_region("eastus", ["storage", "keyvault"])
        assert all(result.values())

    def test_select_optimal_americas(self) -> None:
        v = RegionalValidator()
        regions = v.select_optimal_regions("dev", "americas")
        assert regions["primary"] == "eastus"

    def test_select_optimal_europe(self) -> None:
        v = RegionalValidator()
        regions = v.select_optimal_regions("dev", "europe")
        assert regions["primary"] == "westeurope"

    def test_select_optimal_asia(self) -> None:
        v = RegionalValidator()
        regions = v.select_optimal_regions("dev", "asia")
        assert regions["primary"] == "southeastasia"

    def test_get_region_summary(self) -> None:
        v = RegionalValidator()
        summary = v.get_region_summary("eastus", ["storage"])
        assert summary["storage"] == "Available"


# ====================================================================
# InfrastructureManager tests
# ====================================================================


class TestInfrastructureManager:
    """Tests for manager method wiring and subprocess calls."""

    @pytest.fixture()
    def manager(self) -> InfrastructureManager:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
        )
        return InfrastructureManager(cfg)

    def test_instantiation(self, manager: InfrastructureManager) -> None:
        assert manager.config.resource_group == "rg-test"

    @mock.patch.object(InfrastructureManager, "_run")
    def test_plan_calls_lint_validate_whatif(self, mock_run: mock.Mock, manager: InfrastructureManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = manager.plan()
        assert result is True
        # ensure_resource_group + lint + validate + what-if = 4 calls
        assert mock_run.call_count == 4

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_exit_code_2_is_success(self, mock_run: mock.Mock, manager: InfrastructureManager) -> None:
        # Azure CLI 2.57+ returns exit code 2 when changes are detected — treat as success.
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="+ Create resource", stderr="",
        )
        assert manager._what_if() is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_genuine_failure_returns_false(self, mock_run: mock.Mock, manager: InfrastructureManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ResourceGroupNotFound",
        )
        assert manager._what_if() is False

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_rbac_permission_error_treated_as_warning(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        # SP lacks Microsoft.Authorization/roleAssignments/write — what-if must not abort the pipeline.
        rbac_error = (
            "ERROR: InvalidTemplateDeployment - Authorization failed for template resource "
            "of type 'Microsoft.Authorization/roleAssignments'. The client does not have "
            "permission to perform action 'Microsoft.Authorization/roleAssignments/write'."
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=rbac_error,
        )
        assert manager._what_if() is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_parses_json_change_counts(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        # Ensure change counts are captured in _what_if_counts for the audit log.
        what_if_json = json.dumps({
            "changes": [
                {"changeType": "Create", "resourceId": "/subs/.../r1"},
                {"changeType": "Create", "resourceId": "/subs/.../r2"},
                {"changeType": "NoChange", "resourceId": "/subs/.../r3"},
                {"changeType": "Modify", "resourceId": "/subs/.../r4"},
            ]
        })
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=what_if_json, stderr="",
        )
        result = manager._what_if()
        assert result is True
        assert manager._what_if_counts["create"] == 2
        assert manager._what_if_counts["no_change"] == 1
        assert manager._what_if_counts["modify"] == 1

    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_whatif_exit_code_2_does_not_abort(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        # Simulate: rg_create=0, lint=0, validate=0, what-if=2 (changes detected), deploy=0
        results = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),  # ensure_resource_group
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),    # lint
            subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),  # validate
            subprocess.CompletedProcess(args=[], returncode=2, stdout="+ Create", stderr=""),  # what-if
            subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),  # deploy
        ]
        mock_run.side_effect = results
        with mock.patch.object(
            manager, "_az", return_value=json.dumps([{"name": "r", "state": "Succeeded"}])
        ):
            result = manager.deploy()
        assert result is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_calls_all_steps(self, mock_run: mock.Mock, manager: InfrastructureManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps([{"name": "r", "state": "Succeeded"}]), stderr="",
        )
        # Mock _az for health-check
        with mock.patch.object(manager, "_az", return_value=json.dumps([{"name": "r", "state": "Succeeded"}])):
            result = manager.deploy()
        assert result is True

    @mock.patch.object(InfrastructureManager, "_az")
    def test_list_resources_empty(self, mock_az: mock.Mock, manager: InfrastructureManager) -> None:
        mock_az.return_value = json.dumps([])
        result = manager.list_resources()
        assert result is True

    @mock.patch.object(InfrastructureManager, "_az")
    def test_status_no_deployments(self, mock_az: mock.Mock, manager: InfrastructureManager) -> None:
        mock_az.return_value = json.dumps([])
        result = manager.status()
        assert result is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_ensure_resource_group_succeeds(self, mock_run: mock.Mock, manager: InfrastructureManager) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"id": "/rg/rg-test", "properties": {"provisioningState": "Succeeded"}}',
            stderr="",
        )
        assert manager._ensure_resource_group() is True
        cmd = mock_run.call_args[0][0]
        assert "az" in cmd and "group" in cmd and "create" in cmd
        assert "rg-test" in cmd
        assert "eastus" in cmd

    @mock.patch.object(InfrastructureManager, "_run")
    def test_ensure_resource_group_failure_returns_false(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="AuthorizationFailed",
        )
        assert manager._ensure_resource_group() is False

    @mock.patch.object(InfrastructureManager, "_run")
    def test_plan_aborts_when_resource_group_creation_fails(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="AuthorizationFailed",
        )
        result = manager.plan()
        assert result is False
        # Only the resource group creation call was made; lint/validate/what-if were not reached.
        assert mock_run.call_count == 1

    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_aborts_when_resource_group_creation_fails(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="AuthorizationFailed",
        )
        result = manager.deploy()
        assert result is False
        assert mock_run.call_count == 1


# ====================================================================
# Single-step public method tests
# ====================================================================


class TestInfrastructureManagerSteps:
    """Tests for the single-step public entrypoints added to InfrastructureManager."""

    @pytest.fixture()
    def manager(self) -> InfrastructureManager:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
        )
        return InfrastructureManager(cfg)

    @pytest.fixture()
    def manager_allow_warnings(self) -> InfrastructureManager:
        cfg = DeploymentConfig(
            environment="dev",
            resource_group="rg-test",
            location="eastus",
            template="deployment/main-modular.bicep",
            allow_warnings=True,
        )
        return InfrastructureManager(cfg)

    # ── ensure_rg ──────────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_run")
    def test_ensure_rg_delegates_to_private(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
        assert manager.ensure_rg() is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "az" in cmd and "group" in cmd and "create" in cmd

    @mock.patch.object(InfrastructureManager, "_run")
    def test_ensure_rg_returns_false_on_failure(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="AuthFailed")
        assert manager.ensure_rg() is False

    # ── lint ───────────────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_run")
    def test_lint_returns_true_on_success(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert manager.lint() is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_lint_returns_false_on_failure_without_allow_warnings(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        assert manager.lint() is False

    @mock.patch.object(InfrastructureManager, "_run")
    def test_lint_returns_true_on_failure_with_allow_warnings(
        self, mock_run: mock.Mock, manager_allow_warnings: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="warning")
        assert manager_allow_warnings.lint() is True

    # ── validate ───────────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_run")
    def test_validate_returns_true_on_success(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert manager.validate() is True

    @mock.patch.object(InfrastructureManager, "_run")
    def test_validate_uses_output_format_none(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Validate must not stream a raw JSON blob — it uses --output none."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        manager.validate()
        cmd = mock_run.call_args[0][0]
        # --output none suppresses the ARM template JSON blob
        assert "--output" in cmd
        output_idx = cmd.index("--output")
        assert cmd[output_idx + 1] == "none"

    @mock.patch.object(InfrastructureManager, "_run")
    def test_validate_returns_false_on_failure_without_allow_warnings(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="InvalidTemplate")
        assert manager.validate() is False

    @mock.patch.object(InfrastructureManager, "_run")
    def test_validate_returns_true_on_failure_with_allow_warnings(
        self, mock_run: mock.Mock, manager_allow_warnings: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="warning")
        assert manager_allow_warnings.validate() is True

    # ── what_if ────────────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_writes_audit_on_success(
        self, mock_run: mock.Mock, manager: InfrastructureManager, tmp_path: Path
    ) -> None:
        import orchestrator.core.manager as manager_module
        what_if_json = json.dumps({
            "changes": [
                {"changeType": "Create", "resourceId": "/r1"},
                {"changeType": "Modify", "resourceId": "/r2"},
            ]
        })
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=what_if_json, stderr="")
        original_audit_dir = manager_module._AUDIT_DIR
        manager_module._AUDIT_DIR = tmp_path
        try:
            result = manager.what_if()
        finally:
            manager_module._AUDIT_DIR = original_audit_dir
        assert result is True
        # Audit file should have been written
        audit_files = list(tmp_path.glob("what-if_*.json"))
        assert len(audit_files) == 1
        audit_data = json.loads(audit_files[0].read_text())
        assert audit_data["action"] == "what-if"
        assert audit_data["what_if_creates"] == 1
        assert audit_data["what_if_modifies"] == 1

    @mock.patch.object(InfrastructureManager, "_run")
    def test_what_if_returns_false_on_failure(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")
        assert manager.what_if() is False

    # ── deploy_bicep ───────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_bicep_runs_az_deployment_create(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert manager.deploy_bicep() is True
        cmd = mock_run.call_args[0][0]
        assert "deployment" in cmd and "create" in cmd

    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_bicep_returns_false_on_failure(
        self, mock_run: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="DeployFailed")
        assert manager.deploy_bicep() is False

    # ── health_check ───────────────────────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_az")
    def test_health_check_all_succeeded(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_az.return_value = json.dumps([
            {"name": "storage1", "state": "Succeeded"},
            {"name": "funcapp1", "state": "Succeeded"},
        ])
        assert manager.health_check() is True

    @mock.patch.object(InfrastructureManager, "_az")
    def test_health_check_resource_not_succeeded(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_az.return_value = json.dumps([
            {"name": "storage1", "state": "Succeeded"},
            {"name": "funcapp1", "state": "Failed"},
        ])
        assert manager.health_check() is False

    # ── deploy_function_apps ───────────────────────────────────────────

    @mock.patch("orchestrator.integration.sdk_bridge.SDKBridge.deploy_function_apps")
    def test_deploy_function_apps_returns_true_on_all_succeeded(
        self, mock_deploy: mock.Mock, manager: InfrastructureManager
    ) -> None:
        from orchestrator.integration.sdk_bridge import AppDeploymentStatus
        mock_deploy.return_value = [
            AppDeploymentStatus(app_name="agent-operating-system", status="succeeded"),
            AppDeploymentStatus(app_name="mcp-erpnext", status="succeeded"),
        ]
        assert manager.deploy_function_apps() is True

    @mock.patch("orchestrator.integration.sdk_bridge.SDKBridge.deploy_function_apps")
    def test_deploy_function_apps_returns_false_when_app_fails(
        self, mock_deploy: mock.Mock, manager: InfrastructureManager
    ) -> None:
        from orchestrator.integration.sdk_bridge import AppDeploymentStatus
        mock_deploy.return_value = [
            AppDeploymentStatus(app_name="agent-operating-system", status="succeeded"),
            AppDeploymentStatus(app_name="mcp-erpnext", status="failed", error="deploy error"),
        ]
        assert manager.deploy_function_apps() is False

    @mock.patch("orchestrator.integration.sdk_bridge.SDKBridge.deploy_function_apps")
    def test_deploy_function_apps_returns_true_when_skipped(
        self, mock_deploy: mock.Mock, manager: InfrastructureManager
    ) -> None:
        from orchestrator.integration.sdk_bridge import AppDeploymentStatus
        mock_deploy.return_value = [
            AppDeploymentStatus(
                app_name="agent-operating-system",
                status="skipped",
                error="aos-client-sdk not available",
            ),
        ]
        assert manager.deploy_function_apps() is True

    # ── sync_kernel_config ─────────────────────────────────────────────

    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.validate_kernel_config")
    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.extract_kernel_env")
    def test_sync_kernel_config_returns_true_when_valid(
        self,
        mock_extract: mock.Mock,
        mock_validate: mock.Mock,
        manager: InfrastructureManager,
    ) -> None:
        mock_extract.return_value = {"SERVICE_BUS_CONNECTION": "sb://...", "KEY_VAULT_URL": "https://..."}
        mock_validate.return_value = {"present": ["SERVICE_BUS_CONNECTION", "KEY_VAULT_URL"], "missing": []}
        assert manager.sync_kernel_config() is True

    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.validate_kernel_config")
    @mock.patch("orchestrator.integration.kernel_bridge.KernelBridge.extract_kernel_env")
    def test_sync_kernel_config_returns_false_when_missing_vars(
        self,
        mock_extract: mock.Mock,
        mock_validate: mock.Mock,
        manager: InfrastructureManager,
    ) -> None:
        mock_extract.return_value = {}
        mock_validate.return_value = {"present": [], "missing": ["SERVICE_BUS_CONNECTION"]}
        assert manager.sync_kernel_config() is False

    # ── Bicep phase deployment methods ─────────────────────────────────

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_returns_true_on_zero_exit(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert manager._deploy_phase("deployment/phases/01-foundation.bicep", "foundation") is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "az" in cmd and "deployment" in cmd and "create" in cmd
        assert "--name" in cmd
        assert "phase-foundation-dev" in cmd

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_excludes_location_when_include_location_false(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Phase templates without a `location` param (e.g. governance) must not receive it."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        manager._deploy_phase(
            "deployment/phases/05-governance.bicep", "governance",
            include_location=False, include_location_ml=False,
        )
        cmd = mock_run.call_args[0][0]
        params_idx = cmd.index("--parameters")
        overrides = cmd[params_idx + 1:]
        assert not any(o.startswith("location=") for o in overrides)
        assert not any(o.startswith("locationML=") for o in overrides)

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_excludes_tags_when_include_tags_false(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Phase templates without a `tags` param (e.g. governance) must not receive it."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        manager._deploy_phase(
            "deployment/phases/05-governance.bicep", "governance",
            include_location=False, include_location_ml=False, include_tags=False,
        )
        cmd = mock_run.call_args[0][0]
        params_idx = cmd.index("--parameters")
        overrides = cmd[params_idx + 1:]
        assert not any(o.startswith("tags=") for o in overrides)

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_excludes_location_ml_when_include_location_ml_false(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Phase templates without a `locationML` param (e.g. foundation) must not receive it."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        manager._deploy_phase(
            "deployment/phases/01-foundation.bicep", "foundation",
            include_location_ml=False,
        )
        cmd = mock_run.call_args[0][0]
        params_idx = cmd.index("--parameters")
        overrides = cmd[params_idx + 1:]
        assert not any(o.startswith("locationML=") for o in overrides)

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_returns_false_on_nonzero_exit(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        assert manager._deploy_phase("deployment/phases/01-foundation.bicep", "foundation") is False

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_rbac_policy_warning_treated_as_success(
        self, mock_run: mock.Mock, _mock_status: mock.Mock,
        manager_allow_warnings: InfrastructureManager,
    ) -> None:
        """Phase 5 governance policyAssignments/write RBAC errors are non-fatal when allow_warnings=True."""
        rbac_err = (
            "ERROR: {\"code\":\"InvalidTemplateDeployment\",\"message\":\"Deployment failed: "
            "Authorization failed for template resource 'aos-allowed-locations-staging' "
            "of type 'Microsoft.Authorization/policyAssignments'. The client does not "
            "have permission to perform action 'Microsoft.Authorization/policyAssignments/write'\"}"
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=rbac_err,
        )
        result = manager_allow_warnings._deploy_phase(
            "deployment/phases/05-governance.bicep", "governance",
            include_location=False, include_location_ml=False, include_tags=False,
        )
        assert result is True

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_rbac_warning_not_swallowed_without_allow_warnings(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """RBAC errors are still failures when allow_warnings=False (default)."""
        rbac_err = "Authorization failed for template resource 'aos-allowed-locations-staging'"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=rbac_err,
        )
        assert manager._deploy_phase(
            "deployment/phases/05-governance.bicep", "governance",
            include_location=False, include_location_ml=False, include_tags=False,
        ) is False

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_all_nested_failures_are_rbac", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_nested_rbac_warning_treated_as_success(
        self,
        mock_run: mock.Mock,
        _mock_nested: mock.Mock,
        _mock_status: mock.Mock,
        manager_allow_warnings: InfrastructureManager,
    ) -> None:
        """Phase 4 nested RBAC errors (DeploymentFailed outer, RBAC inner) are non-fatal with allow_warnings."""
        outer_err = (
            'ERROR: {"status":"Failed","error":{"code":"DeploymentFailed",'
            '"message":"At least one resource deployment operation failed."}}'
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=outer_err,
        )
        result = manager_allow_warnings._deploy_phase(
            "deployment/phases/04-function-apps.bicep", "function-apps",
            include_location_ml=False,
        )
        assert result is True

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_all_nested_failures_are_rbac", return_value=False)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_nested_non_rbac_failure_still_fails(
        self,
        mock_run: mock.Mock,
        _mock_nested: mock.Mock,
        _mock_status: mock.Mock,
        manager_allow_warnings: InfrastructureManager,
    ) -> None:
        """Non-RBAC nested failures must still be treated as real failures even with allow_warnings."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr='ERROR: {"code":"DeploymentFailed"}',
        )
        result = manager_allow_warnings._deploy_phase(
            "deployment/phases/04-function-apps.bicep", "function-apps",
            include_location_ml=False,
        )
        assert result is False

    @mock.patch.object(InfrastructureManager, "_az")
    def test_all_nested_failures_are_rbac_returns_true_when_all_rbac(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Returns True when every failed operation statusMessage is an RBAC error."""
        rbac_msg = {
            "status": "Failed",
            "error": {
                "code": "DeploymentFailed",
                "details": [
                    {
                        "code": "AuthorizationFailed",
                        "message": (
                            "The client does not have authorization to perform action "
                            "'Microsoft.Authorization/roleAssignments/write' over scope '/subscriptions/...'."
                        ),
                    }
                ],
            },
        }
        mock_az.return_value = json.dumps([rbac_msg, rbac_msg])
        assert manager._all_nested_failures_are_rbac("phase-function-apps-dev") is True

    @mock.patch.object(InfrastructureManager, "_az")
    def test_all_nested_failures_are_rbac_returns_false_when_mixed(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Returns False when at least one failure is not RBAC-related."""
        rbac_msg = {
            "status": "Failed",
            "error": {"details": [{"message": "Microsoft.Authorization/roleAssignments/write"}]},
        }
        non_rbac_msg = {"status": "Failed", "error": {"code": "ResourceNotFound", "message": "Not found"}}
        mock_az.return_value = json.dumps([rbac_msg, non_rbac_msg])
        assert manager._all_nested_failures_are_rbac("phase-function-apps-dev") is False

    @mock.patch.object(InfrastructureManager, "_az")
    def test_all_nested_failures_are_rbac_returns_false_when_no_failures(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Returns False when the query returns an empty list (no failed operations)."""
        mock_az.return_value = json.dumps([])
        assert manager._all_nested_failures_are_rbac("phase-function-apps-dev") is False

    @mock.patch.object(InfrastructureManager, "_az")
    def test_all_nested_failures_are_rbac_returns_false_when_query_fails(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Returns False when the az query fails (deployment not found, etc.)."""
        mock_az.return_value = None
        assert manager._all_nested_failures_are_rbac("phase-function-apps-dev") is False

    @mock.patch.object(InfrastructureManager, "_query_phase_deployment_status", return_value=True)
    @mock.patch.object(InfrastructureManager, "_run")
    def test_deploy_phase_prints_resource_group_prominently(
        self, mock_run: mock.Mock, _mock_status: mock.Mock, manager: InfrastructureManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Resource group must appear on its own line in the phase output."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        manager._deploy_phase("deployment/phases/01-foundation.bicep", "foundation")
        captured = capsys.readouterr()
        assert "Resource Group" in captured.out
        assert manager.config.resource_group in captured.out

    @mock.patch.object(InfrastructureManager, "_deploy_phase", return_value=True)
    def test_deploy_bicep_foundation_delegates_to_phase(
        self, mock_phase: mock.Mock, manager: InfrastructureManager
    ) -> None:
        assert manager.deploy_bicep_foundation() is True
        mock_phase.assert_called_once_with(
            "deployment/phases/01-foundation.bicep", "foundation",
            include_location_ml=False,
        )

    @mock.patch.object(InfrastructureManager, "_deploy_phase", return_value=True)
    def test_deploy_bicep_ai_services_delegates_to_phase(
        self, mock_phase: mock.Mock, manager: InfrastructureManager
    ) -> None:
        assert manager.deploy_bicep_ai_services() is True
        mock_phase.assert_called_once_with(
            "deployment/phases/02-ai-services.bicep", "ai-services"
        )

    @mock.patch.object(InfrastructureManager, "_deploy_phase", return_value=True)
    def test_deploy_bicep_ai_apps_delegates_to_phase(
        self, mock_phase: mock.Mock, manager: InfrastructureManager
    ) -> None:
        assert manager.deploy_bicep_ai_apps() is True
        mock_phase.assert_called_once_with(
            "deployment/phases/03-ai-applications.bicep", "ai-apps"
        )

    @mock.patch.object(InfrastructureManager, "_deploy_phase", return_value=True)
    def test_deploy_bicep_function_apps_delegates_to_phase(
        self, mock_phase: mock.Mock, manager: InfrastructureManager
    ) -> None:
        assert manager.deploy_bicep_function_apps() is True
        mock_phase.assert_called_once_with(
            "deployment/phases/04-function-apps.bicep", "function-apps",
            include_location_ml=False,
        )

    @mock.patch.object(InfrastructureManager, "_deploy_phase", return_value=True)
    def test_deploy_bicep_governance_delegates_to_phase(
        self, mock_phase: mock.Mock, manager: InfrastructureManager
    ) -> None:
        assert manager.deploy_bicep_governance() is True
        mock_phase.assert_called_once_with(
            "deployment/phases/05-governance.bicep", "governance",
            include_location=False,
            include_location_ml=False,
            include_tags=False,
        )

    @mock.patch.object(InfrastructureManager, "_az")
    def test_query_phase_deployment_status_uses_correct_az_command(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        """Must use `az deployment operation group list`, not `az deployment group operation list`."""
        mock_az.return_value = json.dumps([])
        manager._query_phase_deployment_status("phase-foundation-dev")
        call_args = mock_az.call_args[0][0]
        # Correct order: deployment → operation → group → list
        assert call_args[:4] == ["deployment", "operation", "group", "list"]

    @mock.patch.object(InfrastructureManager, "_az")
    def test_query_phase_deployment_status_returns_true_when_all_succeeded(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_az.return_value = json.dumps([
            {"name": "monitoring-aos-dev", "type": "Microsoft.Resources/deployments", "state": "Succeeded"},
            {"name": "storage-aos-dev",    "type": "Microsoft.Resources/deployments", "state": "Succeeded"},
        ])
        assert manager._query_phase_deployment_status("phase-foundation-dev") is True

    @mock.patch.object(InfrastructureManager, "_az")
    def test_query_phase_deployment_status_returns_false_when_module_failed(
        self, mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        mock_az.return_value = json.dumps([
            {"name": "monitoring-aos-dev", "type": "Microsoft.Resources/deployments", "state": "Succeeded"},
            {"name": "storage-aos-dev",    "type": "Microsoft.Resources/deployments", "state": "Failed"},
        ])
        assert manager._query_phase_deployment_status("phase-foundation-dev") is False

    @mock.patch.object(InfrastructureManager, "_az", return_value=None)
    def test_query_phase_deployment_status_returns_true_when_deployment_not_found(
        self, _mock_az: mock.Mock, manager: InfrastructureManager
    ) -> None:
        # When the deployment doesn't exist yet (e.g. dry-run), return True (non-fatal).
        assert manager._query_phase_deployment_status("phase-foundation-dev") is True

    # ── fetch_identity_client_ids ──────────────────────────────────────

    @mock.patch("orchestrator.core.manager.ManagedIdentityClient")
    @mock.patch("orchestrator.core.manager.KeyVaultIdentityStore")
    @mock.patch("subprocess.run")
    def test_fetch_identity_client_ids_uses_config_subscription_id(
        self,
        mock_run: mock.Mock,
        mock_kv_store_cls: mock.Mock,
        mock_msi_cls: mock.Mock,
        manager: InfrastructureManager,
    ) -> None:
        """subscription_id from config is forwarded to ManagedIdentityClient."""
        manager.config.subscription_id = "sub-from-config"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://kv-test.vault.azure.net\n", stderr=""
        )
        from orchestrator.integration.identity_client import IdentityInfo
        mock_identity = IdentityInfo(
            name="id-my-app-dev",
            client_id="client-uuid",
            principal_id="principal-uuid",
            resource_id="/sub/rg/id",
            location="eastus",
            resource_group="rg-test",
        )
        mock_msi_cls.return_value.list_function_app_identities.return_value = [mock_identity]

        result = manager.fetch_identity_client_ids()

        assert result is True
        mock_msi_cls.assert_called_once_with(
            subscription_id="sub-from-config",
            resource_group="rg-test",
        )

    @mock.patch("orchestrator.core.manager.ManagedIdentityClient")
    @mock.patch("orchestrator.core.manager.KeyVaultIdentityStore")
    @mock.patch("subprocess.run")
    def test_fetch_identity_client_ids_falls_back_to_env_var(
        self,
        mock_run: mock.Mock,
        mock_kv_store_cls: mock.Mock,
        mock_msi_cls: mock.Mock,
        manager: InfrastructureManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When subscription_id is empty in config, AZURE_SUBSCRIPTION_ID env var is used."""
        manager.config.subscription_id = ""
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-from-env")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://kv-test.vault.azure.net\n", stderr=""
        )
        from orchestrator.integration.identity_client import IdentityInfo
        mock_identity = IdentityInfo(
            name="id-my-app-dev",
            client_id="client-uuid",
            principal_id="principal-uuid",
            resource_id="/sub/rg/id",
            location="eastus",
            resource_group="rg-test",
        )
        mock_msi_cls.return_value.list_function_app_identities.return_value = [mock_identity]

        result = manager.fetch_identity_client_ids()

        assert result is True
        mock_msi_cls.assert_called_once_with(
            subscription_id="sub-from-env",
            resource_group="rg-test",
        )

    @mock.patch("subprocess.run")
    def test_fetch_identity_client_ids_returns_false_when_subscription_id_missing(
        self,
        mock_run: mock.Mock,
        manager: InfrastructureManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns False (and prints a clear error) when subscription_id is absent."""
        manager.config.subscription_id = ""
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://kv-test.vault.azure.net\n", stderr=""
        )

        result = manager.fetch_identity_client_ids()

        assert result is False


# ====================================================================
# deploy.py CLI — step subcommand tests
# ====================================================================


class TestDeployPyStepCommands:
    """Tests for the individual pipeline step CLI subcommands in deploy.py."""

    BASE_ARGS = [
        "--resource-group", "rg-test",
        "--location", "eastus",
        "--environment", "dev",
        "--template", "deployment/main-modular.bicep",
    ]

    def _run(self, argv: list[str]) -> int:
        """Import and invoke deploy.main() directly."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from deploy import main  # noqa: PLC0415
        return main(argv)

    @mock.patch("orchestrator.core.manager.InfrastructureManager.ensure_rg", return_value=True)
    def test_ensure_rg_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["ensure-rg"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.ensure_rg", return_value=False)
    def test_ensure_rg_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["ensure-rg"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.lint", return_value=True)
    def test_lint_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["lint"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.validate", return_value=True)
    def test_validate_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["validate"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.what_if", return_value=True)
    def test_what_if_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["what-if"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep", return_value=True)
    def test_deploy_bicep_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.health_check", return_value=True)
    def test_health_check_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["health-check"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.what_if", return_value=False)
    def test_what_if_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["what-if"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep", return_value=False)
    def test_deploy_bicep_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_function_apps", return_value=True)
    def test_deploy_function_apps_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-function-apps"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_function_apps", return_value=False)
    def test_deploy_function_apps_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-function-apps"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.sync_kernel_config", return_value=True)
    def test_sync_kernel_config_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["sync-kernel-config"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.sync_kernel_config", return_value=False)
    def test_sync_kernel_config_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["sync-kernel-config"] + self.BASE_ARGS) == 1

    # ── Granular Bicep phase subcommands ───────────────────────────────

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_foundation", return_value=True)
    def test_deploy_bicep_foundation_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-foundation"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_foundation", return_value=False)
    def test_deploy_bicep_foundation_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-foundation"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_ai_services", return_value=True)
    def test_deploy_bicep_ai_services_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-ai-services"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_ai_services", return_value=False)
    def test_deploy_bicep_ai_services_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-ai-services"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_ai_apps", return_value=True)
    def test_deploy_bicep_ai_apps_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-ai-apps"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_ai_apps", return_value=False)
    def test_deploy_bicep_ai_apps_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-ai-apps"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_function_apps", return_value=True)
    def test_deploy_bicep_function_apps_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-function-apps"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_function_apps", return_value=False)
    def test_deploy_bicep_function_apps_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-function-apps"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_governance", return_value=True)
    def test_deploy_bicep_governance_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-governance"] + self.BASE_ARGS) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.deploy_bicep_governance", return_value=False)
    def test_deploy_bicep_governance_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(["deploy-bicep-governance"] + self.BASE_ARGS) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.fetch_identity_client_ids", return_value=True)
    def test_fetch_identity_client_ids_subcommand_exits_zero(self, mock_fn: mock.Mock) -> None:
        assert self._run(
            ["fetch-identity-client-ids"] + self.BASE_ARGS
            + ["--subscription-id", "sub-123"]
        ) == 0
        mock_fn.assert_called_once()

    @mock.patch("orchestrator.core.manager.InfrastructureManager.fetch_identity_client_ids", return_value=False)
    def test_fetch_identity_client_ids_subcommand_exits_one_on_failure(self, mock_fn: mock.Mock) -> None:
        assert self._run(
            ["fetch-identity-client-ids"] + self.BASE_ARGS
            + ["--subscription-id", "sub-123"]
        ) == 1

    @mock.patch("orchestrator.core.manager.InfrastructureManager.fetch_identity_client_ids", return_value=True)
    def test_fetch_identity_client_ids_subscription_id_forwarded_to_config(
        self, mock_fn: mock.Mock
    ) -> None:
        """--subscription-id CLI argument is forwarded to DeploymentConfig.subscription_id."""
        assert self._run(
            ["fetch-identity-client-ids"] + self.BASE_ARGS + ["--subscription-id", "sub-xyz"]
        ) == 0
        mock_fn.assert_called_once()
