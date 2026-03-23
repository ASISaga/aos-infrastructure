"""Tests for ``orchestrator.cli.azure_ops`` — Azure SDK CLI helper.

Validates that each subcommand correctly calls the Azure SDK and formats
output.  Azure SDK classes are mocked at the import boundary.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from orchestrator.cli import azure_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cli(args: list[str], capsys: pytest.CaptureFixture) -> str:
    """Run azure_ops.main() with the given CLI args and return stdout."""
    with mock.patch("sys.argv", ["azure_ops.py"] + args):
        azure_ops.main()
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# resource-group-exists
# ---------------------------------------------------------------------------

class TestResourceGroupExists:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_exists_true(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        mock_rc.return_value.resource_groups.check_existence.return_value = True
        out = _run_cli(["--subscription-id", "sub-1", "resource-group-exists", "--resource-group", "rg-test"], capsys)
        assert out.strip() == "true"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_exists_false(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        mock_rc.return_value.resource_groups.check_existence.return_value = False
        out = _run_cli(["--subscription-id", "sub-1", "resource-group-exists", "--resource-group", "rg-missing"], capsys)
        assert out.strip() == "false"


# ---------------------------------------------------------------------------
# resource-group-show
# ---------------------------------------------------------------------------

class TestResourceGroupShow:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_shows_json(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        rg = mock.MagicMock()
        rg.name = "rg-test"
        rg.location = "eastus"
        rg.properties.provisioning_state = "Succeeded"
        rg.tags = {"env": "dev"}
        mock_rc.return_value.resource_groups.get.return_value = rg
        out = _run_cli(["--subscription-id", "sub-1", "resource-group-show", "--resource-group", "rg-test"], capsys)
        data = json.loads(out)
        assert data["name"] == "rg-test"
        assert data["location"] == "eastus"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_not_found_exits(self, mock_rc: mock.MagicMock) -> None:
        mock_rc.return_value.resource_groups.get.side_effect = Exception("Not found")
        with pytest.raises(SystemExit):
            with mock.patch("sys.argv", ["azure_ops.py", "--subscription-id", "sub-1",
                                         "resource-group-show", "--resource-group", "missing"]):
                azure_ops.main()


# ---------------------------------------------------------------------------
# list-resources
# ---------------------------------------------------------------------------

class TestListResources:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_json_output(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        r = mock.MagicMock()
        r.name = "func-app"
        r.type = "Microsoft.Web/sites"
        r.location = "eastus"
        r.properties = {"provisioningState": "Succeeded"}
        mock_rc.return_value.resources.list_by_resource_group.return_value = [r]
        out = _run_cli(["--subscription-id", "sub-1", "list-resources", "--resource-group", "rg-test", "--output", "json"], capsys)
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["name"] == "func-app"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_count_output(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        r = mock.MagicMock()
        r.name = "res1"
        r.type = "Microsoft.Storage/storageAccounts"
        r.location = "eastus"
        r.properties = {}
        mock_rc.return_value.resources.list_by_resource_group.return_value = [r, r]
        out = _run_cli(["--subscription-id", "sub-1", "list-resources", "--resource-group", "rg-test"], capsys)
        # Default output is "json", so parse it
        data = json.loads(out)
        assert len(data) == 2


# ---------------------------------------------------------------------------
# list-deployments
# ---------------------------------------------------------------------------

class TestListDeployments:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_json_output(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        d = mock.MagicMock()
        d.name = "deploy-1"
        d.properties.provisioning_state = "Succeeded"
        d.properties.timestamp.isoformat.return_value = "2026-01-01T00:00:00"
        d.properties.error = None
        mock_rc.return_value.deployments.list_by_resource_group.return_value = [d]
        out = _run_cli(["--subscription-id", "sub-1", "list-deployments", "--resource-group", "rg-test"], capsys)
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["name"] == "deploy-1"
        assert data[0]["state"] == "Succeeded"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_failed_filter(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        d1 = mock.MagicMock()
        d1.name = "deploy-ok"
        d1.properties.provisioning_state = "Succeeded"
        d1.properties.timestamp.isoformat.return_value = "2026-01-01T00:00:00"
        d1.properties.error = None

        d2 = mock.MagicMock()
        d2.name = "deploy-fail"
        d2.properties.provisioning_state = "Failed"
        d2.properties.timestamp.isoformat.return_value = "2026-01-02T00:00:00"
        d2.properties.error.code = "BadRequest"
        d2.properties.error.message = "Something went wrong"

        mock_rc.return_value.deployments.list_by_resource_group.return_value = [d1, d2]
        out = _run_cli(["--subscription-id", "sub-1", "list-deployments", "--resource-group", "rg-test", "--query", "failed"], capsys)
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["name"] == "deploy-fail"


# ---------------------------------------------------------------------------
# list-function-apps
# ---------------------------------------------------------------------------

class TestListFunctionApps:

    @mock.patch("orchestrator.cli.azure_ops._web_client")
    def test_names_output(self, mock_wc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        app1 = mock.MagicMock()
        app1.name = "func-my-app"
        app1.kind = "functionapp,linux"
        app1.default_host_name = "func-my-app.azurewebsites.net"
        app1.state = "Running"

        app2 = mock.MagicMock()
        app2.name = "webapp-not-func"
        app2.kind = "app"
        app2.default_host_name = "webapp.azurewebsites.net"
        app2.state = "Running"

        mock_wc.return_value.web_apps.list_by_resource_group.return_value = [app1, app2]
        out = _run_cli(["--subscription-id", "sub-1", "list-function-apps", "--resource-group", "rg-test", "--output", "names"], capsys)
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 1
        assert lines[0] == "func-my-app"


# ---------------------------------------------------------------------------
# show-source-control
# ---------------------------------------------------------------------------

class TestShowSourceControl:

    @mock.patch("orchestrator.cli.azure_ops._web_client")
    def test_configured(self, mock_wc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        sc = mock.MagicMock()
        sc.repo_url = "https://github.com/ASISaga/test-repo"
        sc.branch = "main"
        sc.is_manual_integration = False
        mock_wc.return_value.web_apps.get_source_control.return_value = sc
        out = _run_cli(["--subscription-id", "sub-1", "show-source-control", "--resource-group", "rg-test", "--name", "func-app"], capsys)
        data = json.loads(out)
        assert data["repoUrl"] == "https://github.com/ASISaga/test-repo"

    @mock.patch("orchestrator.cli.azure_ops._web_client")
    def test_not_configured(self, mock_wc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        mock_wc.return_value.web_apps.get_source_control.side_effect = Exception("Not found")
        out = _run_cli(["--subscription-id", "sub-1", "show-source-control", "--resource-group", "rg-test", "--name", "func-app"], capsys)
        data = json.loads(out)
        assert data["repoUrl"] == ""


# ---------------------------------------------------------------------------
# list-keyvaults
# ---------------------------------------------------------------------------

class TestListKeyvaults:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_uri_output(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        kv = mock.MagicMock()
        kv.name = "kv-aos-dev"
        kv.properties = {"vaultUri": "https://kv-aos-dev.vault.azure.net/"}
        mock_rc.return_value.resources.list_by_resource_group.return_value = [kv]
        out = _run_cli(["--subscription-id", "sub-1", "list-keyvaults", "--resource-group", "rg-test", "--output", "uri"], capsys)
        assert out.strip() == "https://kv-aos-dev.vault.azure.net/"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_uri_empty_when_no_vaults(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        mock_rc.return_value.resources.list_by_resource_group.return_value = []
        out = _run_cli(["--subscription-id", "sub-1", "list-keyvaults", "--resource-group", "rg-test", "--output", "uri"], capsys)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# list-servicebus-namespaces
# ---------------------------------------------------------------------------

class TestListServiceBusNamespaces:

    @mock.patch("orchestrator.cli.azure_ops._servicebus_client")
    def test_names_output(self, mock_sbc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        ns = mock.MagicMock()
        ns.name = "sb-aos-dev"
        ns.status = "Active"
        ns.location = "eastus"
        mock_sbc.return_value.namespaces.list_by_resource_group.return_value = [ns]
        out = _run_cli(["--subscription-id", "sub-1", "list-servicebus-namespaces", "--resource-group", "rg-test", "--output", "names"], capsys)
        assert out.strip() == "sb-aos-dev"


# ---------------------------------------------------------------------------
# show-resource
# ---------------------------------------------------------------------------

class TestShowResource:

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_found(self, mock_rc: mock.MagicMock, capsys: pytest.CaptureFixture) -> None:
        r = mock.MagicMock()
        r.name = "func-app"
        r.type = "Microsoft.Web/sites"
        r.location = "eastus"
        r.id = "/sub/rg/func-app"
        r.properties = {"provisioningState": "Succeeded"}
        r.tags = {"env": "dev"}
        mock_rc.return_value.resources.list_by_resource_group.return_value = [r]
        out = _run_cli(["--subscription-id", "sub-1", "show-resource", "--resource-group", "rg-test", "--name", "func-app"], capsys)
        data = json.loads(out)
        assert data["name"] == "func-app"
        assert data["type"] == "Microsoft.Web/sites"

    @mock.patch("orchestrator.cli.azure_ops._resource_client")
    def test_not_found_exits(self, mock_rc: mock.MagicMock) -> None:
        mock_rc.return_value.resources.list_by_resource_group.return_value = []
        with pytest.raises(SystemExit):
            with mock.patch("sys.argv", ["azure_ops.py", "--subscription-id", "sub-1",
                                         "show-resource", "--resource-group", "rg-test", "--name", "missing"]):
                azure_ops.main()
