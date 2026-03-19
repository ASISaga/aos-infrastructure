"""Tests for AOS deployment orchestrator.

Covers configuration creation, validation, regional validation, and
InfrastructureManager method signatures using mocked subprocess calls.
"""

from __future__ import annotations

import argparse
import json
import subprocess
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
