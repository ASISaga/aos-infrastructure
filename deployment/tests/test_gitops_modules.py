"""Tests for GitOps Bicep modules (gitops-feedback.bicep and gitops-compliance.bicep).

Validates that the module files exist, have the correct structure, expose the right
parameters and outputs, and that the Logic App expression strings are valid.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULES_DIR = Path(__file__).parent.parent / "modules"
MAIN_BICEP = Path(__file__).parent.parent / "main-modular.bicep"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# gitops-compliance-rbac.bicep
# ---------------------------------------------------------------------------

class TestGitopsComplianceRbac:
    """Tests for the subscription-scoped Reader role assignment module."""

    @pytest.fixture(autouse=True)
    def _content(self) -> None:
        self.path = MODULES_DIR / "gitops-compliance-rbac.bicep"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        assert self.path.exists()

    def test_target_scope_subscription(self) -> None:
        assert "targetScope = 'subscription'" in self.text

    def test_has_principal_id_param(self) -> None:
        assert "param complianceLogicAppPrincipalId string" in self.text

    def test_assigns_reader_role(self) -> None:
        # Reader role definition ID
        assert "acdd72a7-3385-48ef-bd42-f606fba81ae7" in self.text

    def test_role_assignment_resource_type(self) -> None:
        assert "Microsoft.Authorization/roleAssignments" in self.text

    def test_has_output(self) -> None:
        assert "output roleAssignmentId string" in self.text

    def test_uses_subscription_scope(self) -> None:
        assert "scope: subscription()" in self.text or "scope subscription()" in self.text


# ---------------------------------------------------------------------------
# gitops-feedback.bicep
# ---------------------------------------------------------------------------

class TestGitopsFeedback:
    """Tests for the ARM Event-Driven Deployment Feedback Logic App module."""

    @pytest.fixture(autouse=True)
    def _content(self) -> None:
        self.path = MODULES_DIR / "gitops-feedback.bicep"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        assert self.path.exists()

    def test_required_parameters_present(self) -> None:
        for param in ("location", "environment", "projectName", "tags",
                      "githubOrg", "githubRepo", "githubToken"):
            assert f"param {param}" in self.text, f"Missing parameter: {param}"

    def test_github_token_is_secure(self) -> None:
        # githubToken must be marked @secure()
        idx = self.text.index("param githubToken string")
        snippet = self.text[max(0, idx - 60):idx]
        assert "@secure()" in snippet

    def test_logic_app_resource(self) -> None:
        assert "Microsoft.Logic/workflows@2019-05-01" in self.text

    def test_logic_app_system_assigned_identity(self) -> None:
        assert "SystemAssigned" in self.text

    def test_event_grid_system_topic(self) -> None:
        assert "Microsoft.EventGrid/systemTopics@2022-06-15" in self.text
        assert "Microsoft.Resources.ResourceGroups" in self.text

    def test_event_subscription(self) -> None:
        assert "Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15" in self.text

    def test_advanced_filter_on_operation_name(self) -> None:
        # Must filter specifically for deployment write operations
        assert "Microsoft.Resources/deployments/write" in self.text
        assert "StringIn" in self.text
        assert "data.operationName" in self.text

    def test_filters_success_and_failure_events(self) -> None:
        assert "Microsoft.Resources.ResourceWriteSuccess" in self.text
        assert "Microsoft.Resources.ResourceWriteFailure" in self.text

    def test_reader_role_assignment(self) -> None:
        # Logic App MSI must have Reader role on the resource group
        assert "acdd72a7-3385-48ef-bd42-f606fba81ae7" in self.text
        assert "Microsoft.Authorization/roleAssignments@2022-04-01" in self.text

    def test_managed_service_identity_auth(self) -> None:
        assert "ManagedServiceIdentity" in self.text

    def test_callback_url_used_for_event_subscription(self) -> None:
        assert "listCallbackUrl(" in self.text

    def test_github_deployment_status_api_endpoint(self) -> None:
        assert "api.github.com" in self.text
        assert "/deployments/" in self.text
        assert "/statuses" in self.text

    def test_outputs_present(self) -> None:
        for output in ("logicAppName", "systemTopicName", "eventSubscriptionName"):
            assert f"output {output}" in self.text, f"Missing output: {output}"

    def test_logic_app_expression_uses_multiline_strings(self) -> None:
        # Triple-single-quote strings should be present for Logic App expressions
        assert "'''" in self.text

    def test_no_bicep_string_errors_in_expression_vars(self) -> None:
        # All expr* variables should use triple-quote strings
        # so single-quote-escaped expressions don't confuse the parser
        expr_var_matches = re.findall(r"var expr\w+\s*=\s*(.+)", self.text)
        for match in expr_var_matches:
            stripped = match.strip()
            assert stripped.startswith("'''"), (
                f"Expression variable should use triple-quote string, got: {stripped[:60]}"
            )

    def test_status_message_extracted_from_event_data(self) -> None:
        # Failure description must use statusMessage from Event Grid data
        assert "statusMessage" in self.text

    def test_github_token_stored_as_securestring_parameter(self) -> None:
        assert "securestring" in self.text


# ---------------------------------------------------------------------------
# gitops-compliance.bicep
# ---------------------------------------------------------------------------

class TestGitopsCompliance:
    """Tests for the Policy Compliance Aggregator Logic App module."""

    @pytest.fixture(autouse=True)
    def _content(self) -> None:
        self.path = MODULES_DIR / "gitops-compliance.bicep"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        assert self.path.exists()

    def test_required_parameters_present(self) -> None:
        for param in ("location", "environment", "projectName", "tags",
                      "githubOrg", "githubRepo", "githubToken", "recurrenceIntervalHours"):
            assert f"param {param}" in self.text, f"Missing parameter: {param}"

    def test_github_token_is_secure(self) -> None:
        idx = self.text.index("param githubToken string")
        snippet = self.text[max(0, idx - 60):idx]
        assert "@secure()" in snippet

    def test_logic_app_resource(self) -> None:
        assert "Microsoft.Logic/workflows@2019-05-01" in self.text

    def test_system_assigned_identity(self) -> None:
        assert "SystemAssigned" in self.text

    def test_recurrence_trigger(self) -> None:
        assert "Recurrence" in self.text
        assert "frequency" in self.text
        assert "interval" in self.text
        # Recurrence must be the trigger type (not just a property name)
        assert "type: 'Recurrence'" in self.text

    def test_resource_graph_query_action(self) -> None:
        assert "Microsoft.ResourceGraph/resources" in self.text
        assert "ManagedServiceIdentity" in self.text

    def test_kql_query_targets_iso27001_and_mcsb(self) -> None:
        assert "ISO 27001" in self.text
        assert "MCSB" in self.text or "Microsoft Cloud Security Benchmark" in self.text

    def test_kql_query_filters_non_compliant(self) -> None:
        assert "NonCompliant" in self.text
        assert "complianceState" in self.text

    def test_kql_query_joins_resources(self) -> None:
        # Must join policyresources with resources table
        assert "join kind=inner" in self.text
        assert "Resources" in self.text

    def test_markdown_table_formatting(self) -> None:
        assert "Policy Requirement" in self.text
        assert "Affected Resource ID" in self.text
        assert "|---|---|" in self.text

    def test_select_action_builds_table_rows(self) -> None:
        assert "Select" in self.text
        # Row format: | PolicyRequirement | ResourceId |
        assert "item()[0]" in self.text
        assert "item()[1]" in self.text

    def test_join_action_combines_rows(self) -> None:
        assert "Join" in self.text

    def test_github_issue_search(self) -> None:
        assert "api.github.com" in self.text
        assert "search/issues" in self.text
        assert "[Compliance]" in self.text or "%5BCompliance%5D" in self.text

    def test_compliance_issue_title(self) -> None:
        assert "[Compliance] Infrastructure Remediation Required" in self.text

    def test_creates_or_updates_issue(self) -> None:
        # Both PATCH (update) and POST (create) operations must be present
        assert "'PATCH'" in self.text or '"PATCH"' in self.text
        assert "'POST'" in self.text or '"POST"' in self.text

    def test_labels_on_create(self) -> None:
        # New issues should be labeled
        assert "compliance" in self.text
        assert "infrastructure" in self.text

    def test_subscription_rbac_module_called_with_subscription_scope(self) -> None:
        assert "gitops-compliance-rbac.bicep" in self.text
        assert "scope: subscription()" in self.text

    def test_outputs_present(self) -> None:
        for output in ("logicAppName", "logicAppPrincipalId",
                       "subscriptionReaderRoleAssignmentId"):
            assert f"output {output}" in self.text, f"Missing output: {output}"


# ---------------------------------------------------------------------------
# main-modular.bicep integration
# ---------------------------------------------------------------------------

class TestMainModularGitopsIntegration:
    """Tests that main-modular.bicep correctly wires up the GitOps modules."""

    @pytest.fixture(autouse=True)
    def _content(self) -> None:
        self.text = _read(MAIN_BICEP)

    def test_enable_gitops_feedback_param(self) -> None:
        assert "param enableGitOpsFeedback bool" in self.text

    def test_enable_gitops_compliance_param(self) -> None:
        assert "param enableGitOpsCompliance bool" in self.text

    def test_github_infra_repo_param(self) -> None:
        assert "param githubInfraRepo string" in self.text

    def test_github_token_param(self) -> None:
        assert "param githubToken string" in self.text

    def test_github_token_is_secure(self) -> None:
        idx = self.text.index("param githubToken string")
        snippet = self.text[max(0, idx - 60):idx]
        assert "@secure()" in snippet

    def test_gitops_feedback_module_referenced(self) -> None:
        assert "modules/gitops-feedback.bicep" in self.text

    def test_gitops_compliance_module_referenced(self) -> None:
        assert "modules/gitops-compliance.bicep" in self.text

    def test_gitops_feedback_module_conditional(self) -> None:
        assert "= if (enableGitOpsFeedback)" in self.text

    def test_gitops_compliance_module_conditional(self) -> None:
        assert "= if (enableGitOpsCompliance)" in self.text

    def test_gitops_feedback_output(self) -> None:
        assert "output gitopsFeedbackEnabled bool" in self.text

    def test_gitops_compliance_output(self) -> None:
        assert "output gitopsComplianceEnabled bool" in self.text

    def test_both_modules_disabled_by_default(self) -> None:
        # Default values should be false (opt-in deployment)
        feedback_match = re.search(r"param enableGitOpsFeedback bool\s*=\s*(\w+)", self.text)
        compliance_match = re.search(r"param enableGitOpsCompliance bool\s*=\s*(\w+)", self.text)
        assert feedback_match and feedback_match.group(1) == "false", \
            "enableGitOpsFeedback should default to false"
        assert compliance_match and compliance_match.group(1) == "false", \
            "enableGitOpsCompliance should default to false"


# ---------------------------------------------------------------------------
# Bicep lint validation
# ---------------------------------------------------------------------------

class TestBicepLint:
    """Run az bicep lint on each new module to ensure zero Bicep errors."""

    @pytest.mark.parametrize("module_file", [
        "gitops-compliance-rbac.bicep",
        "gitops-feedback.bicep",
        "gitops-compliance.bicep",
    ])
    def test_no_bicep_errors(self, module_file: str) -> None:
        """Bicep lint must exit 0 (no errors) for the given module file."""
        module_path = MODULES_DIR / module_file
        result = subprocess.run(
            ["az", "bicep", "lint", "--file", str(module_path)],
            capture_output=True,
            text=True,
        )
        errors = [line for line in (result.stdout + result.stderr).splitlines()
                  if ": Error " in line]
        assert result.returncode == 0 and not errors, (
            f"Bicep lint errors in {module_file}:\n"
            + "\n".join(errors)
        )
