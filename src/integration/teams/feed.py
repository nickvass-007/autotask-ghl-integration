"""Transaction feed + audit lookup queries for the Teams bot (Spec §11.2, §11.4).

Pure DB reads (no bot SDK dependency) so they're easy to test and reuse from the
admin UI. Backs the ``/transactions``, ``/pending``, daily-digest, and "what
happened to this contact?" commands.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..db.base import utcnow
from ..db.enums import ApprovalStatus
from ..db.models import ApprovalQueue, AuditLog, TransactionLog


def recent_transactions(session: Session, limit: int = 20) -> list[TransactionLog]:
    env = get_settings().environment
    stmt = (
        select(TransactionLog)
        .where(TransactionLog.environment == env)
        .order_by(TransactionLog.timestamp.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def pending_approvals(session: Session, limit: int = 50) -> list[ApprovalQueue]:
    env = get_settings().environment
    stmt = (
        select(ApprovalQueue)
        .where(ApprovalQueue.environment == env, ApprovalQueue.status == ApprovalStatus.PENDING)
        .order_by(ApprovalQueue.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def daily_digest(session: Session) -> dict:
    """Counts over the last 24h for the daily digest (Spec §11.2)."""
    env = get_settings().environment
    since = utcnow() - timedelta(hours=24)
    rows = (
        session.execute(
            select(TransactionLog.status).where(
                TransactionLog.environment == env, TransactionLog.timestamp >= since
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for status in rows:
        key = getattr(status, "value", str(status))
        counts[key] = counts.get(key, 0) + 1
    pending = len(pending_approvals(session))
    return {"environment": env.value, "window_hours": 24, "by_status": counts, "pending_approvals": pending}


def audit_history(session: Session, *, entity_type: str, entity_id: str, limit: int = 50) -> list[AuditLog]:
    """'What happened to this contact/company?' (Spec §11.4)."""
    env = get_settings().environment
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.environment == env,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())
