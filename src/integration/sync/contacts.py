"""Contacts flow orchestrator — the Stage-1 reference implementation (Spec §9).

Ties the whole protected pipeline together for a GHL contact arriving at the
boundary:

    idempotency -> mapping lookup -> dedupe -> Account-linkage guard ->
    conflict detection -> (additive auto-apply | approval) -> before-state audit ->
    gated Autotask write -> transaction feed -> circuit-breaker evaluation

Every GHL -> Autotask write obeys §5: clean miss = safe create, fuzzy = approval,
populated-field conflict = approval (never auto-overwrite), empty field = additive.
The Autotask **before-state is captured to the immutable audit log before any
write** so a human can revert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalCompany, CanonicalContact
from ..config.mapping import load_contacts_mapping
from ..config.settings import get_settings
from ..core import circuit_breaker
from ..core.idempotency import already_processed, mark_processed
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import (
    Actor,
    ApprovalType,
    CanonicalEntityType,
    Direction,
    Operation,
    Severity,
    System,
    TransactionStatus,
)
from ..db.models import EntityMapping
from ..db.enums import ApprovalStatus
from .approvals import ApprovalRequest, decide, get_approval, raise_approval
from .audit import contact_dict, record_before_state, record_transaction
from .conflicts import FieldChanges, detect_changes
from .linkage import LinkOutcome, resolve_account
from .matching import MatchClass, match_contact

log = get_logger(__name__)

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MED: 1, Severity.HIGH: 2}


@dataclass(slots=True)
class ContactSyncOutcome:
    action: str                       # created | updated | linked | approval | skipped | blocked
    correlation_id: str
    autotask_id: str | None = None
    ghl_id: str | None = None
    approval_ids: list[int] = field(default_factory=list)
    detail: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# entity_mapping helpers
# ─────────────────────────────────────────────────────────────────────────────
def _find_mapping_by_ghl(session: Session, ghl_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
        EntityMapping.ghl_id == ghl_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _link_mapping(session: Session, *, ghl_id: str | None, autotask_id: str | None) -> EntityMapping:
    row = EntityMapping(
        canonical_entity_type=CanonicalEntityType.CONTACT,
        autotask_entity_type="Contact",
        autotask_id=autotask_id,
        ghl_entity_type="contact",
        ghl_id=ghl_id,
        environment=get_settings().environment,
        last_synced_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point: GHL contact -> Autotask (the gated direction)
# ─────────────────────────────────────────────────────────────────────────────
async def process_ghl_contact(
    session: Session,
    *,
    ghl_contact: CanonicalContact,
    event_id: str,
    autotask,  # AutotaskConnector (duck-typed for testability)
    correlation_id: str | None = None,
) -> ContactSyncOutcome:
    settings = get_settings()
    mapping = load_contacts_mapping()
    correlation_id = correlation_id or new_correlation_id()
    ghl_id = ghl_contact.source_id

    # 1) Idempotency — exactly-once handling (Spec §12.1).
    if already_processed(session, event_id, System.GHL):
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="contact",
            entity_ref=ghl_id,
            status=TransactionStatus.SKIPPED,
            summary=f"Duplicate event {event_id} ignored (already processed)",
        )
        return ContactSyncOutcome("skipped", correlation_id, ghl_id=ghl_id, detail="duplicate event")
    mark_processed(session, event_id, System.GHL)

    # 2) Known contact? (entity_mapping is the identity spine that stops dupes.)
    existing_link = _find_mapping_by_ghl(session, ghl_id) if ghl_id else None
    if existing_link and existing_link.autotask_id:
        existing = await autotask.get_contact(existing_link.autotask_id)
        if existing is None:
            # Mapping points at a record that no longer exists — surface, don't guess.
            return _approval_only(
                session,
                correlation_id=correlation_id,
                approval_type=ApprovalType.CONTACT_FIELD_CONFLICT,
                severity=Severity.MED,
                ghl_contact=ghl_contact,
                autotask_id=existing_link.autotask_id,
                reason="Mapped Autotask contact not found — manual reconciliation required.",
            )
        return await _reconcile_existing(
            session,
            correlation_id=correlation_id,
            ghl_contact=ghl_contact,
            existing=existing,
            autotask_id=existing_link.autotask_id,
            autotask=autotask,
            mapping=mapping,
        )

    # 3) Unknown -> dedupe (Spec §9.1).
    candidates = await autotask.find_contacts(email=ghl_contact.email)
    match = match_contact(ghl_contact, candidates)

    if match.classification is MatchClass.CONFIDENT and match.matched:
        # Safe to link; then reconcile fields against the matched record.
        _link_mapping(session, ghl_id=ghl_id, autotask_id=match.matched.source_id)
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.UPDATE,
            entity_type="contact",
            entity_ref=match.matched.source_id,
            status=TransactionStatus.SUCCESS,
            summary=f"Linked GHL contact {ghl_id} to Autotask {match.matched.source_id} ({match.rule})",
        )
        return await _reconcile_existing(
            session,
            correlation_id=correlation_id,
            ghl_contact=ghl_contact,
            existing=match.matched,
            autotask_id=match.matched.source_id,
            autotask=autotask,
            mapping=mapping,
            base_action="linked",
        )

    if match.classification is MatchClass.POSSIBLE:
        # Fuzzy/possible duplicate -> NEVER auto-create. Approval (Spec §9.1).
        approval = raise_approval(
            session,
            ApprovalRequest(
                approval_type=ApprovalType.CONTACT_POSSIBLE_DUPLICATE,
                severity=Severity.MED,
                canonical_entity_type=CanonicalEntityType.CONTACT,
                source_system=System.GHL,
                target_system=System.AUTOTASK,
                ghl_id=ghl_id,
                autotask_id=match.matched.source_id if match.matched else None,
                proposed_change={
                    "incoming": contact_dict(ghl_contact),
                    "candidates": [contact_dict(c) for c in (match.candidates or [match.matched])],
                    "rule": match.rule,
                },
                detected_reason=f"Possible duplicate via {match.rule} — confirm link or create new.",
                correlation_id=correlation_id,
            ),
        )
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.CONFLICT,
            entity_type="contact",
            entity_ref=ghl_id,
            status=TransactionStatus.CONFLICT,
            summary=f"Possible duplicate ({match.rule}) -> approval #{approval.id}",
        )
        circuit_breaker.evaluate(session)
        return ContactSyncOutcome(
            "approval", correlation_id, ghl_id=ghl_id, approval_ids=[approval.id], detail=match.rule
        )

    # 4) Clean miss -> Account-linkage guard (Spec §9.3) before any create.
    return await _create_with_linkage(
        session,
        correlation_id=correlation_id,
        ghl_contact=ghl_contact,
        autotask=autotask,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reconcile an existing/linked contact: additive auto-apply + conflict approvals
# ─────────────────────────────────────────────────────────────────────────────
async def _reconcile_existing(
    session: Session,
    *,
    correlation_id: str,
    ghl_contact: CanonicalContact,
    existing: CanonicalContact,
    autotask_id: str,
    autotask,
    mapping,
    base_action: str = "updated",
) -> ContactSyncOutcome:
    settings = get_settings()
    changes: FieldChanges = detect_changes(ghl_contact, existing, mapping)
    approval_ids: list[int] = []
    action = "skipped"

    # Additive fields fill EMPTY Autotask fields — auto-apply (default on), logged.
    if changes.additive and settings.auto_apply_additive:
        circuit_breaker.assert_writable(session)  # respect the breaker (Spec §5.5)
        record_before_state(
            session,
            correlation_id=correlation_id,
            operation=Operation.UPDATE,
            target_system=System.AUTOTASK,
            entity_type="Contact",
            entity_id=autotask_id,
            before=existing,
            after={**contact_dict(existing), **changes.additive},
            actor=Actor.SYSTEM,
            result="additive",
        )
        result = await autotask.update_contact(autotask_id, changes.additive)
        status = TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.UPDATE,
            entity_type="contact",
            entity_ref=autotask_id,
            status=status,
            summary=f"Additive update of {len(changes.additive)} empty field(s): "
            f"{', '.join(changes.additive)}",
            detail={"fields": list(changes.additive)},
        )
        action = base_action

    # Conflicts on POPULATED fields -> one approval card, never auto-overwrite.
    if changes.has_conflicts:
        severity = max((c.severity for c in changes.conflicts), key=_SEVERITY_ORDER.get)
        approval = raise_approval(
            session,
            ApprovalRequest(
                approval_type=ApprovalType.CONTACT_FIELD_CONFLICT,
                severity=severity,
                canonical_entity_type=CanonicalEntityType.CONTACT,
                source_system=System.GHL,
                target_system=System.AUTOTASK,
                ghl_id=ghl_contact.source_id,
                autotask_id=autotask_id,
                proposed_change={
                    "fields": [
                        {
                            "field": c.canonical,
                            "before": c.before,
                            "after": c.after,
                            "severity": c.severity.value,
                        }
                        for c in changes.conflicts
                    ]
                },
                detected_reason=f"{len(changes.conflicts)} populated-field conflict(s) on Autotask Contact {autotask_id}",
                correlation_id=correlation_id,
            ),
        )
        approval_ids.append(approval.id)
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.CONFLICT,
            entity_type="contact",
            entity_ref=autotask_id,
            status=TransactionStatus.CONFLICT,
            summary=f"{len(changes.conflicts)} field conflict(s) -> approval #{approval.id}",
        )
        action = "approval"

    if action == "skipped":
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="contact",
            entity_ref=autotask_id,
            status=TransactionStatus.SKIPPED,
            summary="No changes to apply (in sync)",
        )

    circuit_breaker.evaluate(session)
    return ContactSyncOutcome(
        action, correlation_id, autotask_id=autotask_id, ghl_id=ghl_contact.source_id, approval_ids=approval_ids
    )


# ─────────────────────────────────────────────────────────────────────────────
# Clean miss: resolve the Account, then create (or route to approval)
# ─────────────────────────────────────────────────────────────────────────────
async def _create_with_linkage(
    session: Session,
    *,
    correlation_id: str,
    ghl_contact: CanonicalContact,
    autotask,
) -> ContactSyncOutcome:
    settings = get_settings()
    accounts = await autotask.find_accounts(ghl_contact.company_name or "")
    link = resolve_account(
        ghl_contact.company_name,
        accounts,
        holding_account_id=settings.autotask_holding_account_id or None,
    )

    # Account ambiguity / absence -> approval, never auto-create an Account (Spec §9.3).
    if link.outcome in (LinkOutcome.APPROVAL_LINK, LinkOutcome.APPROVAL_CREATE):
        approval_type = (
            ApprovalType.ACCOUNT_LINK
            if link.outcome is LinkOutcome.APPROVAL_LINK
            else ApprovalType.ACCOUNT_CREATE
        )
        approval = raise_approval(
            session,
            ApprovalRequest(
                approval_type=approval_type,
                severity=Severity.HIGH,  # account decisions carry billing/ownership weight
                canonical_entity_type=CanonicalEntityType.CONTACT,
                source_system=System.GHL,
                target_system=System.AUTOTASK,
                ghl_id=ghl_contact.source_id,
                proposed_change={
                    "incoming": contact_dict(ghl_contact),
                    "company_name": ghl_contact.company_name,
                    "candidates": [
                        {"id": c.company_id, "name": c.name} for c in (link.candidates or [])
                    ],
                },
                detected_reason=link.reason,
                correlation_id=correlation_id,
            ),
        )
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.CONFLICT,
            entity_type="contact",
            entity_ref=ghl_contact.source_id,
            status=TransactionStatus.CONFLICT,
            summary=f"Account linkage -> approval #{approval.id} ({link.outcome})",
        )
        circuit_breaker.evaluate(session)
        return ContactSyncOutcome(
            "approval",
            correlation_id,
            ghl_id=ghl_contact.source_id,
            approval_ids=[approval.id],
            detail=link.reason,
        )

    # LINKED (exact) or HOLDING -> companyID resolved -> safe to create the Contact.
    ghl_contact.company_id = link.company_id
    circuit_breaker.assert_writable(session)
    record_before_state(
        session,
        correlation_id=correlation_id,
        operation=Operation.CREATE,
        target_system=System.AUTOTASK,
        entity_type="Contact",
        entity_id=None,
        before=None,  # creation -> no prior state
        after=ghl_contact,
        actor=Actor.SYSTEM,
        result="create",
    )
    result = await autotask.create_contact(ghl_contact)
    if not result.ok:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.ERROR,
            entity_type="contact",
            entity_ref=ghl_contact.source_id,
            status=TransactionStatus.ERROR,
            summary=f"Create failed: {result.detail}",
        )
        circuit_breaker.evaluate(session)
        return ContactSyncOutcome("blocked", correlation_id, ghl_id=ghl_contact.source_id, detail=result.detail)

    _link_mapping(session, ghl_id=ghl_contact.source_id, autotask_id=result.external_id)
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CREATE,
        entity_type="contact",
        entity_ref=result.external_id,
        status=TransactionStatus.SUCCESS,
        summary=f"Created Autotask Contact {result.external_id} ({link.outcome}); {link.reason}",
        detail={"linkage": link.outcome.value},
    )

    approval_ids: list[int] = []
    # Holding-account placement still needs a human to resolve the real Account later.
    if link.outcome is LinkOutcome.HOLDING:
        approval = raise_approval(
            session,
            ApprovalRequest(
                approval_type=ApprovalType.ACCOUNT_LINK,
                severity=Severity.MED,
                canonical_entity_type=CanonicalEntityType.CONTACT,
                source_system=System.GHL,
                target_system=System.AUTOTASK,
                ghl_id=ghl_contact.source_id,
                autotask_id=result.external_id,
                proposed_change={"company_name": ghl_contact.company_name, "held_on": link.company_id},
                detected_reason=f"Contact parked on holding account {link.company_id}; resolve real Account.",
                correlation_id=correlation_id,
            ),
        )
        approval_ids.append(approval.id)

    circuit_breaker.evaluate(session)
    return ContactSyncOutcome(
        "created",
        correlation_id,
        autotask_id=result.external_id,
        ghl_id=ghl_contact.source_id,
        approval_ids=approval_ids,
        detail=link.outcome.value,
    )


async def apply_decision(
    session: Session,
    *,
    approval_id: int,
    approve: bool,
    decided_by: str,
    autotask,
    chosen_account_id: str | None = None,
    chosen_autotask_contact_id: str | None = None,
) -> ContactSyncOutcome:
    """Apply a human decision from the Teams bot (Spec §11.1).

    Approved -> perform the gated Autotask write with before-state audit.
    Rejected -> leave Autotask untouched (the protective default), log it.

    ``chosen_account_id`` / ``chosen_autotask_contact_id`` carry the operator's
    pick when the approval offered candidates (Override on a card)."""
    approval = get_approval(session, approval_id)
    if approval is None:
        return ContactSyncOutcome("skipped", new_correlation_id(), detail="approval not found")
    if approval.status != ApprovalStatus.PENDING:
        return ContactSyncOutcome("skipped", approval.correlation_id, detail=f"already {approval.status}")

    corr = approval.correlation_id
    decide(session, approval_id, approve=approve, decided_by=decided_by)

    if not approve:
        # Rejected -> discard, Autotask untouched (Spec §11.1). Pushing the
        # authoritative Autotask value back to GHL to resolve drift is Stage 2.
        record_transaction(
            session,
            correlation_id=corr,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="contact",
            entity_ref=approval.autotask_id or approval.ghl_id,
            status=TransactionStatus.SKIPPED,
            summary=f"Approval #{approval_id} rejected by {decided_by} — Autotask unchanged",
        )
        return ContactSyncOutcome("skipped", corr, autotask_id=approval.autotask_id, detail="rejected")

    atype = ApprovalType(approval.approval_type)
    circuit_breaker.assert_writable(session)

    # ── Field conflict: apply the chosen 'after' values to the existing Contact ──
    if atype is ApprovalType.CONTACT_FIELD_CONFLICT and approval.autotask_id:
        changes = {f["field"]: f["after"] for f in approval.proposed_change.get("fields", [])}
        before = await autotask.get_contact(approval.autotask_id)
        record_before_state(
            session,
            correlation_id=corr,
            operation=Operation.UPDATE,
            target_system=System.AUTOTASK,
            entity_type="Contact",
            entity_id=approval.autotask_id,
            before=before,
            after={**(contact_dict(before) or {}), **changes},
            actor=Actor.APPROVAL,
            result="approved",
        )
        result = await autotask.update_contact(approval.autotask_id, changes)
        record_transaction(
            session,
            correlation_id=corr,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.UPDATE,
            entity_type="contact",
            entity_ref=approval.autotask_id,
            status=TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
            summary=f"Applied approved field changes ({', '.join(changes)}) by {decided_by}",
        )
        circuit_breaker.evaluate(session)
        return ContactSyncOutcome("updated", corr, autotask_id=approval.autotask_id, detail="approved")

    # ── Account create: create the Account (approved act), then the Contact ──
    if atype is ApprovalType.ACCOUNT_CREATE:
        incoming = approval.proposed_change.get("incoming", {})
        company = CanonicalCompany(source_system=System.GHL, name=approval.proposed_change.get("company_name"))
        record_before_state(
            session,
            correlation_id=corr,
            operation=Operation.CREATE,
            target_system=System.AUTOTASK,
            entity_type="Account",
            entity_id=None,
            before=None,
            after=company,
            actor=Actor.APPROVAL,
            result="approved",
        )
        acct = await autotask.create_account(company)
        contact = _contact_from_dict(incoming)
        contact.company_id = acct.external_id
        return await _create_after_approval(session, corr, contact, autotask, decided_by)

    # ── Account link / possible duplicate: use the operator's chosen target ──
    if atype in (ApprovalType.ACCOUNT_LINK, ApprovalType.CONTACT_POSSIBLE_DUPLICATE):
        if atype is ApprovalType.CONTACT_POSSIBLE_DUPLICATE and chosen_autotask_contact_id:
            _link_mapping(session, ghl_id=approval.ghl_id, autotask_id=chosen_autotask_contact_id)
            record_transaction(
                session,
                correlation_id=corr,
                direction=Direction.GHL_TO_AUTOTASK,
                operation=Operation.UPDATE,
                entity_type="contact",
                entity_ref=chosen_autotask_contact_id,
                status=TransactionStatus.SUCCESS,
                summary=f"Linked GHL {approval.ghl_id} to chosen Autotask contact by {decided_by}",
            )
            return ContactSyncOutcome("linked", corr, autotask_id=chosen_autotask_contact_id)

        account_id = chosen_account_id or approval.proposed_change.get("held_on")
        if not account_id:
            return ContactSyncOutcome("skipped", corr, detail="approved but no account chosen")
        incoming = approval.proposed_change.get("incoming")
        if incoming:  # fuzzy link approved before contact creation -> create now
            contact = _contact_from_dict(incoming)
            contact.company_id = account_id
            return await _create_after_approval(session, corr, contact, autotask, decided_by)
        # holding-account resolution: contact already exists, just record the link
        record_transaction(
            session,
            correlation_id=corr,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.UPDATE,
            entity_type="contact",
            entity_ref=approval.autotask_id,
            status=TransactionStatus.SUCCESS,
            summary=f"Account link resolved to {account_id} by {decided_by}",
        )
        return ContactSyncOutcome("linked", corr, autotask_id=approval.autotask_id)

    return ContactSyncOutcome("skipped", corr, detail=f"no apply handler for {atype}")


def _contact_from_dict(data: dict) -> CanonicalContact:
    contact = CanonicalContact(source_system=System.GHL, source_id=data.get("source_id"))
    for key, value in data.items():
        if hasattr(contact, key) and key not in {"source_system", "entity_type"}:
            setattr(contact, key, value)
    return contact


async def _create_after_approval(
    session: Session,
    correlation_id: str,
    contact: CanonicalContact,
    autotask,
    decided_by: str,
) -> ContactSyncOutcome:
    record_before_state(
        session,
        correlation_id=correlation_id,
        operation=Operation.CREATE,
        target_system=System.AUTOTASK,
        entity_type="Contact",
        entity_id=None,
        before=None,
        after=contact,
        actor=Actor.APPROVAL,
        result="approved",
    )
    result = await autotask.create_contact(contact)
    if result.ok:
        _link_mapping(session, ghl_id=contact.source_id, autotask_id=result.external_id)
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CREATE if result.ok else Operation.ERROR,
        entity_type="contact",
        entity_ref=result.external_id,
        status=TransactionStatus.SUCCESS if result.ok else TransactionStatus.ERROR,
        summary=f"Created Autotask Contact {result.external_id} after approval by {decided_by}",
    )
    circuit_breaker.evaluate(session)
    return ContactSyncOutcome(
        "created" if result.ok else "blocked",
        correlation_id,
        autotask_id=result.external_id,
        ghl_id=contact.source_id,
    )


def _approval_only(
    session: Session,
    *,
    correlation_id: str,
    approval_type: ApprovalType,
    severity: Severity,
    ghl_contact: CanonicalContact,
    autotask_id: str | None,
    reason: str,
) -> ContactSyncOutcome:
    approval = raise_approval(
        session,
        ApprovalRequest(
            approval_type=approval_type,
            severity=severity,
            canonical_entity_type=CanonicalEntityType.CONTACT,
            source_system=System.GHL,
            target_system=System.AUTOTASK,
            ghl_id=ghl_contact.source_id,
            autotask_id=autotask_id,
            proposed_change={"incoming": contact_dict(ghl_contact)},
            detected_reason=reason,
            correlation_id=correlation_id,
        ),
    )
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CONFLICT,
        entity_type="contact",
        entity_ref=ghl_contact.source_id,
        status=TransactionStatus.CONFLICT,
        summary=f"{reason} -> approval #{approval.id}",
    )
    return ContactSyncOutcome(
        "approval", correlation_id, autotask_id=autotask_id, ghl_id=ghl_contact.source_id, approval_ids=[approval.id]
    )
