"""Approval queue service (Spec §5, §11).

Every gated/ambiguous change is parked here for a human decision instead of being
written to Autotask. The Teams bot renders these as Adaptive Cards and calls
``decide`` on Approve/Reject. Expiry defaults to **protect Autotask** (no change).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import (
    ApprovalStatus,
    ApprovalType,
    CanonicalEntityType,
    Severity,
    System,
)
from ..db.models import ApprovalQueue
from ..teams.notify import announce_approval

log = get_logger(__name__)


@dataclass(slots=True)
class ApprovalRequest:
    approval_type: ApprovalType
    severity: Severity
    canonical_entity_type: CanonicalEntityType
    source_system: System
    target_system: System
    proposed_change: dict
    detected_reason: str
    correlation_id: str
    autotask_id: str | None = None
    ghl_id: str | None = None


def raise_approval(session: Session, req: ApprovalRequest) -> ApprovalQueue:
    """Create a pending approval. This is the ONLY path a gated change takes —
    it is never written to Autotask directly (Spec §5.2)."""
    settings = get_settings()
    row = ApprovalQueue(
        status=ApprovalStatus.PENDING,
        severity=req.severity,
        approval_type=req.approval_type,
        canonical_entity_type=req.canonical_entity_type,
        source_system=req.source_system,
        target_system=req.target_system,
        autotask_id=req.autotask_id,
        ghl_id=req.ghl_id,
        proposed_change=req.proposed_change,
        detected_reason=req.detected_reason,
        environment=settings.environment,
        correlation_id=req.correlation_id,
        expires_at=utcnow() + timedelta(hours=settings.approval_expiry_hours),
    )
    session.add(row)
    session.flush()
    log.info(
        "Approval raised #%s type=%s severity=%s reason=%s",
        row.id,
        req.approval_type,
        req.severity,
        req.detected_reason,
    )
    # Teams card + (HIGH) admin email — deferred to after this transaction
    # commits, so a later rollback can't leave a card for a phantom approval.
    announce_approval(session, row)
    return row


def list_pending(session: Session, limit: int = 50) -> list[ApprovalQueue]:
    env = get_settings().environment
    stmt = (
        select(ApprovalQueue)
        .where(
            ApprovalQueue.environment == env,
            ApprovalQueue.status == ApprovalStatus.PENDING,
        )
        .order_by(ApprovalQueue.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def get_approval(session: Session, approval_id: int) -> ApprovalQueue | None:
    return session.get(ApprovalQueue, approval_id)


def decide(
    session: Session,
    approval_id: int,
    *,
    approve: bool,
    decided_by: str,
) -> ApprovalQueue | None:
    """Record a human decision. The CALLER (the Contacts orchestrator) performs the
    actual Autotask write on approval, so this stays free of connector I/O."""
    row = session.get(ApprovalQueue, approval_id)
    if row is None or row.status != ApprovalStatus.PENDING:
        return row
    row.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    row.decided_at = utcnow()
    row.decided_by = decided_by
    return row


def expire_stale(session: Session) -> int:
    """Expire overdue approvals. Default action is to PROTECT Autotask: no change is
    applied; the item is marked expired and admins are re-notified (Spec §11.1)."""
    env = get_settings().environment
    now = utcnow()
    stmt = select(ApprovalQueue).where(
        ApprovalQueue.environment == env,
        ApprovalQueue.status == ApprovalStatus.PENDING,
        ApprovalQueue.expires_at.is_not(None),
        ApprovalQueue.expires_at < now,
    )
    rows = list(session.execute(stmt).scalars().all())
    for row in rows:
        row.status = ApprovalStatus.EXPIRED
        row.decided_at = now
        row.decided_by = "system:expiry"
    if rows:
        log.warning("Expired %d stale approvals (Autotask left unchanged)", len(rows))
    return len(rows)
