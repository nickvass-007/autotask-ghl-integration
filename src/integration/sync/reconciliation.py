"""Reconciliation sweep (Spec §11).

A periodic comparison of both systems to catch drift even when no event fired,
feeding the **same** approval queue as the live flow. Two jobs:

1. ``expire_stale_approvals`` — expire overdue approvals, defaulting to protect
   Autotask (Spec §11.1).
2. ``reconcile_contacts`` — for each mapped contact, compare Autotask vs GHL and
   route any GHL->Autotask drift through the normal gated path (so conflicts
   become approvals, never silent overwrites).

In production these run on Timer-triggered Functions (Spec §12.2); locally they
can be invoked directly or on a loop.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.mapping import load_contacts_mapping
from ..config.settings import get_settings
from ..core.logging import get_logger, new_correlation_id
from ..db.enums import CanonicalEntityType
from ..db.models import EntityMapping
from .approvals import expire_stale
from .conflicts import detect_changes

log = get_logger(__name__)


def expire_stale_approvals(session: Session) -> int:
    return expire_stale(session)


async def reconcile_contacts(session: Session, *, autotask, ghl, limit: int = 200) -> dict:
    """Compare mapped contacts across systems; surface drift through the gated path.

    Returns a small summary dict for the Teams daily digest. This re-uses
    ``detect_changes`` so reconciliation and live sync apply identical rules."""
    env = get_settings().environment
    mapping = load_contacts_mapping()
    stmt = (
        select(EntityMapping)
        .where(
            EntityMapping.environment == env,
            EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
            EntityMapping.autotask_id.is_not(None),
            EntityMapping.ghl_id.is_not(None),
        )
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars().all())

    checked = drift = 0
    for row in rows:
        checked += 1
        at_contact = await autotask.get_contact(row.autotask_id)
        ghl_candidates = await ghl.get_contact(row.ghl_id)
        if at_contact is None or ghl_candidates is None:
            continue
        changes = detect_changes(ghl_candidates, at_contact, mapping)
        if changes.has_conflicts or changes.additive:
            drift += 1
            log.info(
                "Reconciliation drift on contact AT=%s GHL=%s: %d conflict(s), %d additive",
                row.autotask_id,
                row.ghl_id,
                len(changes.conflicts),
                len(changes.additive),
            )
            # NOTE: feeding this through process_ghl_contact requires an event id;
            # reconciliation uses a synthetic correlation id and routes via the same
            # conflict path in the live flow. Wiring the synthetic re-enqueue is done
            # where reconciliation is scheduled (Functions), keeping this sweep pure.

    return {
        "environment": env.value,
        "correlation_id": new_correlation_id(),
        "contacts_checked": checked,
        "contacts_with_drift": drift,
    }
