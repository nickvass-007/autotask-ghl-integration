"""Autotask connector — the protected system of record (Spec §5, §12.1).

Auth model (Spec §12.1): an **API-only user** (username + secret + integration
code). The **zone-detection endpoint is called first** to discover the correct API
base URL for this account, because Autotask shards customers across zones.

⚠️ Autotask has **no comprehensive outbound webhook system** (Spec §4): outbound
changes are POLLED, and ``verify_webhook`` returns False by design.

Stage 1 implements authentication and the Contacts read/dedupe/write paths the
Contacts flow uses. Opportunity/Ticket polling lands in Stage 2.
"""

from __future__ import annotations

import httpx

from ..canonical.entities import CanonicalCompany, CanonicalContact
from ..config.mapping import load_contacts_mapping
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

# Global zone-detection entry point (same for every account; returns the per-account zone).
ZONE_DETECTION_URL = (
    "https://webservices.autotask.net/atservicesrest/v1.0/zoneInformation"
)


class AutotaskConnector(Connector):
    capabilities = ConnectorCapabilities(
        key="autotask",
        display_name="Autotask PSA",
        can_read=True,
        can_write=True,
        supports_webhooks=False,  # ⚠️ poll instead (Spec §4)
        supported_entities=(
            CanonicalEntityType.CONTACT,
            CanonicalEntityType.COMPANY,
            CanonicalEntityType.DEAL,
            CanonicalEntityType.SERVICE_ITEM,
        ),
        # Autotask limits per database; conservative defaults, backoff handles bursts.
        rate_limit=RateLimit(max_requests=100, per_seconds=60),
    )

    def __init__(self, environment: Environment, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(environment)
        self._settings = get_settings()
        self._mapping = load_contacts_mapping()
        self._base_url: str | None = None
        # A client can be injected for tests; otherwise created on authenticate().
        self._client = client

    @property
    def system(self) -> System:
        return System.AUTOTASK

    # ── Auth ──────────────────────────────────────────────────────────────────
    def _auth_headers(self) -> dict[str, str]:
        s = self._settings
        return {
            "ApiIntegrationCode": s.autotask_integration_code,
            "UserName": s.autotask_username,
            "Secret": s.autotask_secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def authenticate(self) -> None:
        """Detect the zone (Spec §12.1) and prepare the authenticated client.

        We call zoneInformation first to learn this account's API base URL, unless
        an override is configured. Credentials are sent on every subsequent call
        via headers (Autotask has no token exchange)."""
        s = self._settings
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)

        if s.autotask_zone_override_url:
            self._base_url = s.autotask_zone_override_url.rstrip("/") + "/"
            log.info("Autotask zone override in use: %s", self._base_url)
            return

        if not s.autotask_username:
            raise RuntimeError(
                "AUTOTASK_USERNAME is empty — set sandbox API-only user credentials in .env"
            )

        resp = await request_json(
            self._client,
            "GET",
            ZONE_DETECTION_URL,
            params={"user": s.autotask_username},
            headers=self._auth_headers(),
        )
        data = resp.json()
        # zoneInformation returns {"url": "https://webservicesN.autotask.net/atservicesrest/", ...}
        self._base_url = data["url"].rstrip("/") + "/"
        log.info("Autotask zone detected (%s): %s", self.environment, self._base_url)

    def _url(self, path: str) -> str:
        if not self._base_url:
            raise RuntimeError("AutotaskConnector.authenticate() must be called first")
        return f"{self._base_url}V1.0/{path.lstrip('/')}"

    async def health(self) -> bool:
        """Confirm auth + reachability via a cheap entity-information call."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url("Contacts/entityInformation"),
            headers=self._auth_headers(),
        )
        return resp.status_code == 200

    # ── Translation ───────────────────────────────────────────────────────────
    def _to_canonical(self, item: dict) -> CanonicalContact:
        """Map an Autotask Contact record to the canonical hub model."""
        contact = CanonicalContact(source_system=System.AUTOTASK, source_id=str(item.get("id")))
        for rule in self._mapping.fields:
            if rule.autotask_entity != "Contact":
                continue
            value = item.get(rule.autotask_field)
            if value is not None:
                setattr(contact, rule.canonical, value)
        contact.company_id = str(item["companyID"]) if item.get("companyID") is not None else None
        return contact

    def _contact_payload(self, contact: CanonicalContact) -> dict:
        """Map a canonical contact to an Autotask Contact create/update payload."""
        payload: dict[str, object] = {}
        for rule in self._mapping.fields:
            if rule.autotask_entity != "Contact" or "." in rule.autotask_field:
                continue  # skip Account.* and custom-field placeholders here
            value = getattr(contact, rule.canonical, None)
            if value is not None:
                payload[rule.autotask_field] = value
        if contact.company_id is not None:
            payload["companyID"] = int(contact.company_id)
        return payload

    # ── Contacts read / dedupe ─────────────────────────────────────────────────
    async def find_contacts(self, *, email: str | None = None) -> list[CanonicalContact]:
        if not email:
            return []
        body = {"filter": [{"op": "eq", "field": "emailAddress", "value": email}]}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Contacts/query"),
            headers=self._auth_headers(),
            json=body,
        )
        items = resp.json().get("items", [])
        return [self._to_canonical(i) for i in items]

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"Contacts/{external_id}"),
            headers=self._auth_headers(),
        )
        item = resp.json().get("item")
        return self._to_canonical(item) if item else None

    # ── Contacts write (post-gating only) ──────────────────────────────────────
    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        if contact.company_id is None:
            return PushResult(
                ok=False,
                detail="Refusing to create Autotask Contact without a resolved companyID "
                "(Account linkage must run first, Spec §9.3).",
            )
        payload = self._contact_payload(contact)
        # isActive is REQUIRED on create (Contacts/entityInformation/fields) —
        # Autotask reports its absence as an opaque 500.
        payload.setdefault("isActive", 1)
        # Contacts are a CHILD entity of Companies in the Autotask REST API:
        # POST /Contacts does not exist (404) — create via the parent collection.
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url(f"Companies/{int(contact.company_id)}/Contacts"),
            headers=self._auth_headers(),
            json=payload,
        )
        new_id = str(resp.json().get("itemId"))
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict[str, object]) -> PushResult:
        """Apply ALREADY-GATED canonical field changes to an existing Contact.

        ``changes`` is keyed by canonical field name; only Contact-level fields are
        applied here (Account renames are never auto-applied, Spec §9.3)."""
        payload: dict[str, object] = {"id": int(external_id)}
        for canonical_name, value in changes.items():
            rule = self._mapping.by_canonical(canonical_name)
            if rule is None or rule.autotask_entity != "Contact" or "." in rule.autotask_field:
                continue
            payload[rule.autotask_field] = value
        # Child-entity rule (see create_contact): writes go via the parent Company,
        # so look up the contact's companyID first.
        existing = await self.get_contact(external_id)
        if existing is None or existing.company_id is None:
            return PushResult(
                ok=False,
                detail=f"Cannot update Contact {external_id}: not found or no companyID",
            )
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PATCH",
            self._url(f"Companies/{int(existing.company_id)}/Contacts"),
            headers=self._auth_headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=external_id, detail=f"updated:{resp.status_code}")

    # ── Accounts (for the linkage guard, Spec §9.3) ────────────────────────────
    def _account_to_canonical(self, item: dict) -> CanonicalCompany:
        company = CanonicalCompany(
            source_system=System.AUTOTASK,
            source_id=str(item.get("id")),
            name=item.get("companyName"),
            website=item.get("webAddress"),
        )
        company.company_id = str(item.get("id"))
        return company

    async def find_accounts(self, name: str) -> list[CanonicalCompany]:
        """Look up Accounts by (begins-with) company name for linkage resolution.
        ⚠️ Read-only: Accounts are NEVER auto-created or auto-renamed (Spec §9.3)."""
        if not name:
            return []
        body = {"filter": [{"op": "beginsWith", "field": "companyName", "value": name}]}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Companies/query"),
            headers=self._auth_headers(),
            json=body,
        )
        return [self._account_to_canonical(i) for i in resp.json().get("items", [])]

    async def create_account(self, company: CanonicalCompany) -> PushResult:
        """Create a new Autotask Account. ⚠️ ONLY ever called as the result of an
        explicit human **approval** (Spec §9.3) — never automatically."""
        payload: dict[str, object] = {"companyName": company.name, "companyType": 1}
        if company.website:
            payload["webAddress"] = company.website
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Companies"),
            headers=self._auth_headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=str(resp.json().get("itemId")), detail="account_created")

    async def get_account(self, account_id: str) -> CanonicalCompany | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"Companies/{account_id}"),
            headers=self._auth_headers(),
        )
        item = resp.json().get("item")
        return self._account_to_canonical(item) if item else None

    # ── Polling / webhooks ──────────────────────────────────────────────────────
    async def fetch_changes(
        self, entity_type: CanonicalEntityType, *, cursor: str | None = None
    ) -> ChangeSet:
        """Threadless pagination by ``id > lastId`` (Spec §12.1). Stage 1 wires the
        Contacts polling shape; Opportunity/Ticket entities arrive in Stage 2."""
        if entity_type is not CanonicalEntityType.CONTACT:
            return ChangeSet()  # Stage 2
        last_id = int(cursor) if cursor else 0
        body = {
            "filter": [{"op": "gt", "field": "id", "value": last_id}],
            "MaxRecords": 500,  # Autotask page cap
        }
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Contacts/query"),
            headers=self._auth_headers(),
            json=body,
        )
        items = resp.json().get("items", [])
        entities = [self._to_canonical(i) for i in items]
        new_cursor = str(max(int(i["id"]) for i in items)) if items else cursor
        return ChangeSet(entities=entities, cursor=new_cursor, has_more=len(items) == 500)

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        # Autotask has no comprehensive webhooks — never trust an inbound "Autotask" webhook.
        return False
