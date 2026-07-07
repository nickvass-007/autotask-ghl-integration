"""Flow 2a/2b — Autotask → GHL mirrors (Spec §10.2, §10.6).

This direction is FREE-FLOWING: Autotask is authoritative and overwrites GHL.

- ``mirror_autotask_opportunity`` — Autotask Opportunity → GHL card in the
  **Sales pipeline** (amount, stage, close date all AT-authoritative).
- ``mirror_autotask_ticket`` — Autotask Ticket → GHL card in the **Service
  pipeline**, a read-only visibility mirror for the whole of v1.

An Autotask stage/status with no mapping raises an ``unmapped_stage`` approval —
the engine never guesses (Spec §10.3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalDeal, CanonicalServiceItem
from ..config.settings import get_settings
from ..config.stages import StageMap
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import (
    ApprovalType,
    CanonicalEntityType,
    Direction,
    Operation,
    Severity,
    System,
    TransactionStatus,
)
from ..db.models import EntityMapping
from .approvals import ApprovalRequest, raise_approval
from .audit import canonical_to_dict, record_transaction

log = get_logger(__name__)


def _find_mapping(
    session: Session, entity_type: CanonicalEntityType, *, autotask_id: str
) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == entity_type,
        EntityMapping.autotask_id == autotask_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _link(
    session: Session,
    entity_type: CanonicalEntityType,
    *,
    autotask_id: str | None,
    ghl_id: str | None,
    autotask_entity_type: str,
    ghl_entity_type: str = "opportunity",
) -> EntityMapping:
    row = EntityMapping(
        canonical_entity_type=entity_type,
        autotask_entity_type=autotask_entity_type,
        autotask_id=autotask_id,
        ghl_entity_type=ghl_entity_type,
        ghl_id=ghl_id,
        environment=get_settings().environment,
        last_synced_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _contact_ghl_id(session: Session, autotask_contact_id: str | None) -> str | None:
    """Resolve an Autotask contact to its GHL contact via the Flow-1 identity spine."""
    if not autotask_contact_id:
        return None
    row = _find_mapping(session, CanonicalEntityType.CONTACT, autotask_id=autotask_contact_id)
    return row.ghl_id if row else None


def _raise_unmapped(
    session: Session,
    *,
    correlation_id: str,
    entity_label: str,
    source_id: str | None,
    value: str | None,
    entity_type: CanonicalEntityType,
) -> int:
    approval = raise_approval(
        session,
        ApprovalRequest(
            approval_type=ApprovalType.UNMAPPED_STAGE,
            severity=Severity.MED,
            canonical_entity_type=entity_type,
            source_system=System.AUTOTASK,
            target_system=System.GHL,
            autotask_id=source_id,
            proposed_change={"autotask_value": value, "entity": entity_label},
            detected_reason=(
                f"Autotask {entity_label} value {value!r} has no stage-map entry — "
                "add it to stage_mapping.yaml (never guessed, Spec §10.3)."
            ),
            correlation_id=correlation_id,
        ),
    )
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.CONFLICT,
        entity_type=entity_label,
        entity_ref=source_id,
        status=TransactionStatus.CONFLICT,
        summary=f"Unmapped Autotask {entity_label} value {value!r} -> approval #{approval.id}",
    )
    return approval.id


async def mirror_autotask_opportunity(
    session: Session,
    *,
    deal: CanonicalDeal,
    ghl,
    stage_map: StageMap,
    correlation_id: str | None = None,
) -> str:
    """Mirror an Autotask Opportunity into the GHL Sales pipeline (overwrite)."""
    correlation_id = correlation_id or new_correlation_id()
    target = stage_map.ghl_stage_for("opportunity", deal.stage or "")
    if target is None:
        _raise_unmapped(
            session,
            correlation_id=correlation_id,
            entity_label="opportunity_stage",
            source_id=deal.source_id,
            value=deal.stage,
            entity_type=CanonicalEntityType.DEAL,
        )
        return "approval"
    pipeline_id, stage_id = target

    link = _find_mapping(session, CanonicalEntityType.DEAL, autotask_id=deal.source_id or "")
    if link and link.ghl_id:
        changes: dict[str, object] = {
            "name": deal.name,
            "monetaryValue": deal.monetary_value,
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
        }
        await ghl.update_opportunity(link.ghl_id, changes)
        link.last_synced_at = utcnow()
        action, ref = "updated", link.ghl_id
    else:
        deal.contact_id = _contact_ghl_id(session, deal.contact_id) or deal.contact_id
        result = await ghl.create_opportunity(deal, pipeline_id=pipeline_id, stage_id=stage_id)
        _link(
            session,
            CanonicalEntityType.DEAL,
            autotask_id=deal.source_id,
            ghl_id=result.external_id,
            autotask_entity_type="Opportunity",
        )
        action, ref = "created", result.external_id

    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.UPDATE if action == "updated" else Operation.CREATE,
        entity_type="deal",
        entity_ref=ref,
        status=TransactionStatus.SUCCESS,
        summary=f"Autotask Opportunity {deal.source_id} -> GHL {action} (authoritative mirror)",
        detail={"after": canonical_to_dict(deal)},
    )
    return action


async def mirror_autotask_ticket(
    session: Session,
    *,
    ticket: CanonicalServiceItem,
    ghl,
    stage_map: StageMap,
    correlation_id: str | None = None,
) -> str:
    """Mirror an Autotask Ticket into the GHL Service pipeline (read-only card).

    ⚠️ One-way for the whole of v1 (Spec §10.2): GHL edits to these cards are
    never pushed back; the next sweep simply overwrites them."""
    correlation_id = correlation_id or new_correlation_id()
    target = stage_map.ghl_stage_for("ticket", ticket.status or "")
    if target is None:
        _raise_unmapped(
            session,
            correlation_id=correlation_id,
            entity_label="ticket_status",
            source_id=ticket.source_id,
            value=ticket.status,
            entity_type=CanonicalEntityType.SERVICE_ITEM,
        )
        return "approval"
    pipeline_id, stage_id = target

    link = _find_mapping(
        session, CanonicalEntityType.SERVICE_ITEM, autotask_id=ticket.source_id or ""
    )
    if link and link.ghl_id:
        await ghl.update_opportunity(
            link.ghl_id,
            {"name": ticket.title, "pipelineId": pipeline_id, "pipelineStageId": stage_id},
        )
        link.last_synced_at = utcnow()
        action, ref = "updated", link.ghl_id
    else:
        card = CanonicalDeal(source_system=System.AUTOTASK, source_id=ticket.source_id)
        card.name = f"[Ticket] {ticket.title or ticket.source_id}"
        card.status = "open"
        card.contact_id = _contact_ghl_id(session, ticket.contact_id)
        result = await ghl.create_opportunity(card, pipeline_id=pipeline_id, stage_id=stage_id)
        _link(
            session,
            CanonicalEntityType.SERVICE_ITEM,
            autotask_id=ticket.source_id,
            ghl_id=result.external_id,
            autotask_entity_type="Ticket",
        )
        action, ref = "created", result.external_id

    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.UPDATE if action == "updated" else Operation.CREATE,
        entity_type="service_item",
        entity_ref=ref,
        status=TransactionStatus.SUCCESS,
        summary=f"Autotask Ticket {ticket.source_id} -> GHL service card {action} (read-only mirror)",
    )
    return action
