"""Tests for the Managed Identity client and Key Vault identity store.

Validates that ``ManagedIdentityClient`` correctly wraps
``ManagedServiceIdentityClient`` and that ``KeyVaultIdentityStore``
stores and retrieves client IDs via the Azure Key Vault SDK.

All tests mock the Azure SDK — no real Azure credentials are required.
"""

from __future__ import annotations

from unittest import mock

from azure.core.exceptions import ResourceNotFoundError

from orchestrator.integration.identity_client import (
    IdentityInfo,
    KeyVaultIdentityStore,
    ManagedIdentityClient,
)


# ====================================================================
# IdentityInfo tests
# ====================================================================


class TestIdentityInfo:
    """Tests for the IdentityInfo dataclass."""

    def test_fields_accessible(self) -> None:
        info = IdentityInfo(
            name="id-agent-operating-system-dev",
            client_id="aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa",
            principal_id="bbbbbbbb-0000-0000-0000-bbbbbbbbbbbb",
            resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/id",
            location="eastus",
            resource_group="rg-aos-dev",
        )
        assert info.name == "id-agent-operating-system-dev"
        assert info.client_id == "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
        assert info.resource_group == "rg-aos-dev"


# ====================================================================
# ManagedIdentityClient tests
# ====================================================================


@mock.patch(
    "orchestrator.integration.identity_client.ManagedServiceIdentityClient"
)
@mock.patch(
    "orchestrator.integration.identity_client.DefaultAzureCredential"
)
class TestManagedIdentityClient:
    """Tests for ManagedIdentityClient using mocked Azure MSI SDK."""

    def _make_identity(self, name: str, client_id: str) -> mock.MagicMock:
        """Build a minimal mock MSI identity object."""
        identity = mock.MagicMock()
        identity.name = name
        identity.client_id = client_id
        identity.principal_id = "principal-" + client_id
        identity.id = f"/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity/{name}"
        identity.location = "eastus"
        return identity

    def test_init_creates_msi_client(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        ManagedIdentityClient("sub-123", "rg-aos-dev")
        mock_msi.assert_called_once()
        mock_credential.assert_called_once()

    def test_get_identity_success(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        mock_identity = self._make_identity(
            "id-agent-operating-system-dev",
            "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
        )
        mock_msi.return_value.user_assigned_identities.get.return_value = mock_identity

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        info = client.get_identity("id-agent-operating-system-dev")

        assert info is not None
        assert info.client_id == "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa"
        assert info.name == "id-agent-operating-system-dev"
        assert info.resource_group == "rg-aos-dev"

    def test_get_identity_not_found(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        mock_msi.return_value.user_assigned_identities.get.side_effect = Exception(
            "ResourceNotFound"
        )

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        info = client.get_identity("id-nonexistent-dev")

        assert info is None

    def test_get_client_id_success(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        mock_identity = self._make_identity(
            "id-business-infinity-dev",
            "cccccccc-2222-2222-2222-cccccccccccc",
        )
        mock_msi.return_value.user_assigned_identities.get.return_value = mock_identity

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        client_id = client.get_client_id("business-infinity", "dev")

        assert client_id == "cccccccc-2222-2222-2222-cccccccccccc"
        # Verify the identity name is constructed correctly
        mock_msi.return_value.user_assigned_identities.get.assert_called_once_with(
            "rg-aos-dev", "id-business-infinity-dev"
        )

    def test_get_client_id_not_found(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        mock_msi.return_value.user_assigned_identities.get.side_effect = Exception(
            "ResourceNotFound"
        )

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        client_id = client.get_client_id("nonexistent-app", "dev")

        assert client_id is None

    def test_list_function_app_identities_filters_by_prefix(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        identities = [
            self._make_identity("id-agent-operating-system-dev", "aaaa-1111"),
            self._make_identity("id-mcp-erpnext-dev", "bbbb-2222"),
            self._make_identity("other-resource-dev", "cccc-3333"),
        ]
        mock_msi.return_value.user_assigned_identities.list_by_resource_group.return_value = identities

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        result = client.list_function_app_identities(prefix="id-")

        assert len(result) == 2
        names = {r.name for r in result}
        assert "id-agent-operating-system-dev" in names
        assert "id-mcp-erpnext-dev" in names
        assert "other-resource-dev" not in names

    def test_list_function_app_identities_empty(
        self, mock_credential: mock.MagicMock, mock_msi: mock.MagicMock
    ) -> None:
        mock_msi.return_value.user_assigned_identities.list_by_resource_group.return_value = []

        client = ManagedIdentityClient("sub-123", "rg-aos-dev")
        result = client.list_function_app_identities()

        assert result == []


# ====================================================================
# KeyVaultIdentityStore tests
# ====================================================================


@mock.patch("orchestrator.integration.identity_client.DefaultAzureCredential")
@mock.patch("orchestrator.integration.identity_client.SecretClient")
class TestKeyVaultIdentityStore:
    """Tests for KeyVaultIdentityStore using mocked Key Vault SDK."""

    def test_secret_name_format(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        assert (
            KeyVaultIdentityStore.secret_name("agent-operating-system", "dev")
            == "clientid-agent-operating-system-dev"
        )

    def test_secret_name_mcp(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        assert (
            KeyVaultIdentityStore.secret_name("mcp-erpnext", "prod")
            == "clientid-mcp-erpnext-prod"
        )

    def test_init_creates_secret_client(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        KeyVaultIdentityStore("https://kv-aos-dev.vault.azure.net")
        mock_sc.assert_called_once()
        mock_credential.assert_called_once()

    def test_set_client_id_calls_set_secret(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        store = KeyVaultIdentityStore("https://kv-aos-dev.vault.azure.net")
        store.set_client_id("agent-operating-system", "dev", "test-uuid-1234")

        mock_sc.return_value.set_secret.assert_called_once_with(
            "clientid-agent-operating-system-dev", "test-uuid-1234"
        )

    def test_get_client_id_returns_value(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        mock_secret = mock.MagicMock()
        mock_secret.value = "retrieved-uuid-5678"
        mock_sc.return_value.get_secret.return_value = mock_secret

        store = KeyVaultIdentityStore("https://kv-aos-dev.vault.azure.net")
        result = store.get_client_id("business-infinity", "staging")

        assert result == "retrieved-uuid-5678"
        mock_sc.return_value.get_secret.assert_called_once_with(
            "clientid-business-infinity-staging"
        )

    def test_get_client_id_not_found_returns_none(
        self, mock_sc: mock.MagicMock, mock_credential: mock.MagicMock
    ) -> None:
        mock_sc.return_value.get_secret.side_effect = ResourceNotFoundError("not found")

        store = KeyVaultIdentityStore("https://kv-aos-dev.vault.azure.net")
        result = store.get_client_id("nonexistent-app", "dev")

        assert result is None
