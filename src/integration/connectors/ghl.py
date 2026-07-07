"""GoHighLevel connector — the system of engagement (Spec §12.1).

Auth model: OAuth 2.0 Marketplace app with **refresh-token rotation**. GHL has no
true sandbox, so the "sandbox" environment points at a dedicated **test
sub-account/location** (Spec §6).

Inbound GHL webhooks **must have their signatures verified** (Spec §4, §12.1).

Stage 1 implements OAuth (authorize URL, code exchange, refresh rotation), webhook
verification, and the Contacts read/write paths.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..canonical.entities import CanonicalContact
from ..config.settings import get_settings
from ..core.http import request_json
from ..core.logging import get_logger
from ..db.enums import CanonicalEntityType, Environment, System
from .base import (
    ChangeSet,
    Connector,
    ConnectorCapabilities,
    PushResult,
    RateLimit,
)

log = get_logger(__name__)

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_AUTHORIZE_URL = "https://marketplace.gohighlevel.com/oauth/chooselocation"
GHL_TOKEN_URL = f"{GHL_API_BASE}/oauth/token"
GHL_API_VERSION = "2021-07-28"  # required Version header on GHL v2 endpoints


@dataclass(slots=True)
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_in: int = 0


class GHLConnector(Connector):
    capabilities = ConnectorCapabilities(
        key="ghl",
        display_name="GoHighLevel",
        can_read=True,
        can_write=True,
        supports_webhooks=True,
        supported_entities=(
            CanonicalEntityType.CONTACT,
            CanonicalEntityType.DEAL,
        ),
        # GHL enforces burst + daily caps; backoff + jitter handle bursts (Spec §12.1).
        rate_limit=RateLimit(max_requests=100, per_seconds=10, daily_cap=200_000),
    )

    def __init__(
        self,
        environment: Environment,
        client: httpx.AsyncClient | None = None,
        token: OAuthToken | None = None,
    ) -> None:
        super().__init__(environment)
        self._settings = get_settings()
        self._client = client
        # In production these live in Key Vault; injected here for tests / local dev.
        self._token = token

    @property
    def system(self) -> System:
        return System.GHL

    # ── OAuth ──────────────────────────────────────────────────────────────────
    def authorize_url(self, state: str) -> str:
        """The URL to send an admin to, to grant the Marketplace app access (Spec §12.3)."""
        s = self._settings
        params = {
            "response_type": "code",
            "client_id": s.ghl_client_id,
            "redirect_uri": s.ghl_redirect_uri,
            "scope": " ".join(s.ghl_scope_list),
            "state": state,
        }
        return f"{GHL_AUTHORIZE_URL}?{urlencode(params)}"

    async def _post_token(self, grant: dict[str, str]) -> OAuthToken:
        s = self._settings
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        data = {
            "client_id": s.ghl_client_id,
            "client_secret": s.ghl_client_secret,
            **grant,
        }
        resp = await request_json(
            self._client,
            "POST",
            GHL_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        body = resp.json()
        # ⚠️ GHL rotates the refresh token — always persist the NEW one (Spec §12.1).
        self._token = OAuthToken(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_in=body.get("expires_in", 0),
        )
        return self._token

    async def exchange_code(self, code: str) -> OAuthToken:
        """Exchange the OAuth callback ``code`` for the first token pair."""
        return await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.ghl_redirect_uri,
            }
        )

    async def refresh(self) -> OAuthToken:
        """Rotate the refresh token (Spec §12.1). The new refresh token replaces the old."""
        if self._token is None:
            raise RuntimeError("No GHL token to refresh — run the OAuth flow first")
        return await self._post_token(
            {"grant_type": "refresh_token", "refresh_token": self._token.refresh_token}
        )

    async def authenticate(self) -> None:
        if self._token is None:
            raise RuntimeError(
                "GHL is not authorised. Complete the OAuth flow "
                "(GET /oauth/ghl/authorize -> callback) for this environment first."
            )
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        assert self._token is not None
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Version": GHL_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def health(self) -> bool:
        return self._token is not None and self._client is not None

    # ── Translation ────────────────────────────────────────────────────────────
    def _to_canonical(self, item: dict) -> CanonicalContact:
        return CanonicalContact(
            source_system=System.GHL,
            source_id=str(item.get("id")) if item.get("id") else None,
            email=item.get("email"),
            first_name=item.get("firstName"),
            last_name=item.get("lastName"),
            phone=item.get("phone"),
            mobile=item.get("mobilePhone") or item.get("mobile"),
            address1=item.get("address1"),
            city=item.get("city"),
            state=item.get("state"),
            postal_code=item.get("postalCode"),
            country=item.get("country"),
            company_name=item.get("companyName"),
            website=item.get("website"),
            source=item.get("source"),
            tags=list(item.get("tags", [])),
        )

    def _payload(self, contact: CanonicalContact) -> dict:
        payload = {
            "locationId": self._settings.ghl_location_id,
            "email": contact.email,
            "firstName": contact.first_name,
            "lastName": contact.last_name,
            "phone": contact.phone,
            "address1": contact.address1,
            "city": contact.city,
            "state": contact.state,
            "postalCode": contact.postal_code,
            "country": contact.country,
            "companyName": contact.company_name,
            "website": contact.website,
        }
        return {k: v for k, v in payload.items() if v is not None}

    # ── Contacts read / dedupe ──────────────────────────────────────────────────
    async def find_contacts(self, *, email: str | None = None) -> list[CanonicalContact]:
        if not email:
            return []
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/contacts/",
            headers=self._headers(),
            params={"locationId": self._settings.ghl_location_id, "query": email},
        )
        items = resp.json().get("contacts", [])
        return [self._to_canonical(i) for i in items]

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/contacts/{external_id}",
            headers=self._headers(),
        )
        item = resp.json().get("contact")
        return self._to_canonical(item) if item else None

    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            f"{GHL_API_BASE}/contacts/",
            headers=self._headers(),
            json=self._payload(contact),
        )
        new_id = str(resp.json().get("contact", {}).get("id"))
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict[str, object]) -> PushResult:
        # changes keyed by GHL field name (engine resolves direction); strip Nones.
        body = {k: v for k, v in changes.items() if v is not None}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PUT",
            f"{GHL_API_BASE}/contacts/{external_id}",
            headers=self._headers(),
            json=body,
        )
        return PushResult(ok=True, external_id=external_id, detail=f"updated:{resp.status_code}")

    # ── Polling / webhooks ──────────────────────────────────────────────────────
    async def fetch_changes(
        self, entity_type: CanonicalEntityType, *, cursor: str | None = None
    ) -> ChangeSet:
        # GHL emits webhooks for contacts; polling is a reconciliation backstop (Stage 2+).
        return ChangeSet()

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify an inbound GHL webhook signature (Spec §4, §12.1).

        GHL signs webhook payloads; we compute an HMAC-SHA256 over the raw body with
        the shared ``GHL_WEBHOOK_SECRET`` and constant-time compare against the
        signature header. (If your Marketplace app is configured for RSA public-key
        signatures instead, swap this body for a public-key verify — the call site is
        unchanged.) An unverified webhook is rejected, never processed."""
        secret = self._settings.ghl_webhook_secret
        if not secret:
            log.warning("GHL_WEBHOOK_SECRET unset — rejecting webhook (fail closed)")
            return False
        provided = headers.get("x-wh-signature") or headers.get("X-Wh-Signature") or ""
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(provided, expected)
