"""Stage E classification sync (Spec §8.3) and notes sync (Spec §10.5)."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalContact, CanonicalDeal
from integration.db.base import utcnow
from integration.db.enums import CanonicalEntityType, Environment, System
from integration.sync.classification import sync_classifications
from integration.sync.notes import mirror_ticket_note, push_ghl_note
from integration.db.models import EntityMapping

from fakes import FakeAutotask, FakeGHL

pytestmark = pytest.mark.asyncio


def _map(session, *, entity=CanonicalEntityType.CONTACT, autotask_id="500", ghl_id="ghlc-1",
         at_type="Contact", ghl_type="contact"):
    session.add(
        EntityMapping(
            canonical_entity_type=entity,
            autotask_entity_type=at_type,
            autotask_id=autotask_id,
            ghl_entity_type=ghl_type,
            ghl_id=ghl_id,
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()


def _classified_autotask() -> FakeAutotask:
    autotask = FakeAutotask()
    autotask.contacts["500"] = CanonicalContact(
        source_system=System.AUTOTASK, source_id="500", email="c@x.co", company_id="77"
    )
    autotask.accounts_raw["77"] = {"id": 77, "companyType": "1", "classification": "3"}
    autotask.picklists[("Companies", "companyType")] = {"1": "Customer", "2": "Prospect"}
    autotask.picklists[("Companies", "classification")] = {"3": "Managed Services"}
    return autotask


async def test_classification_pushes_tags_and_is_idempotent(session):
    autotask = _classified_autotask()
    ghl = FakeGHL()
    _map(session)

    first = await sync_classifications(session, autotask=autotask, ghl=ghl)
    assert first["contacts_pushed"] == 1
    tags = ghl.tags["ghlc-1"]
    assert "customer" in tags                       # lifecycle tag (§8.3)
    assert "at:customer-type/customer" in tags
    assert "at:classification/managed-services" in tags

    # Second sweep with unchanged values pushes nothing.
    second = await sync_classifications(session, autotask=autotask, ghl=ghl)
    assert second["contacts_pushed"] == 0


async def test_classification_repushes_on_change(session):
    autotask = _classified_autotask()
    ghl = FakeGHL()
    _map(session)
    await sync_classifications(session, autotask=autotask, ghl=ghl)

    autotask.accounts_raw["77"]["classification"] = "4"
    autotask.picklists[("Companies", "classification")]["4"] = "Break Fix"
    result = await sync_classifications(session, autotask=autotask, ghl=ghl)
    assert result["contacts_pushed"] == 1
    assert "at:classification/break-fix" in ghl.tags["ghlc-1"]


# ── Notes (§10.5) ─────────────────────────────────────────────────────────────
async def test_ticket_note_mirrors_to_ghl_contact_once(session):
    ghl = FakeGHL()
    card = CanonicalDeal(source_system=System.AUTOTASK, source_id="T1")
    card.contact_id = "ghlc-1"
    ghl.opportunities["ghlopp1"] = card
    _map(session, entity=CanonicalEntityType.SERVICE_ITEM, autotask_id="T1",
         ghl_id="ghlopp1", at_type="Ticket", ghl_type="opportunity")

    note = {"id": 42, "ticketID": "T1", "title": "Called client", "description": "All good."}
    assert await mirror_ticket_note(session, note=note, ghl=ghl) == "created"
    assert await mirror_ticket_note(session, note=note, ghl=ghl) == "skipped"  # idempotent
    assert len(ghl.notes) == 1
    contact_id, body = ghl.notes[0]
    assert contact_id == "ghlc-1"
    assert "[Autotask note 42]" in body


async def test_ghl_note_without_linked_ticket_is_held(session):
    autotask = FakeAutotask()
    action = await push_ghl_note(
        session, ghl_card_id="ghlopp-x", note_id="n1", note_body="hello", autotask=autotask
    )
    assert action == "held"
    assert not autotask.ticket_notes  # a note must never create a ticket


async def test_ghl_note_appends_to_linked_ticket(session):
    autotask = FakeAutotask()
    _map(session, entity=CanonicalEntityType.SERVICE_ITEM, autotask_id="T1",
         ghl_id="ghlopp1", at_type="Ticket", ghl_type="opportunity")
    action = await push_ghl_note(
        session, ghl_card_id="ghlopp1", note_id="n2", note_body="from sales", autotask=autotask
    )
    assert action == "created"
    ticket_id, title, body = autotask.ticket_notes[0]
    assert ticket_id == "T1"
    assert body == "from sales"


async def test_autotask_originated_note_is_not_echoed_back(session):
    autotask = FakeAutotask()
    _map(session, entity=CanonicalEntityType.SERVICE_ITEM, autotask_id="T1",
         ghl_id="ghlopp1", at_type="Ticket", ghl_type="opportunity")
    action = await push_ghl_note(
        session, ghl_card_id="ghlopp1", note_id="n3",
        note_body="[Autotask note 42] originally from AT", autotask=autotask,
    )
    assert action == "skipped"
    assert not autotask.ticket_notes  # loop guard
