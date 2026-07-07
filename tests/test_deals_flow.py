"""Flow 2c — the gated GHL Opportunity → Autotask direction (Spec §10.2)."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalContact, CanonicalDeal
from integration.db.base import utcnow
from integration.db.enums import ApprovalType, CanonicalEntityType, Environment, System
from integration.db.models import ApprovalQueue, EntityMapping
from integration.sync.deals import apply_deal_decision, process_ghl_opportunity

from fakes import FakeAutotask, FakeGHL, make_ghl_deal, make_stage_map

pytestmark = pytest.mark.asyncio


def _map_contact(session, *, ghl_id="ghlc-1", autotask_id="500"):
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.CONTACT,
            autotask_entity_type="Contact",
            autotask_id=autotask_id,
            ghl_entity_type="contact",
            ghl_id=ghl_id,
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()


def _map_deal(session, *, ghl_id="opp-1", autotask_id="900"):
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.DEAL,
            autotask_entity_type="Opportunity",
            autotask_id=autotask_id,
            ghl_entity_type="opportunity",
            ghl_id=ghl_id,
            environment=Environment.SANDBOX,
            last_synced_at=utcnow(),
        )
    )
    session.flush()


def _at_contact(autotask, *, contact_id="500", company_id="77"):
    autotask.contacts[contact_id] = CanonicalContact(
        source_system=System.AUTOTASK,
        source_id=contact_id,
        email="j@x.co",
        company_id=company_id,
    )


def _at_opp(autotask, *, opp_id="900", stage="1", amount=None, status="1", account="77"):
    d = CanonicalDeal(source_system=System.AUTOTASK, source_id=opp_id)
    d.name = "New Deal"
    d.stage = stage
    d.monetary_value = amount
    d.status = status
    d.account_id = account
    autotask.opportunities[opp_id] = d
    return d


async def test_unknown_pipeline_raises_unmapped_approval(session):
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(pipeline="pipe-mystery"),
        event_id="e1",
        autotask=FakeAutotask(),
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    row = session.get(ApprovalQueue, outcome.approval_ids[0])
    assert row.approval_type == ApprovalType.UNMAPPED_STAGE


async def test_service_pipeline_write_is_blocked_not_queued(session):
    autotask = FakeAutotask()
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(pipeline="pipe-service", stage="stage-open"),
        event_id="e2",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "blocked"
    assert not autotask.created_opportunities
    assert not session.query(ApprovalQueue).all()  # blocked, never queued (§10.2)


async def test_unmapped_stage_raises_approval(session):
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(stage="stage-renamed"),
        event_id="e3",
        autotask=FakeAutotask(),
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"


async def test_clean_miss_creates_autotask_opportunity(session):
    autotask = FakeAutotask()
    _at_contact(autotask)
    _map_contact(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(amount=5000),
        event_id="e4",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "created"
    created = autotask.created_opportunities[0]
    assert created.account_id == "77"
    assert created.stage == "1"  # translated via the stage map


async def test_duplicate_title_on_account_raises_approval(session):
    autotask = FakeAutotask()
    _at_contact(autotask)
    _at_opp(autotask, opp_id="901")  # same normalised title "New Deal"
    _map_contact(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(),
        event_id="e5",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    assert not autotask.created_opportunities


async def test_unresolved_account_raises_approval(session):
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(contact_id="ghl-unknown"),
        event_id="e6",
        autotask=FakeAutotask(),
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    row = session.get(ApprovalQueue, outcome.approval_ids[0])
    assert row.approval_type == ApprovalType.OPPORTUNITY_CREATE


async def test_forward_stage_move_auto_applies(session):
    autotask = FakeAutotask()
    _at_opp(autotask, stage="1")
    _map_deal(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(stage="stage-qualified"),
        event_id="e7",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "updated"
    assert autotask.opp_updates == [("900", {"stage": 2})]


async def test_backwards_stage_move_requires_approval(session):
    autotask = FakeAutotask()
    _at_opp(autotask, stage="3")
    _map_deal(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(stage="stage-new"),
        event_id="e8",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    assert not autotask.opp_updates  # Autotask untouched


async def test_amount_conflict_on_populated_field_requires_approval(session):
    autotask = FakeAutotask()
    _at_opp(autotask, amount=10_000)
    _map_deal(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(amount=99),
        event_id="e9",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    row = session.get(ApprovalQueue, outcome.approval_ids[0])
    assert row.approval_type == ApprovalType.AMOUNT_CONFLICT
    assert autotask.opportunities["900"].monetary_value == 10_000  # unchanged


async def test_amount_fills_empty_autotask_field_additively(session):
    autotask = FakeAutotask()
    _at_opp(autotask, amount=None)
    _map_deal(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(amount=7500),
        event_id="e10",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    assert outcome.action == "updated"
    assert ("900", {"amount": 7500}) in autotask.opp_updates


async def test_won_status_raises_outcome_approval_and_onboarding(session):
    autotask = FakeAutotask()
    _at_opp(autotask, stage="3")
    _map_deal(session)
    ghl = FakeGHL()
    ghl.contacts["ghlc-1"] = CanonicalContact(
        source_system=System.GHL, source_id="ghlc-1", email="new@co.io", company_name="NewCo"
    )
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(stage="stage-won", status="won"),
        event_id="e11",
        autotask=autotask,
        ghl=ghl,
        stage_map=make_stage_map(),
    )
    assert outcome.action == "approval"
    types = {
        row.approval_type for row in session.query(ApprovalQueue).all()
    }
    assert ApprovalType.SALES_OUTCOME_CHANGE in types
    assert ApprovalType.CUSTOMER_ONBOARDING in types  # Stage C fired (§8.2)
    assert not autotask.opp_updates  # nothing auto-applied


async def test_apply_decision_creates_opportunity_after_approval(session):
    autotask = FakeAutotask()
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(contact_id="ghl-unknown", amount=1234),
        event_id="e12",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    approval_id = outcome.approval_ids[0]
    applied = await apply_deal_decision(
        session,
        approval_id=approval_id,
        approve=True,
        decided_by="tester",
        autotask=autotask,
        chosen_account_id="77",
    )
    assert applied.action == "created"
    created = autotask.created_opportunities[0]
    assert created.account_id == "77"
    assert created.monetary_value == 1234


async def test_reject_leaves_autotask_untouched(session):
    autotask = FakeAutotask()
    _at_opp(autotask, amount=10_000)
    _map_deal(session)
    outcome = await process_ghl_opportunity(
        session,
        ghl_deal=make_ghl_deal(amount=99),
        event_id="e13",
        autotask=autotask,
        ghl=FakeGHL(),
        stage_map=make_stage_map(),
    )
    applied = await apply_deal_decision(
        session,
        approval_id=outcome.approval_ids[0],
        approve=False,
        decided_by="tester",
        autotask=autotask,
    )
    assert applied.action == "skipped"
    assert autotask.opportunities["900"].monetary_value == 10_000


async def test_duplicate_event_is_idempotent(session):
    autotask = FakeAutotask()
    _at_contact(autotask)
    _map_contact(session)
    deal = make_ghl_deal()
    first = await process_ghl_opportunity(
        session, ghl_deal=deal, event_id="same", autotask=autotask,
        ghl=FakeGHL(), stage_map=make_stage_map(),
    )
    second = await process_ghl_opportunity(
        session, ghl_deal=deal, event_id="same", autotask=autotask,
        ghl=FakeGHL(), stage_map=make_stage_map(),
    )
    assert first.action == "created"
    assert second.action == "skipped"
    assert len(autotask.created_opportunities) == 1
