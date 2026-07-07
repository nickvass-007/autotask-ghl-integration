"""In-process fake connectors for offline tests.

They implement just the methods the Contacts flow calls, returning canned data and
recording writes, so we can assert on what WOULD be written to Autotask without a
live API. This stands in for recorded sandbox responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from integration.canonical.entities import (
    CanonicalCompany,
    CanonicalContact,
    CanonicalDeal,
)
from integration.config.stages import PipelineMap, StageEntry, StageMap
from integration.connectors.base import PushResult
from integration.db.enums import Direction, System


def make_stage_map() -> StageMap:
    """A realistic two-pipeline stage map for offline Flow-2 tests."""
    sales = PipelineMap(
        ghl_pipeline_id="pipe-sales",
        autotask_entity="opportunity",
        closed_won_stage_id="stage-won",
        stages=(
            StageEntry("stage-new", "1", Direction.GHL_TO_AUTOTASK),
            StageEntry("stage-qualified", "2", Direction.GHL_TO_AUTOTASK),
            StageEntry("stage-proposal", "3", Direction.GHL_TO_AUTOTASK),
            StageEntry("stage-won", "10", Direction.GHL_TO_AUTOTASK),
        ),
    )
    service = PipelineMap(
        ghl_pipeline_id="pipe-service",
        autotask_entity="ticket",
        stages=(
            StageEntry("stage-open", "1", Direction.AUTOTASK_TO_GHL),
            StageEntry("stage-done", "5", Direction.AUTOTASK_TO_GHL),
        ),
    )
    return StageMap(sales=sales, service=service)


def make_ghl_deal(
    *,
    deal_id: str = "opp-1",
    name: str = "New Deal",
    pipeline: str = "pipe-sales",
    stage: str = "stage-new",
    contact_id: str | None = "ghlc-1",
    amount: float | None = None,
    status: str = "open",
) -> CanonicalDeal:
    d = CanonicalDeal(source_system=System.GHL, source_id=deal_id)
    d.name = name
    d.monetary_value = amount
    d.status = status
    d.contact_id = contact_id
    d.extra["pipeline_id"] = pipeline
    d.extra["stage_id"] = stage
    return d


@dataclass
class FakeAutotask:
    contacts: dict[str, CanonicalContact] = field(default_factory=dict)
    accounts: list[CanonicalCompany] = field(default_factory=list)
    accounts_raw: dict[str, dict] = field(default_factory=dict)
    opportunities: dict[str, CanonicalDeal] = field(default_factory=dict)
    picklists: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    created_contacts: list[CanonicalContact] = field(default_factory=list)
    created_accounts: list[CanonicalCompany] = field(default_factory=list)
    created_opportunities: list[CanonicalDeal] = field(default_factory=list)
    ticket_notes: list[tuple[str, str, str]] = field(default_factory=list)
    updates: list[tuple[str, dict]] = field(default_factory=list)
    opp_updates: list[tuple[str, dict]] = field(default_factory=list)
    _next_id: int = 1000

    async def find_contacts(self, *, email: str | None = None) -> list[CanonicalContact]:
        if not email:
            return []
        return [c for c in self.contacts.values() if (c.email or "").lower() == email.lower()]

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        return self.contacts.get(str(external_id))

    async def find_accounts(self, name: str) -> list[CanonicalCompany]:
        if not name:
            return []
        return [a for a in self.accounts if (a.name or "").lower().startswith(name.lower()[:3])]

    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        if contact.company_id is None:
            return PushResult(ok=False, detail="no companyID")
        self._next_id += 1
        new_id = str(self._next_id)
        stored = CanonicalContact(
            source_system=System.AUTOTASK,
            source_id=new_id,
            email=contact.email,
            first_name=contact.first_name,
            last_name=contact.last_name,
            phone=contact.phone,
            company_id=contact.company_id,
        )
        self.contacts[new_id] = stored
        self.created_contacts.append(stored)
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict) -> PushResult:
        self.updates.append((str(external_id), dict(changes)))
        existing = self.contacts.get(str(external_id))
        if existing:
            for k, v in changes.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        return PushResult(ok=True, external_id=str(external_id), detail="updated")

    async def create_account(self, company: CanonicalCompany) -> PushResult:
        self._next_id += 1
        new_id = str(self._next_id)
        company.company_id = new_id
        self.created_accounts.append(company)
        self.accounts.append(company)
        return PushResult(ok=True, external_id=new_id, detail="account_created")

    async def get_account_raw(self, account_id: str) -> dict | None:
        return self.accounts_raw.get(str(account_id))

    async def get_picklist_values(self, entity: str, field_name: str) -> list[str]:
        return list(self.picklists.get((entity, field_name), {}).keys())

    async def get_picklist_labels(self, entity: str, field_name: str) -> dict[str, str]:
        return dict(self.picklists.get((entity, field_name), {}))

    async def find_opportunities(self, *, account_id: str) -> list[CanonicalDeal]:
        return [o for o in self.opportunities.values() if o.account_id == str(account_id)]

    async def get_opportunity(self, external_id: str) -> CanonicalDeal | None:
        return self.opportunities.get(str(external_id))

    async def create_opportunity(self, deal: CanonicalDeal) -> PushResult:
        if deal.account_id is None:
            return PushResult(ok=False, detail="no companyID")
        self._next_id += 1
        new_id = str(self._next_id)
        stored = CanonicalDeal(source_system=System.AUTOTASK, source_id=new_id)
        stored.name = deal.name
        stored.monetary_value = deal.monetary_value
        stored.stage = deal.stage
        stored.status = deal.status or "1"
        stored.account_id = deal.account_id
        stored.contact_id = deal.contact_id
        self.opportunities[new_id] = stored
        self.created_opportunities.append(stored)
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_opportunity(self, external_id: str, changes: dict) -> PushResult:
        self.opp_updates.append((str(external_id), dict(changes)))
        existing = self.opportunities.get(str(external_id))
        if existing:
            if "stage" in changes:
                existing.stage = str(changes["stage"])
            if "amount" in changes:
                existing.monetary_value = changes["amount"]
            if "status" in changes:
                existing.status = str(changes["status"])
        return PushResult(ok=True, external_id=str(external_id), detail="updated")

    async def create_ticket_note(self, ticket_id: str, *, title: str, body: str) -> PushResult:
        self._next_id += 1
        self.ticket_notes.append((str(ticket_id), title, body))
        return PushResult(ok=True, external_id=str(self._next_id), detail="note_created")


@dataclass
class FakeGHL:
    contacts: dict[str, CanonicalContact] = field(default_factory=dict)
    created: list[CanonicalContact] = field(default_factory=list)
    updates: list[tuple[str, dict]] = field(default_factory=list)
    pipelines: list[dict] = field(default_factory=list)
    opportunities: dict[str, CanonicalDeal] = field(default_factory=dict)
    created_opps: list[tuple[CanonicalDeal, str, str]] = field(default_factory=list)
    opp_updates: list[tuple[str, dict]] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)
    tags: dict[str, list[str]] = field(default_factory=dict)
    custom_fields: dict[str, dict] = field(default_factory=dict)
    _next_id: int = 1

    async def authenticate(self) -> None:
        return None

    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        return self.contacts.get(str(external_id))

    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        self._next_id += 1
        new_id = f"ghl{self._next_id}"
        self.created.append(contact)
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_contact(self, external_id: str, changes: dict) -> PushResult:
        self.updates.append((str(external_id), dict(changes)))
        return PushResult(ok=True, external_id=str(external_id), detail="updated")

    async def get_pipelines(self) -> list[dict]:
        return self.pipelines

    async def get_opportunity(self, external_id: str) -> CanonicalDeal | None:
        return self.opportunities.get(str(external_id))

    async def create_opportunity(
        self, deal: CanonicalDeal, *, pipeline_id: str, stage_id: str
    ) -> PushResult:
        self._next_id += 1
        new_id = f"ghlopp{self._next_id}"
        deal.extra["pipeline_id"] = pipeline_id
        deal.extra["stage_id"] = stage_id
        self.opportunities[new_id] = deal
        self.created_opps.append((deal, pipeline_id, stage_id))
        return PushResult(ok=True, external_id=new_id, detail="created")

    async def update_opportunity(self, external_id: str, changes: dict) -> PushResult:
        self.opp_updates.append((str(external_id), dict(changes)))
        return PushResult(ok=True, external_id=str(external_id), detail="updated")

    async def get_contact_notes(self, contact_id: str) -> list[dict]:
        return [{"body": b} for cid, b in self.notes if cid == str(contact_id)]

    async def create_contact_note(self, contact_id: str, body: str) -> PushResult:
        self.notes.append((str(contact_id), body))
        return PushResult(ok=True, external_id=f"note{len(self.notes)}", detail="note_created")

    async def add_tags(self, contact_id: str, tags: list[str]) -> PushResult:
        bucket = self.tags.setdefault(str(contact_id), [])
        bucket.extend(t for t in tags if t not in bucket)
        return PushResult(ok=True, external_id=str(contact_id), detail="tags")

    async def update_custom_fields(self, contact_id: str, fields: dict) -> PushResult:
        self.custom_fields.setdefault(str(contact_id), {}).update(fields)
        return PushResult(ok=True, external_id=str(contact_id), detail="custom")
