"""Teams notification layer (Spec §11): cards, feed, decision service."""

from __future__ import annotations

import pytest

from integration.canonical.entities import CanonicalContact
from integration.config.settings import Settings
from integration.db.enums import (
    ApprovalType,
    CanonicalEntityType,
    Environment,
    Severity,
    System,
)
from integration.db.models import ApprovalQueue
from integration.sync.approvals import ApprovalRequest, raise_approval
from integration.sync.decisions import apply_approval_decision
from integration.teams import notify

from fakes import FakeAutotask, FakeGHL

pytestmark = pytest.mark.asyncio


def _approval_row(severity=Severity.MED) -> ApprovalQueue:
    return ApprovalQueue(
        id=42,
        status="pending",
        severity=severity,
        approval_type=ApprovalType.CONTACT_POSSIBLE_DUPLICATE,
        canonical_entity_type=CanonicalEntityType.CONTACT,
        source_system=System.GHL,
        target_system=System.AUTOTASK,
        proposed_change={"fields": [{"field": "phone", "before": "1", "after": "2"}]},
        detected_reason="possible duplicate",
        environment=Environment.SANDBOX,
        correlation_id="corr-1",
    )


async def test_workflow_card_posts_with_portal_deeplink(monkeypatch):
    sent: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> bool:
        sent.append((url, payload))
        return True

    monkeypatch.setattr(notify, "_post_json", fake_post)
    monkeypatch.setattr(
        notify,
        "get_settings",
        lambda: Settings(
            teams_workflow_webhook_url="https://flow.example/hook",
            public_base_url="https://sync.example",
        ),
    )

    await notify.post_approval_card(_approval_row())

    assert len(sent) == 1
    url, payload = sent[0]
    assert url == "https://flow.example/hook"
    card = payload["attachments"][0]["content"]
    # Workflow transport can't handle Action.Submit -> deep-link to the portal.
    assert card["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "Review in Sync Control Centre",
            "url": "https://sync.example/portal#approvals",
        }
    ]
    assert any("Approval needed" in str(b.get("text", "")) for b in card["body"])


async def test_feed_event_posts_plain_message(monkeypatch):
    sent: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> bool:
        sent.append((url, payload))
        return True

    monkeypatch.setattr(notify, "_post_json", fake_post)
    monkeypatch.setattr(
        notify,
        "get_settings",
        lambda: Settings(teams_workflow_webhook_url="https://flow.example/hook"),
    )

    await notify.post_feed_event("`blocked` ghl_to_autotask deal: write blocked")
    assert sent == [
        (
            "https://flow.example/hook",
            {"type": "message", "text": "`blocked` ghl_to_autotask deal: write blocked"},
        )
    ]


async def test_announce_approval_is_a_noop_when_unconfigured(session):
    """raise_approval fires the announce hook on every approval — with nothing
    configured it must do nothing (and never touch the network or raise)."""
    row = raise_approval(
        session,
        ApprovalRequest(
            approval_type=ApprovalType.ACCOUNT_LINK,
            severity=Severity.MED,
            canonical_entity_type=CanonicalEntityType.CONTACT,
            source_system=System.GHL,
            target_system=System.AUTOTASK,
            proposed_change={},
            detected_reason="test",
            correlation_id="corr-noop",
        ),
    )
    assert row.id is not None
    assert not notify._tasks  # nothing scheduled


# ── Shared decision service ───────────────────────────────────────────────────
async def test_decision_service_routes_contact_approval(session):
    """A rejected contact approval applies via the Flow-1 handler."""
    autotask = FakeAutotask()
    ghl = FakeGHL()
    ghl_contact = CanonicalContact(
        source_system=System.GHL, source_id="ghlc-d1", email="d@x.co"
    )
    row = raise_approval(
        session,
        ApprovalRequest(
            approval_type=ApprovalType.ACCOUNT_CREATE,
            severity=Severity.HIGH,
            canonical_entity_type=CanonicalEntityType.CONTACT,
            source_system=System.GHL,
            target_system=System.AUTOTASK,
            ghl_id="ghlc-d1",
            proposed_change={"incoming": {"email": "d@x.co"}},
            detected_reason="create account?",
            correlation_id="corr-d1",
        ),
    )
    result = await apply_approval_decision(
        session,
        approval_id=row.id,
        decision="reject",
        decided_by="test",
        autotask=autotask,
        ghl=ghl,
    )
    assert result.action == "skipped"  # rejected -> nothing written to Autotask
    assert not autotask.created_accounts
    assert not autotask.created_contacts
    _ = ghl_contact  # incoming payload only travels via proposed_change