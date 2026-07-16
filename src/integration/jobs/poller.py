"""Autotask polling + reconciliation driver (Spec §4, §11).

Autotask has no comprehensive webhooks, so the AT→GHL direction is PULLED:
each sweep advances a per-entity ``sync_cursor`` and mirrors what changed —
contacts (Flow 1), opportunities → Sales pipeline and tickets → Service pipeline
(Flow 2a/2b), plus the classification push (Stage E) and the reconciliation
sweep on the slower interval.

Locally this runs as an in-process background task inside the API (opt-in via
``ENABLE_POLLER=true`` — the GHL OAuth token lives in that process). In
production the same ``poll_once`` / ``reconcile_once`` entry points move onto
Timer-triggered Azure Functions (Spec §12.2).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..config.stages import load_stage_map
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import CanonicalEntityType, System
from ..db.models import SyncCursor
from ..db.session import session_scope
from ..sync.autotask_to_ghl import push_autotask_contact
from ..sync.classification import sync_classifications
from ..sync.companies import mirror_autotask_account
from ..sync.criteria import AccountFilter
from ..sync.mirrors import mirror_autotask_opportunity, mirror_autotask_ticket
from ..sync.notes import mirror_ticket_note
from ..sync.reconciliation import expire_stale_approvals, reconcile_contacts

log = get_logger(__name__)

# Companies sweep FIRST: the contact mirror attaches businessId from the COMPANY
# mapping, so Accounts must be mirrored before their contacts in the same cycle.
_POLL_ENTITIES = (
    CanonicalEntityType.COMPANY,
    CanonicalEntityType.CONTACT,
    CanonicalEntityType.DEAL,
    CanonicalEntityType.SERVICE_ITEM,
)

# TicketNotes get their own cursor lane — they are not a canonical entity type.
_NOTE_CURSOR_KEY = "ticket_note"


def _get_cursor(session: Session, entity_key: str) -> SyncCursor:
    env = get_settings().environment
    row = session.execute(
        select(SyncCursor).where(
            SyncCursor.environment == env,
            SyncCursor.source_system == System.AUTOTASK,
            SyncCursor.entity_type == entity_key,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SyncCursor(
            environment=env,
            source_system=System.AUTOTASK,
            entity_type=entity_key,
            cursor=None,
        )
        session.add(row)
        session.flush()
    return row


async def poll_once(*, autotask, ghl) -> dict:
    """One polling sweep across all mirrored entity types. Returns a summary."""
    stage_map = load_stage_map()
    summary: dict[str, int] = {}
    for entity_type in _POLL_ENTITIES:
        processed = 0
        with session_scope() as session:
            cursor_row = _get_cursor(session, entity_type.value)
            # Customer sync criteria (admin UI) gate the outbound mirror.
            account_filter = AccountFilter(session, autotask)
            changes = await autotask.fetch_changes(entity_type, cursor=cursor_row.cursor)
            for entity in changes.entities:
                try:
                    if entity_type is CanonicalEntityType.COMPANY:
                        if not await account_filter.allows_company(entity):
                            continue  # excluded or outside the sync audience
                        await mirror_autotask_account(session, company=entity, ghl=ghl)
                    elif entity_type is CanonicalEntityType.CONTACT:
                        if not entity.extra.get("is_active", True):
                            continue  # inactive CONTACT — never mirrored outbound
                        if not await account_filter.allows_contact(entity):
                            continue  # outside the configured sync audience
                        await push_autotask_contact(
                            session, at_contact=entity, ghl=ghl, autotask=autotask
                        )
                    elif entity_type is CanonicalEntityType.DEAL:
                        await mirror_autotask_opportunity(
                            session, deal=entity, ghl=ghl, stage_map=stage_map
                        )
                    else:
                        await mirror_autotask_ticket(
                            session, ticket=entity, ghl=ghl, stage_map=stage_map
                        )
                    processed += 1
                except Exception:  # keep the sweep going; the item hits reconciliation later
                    # ⚠️ A failed flush poisons the Postgres transaction — roll back
                    # or every subsequent item in the page fails too.
                    session.rollback()
                    log.exception(
                        "Poller: %s %s failed — continuing sweep",
                        entity_type.value,
                        getattr(entity, "source_id", "?"),
                    )
            cursor_row.cursor = changes.cursor
            cursor_row.updated_at = utcnow()
        summary[entity_type.value] = processed
    summary["ticket_note"] = await _sweep_ticket_notes(autotask=autotask, ghl=ghl)
    log.info("Poll sweep complete: %s", summary)
    return summary


async def _sweep_ticket_notes(*, autotask, ghl) -> int:
    """Mirror new Autotask TicketNotes to the linked GHL contacts (§10.5).

    Each note is committed in its OWN transaction, together with the cursor
    advance, so a durable idempotency mark (``processed_events``) lands before the
    next note is touched. That matters because ``mirror_ticket_note`` posts to GHL
    *before* the commit: batching the whole sweep in one transaction meant a
    mid-sweep failure rolled back marks for notes already posted, re-posting them
    next cycle. Notes are walked in ascending id order and the cursor stops at the
    first hard failure (rather than skipping past it) — there is no reconciliation
    lane for notes, so a transiently-failing note must be retried, not lost."""
    with session_scope() as session:
        cursor = _get_cursor(session, _NOTE_CURSOR_KEY).cursor
    notes, _ = await autotask.fetch_ticket_note_changes(cursor=cursor)

    processed = 0
    for note in sorted(notes, key=lambda n: int(n["id"])):
        try:
            with session_scope() as session:
                action = await mirror_ticket_note(session, note=note, ghl=ghl)
                # Advance the cursor in the SAME transaction as the note's mark.
                cursor_row = _get_cursor(session, _NOTE_CURSOR_KEY)
                cursor_row.cursor = f"id:{int(note['id'])}"
                cursor_row.updated_at = utcnow()
            if action == "created":
                processed += 1
        except Exception:
            # Halt the sweep WITHOUT advancing past this note so it retries next
            # cycle. Its own transaction already rolled back (no partial mark).
            log.exception(
                "Poller: ticket note %s failed — halting note sweep so it retries "
                "next cycle (notes have no reconciliation lane)", note.get("id")
            )
            break
    return processed


async def reconcile_once(*, autotask, ghl) -> dict:
    """Slow-interval sweep: expiries, contact drift, classification push."""
    with session_scope() as session:
        expired = expire_stale_approvals(session)
        drift = await reconcile_contacts(session, autotask=autotask, ghl=ghl)
        classifications = await sync_classifications(session, autotask=autotask, ghl=ghl)
    out = {"approvals_expired": expired, **drift, **classifications}
    log.info("Reconciliation sweep complete: %s", out)
    return out


async def run_poller(*, autotask_factory, ghl_factory, stop: asyncio.Event | None = None) -> None:
    """The in-process loop (ENABLE_POLLER=true). Factories are called each cycle
    so credential/token refreshes are picked up; failures skip the cycle rather
    than kill the loop."""
    settings = get_settings()
    stop = stop or asyncio.Event()
    last_reconcile = 0.0
    loop = asyncio.get_event_loop()
    log.warning(
        "Poller ENABLED: Autotask sweep every %ss, reconciliation every %ss",
        settings.autotask_poll_interval_seconds,
        settings.reconciliation_interval_seconds,
    )
    while not stop.is_set():
        try:
            autotask = await autotask_factory()
            ghl = ghl_factory()
            await ghl.authenticate()  # raises if OAuth hasn't been completed yet
            # Keep the grant fresh: rotate ahead of access-token expiry. The
            # rotation is persisted via on_token_update, so this also keeps the
            # stored refresh token current across long idle periods.
            await ghl.refresh()
            await poll_once(autotask=autotask, ghl=ghl)
            now = loop.time()
            if now - last_reconcile >= settings.reconciliation_interval_seconds:
                await reconcile_once(autotask=autotask, ghl=ghl)
                last_reconcile = now
        except Exception as exc:
            log.warning("Poller cycle skipped: %s", exc)
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.autotask_poll_interval_seconds
            )
        except TimeoutError:
            pass
