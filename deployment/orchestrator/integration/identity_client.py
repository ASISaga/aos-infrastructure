"""Managed Identity client — fetch Function App client IDs via the Azure MSI SDK.

``ManagedIdentityClient`` wraps ``ManagedServiceIdentityClient`` from
``azure.mgmt.msi`` to retrieve User-Assigned Managed Identity details
programmatically, removing any dependency on the Azure portal or CLI.

``KeyVaultIdentityStore`` stores and retrieves Managed Identity client IDs
in Azure Key Vault, providing the decoupled state-sharing mechanism between
the Bicep infrastructure repository and the code repositories.  Code
repositories authenticate with GitHub OIDC using the ``clientId`` stored
here, without manual secret-copying steps.

Usage
-----
>>> client = ManagedIdentityClient("sub-123", "rg-aos-dev")
>>> client_id = client.get_client_id("agent-operating-system", "dev")
>>> store = KeyVaultIdentityStore("https://kv-aos-dev-abc.vault.azure.net")
>>> store.set_client_id("agent-operating-system", "dev", client_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from azure.core.exceptions import ResourceNotFoundError  # type: ignore[import]
from azure.identity import DefaultAzureCredential  # type: ignore[import]
from azure.keyvault.secrets import SecretClient  # type: ignore[import]
from azure.mgmt.msi import ManagedServiceIdentityClient  # type: ignore[import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IdentityInfo:
    """Details of a User-Assigned Managed Identity."""

    name: str
    client_id: str
    principal_id: str
    resource_id: str
    location: str
    resource_group: str


# ---------------------------------------------------------------------------
# Managed Identity client
# ---------------------------------------------------------------------------


class ManagedIdentityClient:
    """Fetch User-Assigned Managed Identity details via the Azure MSI SDK.

    Uses ``ManagedServiceIdentityClient`` from ``azure.mgmt.msi`` instead of
    the Azure portal or CLI so that identity retrieval is fully automated and
    auditable as part of the provisioning pipeline.

    Parameters
    ----------
    subscription_id:
        Azure subscription ID.
    resource_group:
        Resource group containing the managed identities.
    """

    def __init__(self, subscription_id: str, resource_group: str) -> None:
        self.subscription_id = subscription_id
        self.resource_group = resource_group

        credential = DefaultAzureCredential()
        self._msi_client = ManagedServiceIdentityClient(
            credential, self.subscription_id
        )
        logger.info(
            "ManagedIdentityClient initialised for %s/%s",
            subscription_id,
            resource_group,
        )

    def get_identity(self, identity_name: str) -> Optional[IdentityInfo]:
        """Retrieve a single User-Assigned Managed Identity by name.

        Returns ``None`` when the identity does not exist or cannot be read.

        Parameters
        ----------
        identity_name:
            Name of the managed identity (e.g. ``id-agent-operating-system-dev``).
        """
        try:
            identity = self._msi_client.user_assigned_identities.get(
                self.resource_group, identity_name
            )
            return IdentityInfo(
                name=identity_name,
                client_id=identity.client_id or "",
                principal_id=identity.principal_id or "",
                resource_id=identity.id or "",
                location=identity.location or "",
                resource_group=self.resource_group,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Could not retrieve identity %s: %s", identity_name, exc)
            return None

    def get_client_id(self, app_name: str, environment: str) -> Optional[str]:
        """Fetch the client ID for a Function App's User-Assigned Managed Identity.

        Constructs the identity name as ``id-{app_name}-{environment}`` and
        returns the ``clientId`` property.  This is the value required as
        ``AZURE_CLIENT_ID`` in GitHub OIDC authentication.

        Parameters
        ----------
        app_name:
            Azure-safe application name (e.g. ``agent-operating-system``).
        environment:
            Deployment environment (``dev``, ``staging``, or ``prod``).
        """
        identity_name = f"id-{app_name}-{environment}"
        info = self.get_identity(identity_name)
        return info.client_id if info else None

    def list_function_app_identities(self, prefix: str = "id-") -> list[IdentityInfo]:
        """List all User-Assigned Managed Identities matching a name prefix.

        Parameters
        ----------
        prefix:
            Name prefix to filter identities (default: ``id-``).
        """
        results: list[IdentityInfo] = []
        for identity in self._msi_client.user_assigned_identities.list_by_resource_group(
            self.resource_group
        ):
            name = identity.name or ""
            if not name.startswith(prefix):
                continue
            results.append(
                IdentityInfo(
                    name=name,
                    client_id=identity.client_id or "",
                    principal_id=identity.principal_id or "",
                    resource_id=identity.id or "",
                    location=identity.location or "",
                    resource_group=self.resource_group,
                )
            )
        return results


# ---------------------------------------------------------------------------
# Key Vault identity store
# ---------------------------------------------------------------------------


class KeyVaultIdentityStore:
    """Store and retrieve Managed Identity client IDs in Azure Key Vault.

    Uses Azure Key Vault as the **state-sharing mechanism** between the Bicep
    infrastructure repository and code repositories.  After the provisioning
    pipeline stores client IDs here, code repositories can retrieve
    ``AZURE_CLIENT_ID`` via ``az keyvault secret show`` (authenticated with
    their own OIDC token) instead of relying on manual secret setup.

    Secret naming convention: ``clientid-{app-name}-{environment}``
    (Key Vault secret names allow hyphens).

    Parameters
    ----------
    vault_url:
        HTTPS URL of the Key Vault
        (e.g. ``https://kv-aos-dev-abc.vault.azure.net``).
    """

    _SECRET_PREFIX = "clientid-"

    def __init__(self, vault_url: str) -> None:
        self.vault_url = vault_url
        credential = DefaultAzureCredential()
        self._client = SecretClient(vault_url=vault_url, credential=credential)
        logger.info("KeyVaultIdentityStore initialised for %s", vault_url)

    @staticmethod
    def secret_name(app_name: str, environment: str) -> str:
        """Return the Key Vault secret name for a given app and environment."""
        return f"clientid-{app_name}-{environment}"

    def set_client_id(self, app_name: str, environment: str, client_id: str) -> None:
        """Store a Managed Identity client ID as a Key Vault secret.

        Parameters
        ----------
        app_name:
            Azure-safe application name.
        environment:
            Deployment environment.
        client_id:
            Managed Identity client ID (UUID string).
        """
        name = self.secret_name(app_name, environment)
        self._client.set_secret(name, client_id)
        logger.info(
            "Stored client ID for %s/%s in Key Vault", app_name, environment
        )

    def get_client_id(self, app_name: str, environment: str) -> Optional[str]:
        """Retrieve a Managed Identity client ID from Key Vault.

        Returns ``None`` when the secret does not exist.

        Parameters
        ----------
        app_name:
            Azure-safe application name.
        environment:
            Deployment environment.
        """
        name = self.secret_name(app_name, environment)
        try:
            secret = self._client.get_secret(name)
            return secret.value
        except ResourceNotFoundError:
            logger.warning(
                "No client ID secret found for %s/%s", app_name, environment
            )
            return None
