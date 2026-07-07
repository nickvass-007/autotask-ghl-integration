"""Audit + transaction-feed writers (Spec §5.4, §11).

Two append-only records back every operation:

- ``audit_log`` — the immutable **before-state** captured *before* any Autotask
  write, so a human can manually revert (Spec §5.4). Never updated or deleted.
- ``transaction_log`` — the queryable feed of EVERY sync operation surfaced by the
  Teams bot (Spec §11.2).

``record_before_state`` MUST be called before a write reaches Autotask.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass

from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalContact
from ..config.settings import get_settings
from ..db.enums import Actor, Direction, Operation, System, TransactionStatus
from ..db.models import AuditLog, TransactionLog


def canonical_to_dict(entity: object | None) -> dict | None:
    """Serialise a canonical entity to a plain JSON-safe dict for audit storage."""
    if entity is None:
        return None
    if is_dataclass(entity) and not isinstance(entity, type):
        data = {}
        for f in fields(entity):
            value = getattr(entity, f.name)
            # enums -> their value; everything else is already JSON-safe
            data[f.name] = getattr(value, "value", value)
        return data
    if isinstance(entity, dict):
        return entity
    return {"repr": repr(entity)}


def record_before_state(
    session: Session,
    *,
    correlation_id: str,
    operation: Operation,
    target_system: System,
    entity_type: str,
    entity_id: str | None,
    before: object | None,
    after: object | None,
    actor: Actor = Actor.SYSTEM,
    result: str = "pending",
) -> AuditLog:
    """Append an immutable audit row. Call BEFORE the Autotask write (Spec §5.4)."""
    row = AuditLog(
        environment=get_settings().environment,
        correlation_id=correlation_id,
        operation=operation,
        target_system=target_system,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=canonical_to_dict(before),
        after_state=canonical_to_dict(after),
        actor=actor,
        result=result,
    )
    session.add(row)
    session.flush()
    return row


def record_transaction(
    session: Session,
    *,
    correlation_id: str,
    direction: Direction,
    operation: Operation,
    entity_type: str,
    entity_ref: str | None,
    status: TransactionStatus,
    summary: str,
    detail: dict | None = None,
) -> TransactionLog:
    """Append to the queryable transaction feed (Spec §11.2)."""
    row = TransactionLog(
        environment=get_settings().environment,
        correlation_id=correlation_id,
        direction=direction,
        operation=operation,
        entity_type=entity_type,
        entity_ref=entity_ref,
        status=status,
        summary=summary,
        detail=detail,
    )
    session.add(row)
    session.flush()
    return row


# Convenience for the most common case in the Contacts flow.
def contact_dict(contact: CanonicalContact | None) -> dict | None:
    return canonical_to_dict(contact)
