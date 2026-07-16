"""Portal system-health endpoint (/portal/api/health).

DB-only snapshot, safe to poll. The test drives the ASGI app in-process via
httpx ASGITransport (same event loop/thread as the test), so the endpoint's
session_scope() shares the conftest in-memory SQLite connection and sees the
seeded rows. A minimal app mounts only the portal router, so the main app's
lifespan (poller/scheduler) never starts.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from integration.api.portal import router
from integration.config.settings import get_settings
from integration.db.base import Base, utcnow
from integration.db.enums import CanonicalEntityType, Environment, System
from integration.db.models import EntityMapping, OAuthTokenStore, SyncCursor
from integration.db.session import get_engine, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers() -> dict:
    # Auth uses APPROVAL_CALLBACK_SECRET when configured; send whatever is set so
    # the test is independent of the developer's local .env value.
    secret = get_settings().approval_callback_secret
    return {"x-admin-token": secret} if secret else {}


def _async_client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_headers(),
    )


def _seed() -> None:
    # Clean slate on the shared in-memory engine: other test files leave rows
    # (e.g. a 'contact' sync_cursor) that would collide with the seed below.
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    sess = get_sessionmaker()()
    try:
        for et, aid, gid in [
            (CanonicalEntityType.CONTACT, "c-h1", "ghl-c-h1"),
            (CanonicalEntityType.CONTACT, "c-h2", "ghl-c-h2"),
            (CanonicalEntityType.COMPANY, "co-h1", "ghl-co-h1"),
        ]:
            sess.add(
                EntityMapping(
                    canonical_entity_type=et,
                    autotask_id=aid,
                    ghl_id=gid,
                    environment=Environment.SANDBOX,
                    last_synced_at=utcnow(),
                )
            )
        sess.add(
            OAuthTokenStore(
                environment=Environment.SANDBOX,
                system=System.GHL,
                access_token="tok-abc",
                refresh_token="ref-abc",
                expires_in=3600,
            )
        )
        sess.add(
            SyncCursor(
                environment=Environment.SANDBOX,
                source_system=System.AUTOTASK,
                entity_type="contact",
                cursor="id:12345",
            )
        )
        sess.commit()
    finally:
        sess.close()


async def test_health_reports_mappings_connectors_and_cursors() -> None:
    _seed()
    async with _async_client() as client:
        r = await client.get("/portal/api/health")
    assert r.status_code == 200
    d = r.json()

    # Mapping counts by entity type (>= what we seeded — other tests may add rows).
    assert d["mappings"]["contact"] >= 2
    assert d["mappings"]["company"] >= 1
    assert set(d["mappings"]) == {"contact", "company", "deal", "service_item"}

    # GHL shows authorised once a token row exists; Autotask config reflects settings.
    assert d["connectors"]["ghl"]["authorized"] is True
    assert "configured" in d["connectors"]["autotask"]

    # Background job config is surfaced for at-a-glance ops visibility.
    assert "poller_enabled" in d["background"]
    assert d["background"]["poll_interval_s"] > 0

    # The seeded polling cursor is listed.
    assert any(c["entity_type"] == "contact" and c["cursor"] == "id:12345" for c in d["cursors"])


async def test_health_ghl_unauthorized_when_no_token() -> None:
    # Fresh in-memory schema with NO token row -> GHL reads as not authorised.
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    async with _async_client() as client:
        r = await client.get("/portal/api/health")
    assert r.status_code == 200
    assert r.json()["connectors"]["ghl"]["authorized"] is False
