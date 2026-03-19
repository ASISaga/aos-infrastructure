"""Tests for deployment/orchestrator/cli/workflow_helper.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the module is importable from the test runner's working directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator" / "cli"))

import workflow_helper  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(argv: list[str], env: dict | None = None) -> int:
    """Call main() with the given argv; optionally override env vars."""
    merged = {**os.environ, **(env or {})}
    with patch.dict(os.environ, merged, clear=True):
        return workflow_helper.main(argv)


def _capture_outputs(argv: list[str], env: dict | None = None) -> dict[str, str]:
    """Run main() and capture every key written to GITHUB_OUTPUT."""
    outputs: dict[str, str] = {}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as fh:
        gh_output = fh.name

    merged = {"GITHUB_OUTPUT": gh_output, **(env or {})}
    with patch.dict(os.environ, merged, clear=True):
        workflow_helper.main(argv)

    for line in Path(gh_output).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            outputs[key] = val

    Path(gh_output).unlink(missing_ok=True)
    return outputs


# ===========================================================================
# _output helper
# ===========================================================================

class TestOutput:
    def test_writes_to_github_output_file(self, tmp_path):
        gh_output = tmp_path / "output.env"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(gh_output)}, clear=False):
            workflow_helper._output("my_key", "my_value")
        assert "my_key=my_value" in gh_output.read_text()

    def test_falls_back_to_stdout_when_no_env_var(self, capsys):
        with patch.dict(os.environ, {}, clear=True):
            workflow_helper._output("k", "v")
        captured = capsys.readouterr()
        assert "k=v" in captured.out


# ===========================================================================
# check-trigger
# ===========================================================================

class TestCheckTrigger:
    def test_workflow_dispatch_triggers_deploy(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_ENVIRONMENT": "staging",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "westeurope",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
            },
        )
        assert out["should_deploy"] == "true"
        assert out["is_dry_run"] == "false"
        assert out["environment"] == "staging"
        assert out["location"] == "westeurope"

    def test_pull_request_deploy_dev_label(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "pull_request",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "PR_LABEL_DEPLOY_DEV": "true",
                "PR_LABEL_DEPLOY_STAGING": "false",
                "PR_LABEL_STATUS_APPROVED": "false",
                "PR_LABEL_ACTION_DEPLOY": "false",
                "COMMENT_BODY": "",
            },
        )
        assert out["should_deploy"] == "true"
        assert out["is_dry_run"] == "true"
        assert out["environment"] == "dev"

    def test_pull_request_deploy_staging_requires_approved(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "pull_request",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "PR_LABEL_DEPLOY_DEV": "false",
                "PR_LABEL_DEPLOY_STAGING": "true",
                "PR_LABEL_STATUS_APPROVED": "false",
                "PR_LABEL_ACTION_DEPLOY": "false",
                "COMMENT_BODY": "",
            },
        )
        assert out["should_deploy"] == "false"

    def test_pull_request_deploy_staging_with_approved(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "pull_request",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "PR_LABEL_DEPLOY_DEV": "false",
                "PR_LABEL_DEPLOY_STAGING": "true",
                "PR_LABEL_STATUS_APPROVED": "true",
                "PR_LABEL_ACTION_DEPLOY": "false",
                "COMMENT_BODY": "",
            },
        )
        assert out["should_deploy"] == "true"
        assert out["environment"] == "staging"

    def test_issue_comment_deploy_command(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "issue_comment",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "COMMENT_BODY": "/deploy prod",
            },
        )
        assert out["should_deploy"] == "true"
        assert out["environment"] == "prod"
        assert out["is_dry_run"] == "false"

    def test_issue_comment_plan_sets_dry_run(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "issue_comment",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "COMMENT_BODY": "/deploy plan",
            },
        )
        assert out["should_deploy"] == "true"
        assert out["is_dry_run"] == "true"

    def test_unknown_event_no_deploy(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ENVIRONMENT": "dev",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
                "COMMENT_BODY": "",
            },
        )
        assert out["should_deploy"] == "false"

    def test_resource_group_auto_generated(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_ENVIRONMENT": "staging",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
            },
        )
        assert out["resource_group"] == "rg-aos-staging"

    def test_resource_group_explicit(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_ENVIRONMENT": "prod",
                "INPUT_RESOURCE_GROUP": "my-custom-rg",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
            },
        )
        assert out["resource_group"] == "my-custom-rg"

    def test_parameters_file_matches_environment(self):
        out = _capture_outputs(
            ["check-trigger"],
            env={
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_ENVIRONMENT": "prod",
                "INPUT_RESOURCE_GROUP": "",
                "INPUT_LOCATION": "",
                "INPUT_GEOGRAPHY": "",
                "INPUT_TEMPLATE": "deployment/main-modular.bicep",
                "INPUT_SKIP_HEALTH_CHECKS": "false",
            },
        )
        assert out["parameters_file"] == "deployment/parameters/prod.bicepparam"


# ===========================================================================
# select-regions
# ===========================================================================

class TestSelectRegions:
    def test_explicit_location_takes_precedence(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--location", "westus", "--geography", "americas"],
        )
        assert out["primary_region"] == "westus"

    def test_geography_americas(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--geography", "americas"],
        )
        assert out["primary_region"] == "eastus"
        assert out["ml_region"] == "eastus"

    def test_geography_europe(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--geography", "europe"],
        )
        assert out["primary_region"] == "westeurope"
        assert out["ml_region"] == "westeurope"

    def test_geography_asia(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--geography", "asia"],
        )
        assert out["primary_region"] == "southeastasia"
        assert out["ml_region"] == "southeastasia"

    def test_default_region_when_no_geography(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev"],
        )
        assert out["primary_region"] == "eastus"

    def test_staging_eastus_gets_separate_ml_region(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "staging", "--geography", "americas"],
        )
        assert out["primary_region"] == "eastus"
        assert out["ml_region"] == "eastus2"

    def test_prod_eastus_gets_separate_ml_region(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "prod", "--geography", "americas"],
        )
        assert out["primary_region"] == "eastus"
        assert out["ml_region"] == "eastus2"

    def test_dev_eastus_does_not_split_ml_region(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--geography", "americas"],
        )
        assert out["ml_region"] == "eastus"

    def test_unknown_geography_falls_back_to_default(self):
        out = _capture_outputs(
            ["select-regions", "--environment", "dev", "--geography", "unknown-geo"],
        )
        assert out["primary_region"] == "eastus"


# ===========================================================================
# analyze-output
# ===========================================================================

class TestAnalyzeOutput:
    def test_success_on_exit_code_zero(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("Deployment succeeded.\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "0"],
        )
        assert out["status"] == "success"
        assert out["should_retry"] == "false"
        assert out["is_transient"] == "false"

    def test_transient_failure_on_timeout(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("Error: Timeout exceeded while waiting for deployment.\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["status"] == "failed"
        assert out["failure_type"] == "environmental"
        assert out["should_retry"] == "true"
        assert out["is_transient"] == "true"

    def test_transient_failure_on_throttling(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("ThrottlingException: too many requests\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["failure_type"] == "environmental"
        assert out["should_retry"] == "true"

    def test_logic_failure_on_unknown_error(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("Error: Invalid parameter 'location'. Expected one of: eastus, westus.\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["status"] == "failed"
        assert out["failure_type"] == "logic"
        assert out["should_retry"] == "false"

    def test_error_file_written_on_failure(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("Error: something went wrong\n")
        with tempfile.TemporaryDirectory() as work_dir:
            orig_dir = os.getcwd()
            os.chdir(work_dir)
            try:
                out = _capture_outputs(
                    ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
                )
                assert out.get("error_file") == "error-message.txt"
                assert Path("error-message.txt").exists()
            finally:
                os.chdir(orig_dir)

    def test_missing_log_file_treated_as_logic_failure(self):
        out = _capture_outputs(
            ["analyze-output", "--log-file", "/nonexistent/path.log", "--exit-code", "1"],
        )
        assert out["status"] == "failed"
        assert out["failure_type"] == "logic"

    @pytest.mark.parametrize("pattern", [
        "RetryableError",
        "ServiceUnavailable",
        "InternalServerError",
        "ECONNRESET",
        "socket hang up",
        "could not resolve host",
    ])
    def test_all_transient_patterns_detected(self, tmp_path, pattern):
        log = tmp_path / "output.log"
        log.write_text(f"Deployment failed: {pattern}\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["is_transient"] == "true", f"Pattern {pattern!r} not detected as transient"

    def test_rbac_permission_error_classified_as_permissions(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text(
            "ERROR: InvalidTemplateDeployment - Authorization failed for template resource "
            "of type 'Microsoft.Authorization/roleAssignments'. The client does not have "
            "permission to perform action 'Microsoft.Authorization/roleAssignments/write'.\n"
        )
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["status"] == "failed"
        assert out["failure_type"] == "permissions"
        assert out["should_retry"] == "false"
        assert out["is_transient"] == "false"
        assert out["rbac_permission_error"] == "true"

    def test_rbac_permission_error_does_not_trigger_retry(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text(
            "does not have permission to perform action 'Microsoft.Authorization/roleAssignments/write'\n"
        )
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "1"],
        )
        assert out["should_retry"] == "false"
        assert out["rbac_permission_error"] == "true"

    def test_success_emits_rbac_permission_error_false(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("Deployment succeeded.\n")
        out = _capture_outputs(
            ["analyze-output", "--log-file", str(log), "--exit-code", "0"],
        )
        assert out["rbac_permission_error"] == "false"


# ===========================================================================
# retry
# ===========================================================================

class TestRetry:
    def _base_args(self) -> list[str]:
        return [
            "retry",
            "--resource-group", "rg-test",
            "--location", "eastus",
            "--environment", "dev",
            "--template", "deployment/main-modular.bicep",
        ]

    def test_success_on_first_attempt(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Deployment succeeded."

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("time.sleep") as mock_sleep:
            out = _capture_outputs(self._base_args())

        assert out["retry_success"] == "true"
        assert out["retry_count"] == "1"
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    def test_success_on_second_attempt(self):
        fail_result = MagicMock(returncode=1, stdout="Error occurred")
        ok_result = MagicMock(returncode=0, stdout="Deployment succeeded.")

        with patch("subprocess.run", side_effect=[fail_result, ok_result]), \
             patch("time.sleep"):
            out = _capture_outputs(self._base_args())

        assert out["retry_success"] == "true"
        assert out["retry_count"] == "2"

    def test_all_retries_exhausted(self):
        fail_result = MagicMock(returncode=1, stdout="Error")
        with patch("subprocess.run", return_value=fail_result), \
             patch("time.sleep"):
            out = _capture_outputs([*self._base_args(), "--max-retries", "2"])

        assert out["retry_success"] == "false"
        assert out["retry_count"] == "2"

    def test_exponential_backoff_applied(self):
        fail_result = MagicMock(returncode=1, stdout="")
        sleep_calls: list[float] = []

        def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with patch("subprocess.run", return_value=fail_result), \
             patch("time.sleep", side_effect=fake_sleep):
            _capture_outputs([*self._base_args(), "--max-retries", "4"])

        # First attempt has no sleep; attempt 2 → 10s, attempt 3 → 20s, attempt 4 → 40s
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == workflow_helper._RETRY_BASE_DELAY
        assert sleep_calls[1] == workflow_helper._RETRY_BASE_DELAY * 2
        assert sleep_calls[2] == workflow_helper._RETRY_BASE_DELAY * 4

    def test_no_sleep_before_first_attempt(self):
        ok_result = MagicMock(returncode=0, stdout="ok")
        with patch("subprocess.run", return_value=ok_result), \
             patch("time.sleep") as mock_sleep:
            _capture_outputs(self._base_args())
        mock_sleep.assert_not_called()

    def test_git_sha_included_when_valid(self):
        ok_result = MagicMock(returncode=0, stdout="ok")
        with patch("subprocess.run", return_value=ok_result) as mock_run, \
             patch("time.sleep"):
            _capture_outputs([*self._base_args(), "--git-sha", "abc1234"])

        call_args = mock_run.call_args[0][0]
        assert "--git-sha" in call_args
        assert "abc1234" in call_args

    def test_invalid_git_sha_excluded(self):
        ok_result = MagicMock(returncode=0, stdout="ok")
        with patch("subprocess.run", return_value=ok_result) as mock_run, \
             patch("time.sleep"):
            _capture_outputs([*self._base_args(), "--git-sha", "not-a-sha!!"])

        call_args = mock_run.call_args[0][0]
        assert "--git-sha" not in call_args

    def test_parameters_included_when_provided(self):
        ok_result = MagicMock(returncode=0, stdout="ok")
        with patch("subprocess.run", return_value=ok_result) as mock_run, \
             patch("time.sleep"):
            _capture_outputs([*self._base_args(), "--parameters", "deployment/parameters/dev.bicepparam"])

        call_args = mock_run.call_args[0][0]
        assert "--parameters" in call_args


# ===========================================================================
# extract-summary
# ===========================================================================

class TestExtractSummary:
    def test_reads_successful_audit_file(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "deploy-001.json").write_text(json.dumps({
            "status": "success",
            "deployed_resources": 42,
            "duration": 153,
        }))
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        assert out["deployed_resources"] == "42"
        assert out["duration"] == "153"

    def test_ignores_failed_audit_entries(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "deploy-001.json").write_text(json.dumps({
            "status": "failed",
            "deployed_resources": 5,
            "duration": 30,
        }))
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        assert out["deployed_resources"] == "0"
        assert out["duration"] == "N/A"

    def test_no_audit_directory_returns_defaults(self, tmp_path):
        out = _capture_outputs(["extract-summary", "--audit-dir", str(tmp_path / "nonexistent")])
        assert out["deployed_resources"] == "0"
        assert out["duration"] == "N/A"

    def test_skips_malformed_json(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "bad.json").write_text("not valid json{{")
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        assert out["deployed_resources"] == "0"

    def test_uses_last_successful_entry(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "a.json").write_text(json.dumps({"status": "success", "deployed_resources": 10, "duration": 60}))
        (audit_dir / "b.json").write_text(json.dumps({"status": "success", "deployed_resources": 20, "duration": 90}))
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        # Both are iterated; the last successful wins
        assert out["deployed_resources"] == "20"

    def test_emits_what_if_counts_from_audit(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "deploy-001.json").write_text(json.dumps({
            "status": "success",
            "deployed_resources": 5,
            "duration": 120,
            "what_if_creates": 3,
            "what_if_no_changes": 7,
            "what_if_modifies": 1,
            "what_if_deletes": 0,
        }))
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        assert out["what_if_creates"] == "3"
        assert out["what_if_no_changes"] == "7"
        assert out["what_if_modifies"] == "1"
        assert out["what_if_deletes"] == "0"

    def test_what_if_counts_default_to_zero_when_absent(self, tmp_path):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "deploy-001.json").write_text(json.dumps({
            "status": "success",
            "deployed_resources": 2,
            "duration": 60,
        }))
        out = _capture_outputs(["extract-summary", "--audit-dir", str(audit_dir)])
        assert out["what_if_creates"] == "0"
        assert out["what_if_no_changes"] == "0"


# ===========================================================================
# main() / argument parsing
# ===========================================================================

class TestMain:
    def test_unknown_command_exits_nonzero(self):
        with pytest.raises(SystemExit):
            workflow_helper.main(["unknown-command"])

    def test_no_command_exits_nonzero(self):
        with pytest.raises(SystemExit):
            workflow_helper.main([])

    def test_returns_zero_on_success(self):
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "INPUT_ENVIRONMENT": "dev",
            "INPUT_RESOURCE_GROUP": "",
            "INPUT_LOCATION": "",
            "INPUT_GEOGRAPHY": "",
            "INPUT_TEMPLATE": "deployment/main-modular.bicep",
            "INPUT_SKIP_HEALTH_CHECKS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            rc = workflow_helper.main(["check-trigger"])
        assert rc == 0
