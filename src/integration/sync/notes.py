"""Notes sync — additive, low-risk (Spec §10.5).

- Autotask Ticket Note → the GHL contact linked to the mirrored service card, as
  a GHL note stamped with the Autotask note id. Idempotent via the
  ``processed_events`` ledger (``atnote:<id>``) — never double-posted.
- GHL note on a synced service card → Autotask Ticket Note **only if** a linked
  Autotask Ticket already exists; otherwise HELD (skip + log). A note must never
  cause a ticket to exist.

Notes are never edited or deleted across the boundary — append-only both ways.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..core.idempotency import already_processed, mark_processed
from ..core.logging import get_logger, new_correlation_id
from ..db.enums import (
    CanonicalEntityType,
    Direction,
    Operation,
    System,
    TransactionStatus,
)
from ..db.models import EntityMapping
from .audit import record_transaction

log = get_logger(__name__)

AT_NOTE_STAMP = "[Autotask note {note_id}]"
GHL_NOTE_STAMP = "[GHL note {note_id}]"


def _service_mapping_by_autotask(session: Session, ticket_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.SERVICE_ITEM,
        EntityMapping.autotask_id == ticket_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _service_mapping_by_ghl(session: Session, ghl_card_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.SERVICE_ITEM,
        EntityMapping.ghl_id == ghl_card_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _contact_mapping_by_ghl(session: Session, ghl_contact_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
        EntityMapping.ghl_id == ghl_contact_id,
    )
    return session.execute(stmt).scalar_one_or_none()


async def mirror_ticket_note(
    session: Session,
    *,
    note: dict,               # raw Autotask TicketNote item
    ghl,
    correlation_id: str | None = None,
) -> str:
    """Append an Autotask Ticket Note to the linked GHL contact. Returns action."""
    correlation_id = correlation_id or new_correlation_id()
    note_id = str(note.get("id"))
    ticket_id = str(note.get("ticketID"))
    event_id = f"atnote:{note_id}"

    if already_processed(session, event_id, System.AUTOTASK):
        return "skipped"
    mark_processed(session, event_id, System.AUTOTASK)

    link = _service_mapping_by_autotask(session, ticket_id)
    if link is None or link.ghl_id is None:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.AUTOTASK_TO_GHL,
            operation=Operation.SKIP,
            entity_type="note",
            entity_ref=note_id,
            status=TransactionStatus.SKIPPED,
            summary=f"Ticket {ticket_id} note {note_id} skipped — ticket not mirrored to GHL",
        )
        return "skipped"

    # The GHL card's contact carries the note (GHL notes live on contacts).
    card = await ghl.get_opportunity(link.ghl_id)
    contact_id = card.contact_id if card else None
    if not contact_id:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.AUTOTASK_TO_GHL,
            operation=Operation.SKIP,
            entity_type="note",
            entity_ref=note_id,
            status=TransactionStatus.SKIPPED,
            summary=f"Ticket note {note_id} skipped — mirrored card has no contact",
        )
        return "skipped"

    stamp = AT_NOTE_STAMP.format(note_id=note_id)
    body = f"{stamp} {note.get('title') or ''}\n\n{note.get('description') or ''}".strip()
    await ghl.create_contact_note(contact_id, body)
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.CREATE,
        entity_type="note",
        entity_ref=note_id,
        status=TransactionStatus.SUCCESS,
        summary=f"Autotask ticket note {note_id} appended to GHL contact {contact_id}",
    )
    return "created"


async def push_ghl_note(
    session: Session,
    *,
    ghl_card_id: str,
    note_id: str,
    note_body: str,
    autotask,
    correlation_id: str | None = None,
) -> str:
    """GHL note on a synced service card → Autotask Ticket Note, ONLY if the
    linked Ticket already exists (§10.5). Never creates a ticket. Returns action."""
    correlation_id = correlation_id or new_correlation_id()
    event_id = f"ghlnote:{note_id}"
    if already_processed(session, event_id, System.GHL):
        return "skipped"
    mark_processed(session, event_id, System.GHL)

    # Never echo back a note that originated in Autotask (loop guard).
    if note_body.startswith(AT_NOTE_STAMP.split("{", 1)[0]):
        return "skipped"

    link = _service_mapping_by_ghl(session, ghl_card_id)
    if link is None or link.autotask_id is None:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="note",
            entity_ref=note_id,
            status=TransactionStatus.SKIPPED,
            summary=(
                f"GHL note {note_id} HELD — no linked Autotask Ticket exists and a "
                "note must never create one (§10.5)"
            ),
        )
        return "held"

    stamp = GHL_NOTE_STAMP.format(note_id=note_id)
    result = await autotask.create_ticket_note(
        link.autotask_id, title=stamp, body=note_body
    )
    return _record_push(session, correlation_id, note_id, link.autotask_id, result)


def _record_push(session, correlation_id, note_id, ticket_id, result) -> str:
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CREATE if result.ok else Operation.ERROR,
        entity_type="note",
        entity_ref=result.external_id,
        status=TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
        summary=f"GHL note {note_id} appended to Autotask Ticket {ticket_id}",
    )
    return "created" if result.ok else "error"


async def route_ghl_contact_note(
    session: Session,
    *,
    ghl_contact_id: str,
    note_id: str,
    note_body: str,
    autotask,
    correlation_id: str | None = None,
) -> str:
    """Route a GHL contact note (NoteCreate webhook) to its Autotask ticket.

    GHL notes live on CONTACTS, not on pipeline cards, so the webhook only tells
    us the contact. Resolution: mapped contact → that contact's Autotask tickets →
    intersect with the SERVICE_ITEM mappings (only mirrored tickets participate).
    The newest mirrored ticket wins when several are linked — logged in the
    transaction summary. A note must never create a ticket (§10.5)."""
    correlation_id = correlation_id or new_correlation_id()
    event_id = f"ghlnote:{note_id}"
    if already_processed(session, event_id, System.GHL):
        return "skipped"
    mark_processed(session, event_id, System.GHL)

    # Never echo back a note that originated in Autotask (loop guard).
    if note_body.startswith(AT_NOTE_STAMP.split("{", 1)[0]):
        return "skipped"

    def _held(reason: str) -> str:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="note",
            entity_ref=note_id,
            status=TransactionStatus.SKIPPED,
            summary=f"GHL note {note_id} HELD — {reason} (§10.5)",
        )
        return "held"

    contact_link = _contact_mapping_by_ghl(session, ghl_contact_id)
    if contact_link is None or contact_link.autotask_id is None:
        return _held(f"GHL contact {ghl_contact_id} has no linked Autotask Contact")

    tickets = await autotask.find_tickets(contact_id=contact_link.autotask_id)
    env = get_settings().environment
    mirrored_ids = set(
        session.execute(
            select(EntityMapping.autotask_id).where(
                EntityMapping.environment == env,
                EntityMapping.canonical_entity_type == CanonicalEntityType.SERVICE_ITEM,
                EntityMapping.autotask_id.in_([t.source_id for t in tickets] or [""]),
            )
        )
        .scalars()
        .all()
    )
    def _newest(tid: str):
        # Autotask ids are numeric in practice; fall back to lexical for fakes.
        return (1, int(tid), "") if tid.isdigit() else (0, 0, tid)

    candidates = sorted((tid for tid in mirrored_ids if tid), key=_newest, reverse=True)
    if not candidates:
        return _held(
            f"contact {contact_link.autotask_id} has no mirrored ticket and a note "
            "must never create one"
        )

    ticket_id = candidates[0]
    stamp = GHL_NOTE_STAMP.format(note_id=note_id)
    result = await autotask.create_ticket_note(ticket_id, title=stamp, body=note_body)
    picked = f" (newest of {len(candidates)} mirrored tickets)" if len(candidates) > 1 else ""
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CREATE if result.ok else Operation.ERROR,
        entity_type="note",
        entity_ref=result.external_id,
        status=TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
        summary=f"GHL note {note_id} appended to Autotask Ticket {ticket_id}{picked}",
    )
    return "created" if result.ok else "error"
