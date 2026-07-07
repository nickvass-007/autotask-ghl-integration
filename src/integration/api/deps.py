"""Connector dependencies / lifecycle for the API (Spec §6, §12).

Connectors are built for the **active environment** (sandbox by default). The GHL
OAuth token is held here for local dev after the OAuth callback; ⚠️ in production
it lives in Key Vault and is loaded via Managed Identity, never kept in process
memory like this.
"""

from __future__ import annotations

from ..config.settings import get_settings
from ..connectors.autotask import AutotaskConnector
from ..connectors.ghl import GHLConnector, OAuthToken

# Local-dev token holder (replaced by Key Vault in production).
_ghl_token: OAuthToken | None = None


def set_ghl_token(token: OAuthToken) -> None:
    global _ghl_token
    _ghl_token = token


def get_ghl() -> GHLConnector:
    return GHLConnector(get_settings().environment, token=_ghl_token)


async def get_autotask() -> AutotaskConnector:
    connector = AutotaskConnector(get_settings().environment)
    await connector.authenticate()
    return connector
