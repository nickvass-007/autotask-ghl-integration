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


# ── Conversation reference store (for proactive approval cards) ───────────────
# The bot can only post proactively into a channel it has already seen. We store
# the latest conversation reference in the portal_settings KV table on every
# inbound activity, so "add the bot to the ops channel and say hi" is the whole
# setup ritual.
_CONV_REF_KEY = "teams_conversation_ref"


def store_conversation_reference(reference: dict) -> None:
    import json

    from ..db.models import PortalSetting

    env = get_settings().environment
    with session_scope() as session:
        from sqlalchemy import select

        row = session.execute(
            select(PortalSetting).where(
                PortalSetting.environment == env, PortalSetting.key == _CONV_REF_KEY
            )
        ).scalar_one_or_none()
        if row is None:
            row = PortalSetting(environment=env, key=_CONV_REF_KEY, value="{}")
            session.add(row)
        row.value = json.dumps(reference)


def load_conversation_reference() -> dict | None:
    import json

    from sqlalchemy import select

    from ..db.models import PortalSetting

    env = get_settings().environment
    with session_scope() as session:
        row = session.execute(
            select(PortalSetting).where(
                PortalSetting.environment == env, PortalSetting.key == _CONV_REF_KEY
            )
        ).scalar_one_or_none()
        return json.loads(row.value) if row and row.value else None


# ── botbuilder wiring (lazy: the SDK is an optional extra, `pip install .[teams]`) ──
def _build_adapter():
    from botbuilder.integration.aiohttp import (
        CloudAdapter,
        ConfigurationBotFrameworkAuthentication,
    )

    s = get_settings()

    class _Config:
        APP_ID = s.teams_bot_app_id
        APP_PASSWORD = s.teams_bot_app_password
        APP_TYPE = "SingleTenant" if s.teams_bot_tenant_id else "MultiTenant"
        APP_TENANTID = s.teams_bot_tenant_id or None

    return CloudAdapter(ConfigurationBotFrameworkAuthentication(_Config()))


async def _apply_card_decision(value: dict) -> str:
    """Handle an Approve/Reject/Override card submit. The token in the card data
    is verified against the shared secret so a spoofed submit cannot approve a
    change (Spec §11.1)."""
    s = get_settings()
    token = str(value.get("token", ""))
    if not s.approval_callback_secret or token != s.approval_callback_secret:
        return "⚠️ Unverified decision — rejected (bad or missing token)."
    approval_id = int(value.get("approval_id", 0))
    decision = str(value.get("decision", ""))
    if decision not in ("approve", "reject", "override"):
        return f"Unknown decision {decision!r}."

    from ..api.deps import get_autotask, get_ghl  # lazy: avoids an import cycle
    from ..sync.decisions import apply_approval_decision

    autotask = await get_autotask()
    with session_scope() as session:
        result = await apply_approval_decision(
            session,
            approval_id=approval_id,
            decision=decision,
            decided_by="teams-card",
            autotask=autotask,
            ghl=get_ghl(),
            chosen_id=value.get("chosen_id") or None,
        )
    return f"Approval #{approval_id}: **{result.action}** (correlation {result.correlation_id})"


def build_bot():
    """Construct the botbuilder ActivityHandler. Imported lazily (SDK optional)."""
    from botbuilder.core import ActivityHandler, TurnContext

    handle = handle_command

    class OpsBot(ActivityHandler):
        async def on_turn(self, turn_context: "TurnContext") -> None:  # type: ignore[name-defined]
            # Remember where we are so proactive approval cards have a target.
            try:
                ref = TurnContext.get_conversation_reference(turn_context.activity)
                store_conversation_reference(ref.serialize())
            except Exception:
                log.exception("Could not store conversation reference")
            await super().on_turn(turn_context)

        async def on_message_activity(self, turn_context: "TurnContext") -> None:  # type: ignore[name-defined]
            activity = turn_context.activity
            # Card button callback (Approve/Reject/Override) arrives as .value
            if activity.value:
                reply = await _apply_card_decision(dict(activity.value))
                await turn_context.send_activity(reply)
                return
            # Teams prefixes messages with the bot @mention — strip it.
            text = TurnContext.remove_recipient_mention(activity) or activity.text or ""
            await turn_context.send_activity(handle(text))

    return OpsBot()


async def process_bot_activity(body: dict, auth_header: str):
    """CloudAdapter entry point for POST /api/messages when the bot is configured.
    Returns the botbuilder InvokeResponse (or None)."""
    from botbuilder.schema import Activity

    adapter = _build_adapter()
    bot = build_bot()
    activity = Activity().deserialize(body)
    return await adapter.process_activity(auth_header, activity, bot.on_turn)


async def send_proactive_card(card: dict) -> bool:
    """Post an Adaptive Card into the stored conversation (the ops channel).
    Returns False when the bot has never been spoken to / SDK missing."""
    reference = load_conversation_reference()
    if not reference:
        log.info("No stored Teams conversation — say something to the bot first")
        return False

    from botbuilder.core import CardFactory, MessageFactory
    from botbuilder.schema import ConversationReference

    adapter = _build_adapter()
    conv_ref = ConversationReference().deserialize(reference)
    message = MessageFactory.attachment(CardFactory.adaptive_card(card))

    async def _send(turn_context) -> None:
        await turn_context.send_activity(message)

    s = get_settings()
    try:
        await adapter.continue_conversation(conv_ref, _send, bot_app_id=s.teams_bot_app_id)
    except TypeError:
        # botbuilder signature drift across 4.x — older positional form.
        await adapter.continue_conversation(s.teams_bot_app_id, conv_ref, _send)
    return True
