"""Sync profile engine: dry-run, safety gates, approval workflow, scheduling."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integration.canonical.entities import CanonicalContact
from integration.db.enums import Environment, System
from integration.db.models import SyncProfile
from integration.jobs.scheduler import compute_next_run
from integration.sync.profiles import (
    approve_profile,
    criteria_hash,
    live_run_blockers,
    run_dry_run,
    run_live,
)

from fakes import FakeAutotask, FakeGHL

pytestmark = pytest.mark.asyncio

CRITERIA = {"rules": [{"field": "companyType", "operator": "eq", "value": "1"},
                      {"field": "isActive", "operator": "eq", "value": "true"}]}


class DryRunAutotask(FakeAutotask):
    """FakeAutotask + the raw query surface the dry-run engine uses."""

    def __init__(self, companies: list[dict], contact_counts: dict[int, int]):
        super().__init__()
        self._companies = companies
        self._counts = contact_counts
        self._client = self
        self.picklists[("Companies", "companyType")] = {"1": "Customer", "2": "Prospect"}
        self.picklists[("Companies", "classification")] = {"3": "Managed Services", "4": "T&M"}

    def _url(self, path: str) -> str:
        return path

    def _auth_headers(self) -> dict:
        return {}

    def _to_canonical(self, item: dict) -> CanonicalContact:
        contact = CanonicalContact(
            source_system=System.AUTOTASK,
            source_id=str(item["id"]),
            email=item.get("emailAddress"),
            first_name=item.get("firstName"),
            last_name=item.get("lastName"),
            company_id=str(item.get("companyID")),
        )
        contact.extra["is_active"] = bool(item.get("isActive", 1))
        return contact

    async def post(self, url: str, headers=None, json=None):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        if url.endswith("Companies/query"):
            last_id = json["filter"][0]["value"]
            items = [c for c in self._companies if c["id"] > last_id]
            return _Resp({"items": items})
        if url.endswith("Contacts/query/count"):
            ids = json["filter"][0]["value"]
            return _Resp({"queryCount": sum(self._counts.get(i, 0) for i in ids)})
        if url.endswith("Contacts/query"):
            account = json["filter"][0]["value"]
            items = [
                {"id": 9000 + account * 10 + n, "emailAddress": f"c{account}{n}@x.co",
                 "firstName": "T", "lastName": str(n), "companyID": account, "isActive": 1}
                for n in range(self._counts.get(account, 0))
            ]
            return _Resp({"items": items})
        raise AssertionError(f"unexpected url {url}")


def _companies():
    return [
        {"id": 1, "companyName": "ACME", "companyType": 1, "isActive": True, "classification": 3},
        {"id": 2, "companyName": "BETA", "companyType": 1, "isActive": True, "classification": 4},
        {"id": 3, "companyName": "PROSPECTCO", "companyType": 2, "isActive": True, "classification": 3},
        {"id": 4, "companyName": "OLDCO", "companyType": 1, "isActive": False, "classification": 4},
    ]


def _profile(session, **kw) -> SyncProfile:
    p = SyncProfile(
        environment=Environment.SANDBOX,
        name=kw.pop("name", "Test profile"),
        criteria_json=kw.pop("criteria", CRITERIA),
        **kw,
    )
    p.criteria_hash = criteria_hash(p.criteria_json)
    session.add(p)
    session.flush()
    return p


async def test_dry_run_summary_and_breakdown(session):
    autotask = DryRunAutotask(_companies(), {1: 5, 2: 3})
    p = _profile(session)
    job = await run_dry_run(session, profile=p, autotask=autotask, started_by="tester")
    s = job.summary_json
    assert job.status == "succeeded"
    assert s["customers_matched"] == 2          # ACME + BETA
    assert s["customers_excluded"] == 2         # prospect + inactive
    assert s["contacts_matched"] == 8
    sync_rows = [r for r in s["type_breakdown"] if r["action"] == "Sync"]
    assert {(r["type"], r["classification"]) for r in sync_rows} == {
        ("Customer", "Managed Services"), ("Customer", "T&M"),
    }
    assert any("companyType" in " ".join(e["reasons"]) for e in s["excluded_examples"])


async def test_live_blocked_until_dry_run_and_approval(session):
    autotask = DryRunAutotask(_companies(), {1: 5, 2: 3})
    p = _profile(session)
    blockers = live_run_blockers(session, p)
    assert any("dry-run required" in b for b in blockers)

    await run_dry_run(session, profile=p, autotask=autotask, started_by="tester")
    blockers = live_run_blockers(session, p)
    assert any("approval required" in b for b in blockers)

    approve_profile(session, p, approved_by="nick")
    assert live_run_blockers(session, p) == []


async def test_criteria_change_invalidates_dry_run(session):
    autotask = DryRunAutotask(_companies(), {1: 5, 2: 3})
    p = _profile(session)
    await run_dry_run(session, profile=p, autotask=autotask, started_by="tester")
    approve_profile(session, p, approved_by="nick")
    # Simulate the API's criteria-change handling (§6 rule 3).
    p.criteria_json = {"rules": [{"field": "companyType", "operator": "eq", "value": "2"}]}
    blockers = live_run_blockers(session, p)
    assert any("criteria changed" in b for b in blockers)
    with pytest.raises(ValueError):
        approve_profile(session, p, approved_by="nick")


async def test_significant_change_pauses_for_review(session):
    p = _profile(session)
    autotask = DryRunAutotask(_companies(), {1: 5, 2: 3})
    await run_dry_run(session, profile=p, autotask=autotask, started_by="tester")
    approve_profile(session, p, approved_by="nick")
    # Audience grows >20%: two more matching customers appear.
    grown = _companies() + [
        {"id": 5, "companyName": "NEWCO", "companyType": 1, "isActive": True, "classification": 3},
        {"id": 6, "companyName": "NEWCO2", "companyType": 1, "isActive": True, "classification": 3},
    ]
    await run_dry_run(
        session, profile=p, autotask=DryRunAutotask(grown, {1: 5, 2: 3}), started_by="tester"
    )
    assert p.review_state == "review_required"
    assert p.schedule_paused is True
    assert "customer count changed" in (p.review_reason or "")


async def test_live_run_pushes_matched_audience(session):
    autotask = DryRunAutotask(_companies(), {1: 2, 2: 1})
    p = _profile(session, requires_approval_before_live_sync=False)
    await run_dry_run(session, profile=p, autotask=autotask, started_by="tester")
    ghl = FakeGHL()
    job = await run_live(
        session, profile=p, autotask=autotask, ghl=ghl, started_by="tester"
    )
    assert job.status == "succeeded"
    assert job.summary_json["contacts_pushed"] == 3
    assert len(ghl.created) == 3


def test_schedule_presets_and_next_run():
    after = datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)  # Wed 13:00 Sydney
    nxt = compute_next_run("preset", {"preset": "weekday_7am"}, after=after)
    assert nxt is not None
    syd = nxt.astimezone(tz=__import__("zoneinfo").ZoneInfo("Australia/Sydney"))
    assert (syd.hour, syd.minute) == (7, 0)
    assert syd.weekday() == 3  # next weekday morning = Thursday

    assert compute_next_run("manual", None) is None

    nxt = compute_next_run("interval", {"every_hours": 4}, after=after)
    assert nxt == datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)

    # Weekend suppression pushes a Saturday slot to Monday.
    sat = datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc)  # Sat 08:00 Sydney
    nxt = compute_next_run(
        "daily", {"time": "09:00"}, after=sat, allow_weekend=False
    )
    syd = nxt.astimezone(tz=__import__("zoneinfo").ZoneInfo("Australia/Sydney"))
    assert syd.weekday() == 0
