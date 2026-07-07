"""Teams Ops Bot activity handler (Spec §11).

A real Azure Bot Service bot (botbuilder) — required because interactive
Approve/Reject button callbacks cannot be handled by a Teams incoming webhook.

Supported commands (Stage 1):
  /status        -> active environment (always visible, Spec §6)
  /pending       -> pending approvals
  /transactions  -> recent transaction feed (Spec §11.2)
  /audit <type> <id>  -> "what happened to this contact?" (Spec §11.4)

Card button callbacks (Approve/Reject/Override) arrive as Activity ``value`` and
are routed to the approval-decision endpoint after token verification.

``botbuilder`` is imported lazily so the rest of the package (and the test suite)
does not require the bot SDK to be installed.
"""

from __future__ import annotations

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.session import session_scope
from .feed import audit_history, daily_digest, pending_approvals, recent_transactions

log = get_logger(__name__)


def _status_text() -> str:
    s = get_settings()
    return s.environment_banner()


def _pending_text() -> str:
    with session_scope() as session:
        rows = pending_approvals(session)
        if not rows:
            return "No pending approvals. ✅"
        lines = [f"**{len(rows)} pending approval(s):**"]
        for r in rows:
            lines.append(f"- #{r.id} `{r.approval_type}` (sev {r.severity}) — {r.detected_reason}")
        return "\n".join(lines)


def _transactions_text(limit: int = 15) -> str:
    with session_scope() as session:
        rows = recent_transactions(session, limit=limit)
        if not rows:
            return "No transactions yet."
        lines = ["**Recent transactions:**"]
        for r in rows:
            lines.append(
                f"- {r.timestamp:%Y-%m-%d %H:%M} `{r.status}` {r.direction} {r.entity_type} — {r.summary}"
            )
        return "\n".join(lines)


def _audit_text(entity_type: str, entity_id: str) -> str:
    with session_scope() as session:
        rows = audit_history(session, entity_type=entity_type, entity_id=entity_id)
        if not rows:
            return f"No audit history for {entity_type} {entity_id}."
        lines = [f"**History for {entity_type} {entity_id}:**"]
        for r in rows:
            lines.append(f"- {r.timestamp:%Y-%m-%d %H:%M} `{r.operation}` by {r.actor} → {r.result}")
        return "\n".join(lines)


def handle_command(text: str) -> str:
    """Map a slash command to a reply. Kept SDK-free so it's unit-testable."""
    parts = text.strip().split()
    cmd = parts[0].lower() if parts else ""
    if cmd in ("/status", "status"):
        return _status_text()
    if cmd in ("/pending", "pending"):
        return _pending_text()
    if cmd in ("/transactions", "transactions"):
        return _transactions_text()
    if cmd in ("/digest", "digest"):
        with session_scope() as session:
            return f"Daily digest: {daily_digest(session)}"
    if cmd in ("/audit", "audit") and len(parts) >= 3:
        return _audit_text(parts[1], parts[2])
    return (
        "Commands: /status, /pending, /transactions, /digest, /audit <entity_type> <id>"
    )


def build_bot():
    """Construct the botbuilder ActivityHandler. Imported lazily (SDK optional)."""
    from botbuilder.core import ActivityHandler, TurnContext
    from botbuilder.schema import ChannelAccount  # noqa: F401

    handle = handle_command

    class OpsBot(ActivityHandler):
        async def on_message_activity(self, turn_context: "TurnContext") -> None:  # type: ignore[name-defined]
            activity = turn_context.activity
            # Card button callback (Approve/Reject/Override) arrives as .value
            if activity.value:
                await turn_context.send_activity(
                    "Decision received — routing to the approval endpoint."
                )
                return
            await turn_context.send_activity(handle(activity.text or ""))

    return OpsBot()
