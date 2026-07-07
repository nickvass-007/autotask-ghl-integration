"""End-to-end Contacts flow against fake connectors (Spec §9).

These assert the protection rules hold: clean miss creates, fuzzy/conflict routes
to approval (never overwrites), Accounts are never auto-created, every write
captures a before-state, and processing is idempotent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from integration.canonical.entities import CanonicalCompany, CanonicalContact
from integration.db.enums import ApprovalStatus, ApprovalType, System
from integration.db.models import ApprovalQueue, AuditLog, EntityMapping, TransactionLog
from integration.sync import contacts as flow

from fakes import FakeAutotask

pytestmark = pytest.mark.asyncio


def ghl_contact(**kw) -> CanonicalContact:
    return CanonicalContact(source_system=System.GHL, **kw)


def account(name: str, cid: str) -> CanonicalCompany:
    c = CanonicalCompany(source_system=System.AUTOTASK, name=name)
    c.company_id = cid
    return c


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


async def test_clean_miss_with_exact_account_creates_contact(session):
    at = FakeAutotask(accounts=[account("Acme", "10")])
    incoming = ghl_contact(source_id="g1", email="new@acme.com", first_name="Ann", company_name="Acme")

    outcome = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e1", autotask=at)

    assert outcome.action == "created"
    assert len(at.created_contacts) == 1
    assert at.created_contacts[0].company_id == "10"  # linked to existing Account
    # mapping written (the dupe-prevention spine) + before-state audit captured
    assert _count(session, EntityMapping) == 1
    assert _count(session, AuditLog) == 1
    assert session.execute(select(AuditLog.operation)).scalar_one() == "create"


async def test_no_account_match_raises_approval_and_creates_nothing(session):
    at = FakeAutotask(accounts=[])  # no holding account configured
    incoming = ghl_contact(source_id="g2", email="x@brandnew.com", company_name="Brand New Co")

    outcome = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e2", autotask=at)

    assert outcome.action == "approval"
    assert at.created_contacts == []          # ⚠️ never auto-create an Account/Contact
    approval = session.execute(select(ApprovalQueue)).scalar_one()
    assert approval.approval_type == ApprovalType.ACCOUNT_CREATE
    assert approval.status == ApprovalStatus.PENDING


async def test_confident_email_match_links_without_duplicate(session):
    existing = CanonicalContact(
        source_system=System.AUTOTASK, source_id="500", email="dup@acme.com", company_id="10"
    )
    at = FakeAutotask(contacts={"500": existing}, accounts=[account("Acme", "10")])
    incoming = ghl_contact(source_id="g3", email="dup@acme.com", company_name="Acme")

    outcome = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e3", autotask=at)

    assert outcome.action in ("linked", "updated", "skipped")
    assert at.created_contacts == []          # linked, not duplicated
    mapping = session.execute(select(EntityMapping)).scalar_one()
    assert mapping.autotask_id == "500" and mapping.ghl_id == "g3"


async def test_additive_fills_empty_field_automatically(session):
    existing = CanonicalContact(
        source_system=System.AUTOTASK, source_id="600", email="a@acme.com", first_name="Al", phone=None
    )
    at = FakeAutotask(contacts={"600": existing}, accounts=[account("Acme", "10")])
    # Pre-link so we hit the existing-contact reconciliation path.
    session.add(
        EntityMapping(
            canonical_entity_type="contact",
            autotask_entity_type="Contact",
            autotask_id="600",
            ghl_entity_type="contact",
            ghl_id="g4",
            environment="sandbox",
        )
    )
    session.flush()
    incoming = ghl_contact(source_id="g4", email="a@acme.com", first_name="Al", phone="0400123123")

    outcome = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e4", autotask=at)

    assert outcome.action == "updated"
    assert at.updates and at.updates[0][1].get("phone") == "0400123123"  # empty field filled
    assert _count(session, ApprovalQueue) == 0                          # additive needs no approval


async def test_populated_conflict_raises_approval_never_overwrites(session):
    existing = CanonicalContact(
        source_system=System.AUTOTASK, source_id="700", email="b@acme.com", first_name="Robert", phone="0400000000"
    )
    at = FakeAutotask(contacts={"700": existing}, accounts=[account("Acme", "10")])
    session.add(
        EntityMapping(
            canonical_entity_type="contact",
            autotask_entity_type="Contact",
            autotask_id="700",
            ghl_entity_type="contact",
            ghl_id="g5",
            environment="sandbox",
        )
    )
    session.flush()
    # Different first_name AND different phone -> both populated conflicts.
    incoming = ghl_contact(source_id="g5", email="b@acme.com", first_name="Bob", phone="0400999999")

    outcome = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e5", autotask=at)

    assert outcome.action == "approval"
    assert at.updates == []                    # ⚠️ never auto-overwrote a populated field
    approval = session.execute(select(ApprovalQueue)).scalar_one()
    assert approval.approval_type == ApprovalType.CONTACT_FIELD_CONFLICT
    fields = {f["field"] for f in approval.proposed_change["fields"]}
    assert {"first_name", "phone"} <= fields


async def test_idempotent_duplicate_event_is_skipped(session):
    at = FakeAutotask(accounts=[account("Acme", "10")])
    incoming = ghl_contact(source_id="g6", email="c@acme.com", company_name="Acme")

    first = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="dup", autotask=at)
    second = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="dup", autotask=at)

    assert first.action == "created"
    assert second.action == "skipped"
    assert len(at.created_contacts) == 1       # not created twice


async def test_approve_field_conflict_applies_change_with_audit(session):
    existing = CanonicalContact(
        source_system=System.AUTOTASK, source_id="800", email="d@acme.com", first_name="Tom", phone="0411111111"
    )
    at = FakeAutotask(contacts={"800": existing}, accounts=[account("Acme", "10")])
    session.add(
        EntityMapping(
            canonical_entity_type="contact",
            autotask_entity_type="Contact",
            autotask_id="800",
            ghl_entity_type="contact",
            ghl_id="g7",
            environment="sandbox",
        )
    )
    session.flush()
    incoming = ghl_contact(source_id="g7", email="d@acme.com", first_name="Thomas", phone="0411111111")
    out = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e7", autotask=at)
    approval_id = out.approval_ids[0]

    audits_before = _count(session, AuditLog)
    applied = await flow.apply_decision(
        session, approval_id=approval_id, approve=True, decided_by="nick", autotask=at
    )

    assert applied.action == "updated"
    assert at.updates and at.updates[-1][1].get("first_name") == "Thomas"  # applied on approval
    assert _count(session, AuditLog) == audits_before + 1                  # before-state captured
    approval = session.get(ApprovalQueue, approval_id)
    assert approval.status == ApprovalStatus.APPROVED and approval.decided_by == "nick"


async def test_reject_leaves_autotask_untouched(session):
    existing = CanonicalContact(
        source_system=System.AUTOTASK, source_id="900", email="e@acme.com", first_name="Sam", phone="0422222222"
    )
    at = FakeAutotask(contacts={"900": existing}, accounts=[account("Acme", "10")])
    session.add(
        EntityMapping(
            canonical_entity_type="contact",
            autotask_entity_type="Contact",
            autotask_id="900",
            ghl_entity_type="contact",
            ghl_id="g8",
            environment="sandbox",
        )
    )
    session.flush()
    incoming = ghl_contact(source_id="g8", email="e@acme.com", first_name="Samuel", phone="0422222222")
    out = await flow.process_ghl_contact(session, ghl_contact=incoming, event_id="e8", autotask=at)

    applied = await flow.apply_decision(
        session, approval_id=out.approval_ids[0], approve=False, decided_by="nick", autotask=at
    )
    assert applied.action == "skipped"
    assert at.updates == []   # protective default: rejection writes nothing
