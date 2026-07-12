"""Shared approval-decision service (Spec §11.1).

One entry point for every surface that can decide an approval — the HTTP
endpoint (portal / Teams card callback URL) and the Teams bot's card submit —
so the routing rules (which flow applies the decision) live in exactly one
place.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..core.logging import get_logger
from ..db.enums import ApprovalType
from . import contacts as contacts_flow
from . import deals as deals_flow
from .approvals import get_approval

log = get_logger(__name__)


@dataclass(slots=True)
class DecisionResult:
    action: str
    correlation_id: str


async def apply_approval_decision(
    session: Session,
    *,
    approval_id: int,
    decision: str,               # approve | reject | override
    decided_by: str,
    autotask,
    ghl,
    chosen_id: str | None = None,
) -> DecisionResult:
    """Route the decision by approval family and apply it.

    Flow-2 / Stage-C approval types apply via the deals handler; everything else
    is the Flow-1 Contacts handler (same routing the HTTP endpoint always had)."""
    approve = decision in ("approve", "override")
    row = get_approval(session, approval_id)
    is_deal = row is not None and ApprovalType(row.approval_type) in deals_flow.DEAL_APPROVAL_TYPES
    if is_deal:
        outcome = await deals_flow.apply_deal_decision(
            session,
            approval_id=approval_id,
            approve=approve,
            decided_by=decided_by,
            autotask=autotask,
            ghl=ghl,
            chosen_account_id=chosen_id,
        )
    else:
        outcome = await contacts_flow.apply_decision(
            session,
            approval_id=approval_id,
            approve=approve,
            decided_by=decided_by,
            autotask=autotask,
            chosen_account_id=chosen_id,
            chosen_autotask_contact_id=chosen_id,
        )
    return DecisionResult(action=outcome.action, correlation_id=outcome.correlation_id)
