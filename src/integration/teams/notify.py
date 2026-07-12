"""Outbound Teams notifications — approval cards + transaction feed (Spec §11).

Two transports, best-effort and independent:

1. **Teams bot (proactive)** — when the Azure Bot is configured AND the bot has
   been added to a channel (we store the conversation reference on first
   contact), approval cards with working Approve/Reject buttons are posted
   straight into that channel.
2. **Teams Workflow webhook** (``TEAMS_WORKFLOW_WEBHOOK_URL``) — the
   zero-infrastructure fallback: cards and feed lines post via a Power Automate
   "When a Teams webhook request is received" flow. Buttons can't call back on
   this transport, so cards carry a deep-link to the portal instead.

HIGH-severity approvals additionally email admins via Graph (Spec §11 backup
channel). Every send is fire-and-forget: a Teams/Graph outage must never break
or slow the sync path.
"""

from __future__ import annotations

import asyncio

import httpx

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.models import ApprovalQueue
from .alerts import send_admin_alert
from .cards import approval_card

log = get_logger(__name__)

# Strong refs so fire-and-forget tasks aren't garbage-collected mid-flight.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Schedule best-effort background work; silently drop if no loop runs
    (sync-only contexts, e.g. plain unit tests)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _post_json(url: str, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:  # notification-only path — never raises upstream
        log.warning("Teams notification failed: %s", exc)
        return False


def workflow_message(card: dict) -> dict:
    """Wrap an Adaptive Card in the message envelope Teams Workflows expect."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def portal_link_card(approval: ApprovalQueue) -> dict:
    """The approval card variant for the Workflow-webhook transport: same body,
    but Action.Submit can't call back through a Workflow, so the actions are
    replaced with a deep-link to the portal approvals page."""
    s = get_settings()
    card = approval_card(approval, "")  # token never leaves the process on this path
    card["actions"] = [
        {
            "type": "Action.OpenUrl",
            "title": "Review in Sync Control Centre",
            "url": f"{s.public_base_url.rstrip('/')}/portal#approvals",
        }
    ]
    return card


async def post_approval_card(approval: ApprovalQueue) -> None:
    """Post one approval to every configured channel. Best-effort."""
    s = get_settings()
    posted = False

    # 1) Bot proactive message — real Approve/Reject buttons.
    if s.teams_bot_app_id:
        from .bot import send_proactive_card  # lazy: botbuilder is optional

        try:
            posted = await send_proactive_card(
                approval_card(approval, s.approval_callback_secret)
            )
        except Exception as exc:
            log.warning("Proactive bot card failed: %s", exc)

    # 2) Workflow webhook — always posted too (feed channel may differ from
    #    the approvals channel; harmless duplication beats a missed approval).
    if s.teams_workflow_webhook_url:
        ok = await _post_json(
            s.teams_workflow_webhook_url, workflow_message(portal_link_card(approval))
        )
        posted = posted or ok

    if not posted:
        log.info(
            "Approval #%s raised — no Teams channel configured/reachable", approval.id
        )

    # 3) HIGH severity also emails admins so a Teams outage can't hide it (§11).
    if str(getattr(approval.severity, "value", approval.severity)).lower() == "high":
        await send_admin_alert(
            f"HIGH approval #{approval.id}: {approval.approval_type}",
            f"{approval.detected_reason}\n\nReview: "
            f"{s.public_base_url.rstrip('/')}/portal#approvals",
        )


async def post_feed_event(text: str) -> None:
    """One line into the transaction-feed channel (Workflow transport)."""
    s = get_settings()
    if not s.teams_workflow_webhook_url:
        return
    await _post_json(s.teams_workflow_webhook_url, {"type": "message", "text": text})


# ── Sync-context hooks (called from inside flows; must never raise) ───────────
def announce_approval(approval: ApprovalQueue) -> None:
    """Fire-and-forget notification for a freshly-raised approval."""
    try:
        s = get_settings()
        if not (s.teams_bot_app_id or s.teams_workflow_webhook_url or s.admin_email_list):
            return
        # Detach the fields we need now — the DB session may close before the
        # task runs, so the coroutine must not touch the ORM row lazily.
        _ = (approval.id, approval.approval_type, approval.severity, approval.detected_reason)
        _spawn(post_approval_card(approval))
    except Exception:
        log.exception("announce_approval failed (notification only — sync unaffected)")


def announce_event(text: str) -> None:
    """Fire-and-forget feed line (BLOCKED/ERROR transactions, breaker trips)."""
    try:
        if not get_settings().teams_workflow_webhook_url:
            return
        _spawn(post_feed_event(text))
    except Exception:
        log.exception("announce_event failed (notification only — sync unaffected)")
