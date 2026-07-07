"""GoHighLevel connector — the system of engagement (Spec §12.1).

Auth model: OAuth 2.0 Marketplace app with **refresh-token rotation**. GHL has no
true sandbox, so the "sandbox" environment points at a dedicated **test
sub-account/location** (Spec §6).

Inbound GHL webhooks **must have their signatures verified** (Spec §4, §12.1).

Stage 1 implements OAuth (authorize URL, code exchange, refresh rotation), webhook
verification, and the Contacts read/write paths.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

import httpx

from ..canonical.entities import CanonicalContact, CanonicalDeal
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

# GHL's published webhook-signing public keys (marketplace docs, "Webhook
# Integration Guide"). Marketplace-app webhooks are signed by GHL's PRIVATE key —
# there is no per-app shared secret. Current scheme: Ed25519 over the raw body,
# base64 signature in `x-ghl-signature`. Legacy: RSA-SHA256 in `x-wh-signature`
# (sunset 2026-07-01, kept for the transition window).
GHL_ED25519_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=
-----END PUBLIC KEY-----
"""
GHL_LEGACY_RSA_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAokvo/r9tVgcfZ5DysOSC
Frm602qYV0MaAiNnX9O8KxMbiyRKWeL9JpCpVpt4XHIcBOK4u3cLSqJGOLaPuXw6
dO0t6Q/ZVdAV5Phz+ZtzPL16iCGeK9po6D6JHBpbi989mmzMryUnQJezlYJ3DVfB
csedpinheNnyYeFXolrJvcsjDtfAeRx5ByHQmTnSdFUzuAnC9/GepgLT9SM4nCpv
uxmZMxrJt5Rw+VUaQ9B8JSvbMPpez4peKaJPZHBbU3OdeCVx5klVXXZQGNHOs8gF
3kvoV5rTnXV0IknLBXlcKKAQLZcY/Q9rG6Ifi9c+5vqlvHPCUJFT5XUGG5RKgOKU
J062fRtN+rLYZUV+BjafxQauvC8wSWeYja63VSUruvmNj8xkx2zE/Juc+yjLjTXp
IocmaiFeAO6fUtNjDeFVkhf5LNb59vECyrHD2SQIrhgXpO4Q3dVNA5rw576PwTzN
h/AMfHKIjE4xQA1SZuYJmNnmVZLIZBlQAF9Ntd03rfadZ+yDiOXCCs9FkHibELhC
HULgCsnuDJHcrGNd5/Ddm5hxGQ0ASitgHeMZ0kcIOwKDOzOU53lDza6/Y09T7sYJ
PQe7z0cvj7aE4B+Ax1ZoZGPzpJlZtGXCsu9aTEGEnKzmsFqwcSsnw3JB31IGKAyk
T1hhTiaCeIY/OwwwNUY2yvcCAwEAAQ==
-----END PUBLIC KEY-----
"""

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
        on_token_update=None,
    ) -> None:
        super().__init__(environment)
        self._settings = get_settings()
        self._client = client
        # In production these live in Key Vault; injected here for tests / local dev.
        self._token = token
        # ⚠️ GHL rotates the refresh token on every refresh — the owner of the
        # token store passes a callback so no rotation is ever lost.
        self._on_token_update = on_token_update

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
        if self._on_token_update is not None:
            self._on_token_update(self._token)
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
            # Set when the contact's Autotask Account is mirrored as a GHL
            # Business (see sync/companies.py) — links the contact to it.
            "businessId": contact.extra.get("ghl_business_id"),
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
        """Create a GHL contact, resolving the location's duplicate policy.

        Locations with "no duplicated contacts" reject creates that match an
        existing contact and name the ``matchingField`` in the error body:

        - match on **phone**: usually colleagues sharing an office line — we
          retry WITHOUT the phone so each person stays a distinct contact.
        - match on **email** (or anything else identifying): a genuine dupe —
          adopt the existing GHL contact id so the mapping links instead of
          duplicating.
        """
        payload = self._payload(contact)
        dropped: list[str] = []
        for _ in range(4):
            try:
                resp = await request_json(
                    self._client,  # type: ignore[arg-type]
                    "POST",
                    f"{GHL_API_BASE}/contacts/",
                    headers=self._headers(),
                    json=payload,
                )
                new_id = str(resp.json().get("contact", {}).get("id"))
                detail = "created" if not dropped else f"created_dropped:{','.join(dropped)}"
                return PushResult(ok=True, external_id=new_id, detail=detail)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400, 422):
                    raise
                try:
                    data = exc.response.json()
                except ValueError:
                    raise exc from None
                message = str(data.get("message", ""))

                # Location dedupe policy: phone match = shared office line (keep
                # the person, drop the phone); anything else = genuine existing
                # contact — adopt its id so the mapping links, not duplicates.
                if "duplicated contacts" in message:
                    meta = data.get("meta") or {}
                    matching = meta.get("matchingField")
                    existing_id = meta.get("contactId")
                    if matching == "phone" and payload.get("phone"):
                        payload.pop("phone")
                        dropped.append("phone(shared)")
                        continue
                    if existing_id:
                        return PushResult(
                            ok=True,
                            external_id=str(existing_id),
                            detail=f"deduped_existing:{matching}",
                        )
                    raise

                # Data-quality salvage: drop the single invalid field and keep the
                # person, PROVIDED another identifier (email or phone) remains.
                if "email must be an email" in message and payload.get("email"):
                    if not payload.get("phone"):
                        raise  # no identifier would remain — surface as data quality
                    payload.pop("email")
                    dropped.append("email(invalid)")
                    continue
                if "did not seem to be a phone number" in message and payload.get("phone"):
                    if not payload.get("email"):
                        raise
                    payload.pop("phone")
                    dropped.append("phone(invalid)")
                    continue
                raise
        return PushResult(ok=False, detail="create retries exhausted")

    async def update_contact(self, external_id: str, changes: dict[str, object]) -> PushResult:
        # changes keyed by GHL field name (engine resolves direction); strip Nones.
        body = {k: v for k, v in changes.items() if v is not None}
        dropped: list[str] = []
        for _ in range(4):
            if not body:
                # every field was dropped as invalid/conflicting — nothing to apply
                return PushResult(
                    ok=True, external_id=external_id, detail=f"skipped_dropped:{','.join(dropped)}"
                )
            try:
                resp = await request_json(
                    self._client,  # type: ignore[arg-type]
                    "PUT",
                    f"{GHL_API_BASE}/contacts/{external_id}",
                    headers=self._headers(),
                    json=body,
                )
                detail = (
                    f"updated:{resp.status_code}"
                    if not dropped
                    else f"updated_dropped:{','.join(dropped)}"
                )
                return PushResult(ok=True, external_id=external_id, detail=detail)
            except httpx.HTTPStatusError as exc:
                # Same salvage rules as create_contact: an invalid source value in
                # the protected system must not wedge the mirror on every sweep.
                if exc.response.status_code not in (400, 422):
                    raise
                try:
                    data = exc.response.json()
                except ValueError:
                    raise exc from None
                message = str(data.get("message", ""))
                if "email must be an email" in message and "email" in body:
                    body.pop("email")
                    dropped.append("email(invalid)")
                    continue
                if "did not seem to be a phone number" in message and "phone" in body:
                    body.pop("phone")
                    dropped.append("phone(invalid)")
                    continue
                if "duplicated contacts" in message:
                    field = (data.get("meta") or {}).get("matchingField")
                    if field in body:
                        body.pop(field)
                        dropped.append(f"{field}(dup)")
                        continue
                raise
        return PushResult(ok=False, detail="update retries exhausted")

    # ── Pipelines & Opportunities (Flow 2, Spec §10) ────────────────────────────
    async def get_pipelines(self) -> list[dict]:
        """Pipelines + stages for the location — used to validate the stage map
        (Spec §10.3). Shape: [{id, name, stages: [{id, name}, ...]}, ...]."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/opportunities/pipelines",
            headers=self._headers(),
            params={"locationId": self._settings.ghl_location_id},
        )
        return resp.json().get("pipelines", [])

    def _deal_to_canonical(self, item: dict) -> CanonicalDeal:
        deal = CanonicalDeal(
            source_system=System.GHL,
            source_id=str(item.get("id")) if item.get("id") else None,
        )
        deal.name = item.get("name")
        deal.monetary_value = item.get("monetaryValue")
        deal.status = item.get("status")
        deal.contact_id = str(item.get("contactId")) if item.get("contactId") else None
        deal.owner = item.get("assignedTo")
        # GHL pipeline/stage ids ride in the extra bag; the stage map translates them.
        deal.extra["pipeline_id"] = str(item.get("pipelineId") or "")
        deal.extra["stage_id"] = str(item.get("pipelineStageId") or "")
        return deal

    async def get_opportunity(self, external_id: str) -> CanonicalDeal | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/opportunities/{external_id}",
            headers=self._headers(),
        )
        item = resp.json().get("opportunity")
        return self._deal_to_canonical(item) if item else None

    async def create_opportunity(
        self,
        deal: CanonicalDeal,
        *,
        pipeline_id: str,
        stage_id: str,
    ) -> PushResult:
        payload = {
            "locationId": self._settings.ghl_location_id,
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "name": deal.name or "(untitled)",
            "status": deal.status or "open",
        }
        if deal.monetary_value is not None:
            payload["monetaryValue"] = deal.monetary_value
        if deal.contact_id:
            payload["contactId"] = deal.contact_id
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            f"{GHL_API_BASE}/opportunities/",
            headers=self._headers(),
            json=payload,
        )
        new_id = str(resp.json().get("opportunity", {}).get("id"))
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_opportunity(self, external_id: str, changes: dict[str, object]) -> PushResult:
        body = {k: v for k, v in changes.items() if v is not None}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PUT",
            f"{GHL_API_BASE}/opportunities/{external_id}",
            headers=self._headers(),
            json=body,
        )
        return PushResult(ok=True, external_id=external_id, detail=f"updated:{resp.status_code}")

    # ── Businesses (GHL company objects) — mirror of Autotask Accounts ──────────
    async def find_businesses(self, name: str) -> list[dict]:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/businesses/",
            headers=self._headers(),
            params={"locationId": self._settings.ghl_location_id},
        )
        wanted = (name or "").strip().lower()
        return [
            b
            for b in resp.json().get("businesses", [])
            if not wanted or (b.get("name") or "").strip().lower() == wanted
        ]

    async def create_business(self, company) -> PushResult:
        payload = {
            "locationId": self._settings.ghl_location_id,
            "name": company.name or "(unnamed)",
        }
        if getattr(company, "website", None):
            payload["website"] = company.website
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            f"{GHL_API_BASE}/businesses/",
            headers=self._headers(),
            json=payload,
        )
        new_id = str(resp.json().get("business", {}).get("id"))
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_business(self, external_id: str, changes: dict[str, object]) -> PushResult:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PUT",
            f"{GHL_API_BASE}/businesses/{external_id}",
            headers=self._headers(),
            json={k: v for k, v in changes.items() if v is not None},
        )
        return PushResult(ok=True, external_id=external_id, detail=f"updated:{resp.status_code}")

    # ── Notes & tags (additive engagement artefacts, Spec §10.5, §8.3) ──────────
    async def get_contact_notes(self, contact_id: str) -> list[dict]:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            f"{GHL_API_BASE}/contacts/{contact_id}/notes",
            headers=self._headers(),
        )
        return resp.json().get("notes", [])

    async def create_contact_note(self, contact_id: str, body: str) -> PushResult:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            f"{GHL_API_BASE}/contacts/{contact_id}/notes",
            headers=self._headers(),
            json={"body": body},
        )
        return PushResult(
            ok=True, external_id=str(resp.json().get("note", {}).get("id")), detail="note_created"
        )

    async def add_tags(self, contact_id: str, tags: list[str]) -> PushResult:
        """Additive tag application (classification sync + Converted stamp)."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            f"{GHL_API_BASE}/contacts/{contact_id}/tags",
            headers=self._headers(),
            json={"tags": tags},
        )
        return PushResult(ok=True, external_id=contact_id, detail=f"tags:{resp.status_code}")

    async def update_custom_fields(self, contact_id: str, fields: dict[str, object]) -> PushResult:
        """Set custom-field values on a contact (classification sync, Spec §8.3).
        ``fields`` is keyed by GHL custom-field ID."""
        payload = {"customFields": [{"id": k, "value": v} for k, v in fields.items()]}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PUT",
            f"{GHL_API_BASE}/contacts/{contact_id}",
            headers=self._headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=contact_id, detail=f"custom:{resp.status_code}")

    # ── Polling / webhooks ──────────────────────────────────────────────────────
    async def fetch_changes(
        self, entity_type: CanonicalEntityType, *, cursor: str | None = None
    ) -> ChangeSet:
        # GHL emits webhooks for contacts + opportunities; polling is a
        # reconciliation backstop only (Spec §4).
        return ChangeSet()

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify an inbound GHL webhook signature (Spec §4, §12.1).

        Marketplace-app webhooks are signed by GHL's private key — there is no
        per-app shared secret. Verification order (fail closed):

        1. ``x-ghl-signature`` — current scheme: Ed25519 over the raw body,
           base64-encoded, verified against GHL's published public key.
        2. ``x-wh-signature`` + ``GHL_WEBHOOK_SECRET`` set — local-testing path:
           HMAC-SHA256 with the shared secret (used by scripts/send_test_contact.py).
        3. ``x-wh-signature`` — legacy GHL scheme: RSA-SHA256, base64-encoded,
           verified against GHL's legacy public key (sunset 2026-07-01; kept for
           the transition window).
        """
        h = {k.lower(): v for k, v in headers.items()}

        ghl_sig = h.get("x-ghl-signature", "")
        if ghl_sig:
            return _verify_ed25519(body, ghl_sig)

        wh_sig = h.get("x-wh-signature", "")
        if not wh_sig:
            log.warning("GHL webhook has no signature header — rejecting (fail closed)")
            return False

        secret = self._settings.ghl_webhook_secret
        if secret:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(wh_sig, expected):
                return True

        return _verify_rsa_legacy(body, wh_sig)


def _verify_ed25519(body: bytes, signature_b64: str) -> bool:
    key = serialization.load_pem_public_key(GHL_ED25519_PUBLIC_KEY_PEM)
    assert isinstance(key, Ed25519PublicKey)
    try:
        key.verify(base64.b64decode(signature_b64, validate=True), body)
        return True
    except (InvalidSignature, binascii.Error, ValueError):
        log.warning("GHL webhook Ed25519 signature verification failed — rejecting")
        return False


def _verify_rsa_legacy(body: bytes, signature_b64: str) -> bool:
    key = serialization.load_pem_public_key(GHL_LEGACY_RSA_PUBLIC_KEY_PEM)
    assert isinstance(key, RSAPublicKey)
    try:
        key.verify(
            base64.b64decode(signature_b64, validate=True),
            body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, binascii.Error, ValueError):
        log.warning("GHL webhook legacy RSA signature verification failed — rejecting")
        return False
