"""Company mirror — Autotask Account → GHL Business."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalCompany, CanonicalContact
from integration.db.base import utcnow
from integration.db.enums import CanonicalEntityType, Environment, System
from integration.db.models import EntityMapping
from integration.sync.autotask_to_ghl import push_autotask_contact
from integration.sync.companies import company_from_account_raw, mirror_autotask_account

from fakes import FakeGHL

pytestmark = pytest.mark.asyncio


def _company(account_id="77", name="ACME PTY LTD") -> CanonicalCompany:
    c = CanonicalCompany(source_system=System.AUTOTASK, source_id=account_id, name=name)
    c.company_id = account_id
    return c


async def test_mirror_creates_business_and_mapping(session):
    ghl = FakeGHL()
    action, business_id = await mirror_autotask_account(session, company=_company(), ghl=ghl)
    assert action == "created"
    assert ghl.businesses[business_id]["name"] == "ACME PTY LTD"
    link = (
        session.query(EntityMapping)
        .filter_by(canonical_entity_type=CanonicalEntityType.COMPANY, autotask_id="77")
        .one()
    )
    assert link.ghl_id == business_id


async def test_mirror_adopts_existing_same_named_business(session):
    ghl = FakeGHL()
    ghl.businesses["biz-pre"] = {"id": "biz-pre", "name": "ACME PTY LTD"}
    action, business_id = await mirror_autotask_account(session, company=_company(), ghl=ghl)
    assert action == "adopted"
    assert business_id == "biz-pre"


async def test_mirror_updates_linked_business(session):
    ghl = FakeGHL()
    _, business_id = await mirror_autotask_account(session, company=_company(), ghl=ghl)
    action, again = await mirror_autotask_account(
        session, company=_company(name="ACME PTY LTD (renamed)"), ghl=ghl
    )
    assert action == "updated" and again == business_id
    assert ghl.business_updates[-1][1]["name"] == "ACME PTY LTD (renamed)"


async def test_contact_push_attaches_business_id(session):
    ghl = FakeGHL()
    _, business_id = await mirror_autotask_account(session, company=_company(), ghl=ghl)
    contact = CanonicalContact(
        source_system=System.AUTOTASK, source_id="500", email="a@acme.co", company_id="77"
    )
    await push_autotask_contact(session, at_contact=contact, ghl=ghl)
    assert contact.extra["ghl_business_id"] == business_id


def test_company_from_account_raw():
    c = company_from_account_raw({"id": 77, "companyName": "ACME", "webAddress": "acme.co"})
    assert c.source_id == "77" and c.name == "ACME" and c.website == "acme.co"
