"""Poller wiring for companies + ticket notes, and inbound GHL note routing.

The poller tests run against the process-global engine (``session_scope``),
which conftest points at in-memory SQLite — so tables are created here on that
engine, and test data uses unique ids to coexist with other tests.
"""

from __future__ import annotations

import pytest

from integration.canonical.entities import (
    CanonicalCompany,
    CanonicalDeal,
    CanonicalServiceItem,
)
from integration.db.base import Base, utcnow
from integration.db.enums import CanonicalEntityType, Environment, System
from integration.db.models import EntityMapping, SyncExclusion
from integration.db.session import get_engine, get_sessionmaker
from integration.jobs.poller import poll_once
from integration.sync.notes import route_ghl_contact_note

from fakes import FakeAutotask, FakeGHL

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def global_session():
    """A session on the SAME engine session_scope() uses (needed by poll_once)."""
    Base.metadata.create_all(get_engine())
    sess = get_sessionmaker()()
    try:
        yield sess
        sess.commit()
    finally:
        sess.close()


def _company(account_id: str, name: str) -> CanonicalCompany:
    company = CanonicalCompany(
        source_system=System.AUTOTASK, source_id=account_id, name=name
    )
    company.company_id = account_id
    return company


def _service_mapping(session, *, autotask_id: str, ghl_id: str) -> None:
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.SERVICE_ITEM,
            autotask_entity_type="Ticket",
            autotask_id=autotask_id,
            ghl_entity_type="opportunity",
            ghl_id=ghl_id,
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()


# ── Companies sweep ───────────────────────────────────────────────────────────
async def test_poller_mirrors_companies_to_ghl_businesses(global_session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    autotask.poll_queue[CanonicalEntityType.COMPANY] = [
        _company("9001", "Poller Test Co"),
    ]

    summary = await poll_once(autotask=autotask, ghl=ghl)

    assert summary["company"] == 1
    assert any(b["name"] == "Poller Test Co" for b in ghl.businesses.values())
    link = (
        global_session.query(EntityMapping)
        .filter_by(canonical_entity_type=CanonicalEntityType.COMPANY, autotask_id="9001")
        .one()
    )
    assert link.ghl_id in ghl.businesses


async def test_poller_skips_excluded_company(global_session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    global_session.add(
        SyncExclusion(
            environment=Environment.SANDBOX,
            entity_type="account",
            autotask_id="9002",
            reason="test exclusion",
        )
    )
    global_session.commit()
    autotask.poll_queue[CanonicalEntityType.COMPANY] = [
        _company("9002", "Excluded Co"),
    ]

    summary = await poll_once(autotask=autotask, ghl=ghl)

    assert summary["company"] == 0
    assert not any(b["name"] == "Excluded Co" for b in ghl.businesses.values())


# ── Ticket-note sweep ─────────────────────────────────────────────────────────
async def test_poller_sweeps_ticket_notes_to_ghl(global_session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    # Mirrored ticket T9100 -> GHL card, whose contact carries the note.
    card = CanonicalDeal(source_system=System.AUTOTASK, source_id="9100")
    card.contact_id = "ghlc-9100"
    ghl.opportunities["ghlopp-9100"] = card
    _service_mapping(global_session, autotask_id="9100", ghl_id="ghlopp-9100")
    global_session.commit()

    autotask.note_queue = [
        {"id": 9101, "ticketID": "9100", "title": "Site visit", "description": "Done."},
        {"id": 9102, "ticketID": "not-mirrored", "title": "x", "description": "y"},
    ]

    summary = await poll_once(autotask=autotask, ghl=ghl)
    assert summary["ticket_note"] == 1  # the unmirrored ticket's note is skipped
    assert ghl.notes and ghl.notes[0][0] == "ghlc-9100"
    assert "[Autotask note 9101]" in ghl.notes[0][1]

    # Cursor advanced: the second sweep re-processes nothing.
    again = await poll_once(autotask=autotask, ghl=ghl)
    assert again["ticket_note"] == 0
    assert len(ghl.notes) == 1


async def test_poller_does_not_echo_ghl_originated_notes(global_session):
    """A ticket note whose title carries the GHL stamp originated in GHL — the
    sweep must not bounce it back onto the contact (finding #2)."""
    autotask = FakeAutotask()
    ghl = FakeGHL()
    card = CanonicalDeal(source_system=System.AUTOTASK, source_id="9200")
    card.contact_id = "ghlc-9200"
    ghl.opportunities["ghlopp-9200"] = card
    _service_mapping(global_session, autotask_id="9200", ghl_id="ghlopp-9200")
    global_session.commit()

    autotask.note_queue = [
        {"id": 9201, "ticketID": "9200", "title": "[GHL note wn1]", "description": "from GHL"},
    ]
    summary = await poll_once(autotask=autotask, ghl=ghl)
    assert summary["ticket_note"] == 0
    assert ghl.notes == []  # not echoed back


async def test_note_sweep_halts_on_failure_and_retries_without_double_post(global_session):
    """A transient GHL failure must halt the sweep at that note (cursor not
    advanced past it) and NOT re-post the earlier committed note (findings #1, #3)."""
    autotask = FakeAutotask()
    ghl = FakeGHL()
    for tid, gid, cid in (("9300", "ghlopp-9300", "ghlc-9300"), ("9301", "ghlopp-9301", "ghlc-9301")):
        card = CanonicalDeal(source_system=System.AUTOTASK, source_id=tid)
        card.contact_id = cid
        ghl.opportunities[gid] = card
        _service_mapping(global_session, autotask_id=tid, ghl_id=gid)
    global_session.commit()

    ghl.fail_note_for_contacts = {"ghlc-9301"}  # note 9301 will fail
    autotask.note_queue = [
        {"id": 9300, "ticketID": "9300", "title": "ok", "description": "first"},
        {"id": 9301, "ticketID": "9301", "title": "boom", "description": "second"},
    ]

    first = await poll_once(autotask=autotask, ghl=ghl)
    assert first["ticket_note"] == 1  # only 9300 mirrored; sweep halted at 9301
    assert [n[0] for n in ghl.notes] == ["ghlc-9300"]

    # Recover: 9301 now succeeds. 9300 must NOT be posted again (durable mark).
    ghl.fail_note_for_contacts = set()
    second = await poll_once(autotask=autotask, ghl=ghl)
    assert second["ticket_note"] == 1
    assert sorted(n[0] for n in ghl.notes) == ["ghlc-9300", "ghlc-9301"]


# ── Inbound GHL note routing (webhook path) ───────────────────────────────────
async def test_ghl_note_routes_to_newest_mirrored_ticket(session):
    autotask = FakeAutotask()
    for tid in ("300", "301"):
        ticket = CanonicalServiceItem(source_system=System.AUTOTASK, source_id=tid)
        ticket.contact_id = "at-contact-1"
        autotask.tickets[tid] = ticket
    # Contact mapped; BOTH tickets mirrored -> newest (301) wins.
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.CONTACT,
            autotask_entity_type="Contact",
            autotask_id="at-contact-1",
            ghl_entity_type="contact",
            ghl_id="ghlc-route",
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    for tid in ("300", "301"):
        _service_mapping(session, autotask_id=tid, ghl_id=f"card-{tid}")

    action = await route_ghl_contact_note(
        session,
        ghl_contact_id="ghlc-route",
        note_id="wn1",
        note_body="customer called",
        autotask=autotask,
    )
    assert action == "created"
    ticket_id, title, body = autotask.ticket_notes[0]
    assert ticket_id == "301"
    assert "[GHL note wn1]" in title
    assert body == "customer called"


async def test_ghl_note_held_when_contact_unmapped(session):
    autotask = FakeAutotask()
    action = await route_ghl_contact_note(
        session,
        ghl_contact_id="ghlc-unknown",
        note_id="wn2",
        note_body="hello",
        autotask=autotask,
    )
    assert action == "held"
    assert not autotask.ticket_notes


async def test_ghl_note_held_when_no_mirrored_ticket(session):
    autotask = FakeAutotask()
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.CONTACT,
            autotask_entity_type="Contact",
            autotask_id="at-contact-2",
            ghl_entity_type="contact",
            ghl_id="ghlc-noticket",
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()
    action = await route_ghl_contact_note(
        session,
        ghl_contact_id="ghlc-noticket",
        note_id="wn3",
        note_body="hello",
        autotask=autotask,
    )
    assert action == "held"
    assert not autotask.ticket_notes  # a note must never create a ticket


async def test_ghl_note_echo_guard_and_idempotency(session):
    autotask = FakeAutotask()
    echoed = await route_ghl_contact_note(
        session,
        ghl_contact_id="ghlc-any",
        note_id="wn4",
        note_body="[Autotask note 5] mirrored earlier",
        autotask=autotask,
    )
    assert echoed == "skipped"
    # Same note id again -> idempotency ledger short-circuits.
    again = await route_ghl_contact_note(
        session,
        ghl_contact_id="ghlc-any",
        note_id="wn4",
        note_body="anything",
        autotask=autotask,
    )
    assert again == "skipped"
