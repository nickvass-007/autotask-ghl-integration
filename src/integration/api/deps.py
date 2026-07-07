"""Connector dependencies / lifecycle for the API (Spec §6, §12).

Connectors are built for the **active environment** (sandbox by default).

The GHL OAuth token is persisted to the ``oauth_token_store`` table so the
grant survives API restarts — GHL ROTATES the refresh token on every refresh,
so every rotation is written back immediately; losing one forces a manual
re-auth. A process-memory copy is kept as a cache. ⚠️ In production the token
lives in Azure Key Vault via Managed Identity instead (Spec §12.3).
"""

from __future__ import annotations

from sqlalchemy import select

from ..config.settings import get_settings
from ..connectors.autotask import AutotaskConnector
from ..connectors.ghl import GHLConnector, OAuthToken
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import System
from ..db.models import OAuthTokenStore
from ..db.session import session_scope

log = get_logger(__name__)

# Process-memory cache over the DB row.
_ghl_token: OAuthToken | None = None


def set_ghl_token(token: OAuthToken) -> None:
    """Cache + persist the (rotated) GHL token pair."""
    global _ghl_token
    _ghl_token = token
    try:
        env = get_settings().environment
        with session_scope() as session:
            row = session.execute(
                select(OAuthTokenStore).where(
                    OAuthTokenStore.environment == env,
                    OAuthTokenStore.system == System.GHL,
                )
            ).scalar_one_or_none()
            if row is None:
                row = OAuthTokenStore(environment=env, system=System.GHL,
                                      access_token="", refresh_token="")
                session.add(row)
            row.access_token = token.access_token
            row.refresh_token = token.refresh_token
            row.expires_in = token.expires_in
            row.updated_at = utcnow()
    except Exception:
        # Memory still holds the token; persistence failure only costs a re-auth
        # after the next restart. Never let it break the OAuth flow itself.
        log.exception("Failed to persist GHL token — grant will not survive a restart")


def _load_ghl_token() -> OAuthToken | None:
    env = get_settings().environment
    with session_scope() as session:
        row = session.execute(
            select(OAuthTokenStore).where(
                OAuthTokenStore.environment == env,
                OAuthTokenStore.system == System.GHL,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return OAuthToken(
            access_token=row.access_token,
            refresh_token=row.refresh_token,
            expires_in=row.expires_in,
        )


def get_ghl() -> GHLConnector:
    global _ghl_token
    if _ghl_token is None:
        try:
            _ghl_token = _load_ghl_token()
            if _ghl_token is not None:
                log.info("GHL token restored from store — no re-auth needed")
        except Exception:
            log.exception("Could not load persisted GHL token")
    # Every rotation the connector performs is written straight back.
    return GHLConnector(
        get_settings().environment, token=_ghl_token, on_token_update=set_ghl_token
    )


async def get_autotask() -> AutotaskConnector:
    connector = AutotaskConnector(get_settings().environment)
    await connector.authenticate()
    return connector
