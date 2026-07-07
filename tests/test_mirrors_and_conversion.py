"""Flow 2a/2b mirrors (AT→GHL) and Stage-C conversion handoff (Spec §10.2, §8.2)."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalContact, CanonicalDeal, CanonicalServiceItem
from integration.db.base import utcnow
from integration.db.enums import ApprovalType, CanonicalEntityType, Environment, System
from integration.db.models import ApprovalQueue, EntityMapping
from integration.sync.conversion import apply_onboarding, handle_closed_won
from integration.sync.deals import apply_deal_decision
from integration.sync.mirrors import mirror_autotask_opportunity, mirror_autotask_ticket

from fakes import FakeAutotask, FakeGHL, make_ghl_deal, make_stage_map

pytestmark = pytest.mark.asyncio


def _at_deal(*, deal_id="900", stage="2", name="Big Deal", amount=1000.0):
    d = CanonicalDeal(source_system=System.AUTOTASK, source_id=deal_id)
    d.name = name
    d.stage = stage
    d.monetary_value = amount
    d.account_id = "77"
    return d


def _at_ticket(*, ticket_id="T1", status="1", title="Server down"):
    t = CanonicalServiceItem(source_system=System.AUTOTASK, source_id=ticket_id)
    t.status = status
    t.title = title
    return t


async def test_opportunity_mirror_creates_ghl_card(session):
    ghl = FakeGHL()
    action = await mirror_autotask_opportunity(
        session, deal=_at_deal(), ghl=ghl, stage_map=make_stage_map()
    )
    assert action == "created"
    deal, pipeline_id, stage_id = ghl.created_opps[0]
    assert pipeline_id == "pipe-sales"
    assert stage_id == "stage-qualified"  # AT stage 2 translated via the map


async def test_opportunity_mirror_updates_linked_card(session):
    ghl = FakeGHL()
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.DEAL,
            autotask_entity_type="Opportunity",
            autotask_id="900",
            ghl_entity_type="opportunity",
            ghl_id="ghlopp9",
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()
    action = await mirror_autotask_opportunity(
        session, deal=_at_deal(stage="3"), ghl=ghl, stage_map=make_stage_map()
    )
    assert action == "updated"
    ref, changes = ghl.opp_updates[0]
    assert ref == "ghlopp9"
    assert changes["pipelineStageId"] == "stage-proposal"


async def test_unmapped_autotask_stage_routes_to_approval(session):
    ghl = FakeGHL()
    action = await mirror_autotask_opportunity(
        session, deal=_at_deal(stage="42"), ghl=ghl, stage_map=make_stage_map()
    )
    assert action == "approval"
    assert not ghl.created_opps  # never guessed (§10.3)
    row = session.query(ApprovalQueue).one()
    assert row.approval_type == ApprovalType.UNMAPPED_STAGE


async def test_ticket_mirror_creates_service_card(session):
    ghl = FakeGHL()
    action = await mirror_autotask_ticket(
        session, ticket=_at_ticket(), ghl=ghl, stage_map=make_stage_map()
    )
    assert action == "created"
    card, pipeline_id, stage_id = ghl.created_opps[0]
    assert pipeline_id == "pipe-service"
    assert stage_id == "stage-open"
    assert card.name.startswith("[Ticket]")


# ── Stage C — conversion handoff ──────────────────────────────────────────────
async def test_closed_won_raises_onboarding_approval_and_writes_nothing(session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    ghl.contacts["ghlc-1"] = CanonicalContact(
        source_system=System.GHL, source_id="ghlc-1", email="won@newco.io",
        first_name="Won", last_name="Deal", company_name="NewCo",
    )
    outcome = await handle_closed_won(
        session, ghl_deal=make_ghl_deal(stage="stage-won", status="won"),
        autotask=autotask, ghl=ghl,
    )
    assert outcome.action == "approval"
    assert not autotask.created_accounts and not autotask.created_contacts
    row = session.get(ApprovalQueue, outcome.approval_ids[0])
    assert row.approval_type == ApprovalType.CUSTOMER_ONBOARDING


async def test_onboarding_approval_creates_account_contact_and_stamps(session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    ghl.contacts["ghlc-1"] = CanonicalContact(
        source_system=System.GHL, source_id="ghlc-1", email="won@newco.io",
        first_name="Won", last_name="Deal", company_name="NewCo",
    )
    outcome = await handle_closed_won(
        session, ghl_deal=make_ghl_deal(stage="stage-won", status="won"),
        autotask=autotask, ghl=ghl,
    )
    # Human approves via the routed decide path.
    applied = await apply_deal_decision(
        session,
        approval_id=outcome.approval_ids[0],
        approve=True,
        decided_by="tester",
        autotask=autotask,
        ghl=ghl,
    )
    assert applied.action == "created"
    assert autotask.created_accounts[0].name == "NewCo"
    assert autotask.created_contacts[0].email == "won@newco.io"
    # Identity spine written…
    link = (
        session.query(EntityMapping)
        .filter_by(canonical_entity_type=CanonicalEntityType.CONTACT, ghl_id="ghlc-1")
        .one()
    )
    assert link.autotask_id == applied.autotask_id
    # …and the GHL contact carries the converted stamp (§8.2 step 3).
    assert any("Managed in Autotask" in t for t in ghl.tags["ghlc-1"])


async def test_already_converted_contact_only_restamps(session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.CONTACT,
            autotask_entity_type="Contact",
            autotask_id="500",
            ghl_entity_type="contact",
            ghl_id="ghlc-1",
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()
    outcome = await handle_closed_won(
        session, ghl_deal=make_ghl_deal(stage="stage-won", status="won"),
        autotask=autotask, ghl=ghl,
    )
    assert outcome.action == "stamped"
    assert not session.query(ApprovalQueue).all()
    assert ghl.tags["ghlc-1"]


async def test_onboarding_link_to_chosen_account(session):
    autotask = FakeAutotask()
    ghl = FakeGHL()
    ghl.contacts["ghlc-1"] = CanonicalContact(
        source_system=System.GHL, source_id="ghlc-1", email="won@newco.io",
        first_name="Won", last_name="Deal", company_name="NewCo",
    )
    outcome = await handle_closed_won(
        session, ghl_deal=make_ghl_deal(stage="stage-won", status="won"),
        autotask=autotask, ghl=ghl,
    )
    applied = await apply_deal_decision(
        session,
        approval_id=outcome.approval_ids[0],
        approve=True,
        decided_by="tester",
        autotask=autotask,
        ghl=ghl,
        chosen_account_id="77",  # operator picked an existing Account
    )
    assert applied.action == "created"
    assert not autotask.created_accounts  # linked, not created
    assert autotask.created_contacts[0].company_id == "77"
