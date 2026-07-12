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

from ..canonical.entities import (
    CanonicalCompany,
    CanonicalContact,
    CanonicalDeal,
    CanonicalServiceItem,
)
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
        # Carried in the extra bag (not a synced field): lets the outbound sweep
        # skip INACTIVE contacts even when their Account passes the criteria.
        contact.extra["is_active"] = bool(item.get("isActive", 1))
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

    async def get_account_raw(self, account_id: str) -> dict | None:
        """Raw Account fields — classification sync reads picklist ids off this
        (Spec §8.3) without widening the canonical model."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"Companies/{account_id}"),
            headers=self._auth_headers(),
        )
        return resp.json().get("item")

    async def get_picklist_labels(self, entity: str, field_name: str) -> dict[str, str]:
        """{value: label} for a picklist field (e.g. Companies.companyType)."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"{entity}/entityInformation/fields"),
            headers=self._auth_headers(),
        )
        for f in resp.json().get("fields", []):
            if f.get("name") == field_name:
                return {
                    str(v.get("value")): str(v.get("label", v.get("value")))
                    for v in (f.get("picklistValues") or [])
                }
        return {}

    # ── Picklists (stage-map validation, Spec §10.3) ───────────────────────────
    async def get_picklist_values(self, entity: str, field_name: str) -> list[str]:
        """Active picklist values for e.g. Opportunities.stage / Tickets.status."""
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"{entity}/entityInformation/fields"),
            headers=self._auth_headers(),
        )
        for f in resp.json().get("fields", []):
            if f.get("name") == field_name:
                # Non-picklist fields carry an explicit null here — guard it.
                return [
                    str(v.get("value"))
                    for v in (f.get("picklistValues") or [])
                    if v.get("isActive", True)
                ]
        return []

    # ── Opportunities (canonical.Deal, Spec §10) ────────────────────────────────
    def _deal_to_canonical(self, item: dict) -> CanonicalDeal:
        deal = CanonicalDeal(source_system=System.AUTOTASK, source_id=str(item.get("id")))
        deal.name = item.get("title")
        deal.monetary_value = item.get("amount")
        deal.stage = str(item["stage"]) if item.get("stage") is not None else None
        deal.status = str(item["status"]) if item.get("status") is not None else None
        deal.close_date = item.get("projectedCloseDate")
        deal.account_id = str(item["companyID"]) if item.get("companyID") is not None else None
        deal.contact_id = str(item["contactID"]) if item.get("contactID") is not None else None
        owner = item.get("ownerResourceID")
        deal.owner = str(owner) if owner is not None else None
        return deal

    async def find_opportunities(self, *, account_id: str) -> list[CanonicalDeal]:
        """Open Opportunities for an Account — the Flow-2 dedupe pool (Spec §10.2)."""
        body = {"filter": [{"op": "eq", "field": "companyID", "value": int(account_id)}]}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Opportunities/query"),
            headers=self._auth_headers(),
            json=body,
        )
        return [self._deal_to_canonical(i) for i in resp.json().get("items", [])]

    async def get_opportunity(self, external_id: str) -> CanonicalDeal | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"Opportunities/{external_id}"),
            headers=self._auth_headers(),
        )
        item = resp.json().get("item")
        return self._deal_to_canonical(item) if item else None

    async def create_opportunity(self, deal: CanonicalDeal) -> PushResult:
        """Create an Autotask Opportunity. ⚠️ Only ever reached through the gated
        Flow-2 path (clean dedupe miss or explicit approval, Spec §10.2)."""
        if deal.account_id is None:
            return PushResult(
                ok=False,
                detail="Refusing to create Opportunity without a resolved companyID (Spec §10.4).",
            )
        payload: dict[str, object] = {
            "companyID": int(deal.account_id),
            "title": deal.name or "(untitled)",
            "stage": int(deal.stage) if deal.stage else None,
            "status": int(deal.status) if deal.status else 1,
            "amount": deal.monetary_value or 0,
            "projectedCloseDate": deal.close_date,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        # Child-entity rule (same as Contacts): create via the parent Company.
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url(f"Companies/{int(deal.account_id)}/Opportunities"),
            headers=self._auth_headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=str(resp.json().get("itemId")), detail="created")

    async def update_opportunity(self, external_id: str, changes: dict[str, object]) -> PushResult:
        """Apply ALREADY-GATED field changes to an existing Opportunity."""
        existing = await self.get_opportunity(external_id)
        if existing is None or existing.account_id is None:
            return PushResult(
                ok=False, detail=f"Opportunity {external_id} not found or has no companyID"
            )
        payload: dict[str, object] = {"id": int(external_id), **changes}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "PATCH",
            self._url(f"Companies/{int(existing.account_id)}/Opportunities"),
            headers=self._auth_headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=external_id, detail=f"updated:{resp.status_code}")

    # ── Tickets (canonical.ServiceItem) — READ-ONLY for the whole of v1 ─────────
    # ⚠️ GHL must never create or mutate Autotask Tickets (Spec §10.2); this
    # connector deliberately has NO create_ticket/update_ticket methods.
    def _ticket_to_canonical(self, item: dict) -> CanonicalServiceItem:
        ticket = CanonicalServiceItem(source_system=System.AUTOTASK, source_id=str(item.get("id")))
        ticket.title = item.get("title")
        ticket.status = str(item["status"]) if item.get("status") is not None else None
        queue = item.get("queueID")
        ticket.queue = str(queue) if queue is not None else None
        ticket.account_id = str(item["companyID"]) if item.get("companyID") is not None else None
        ticket.contact_id = str(item["contactID"]) if item.get("contactID") is not None else None
        return ticket

    async def get_ticket(self, external_id: str) -> CanonicalServiceItem | None:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "GET",
            self._url(f"Tickets/{external_id}"),
            headers=self._auth_headers(),
        )
        item = resp.json().get("item")
        return self._ticket_to_canonical(item) if item else None

    async def find_tickets(self, *, contact_id: str) -> list[CanonicalServiceItem]:
        """Tickets for an Autotask Contact — used to route inbound GHL notes to
        the right (already-mirrored) ticket. Read-only."""
        body = {"filter": [{"op": "eq", "field": "contactID", "value": int(contact_id)}]}
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("Tickets/query"),
            headers=self._auth_headers(),
            json=body,
        )
        return [self._ticket_to_canonical(i) for i in resp.json().get("items", [])]

    # ── Ticket notes (additive sync, Spec §10.5) ────────────────────────────────
    async def fetch_ticket_notes(self, ticket_id: str) -> list[dict]:
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("TicketNotes/query"),
            headers=self._auth_headers(),
            json={"filter": [{"op": "eq", "field": "ticketID", "value": int(ticket_id)}]},
        )
        return resp.json().get("items", [])

    async def create_ticket_note(self, ticket_id: str, *, title: str, body: str) -> PushResult:
        """Append a note to an EXISTING ticket (notes are additive-only, §10.5)."""
        payload = {
            "ticketID": int(ticket_id),
            "title": title[:250],
            "description": body,
            "noteType": 1,          # standard note
            "publish": 1,           # internal
        }
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url(f"Tickets/{int(ticket_id)}/Notes"),
            headers=self._auth_headers(),
            json=payload,
        )
        return PushResult(ok=True, external_id=str(resp.json().get("itemId")), detail="note_created")

    # ── Polling / webhooks ──────────────────────────────────────────────────────
    _POLL_ENTITY = {
        CanonicalEntityType.COMPANY: "Companies",
        CanonicalEntityType.CONTACT: "Contacts",
        CanonicalEntityType.DEAL: "Opportunities",
        CanonicalEntityType.SERVICE_ITEM: "Tickets",
    }

    def _poll_to_canonical(self, entity_type: CanonicalEntityType, item: dict):
        if entity_type is CanonicalEntityType.COMPANY:
            return self._account_to_canonical(item)
        if entity_type is CanonicalEntityType.CONTACT:
            return self._to_canonical(item)
        if entity_type is CanonicalEntityType.DEAL:
            return self._deal_to_canonical(item)
        return self._ticket_to_canonical(item)

    async def fetch_ticket_note_changes(self, *, cursor: str | None = None) -> tuple[list[dict], str | None]:
        """New TicketNotes since the cursor (``id:<n>``). Notes are append-only in
        our sync (§10.5) so an id sweep is sufficient — edits are never mirrored.
        Returns (raw note items, new cursor)."""
        raw = cursor.split(":", 1)[1] if cursor and ":" in cursor else (cursor or "0")
        body = {
            "filter": [{"op": "gt", "field": "id", "value": int(raw)}],
            "MaxRecords": 500,
        }
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url("TicketNotes/query"),
            headers=self._auth_headers(),
            json=body,
        )
        items = resp.json().get("items", [])
        new_cursor = f"id:{max(int(i['id']) for i in items)}" if items else cursor
        return items, new_cursor

    async def fetch_changes(
        self, entity_type: CanonicalEntityType, *, cursor: str | None = None
    ) -> ChangeSet:
        """Poll for changes (Autotask has no comprehensive webhooks, Spec §4).

        Cursor formats (threadless pagination, Spec §12.1):
        - ``id:<n>``  — creates sweep, ``id > n`` (also the initial backfill)
        - ``ts:<iso>`` — updates sweep, ``lastActivityDate > iso``
        A plain integer cursor is accepted for backwards compatibility (= id sweep).
        """
        table = self._POLL_ENTITY.get(entity_type)
        if table is None:
            return ChangeSet()
        if cursor and cursor.startswith("ts:"):
            filt = {"op": "gt", "field": "lastActivityDate", "value": cursor[3:]}
        else:
            raw = cursor.split(":", 1)[1] if cursor and ":" in cursor else (cursor or "0")
            filt = {"op": "gt", "field": "id", "value": int(raw)}
        body = {"filter": [filt], "MaxRecords": 500}  # Autotask page cap
        resp = await request_json(
            self._client,  # type: ignore[arg-type]
            "POST",
            self._url(f"{table}/query"),
            headers=self._auth_headers(),
            json=body,
        )
        items = resp.json().get("items", [])
        entities = [self._poll_to_canonical(entity_type, i) for i in items]
        if items and (cursor is None or not cursor.startswith("ts:")):
            new_cursor = f"id:{max(int(i['id']) for i in items)}"
        elif items:
            new_cursor = f"ts:{max(str(i.get('lastActivityDate') or '') for i in items)}"
        else:
            new_cursor = cursor
        return ChangeSet(entities=entities, cursor=new_cursor, has_more=len(items) == 500)

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        # Autotask has no comprehensive webhooks — never trust an inbound "Autotask" webhook.
        return False
