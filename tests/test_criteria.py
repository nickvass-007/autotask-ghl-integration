"""Customer sync criteria — audience filtering for the outbound mirror."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalContact
from integration.db.base import utcnow
from integration.db.enums import CanonicalEntityType, Environment, System
from integration.db.models import EntityMapping, SyncCriteria
from integration.sync.classification import sync_classifications
from integration.sync.criteria import AccountFilter, matches

from fakes import FakeAutotask, FakeGHL

pytestmark = pytest.mark.asyncio


def _rule(field="companyType", operator="eq", value="1", **kw) -> SyncCriteria:
    return SyncCriteria(
        environment=Environment.SANDBOX,
        entity_type="contact",
        field=field,
        operator=operator,
        value=value,
        active=True,
        **kw,
    )


def test_matches_operators():
    acct = {"companyType": 1, "classification": "3", "isActive": True}
    assert matches(acct, [_rule("companyType", "eq", "1")]) is True
    assert matches(acct, [_rule("companyType", "eq", "2")]) is False
    assert matches(acct, [_rule("companyType", "ne", "2")]) is True
    assert matches(acct, [_rule("classification", "in", "3, 4")]) is True
    assert matches(acct, [_rule("classification", "not_in", "3, 4")]) is False
    assert matches(acct, [_rule("isActive", "eq", "true")]) is True
    # Rules AND together.
    assert matches(acct, [_rule("companyType", "eq", "1"), _rule("classification", "eq", "9")]) is False


def test_no_rules_syncs_everything_and_missing_account_fails_closed():
    assert matches(None, []) is True
    assert matches({"companyType": 1}, []) is True
    assert matches(None, [_rule()]) is False  # rules exist + no account = no sync


async def test_account_filter_caches_and_gates(session):
    autotask = FakeAutotask()
    autotask.accounts_raw["77"] = {"companyType": "1"}
    autotask.accounts_raw["88"] = {"companyType": "2"}
    session.add(_rule("companyType", "eq", "1"))
    session.flush()

    f = AccountFilter(session, autotask)
    assert f.has_rules
    customer = CanonicalContact(source_system=System.AUTOTASK, source_id="500", company_id="77")
    prospect = CanonicalContact(source_system=System.AUTOTASK, source_id="501", company_id="88")
    orphan = CanonicalContact(source_system=System.AUTOTASK, source_id="502", company_id=None)
    assert await f.allows_contact(customer) is True
    assert await f.allows_contact(prospect) is False
    assert await f.allows_contact(orphan) is False


def test_canonical_contact_carries_active_flag():
    from integration.connectors.autotask import AutotaskConnector

    connector = AutotaskConnector(Environment.SANDBOX)
    active = connector._to_canonical({"id": 1, "emailAddress": "a@x.co", "isActive": 1})
    inactive = connector._to_canonical({"id": 2, "emailAddress": "b@x.co", "isActive": 0})
    assert active.extra["is_active"] is True
    assert inactive.extra["is_active"] is False


async def test_classification_sweep_respects_criteria(session):
    autotask = FakeAutotask()
    autotask.contacts["500"] = CanonicalContact(
        source_system=System.AUTOTASK, source_id="500", email="c@x.co", company_id="77"
    )
    autotask.accounts_raw["77"] = {"id": 77, "companyType": "2", "classification": "3"}
    autotask.picklists[("Companies", "companyType")] = {"1": "Customer", "2": "Prospect"}
    autotask.picklists[("Companies", "classification")] = {"3": "Managed Services"}
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
    session.add(_rule("companyType", "eq", "1"))  # only Customers — this account is a Prospect
    session.flush()

    result = await sync_classifications(session, autotask=autotask, ghl=ghl)
    assert result["contacts_pushed"] == 0
    assert "ghlc-1" not in ghl.tags
