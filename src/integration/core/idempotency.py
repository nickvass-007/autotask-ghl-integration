"""Idempotency ledger (Spec §12.1).

Exactly-once handling: before processing an inbound event we record it in
``processed_events`` (unique on event_id + source_system). A re-delivered event is
detected and skipped. Combined with ``entity_mapping`` (which prevents duplicate
creation), this makes the pipeline safe to retry.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..db.enums import System
from ..db.models import ProcessedEvent


def already_processed(session: Session, event_id: str, source_system: System) -> bool:
    stmt = select(ProcessedEvent).where(
        ProcessedEvent.event_id == event_id,
        ProcessedEvent.source_system == source_system,
    )
    return session.execute(stmt).first() is not None


def mark_processed(
    session: Session,
    event_id: str,
    source_system: System,
    entity_version: str | None = None,
) -> None:
    session.add(
        ProcessedEvent(
            event_id=event_id,
            source_system=source_system,
            entity_version=entity_version,
            environment=get_settings().environment,
        )
    )
