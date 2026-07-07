"""Autotask -> GHL contact push (Spec §5.1, §9.2).

This direction is **free-flowing**: Autotask is authoritative and may overwrite
GHL. No gating, no approvals — but still idempotent and logged. Used by the
Autotask polling job (Spec §4) to keep the engagement surface informed by the
record.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalContact
from ..config.settings import get_settings
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import CanonicalEntityType, Direction, Operation, System, TransactionStatus
from ..db.models import EntityMapping
from .audit import record_transaction
from .contacts import _link_mapping  # reuse mapping writer

log = get_logger(__name__)


def _mapping_by_autotask(session: Session, autotask_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
        EntityMapping.autotask_id == autotask_id,
    )
    return session.execute(stmt).scalar_one_or_none()


async def push_autotask_contact(
    session: Session,
    *,
    at_contact: CanonicalContact,
    ghl,  # GHLConnector
    correlation_id: str | None = None,
) -> str:
    """Mirror an Autotask contact into GHL (overwrite). Returns the action taken."""
    correlation_id = correlation_id or new_correlation_id()
    autotask_id = at_contact.source_id
    link = _mapping_by_autotask(session, autotask_id) if autotask_id else None

    if link and link.ghl_id:
        # Overwrite the GHL contact with authoritative Autotask values (Spec §5.1).
        changes = {
            "firstName": at_contact.first_name,
            "lastName": at_contact.last_name,
            "phone": at_contact.phone,
            "city": at_contact.city,
            "state": at_contact.state,
            "postalCode": at_contact.postal_code,
            "country": at_contact.country,
            "companyName": at_contact.company_name,
        }
        await ghl.update_contact(link.ghl_id, changes)
        link.last_synced_at = utcnow()
        action, ref = "updated", link.ghl_id
    else:
        result = await ghl.create_contact(at_contact)
        _link_mapping(session, ghl_id=result.external_id, autotask_id=autotask_id)
        action, ref = "created", result.external_id

    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.UPDATE if action == "updated" else Operation.CREATE,
        entity_type="contact",
        entity_ref=ref,
        status=TransactionStatus.SUCCESS,
        summary=f"Autotask -> GHL contact {action} (authoritative overwrite)",
    )
    return action
