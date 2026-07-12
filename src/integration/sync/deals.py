"""Flow 2c — gated GHL Opportunity → Autotask (Spec §10.2, §10.4).

The dangerous direction. Every write obeys §5, plus the deal-specific gates:

- A GHL opportunity in the **service pipeline** direction GHL→AT is **BLOCKED and
  logged** (never queued): GHL must never create or mutate Autotask Tickets in v1.
- Unknown pipeline / unmapped stage → ``unmapped_stage`` approval, never guessed.
- New deal (no link): the Account must resolve through the Flow-1 contact map;
  then dedupe against that Account's Opportunities — clean miss = create,
  possible match = ``opportunity_create`` approval.
- Linked deal: a **backwards** stage move or a change to a **closed** outcome →
  ``sales_outcome_change`` approval; an amount change against a **populated**
  Autotask amount → ``amount_conflict`` approval; forward stage moves auto-apply
  (with before-state audit). Reaching **closed-won** additionally triggers the
  Stage-C conversion handoff (Spec §8.2) via ``conversion.handle_closed_won``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalDeal
from ..config.settings import get_settings
from ..config.stages import StageMap
from ..core import circuit_breaker
from ..core.idempotency import already_processed, mark_processed
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import (
    Actor,
    ApprovalStatus,
    ApprovalType,
    CanonicalEntityType,
    Direction,
    Operation,
    Severity,
    System,
    TransactionStatus,
)
from ..db.models import EntityMapping
from . import conversion
from .approvals import ApprovalRequest, decide, get_approval, raise_approval
from .audit import canonical_to_dict, record_before_state, record_transaction

log = get_logger(__name__)


@dataclass(slots=True)
class DealSyncOutcome:
    action: str            # created | updated | approval | blocked | skipped
    correlation_id: str
    autotask_id: str | None = None
    ghl_id: str | None = None
    approval_ids: list[int] = field(default_factory=list)
    detail: str = ""


def _mapping_by_ghl(session: Session, ghl_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.DEAL,
        EntityMapping.ghl_id == ghl_id,
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


def _link_deal(session: Session, *, ghl_id: str | None, autotask_id: str | None) -> EntityMapping:
    row = EntityMapping(
        canonical_entity_type=CanonicalEntityType.DEAL,
        autotask_entity_type="Opportunity",
        autotask_id=autotask_id,
        ghl_entity_type="opportunity",
        ghl_id=ghl_id,
        environment=get_settings().environment,
        last_synced_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _stage_index(stage_map: StageMap, autotask_value: str | None) -> int | None:
    """Position of an Autotask stage value in the sales pipeline order (the YAML
    stage list order IS the pipeline order); None if unknown."""
    for i, entry in enumerate(stage_map.sales.stages):
        if entry.autotask_status_value == str(autotask_value):
            return i
    return None


def _tx(session, correlation_id, operation, status, ref, summary, detail=None):
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=operation,
        entity_type="deal",
        entity_ref=ref,
        status=status,
        summary=summary,
        detail=detail,
    )


def _deal_approval(
    session,
    *,
    approval_type: ApprovalType,
    severity: Severity,
    ghl_deal: CanonicalDeal,
    autotask_id: str | None,
    proposed: dict,
    reason: str,
    correlation_id: str,
) -> int:
    approval = raise_approval(
        session,
        ApprovalRequest(
            approval_type=approval_type,
            severity=severity,
            canonical_entity_type=CanonicalEntityType.DEAL,
            source_system=System.GHL,
            target_system=System.AUTOTASK,
            ghl_id=ghl_deal.source_id,
            autotask_id=autotask_id,
            proposed_change=proposed,
            detected_reason=reason,
            correlation_id=correlation_id,
        ),
    )
    _tx(
        session,
        correlation_id,
        Operation.CONFLICT,
        TransactionStatus.CONFLICT,
        ghl_deal.source_id,
        f"{reason} -> approval #{approval.id}",
    )
    circuit_breaker.evaluate(session)
    return approval.id


async def process_ghl_opportunity(
    session: Session,
    *,
    ghl_deal: CanonicalDeal,
    event_id: str,
    autotask,
    ghl,
    stage_map: StageMap,
    correlation_id: str | None = None,
) -> DealSyncOutcome:
    settings = get_settings()
    correlation_id = correlation_id or new_correlation_id()
    ghl_id = ghl_deal.source_id
    pipeline_id = str(ghl_deal.extra.get("pipeline_id") or "")
    stage_id = str(ghl_deal.extra.get("stage_id") or "")

    # 1) Idempotency (exactly-once, Spec §12.1).
    if already_processed(session, event_id, System.GHL):
        return DealSyncOutcome("skipped", correlation_id, ghl_id=ghl_id, detail="duplicate event")
    mark_processed(session, event_id, System.GHL)

    # 2) Pipeline routing — the primary duplication guard (Spec §10.1).
    pipe = stage_map.pipeline_for(pipeline_id)
    if pipe is None:
        aid = _deal_approval(
            session,
            approval_type=ApprovalType.UNMAPPED_STAGE,
            severity=Severity.MED,
            ghl_deal=ghl_deal,
            autotask_id=None,
            proposed={"pipeline_id": pipeline_id, "stage_id": stage_id},
            reason=f"GHL pipeline {pipeline_id!r} is not in the stage map",
            correlation_id=correlation_id,
        )
        return DealSyncOutcome("approval", correlation_id, ghl_id=ghl_id, approval_ids=[aid])

    if pipe.autotask_entity == "ticket":
        # ⚠️ BLOCKED, not queued: GHL never writes Autotask Tickets in v1 (§10.2).
        _tx(
            session,
            correlation_id,
            Operation.BLOCK,
            TransactionStatus.BLOCKED,
            ghl_id,
            "GHL edit on a service-pipeline card BLOCKED — GHL never writes Autotask Tickets (v1)",
        )
        return DealSyncOutcome("blocked", correlation_id, ghl_id=ghl_id, detail="ticket write blocked")

    at_value = stage_map.autotask_value_for(pipeline_id, stage_id)
    if at_value is None:
        aid = _deal_approval(
            session,
            approval_type=ApprovalType.UNMAPPED_STAGE,
            severity=Severity.MED,
            ghl_deal=ghl_deal,
            autotask_id=None,
            proposed={"pipeline_id": pipeline_id, "stage_id": stage_id},
            reason=f"GHL stage {stage_id!r} has no stage-map entry — never guessed (§10.3)",
            correlation_id=correlation_id,
        )
        return DealSyncOutcome("approval", correlation_id, ghl_id=ghl_id, approval_ids=[aid])

    approval_ids: list[int] = []

    # 3) Stage C — the conversion/handoff signal (Spec §8.2). Defaults to
    # closed-won; configurable earlier via conversion_stage_ids in the stage map.
    if stage_map.is_conversion_trigger(pipeline_id, stage_id):
        onboarding = await conversion.handle_closed_won(
            session, ghl_deal=ghl_deal, autotask=autotask, ghl=ghl, correlation_id=correlation_id
        )
        approval_ids.extend(onboarding.approval_ids)

    link = _mapping_by_ghl(session, ghl_id) if ghl_id else None

    # 4) Linked deal — reconcile against the protected Autotask Opportunity.
    if link and link.autotask_id:
        existing = await autotask.get_opportunity(link.autotask_id)
        if existing is None:
            aid = _deal_approval(
                session,
                approval_type=ApprovalType.OPPORTUNITY_CREATE,
                severity=Severity.MED,
                ghl_deal=ghl_deal,
                autotask_id=link.autotask_id,
                proposed={"incoming": canonical_to_dict(ghl_deal)},
                reason="Mapped Autotask Opportunity not found — manual reconciliation required",
                correlation_id=correlation_id,
            )
            return DealSyncOutcome(
                "approval", correlation_id, ghl_id=ghl_id, approval_ids=approval_ids + [aid]
            )

        action = "skipped"

        # 4a) Closed-outcome protection (never auto-overwrite a sales outcome).
        ghl_status = (ghl_deal.status or "").lower()
        if ghl_status in ("won", "lost") and stage_map.is_closed_won(pipeline_id, stage_id) is False:
            pass  # closed-lost handled below with the stage comparison
        if ghl_status in ("won", "lost"):
            aid = _deal_approval(
                session,
                approval_type=ApprovalType.SALES_OUTCOME_CHANGE,
                severity=Severity.HIGH,
                ghl_deal=ghl_deal,
                autotask_id=link.autotask_id,
                proposed={
                    "field": "status",
                    "before": existing.status,
                    "after": ghl_status,
                    "stage_after": at_value,
                },
                reason=f"GHL marked deal {ghl_status.upper()} — closing an Autotask "
                "Opportunity requires approval (§10.2)",
                correlation_id=correlation_id,
            )
            approval_ids.append(aid)
            action = "approval"

        # 4b) Stage move: forward auto-applies; BACKWARDS requires approval (§10.2).
        elif at_value != (existing.stage or ""):
            before_idx = _stage_index(stage_map, existing.stage)
            after_idx = _stage_index(stage_map, at_value)
            backwards = before_idx is not None and after_idx is not None and after_idx < before_idx
            if backwards:
                aid = _deal_approval(
                    session,
                    approval_type=ApprovalType.SALES_OUTCOME_CHANGE,
                    severity=Severity.MED,
                    ghl_deal=ghl_deal,
                    autotask_id=link.autotask_id,
                    proposed={"field": "stage", "before": existing.stage, "after": at_value},
                    reason="GHL stage change would move the Autotask Opportunity BACKWARDS",
                    correlation_id=correlation_id,
                )
                approval_ids.append(aid)
                action = "approval"
            else:
                circuit_breaker.assert_writable(session)
                record_before_state(
                    session,
                    correlation_id=correlation_id,
                    operation=Operation.UPDATE,
                    target_system=System.AUTOTASK,
                    entity_type="Opportunity",
                    entity_id=link.autotask_id,
                    before=existing,
                    after={**(canonical_to_dict(existing) or {}), "stage": at_value},
                    actor=Actor.SYSTEM,
                    result="stage_forward",
                )
                await autotask.update_opportunity(link.autotask_id, {"stage": int(at_value)})
                _tx(
                    session,
                    correlation_id,
                    Operation.UPDATE,
                    TransactionStatus.SUCCESS,
                    link.autotask_id,
                    f"Forward stage move applied: {existing.stage} -> {at_value}",
                )
                action = "updated"

        # 4c) Amount: populated-field conflict -> approval; empty -> additive.
        ghl_amount = ghl_deal.monetary_value
        at_amount = existing.monetary_value
        if ghl_amount is not None and ghl_amount != at_amount:
            if at_amount in (None, 0):
                if settings.auto_apply_additive:
                    circuit_breaker.assert_writable(session)
                    record_before_state(
                        session,
                        correlation_id=correlation_id,
                        operation=Operation.UPDATE,
                        target_system=System.AUTOTASK,
                        entity_type="Opportunity",
                        entity_id=link.autotask_id,
                        before=existing,
                        after={**(canonical_to_dict(existing) or {}), "amount": ghl_amount},
                        actor=Actor.SYSTEM,
                        result="additive",
                    )
                    await autotask.update_opportunity(link.autotask_id, {"amount": ghl_amount})
                    _tx(
                        session,
                        correlation_id,
                        Operation.UPDATE,
                        TransactionStatus.SUCCESS,
                        link.autotask_id,
                        f"Additive amount fill: {ghl_amount}",
                    )
                    if action == "skipped":
                        action = "updated"
            else:
                aid = _deal_approval(
                    session,
                    approval_type=ApprovalType.AMOUNT_CONFLICT,
                    severity=Severity.HIGH,
                    ghl_deal=ghl_deal,
                    autotask_id=link.autotask_id,
                    proposed={"field": "amount", "before": at_amount, "after": ghl_amount},
                    reason=f"GHL amount {ghl_amount} conflicts with populated Autotask "
                    f"amount {at_amount} (§10.4)",
                    correlation_id=correlation_id,
                )
                approval_ids.append(aid)
                action = "approval"

        if action == "skipped":
            _tx(
                session,
                correlation_id,
                Operation.SKIP,
                TransactionStatus.SKIPPED,
                link.autotask_id,
                "No deal changes to apply (in sync)",
            )
        circuit_breaker.evaluate(session)
        return DealSyncOutcome(
            action,
            correlation_id,
            autotask_id=link.autotask_id,
            ghl_id=ghl_id,
            approval_ids=approval_ids,
        )

    # 5) New deal — resolve the Account through the Flow-1 identity spine (§10.4).
    account_id: str | None = None
    if ghl_deal.contact_id:
        contact_link = _contact_mapping_by_ghl(session, ghl_deal.contact_id)
        if contact_link and contact_link.autotask_id:
            at_contact = await autotask.get_contact(contact_link.autotask_id)
            account_id = at_contact.company_id if at_contact else None

    if account_id is None:
        aid = _deal_approval(
            session,
            approval_type=ApprovalType.OPPORTUNITY_CREATE,
            severity=Severity.HIGH,
            ghl_deal=ghl_deal,
            autotask_id=None,
            proposed={"incoming": canonical_to_dict(ghl_deal), "stage": at_value},
            reason="Deal's Account could not be resolved to an existing Autotask "
            "Account — approval required (§10.4)",
            correlation_id=correlation_id,
        )
        return DealSyncOutcome(
            "approval", correlation_id, ghl_id=ghl_id, approval_ids=approval_ids + [aid]
        )

    # 5a) Dedupe against the Account's existing Opportunities (§10.2).
    candidates = await autotask.find_opportunities(account_id=account_id)
    norm = (ghl_deal.name or "").strip().lower()
    possible = [c for c in candidates if norm and (c.name or "").strip().lower() == norm]
    if possible:
        aid = _deal_approval(
            session,
            approval_type=ApprovalType.OPPORTUNITY_CREATE,
            severity=Severity.MED,
            ghl_deal=ghl_deal,
            autotask_id=possible[0].source_id,
            proposed={
                "incoming": canonical_to_dict(ghl_deal),
                "stage": at_value,
                "account_id": account_id,
                "candidates": [canonical_to_dict(c) for c in possible],
            },
            reason=f"Possible duplicate Opportunity on Account {account_id} — confirm link or create",
            correlation_id=correlation_id,
        )
        return DealSyncOutcome(
            "approval", correlation_id, ghl_id=ghl_id, approval_ids=approval_ids + [aid]
        )

    # 5b) Clean miss -> safe to create (§5.2, §10.2).
    ghl_deal.account_id = account_id
    ghl_deal.stage = at_value
    circuit_breaker.assert_writable(session)
    record_before_state(
        session,
        correlation_id=correlation_id,
        operation=Operation.CREATE,
        target_system=System.AUTOTASK,
        entity_type="Opportunity",
        entity_id=None,
        before=None,
        after=ghl_deal,
        actor=Actor.SYSTEM,
        result="create",
    )
    result = await autotask.create_opportunity(ghl_deal)
    if not result.ok:
        _tx(
            session,
            correlation_id,
            Operation.ERROR,
            TransactionStatus.ERROR,
            ghl_id,
            f"Opportunity create failed: {result.detail}",
        )
        circuit_breaker.evaluate(session)
        return DealSyncOutcome("blocked", correlation_id, ghl_id=ghl_id, detail=result.detail)

    _link_deal(session, ghl_id=ghl_id, autotask_id=result.external_id)
    _tx(
        session,
        correlation_id,
        Operation.CREATE,
        TransactionStatus.SUCCESS,
        result.external_id,
        f"Created Autotask Opportunity {result.external_id} (clean dedupe miss)",
    )
    circuit_breaker.evaluate(session)
    return DealSyncOutcome(
        "created",
        correlation_id,
        autotask_id=result.external_id,
        ghl_id=ghl_id,
        approval_ids=approval_ids,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Human decisions for Flow-2 / Stage-C approval types (Spec §11.1)
# ─────────────────────────────────────────────────────────────────────────────
DEAL_APPROVAL_TYPES = {
    ApprovalType.OPPORTUNITY_CREATE,
    ApprovalType.SALES_OUTCOME_CHANGE,
    ApprovalType.AMOUNT_CONFLICT,
    ApprovalType.UNMAPPED_STAGE,
    ApprovalType.CUSTOMER_ONBOARDING,
}


async def apply_deal_decision(
    session: Session,
    *,
    approval_id: int,
    approve: bool,
    decided_by: str,
    autotask,
    ghl=None,
    chosen_account_id: str | None = None,
) -> DealSyncOutcome:
    approval = get_approval(session, approval_id)
    if approval is None:
        return DealSyncOutcome("skipped", new_correlation_id(), detail="approval not found")
    if approval.status != ApprovalStatus.PENDING:
        return DealSyncOutcome(
            "skipped", approval.correlation_id, detail=f"already {approval.status}"
        )

    corr = approval.correlation_id
    decide(session, approval_id, approve=approve, decided_by=decided_by)

    if not approve:
        _tx(
            session,
            corr,
            Operation.SKIP,
            TransactionStatus.SKIPPED,
            approval.autotask_id or approval.ghl_id,
            f"Approval #{approval_id} rejected by {decided_by} — Autotask unchanged",
        )
        return DealSyncOutcome("skipped", corr, detail="rejected")

    atype = ApprovalType(approval.approval_type)
    circuit_breaker.assert_writable(session)
    proposed = approval.proposed_change or {}

    if atype is ApprovalType.CUSTOMER_ONBOARDING:
        return await conversion.apply_onboarding(
            session,
            approval=approval,
            autotask=autotask,
            ghl=ghl,
            decided_by=decided_by,
            chosen_account_id=chosen_account_id,
        )

    if atype in (ApprovalType.SALES_OUTCOME_CHANGE, ApprovalType.AMOUNT_CONFLICT):
        if not approval.autotask_id:
            return DealSyncOutcome("skipped", corr, detail="no target opportunity")
        field_name = proposed.get("field", "")
        after = proposed.get("after")
        changes: dict[str, object] = {}
        if field_name == "amount":
            changes["amount"] = after
        elif field_name == "stage":
            changes["stage"] = int(after)
        elif field_name == "status":
            # GHL won/lost -> Autotask closed status + the mapped stage if provided.
            changes["status"] = 2 if after == "won" else 3
            if proposed.get("stage_after"):
                changes["stage"] = int(proposed["stage_after"])
        before = await autotask.get_opportunity(approval.autotask_id)
        record_before_state(
            session,
            correlation_id=corr,
            operation=Operation.UPDATE,
            target_system=System.AUTOTASK,
            entity_type="Opportunity",
            entity_id=approval.autotask_id,
            before=before,
            after={**(canonical_to_dict(before) or {}), **changes},
            actor=Actor.APPROVAL,
            result="approved",
        )
        result = await autotask.update_opportunity(approval.autotask_id, changes)
        _tx(
            session,
            corr,
            Operation.UPDATE,
            TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
            approval.autotask_id,
            f"Applied approved deal change ({field_name}) by {decided_by}",
        )
        circuit_breaker.evaluate(session)
        return DealSyncOutcome("updated", corr, autotask_id=approval.autotask_id)

    if atype is ApprovalType.OPPORTUNITY_CREATE:
        incoming = proposed.get("incoming") or {}
        deal = CanonicalDeal(source_system=System.GHL, source_id=incoming.get("source_id"))
        for key, value in incoming.items():
            if hasattr(deal, key) and key not in {"source_system", "entity_type", "extra"}:
                setattr(deal, key, value)
        deal.account_id = chosen_account_id or proposed.get("account_id") or deal.account_id
        deal.stage = proposed.get("stage") or deal.stage
        if deal.account_id is None:
            return DealSyncOutcome("skipped", corr, detail="approved but no account resolved")
        record_before_state(
            session,
            correlation_id=corr,
            operation=Operation.CREATE,
            target_system=System.AUTOTASK,
            entity_type="Opportunity",
            entity_id=None,
            before=None,
            after=deal,
            actor=Actor.APPROVAL,
            result="approved",
        )
        result = await autotask.create_opportunity(deal)
        if result.ok:
            _link_deal(session, ghl_id=approval.ghl_id, autotask_id=result.external_id)
        _tx(
            session,
            corr,
            Operation.CREATE if result.ok else Operation.ERROR,
            TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
            result.external_id,
            f"Created Autotask Opportunity {result.external_id} after approval by {decided_by}",
        )
        circuit_breaker.evaluate(session)
        return DealSyncOutcome(
            "created" if result.ok else "blocked", corr, autotask_id=result.external_id
        )

    # UNMAPPED_STAGE: approving is an acknowledgement — the fix is editing the YAML.
    _tx(
        session,
        corr,
        Operation.SKIP,
        TransactionStatus.SKIPPED,
        approval.ghl_id,
        f"Unmapped-stage approval #{approval_id} acknowledged by {decided_by} — "
        "update stage_mapping.yaml to resolve",
    )
    return DealSyncOutcome("skipped", corr, detail="acknowledged")
