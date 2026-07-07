"""Stage C — Conversion / customer-onboarding handoff (Spec §8.2).

Trigger: a GHL Opportunity in the Sales pipeline reaches the designated
**closed-won** stage (the stage map's ``closed_won_stage_id``). Onboarding into
the protected system of record is ALWAYS a deliberate, approved act:

1. Dedupe the GHL contact/company against Autotask (reusing Flow-1 matching).
2. Clean miss  → ``customer_onboarding`` approval to CREATE Account + Contact.
   Possible match → ``customer_onboarding`` approval to LINK to the candidate.
   Already mapped → nothing to approve; just (re-)stamp the GHL contact.
3. On approval: create/link the Autotask records, write the identity-spine rows,
   capture audit, and **stamp the GHL contact** with the configurable
   ``Converted — Managed in Autotask`` tag so the engagement layer knows the
   record now lives in Autotask (Spec §8.2 step 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalCompany, CanonicalContact, CanonicalDeal
from ..config.settings import get_settings
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
from ..db.models import ApprovalQueue, EntityMapping
from .approvals import ApprovalRequest, raise_approval
from .audit import canonical_to_dict, record_before_state, record_transaction
from .matching import MatchClass, match_contact

log = get_logger(__name__)


@dataclass(slots=True)
class OnboardingOutcome:
    action: str            # approval | stamped | created | linked | skipped
    correlation_id: str
    autotask_id: str | None = None
    ghl_id: str | None = None
    approval_ids: list[int] = field(default_factory=list)
    detail: str = ""


def _contact_mapping_by_ghl(session: Session, ghl_contact_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
        EntityMapping.ghl_id == ghl_contact_id,
    )
    return session.execute(stmt).scalar_one_or_none()


async def _stamp_converted(session: Session, *, ghl, ghl_contact_id: str, correlation_id: str) -> None:
    """Apply the configurable converted stamp (tag) to the GHL contact."""
    tag = get_settings().converted_tag
    await ghl.add_tags(ghl_contact_id, [tag])
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.UPDATE,
        entity_type="contact",
        entity_ref=ghl_contact_id,
        status=TransactionStatus.SUCCESS,
        summary=f"Stamped GHL contact {ghl_contact_id} as {tag!r} (Stage C, §8.2)",
    )


async def handle_closed_won(
    session: Session,
    *,
    ghl_deal: CanonicalDeal,
    autotask,
    ghl,
    correlation_id: str | None = None,
) -> OnboardingOutcome:
    """The boundary event: raise the onboarding approval (or re-stamp if already
    converted). NOTHING is written to Autotask here — only on approval."""
    correlation_id = correlation_id or new_correlation_id()
    ghl_contact_id = ghl_deal.contact_id
    if not ghl_contact_id:
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.SKIP,
            entity_type="deal",
            entity_ref=ghl_deal.source_id,
            status=TransactionStatus.SKIPPED,
            summary="Closed-won deal has no contact — nothing to onboard",
        )
        return OnboardingOutcome("skipped", correlation_id, detail="no contact on deal")

    # Already converted? Just make sure the stamp is present (idempotent).
    existing_link = _contact_mapping_by_ghl(session, ghl_contact_id)
    if existing_link and existing_link.autotask_id:
        await _stamp_converted(
            session, ghl=ghl, ghl_contact_id=ghl_contact_id, correlation_id=correlation_id
        )
        return OnboardingOutcome(
            "stamped",
            correlation_id,
            autotask_id=existing_link.autotask_id,
            ghl_id=ghl_contact_id,
            detail="already mapped — stamp refreshed",
        )

    ghl_contact = await ghl.get_contact(ghl_contact_id)
    if ghl_contact is None:
        return OnboardingOutcome("skipped", correlation_id, detail="GHL contact not found")

    # Dedupe with the SAME engine as Flow 1 (Spec §8.2 step 1).
    candidates = await autotask.find_contacts(email=ghl_contact.email)
    match = match_contact(ghl_contact, candidates)
    accounts = await autotask.find_accounts(ghl_contact.company_name or "")

    approval = raise_approval(
        session,
        ApprovalRequest(
            approval_type=ApprovalType.CUSTOMER_ONBOARDING,
            severity=Severity.HIGH,
            canonical_entity_type=CanonicalEntityType.CONTACT,
            source_system=System.GHL,
            target_system=System.AUTOTASK,
            ghl_id=ghl_contact_id,
            autotask_id=match.matched.source_id if match.matched else None,
            proposed_change={
                "deal": canonical_to_dict(ghl_deal),
                "incoming": canonical_to_dict(ghl_contact),
                "match_rule": match.rule,
                "match_class": match.classification.value,
                "contact_candidates": [
                    canonical_to_dict(c) for c in (match.candidates or ([match.matched] if match.matched else []))
                ],
                "account_candidates": [
                    {"id": a.company_id, "name": a.name} for a in accounts
                ],
            },
            detected_reason=(
                f"New customer onboarding: deal {ghl_deal.name!r} closed-won — "
                + (
                    "possible existing records found; confirm LINK or CREATE."
                    if match.classification is not MatchClass.CLEAN_MISS
                    else "create Autotask Account + Contact? (Accounts are never auto-created, §8.2)"
                )
            ),
            correlation_id=correlation_id,
        ),
    )
    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CONFLICT,
        entity_type="deal",
        entity_ref=ghl_deal.source_id,
        status=TransactionStatus.CONFLICT,
        summary=f"Closed-won -> customer onboarding approval #{approval.id} (Stage C)",
    )
    return OnboardingOutcome(
        "approval", correlation_id, ghl_id=ghl_contact_id, approval_ids=[approval.id]
    )


async def apply_onboarding(
    session: Session,
    *,
    approval: ApprovalQueue,
    autotask,
    ghl,
    decided_by: str,
    chosen_account_id: str | None = None,
) -> "OnboardingOutcome":
    """Apply an APPROVED onboarding: create/link Account + Contact, write the
    identity spine, audit, and stamp the GHL contact (Spec §8.2 step 3).

    The approval row is already marked decided by the caller."""
    corr = approval.correlation_id
    proposed = approval.proposed_change or {}
    incoming = proposed.get("incoming") or {}
    ghl_contact_id = approval.ghl_id

    contact = CanonicalContact(source_system=System.GHL, source_id=ghl_contact_id)
    for key, value in incoming.items():
        if hasattr(contact, key) and key not in {"source_system", "entity_type", "extra", "tags"}:
            setattr(contact, key, value)

    # 1) Resolve the Account: operator's pick, else create (the approved act).
    account_id = chosen_account_id
    if account_id is None:
        company = CanonicalCompany(
            source_system=System.GHL, name=contact.company_name or contact.full_name()
        )
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
            result="onboarding_approved",
        )
        acct = await autotask.create_account(company)
        account_id = acct.external_id
        record_transaction(
            session,
            correlation_id=corr,
            direction=Direction.GHL_TO_AUTOTASK,
            operation=Operation.CREATE,
            entity_type="company",
            entity_ref=account_id,
            status=TransactionStatus.SUCCESS,
            summary=f"Onboarding: created Autotask Account {account_id} (approved by {decided_by})",
        )

    # 2) Contact: link the matched one, else create under the resolved Account.
    autotask_contact_id = approval.autotask_id
    if autotask_contact_id is None:
        contact.company_id = account_id
        record_before_state(
            session,
            correlation_id=corr,
            operation=Operation.CREATE,
            target_system=System.AUTOTASK,
            entity_type="Contact",
            entity_id=None,
            before=None,
            after=contact,
            actor=Actor.APPROVAL,
            result="onboarding_approved",
        )
        result = await autotask.create_contact(contact)
        if not result.ok:
            record_transaction(
                session,
                correlation_id=corr,
                direction=Direction.GHL_TO_AUTOTASK,
                operation=Operation.ERROR,
                entity_type="contact",
                entity_ref=ghl_contact_id,
                status=TransactionStatus.ERROR,
                summary=f"Onboarding contact create failed: {result.detail}",
            )
            return OnboardingOutcome("skipped", corr, detail=result.detail)
        autotask_contact_id = result.external_id

    # 3) Identity spine + stamp.
    session.add(
        EntityMapping(
            canonical_entity_type=CanonicalEntityType.CONTACT,
            autotask_entity_type="Contact",
            autotask_id=autotask_contact_id,
            ghl_entity_type="contact",
            ghl_id=ghl_contact_id,
            environment=get_settings().environment,
            last_synced_at=utcnow(),
        )
    )
    session.flush()
    record_transaction(
        session,
        correlation_id=corr,
        direction=Direction.GHL_TO_AUTOTASK,
        operation=Operation.CREATE,
        entity_type="contact",
        entity_ref=autotask_contact_id,
        status=TransactionStatus.SUCCESS,
        summary=(
            f"Onboarding complete: GHL {ghl_contact_id} -> Autotask Contact "
            f"{autotask_contact_id} on Account {account_id} (by {decided_by})"
        ),
    )
    if ghl is not None and ghl_contact_id:
        await _stamp_converted(
            session, ghl=ghl, ghl_contact_id=ghl_contact_id, correlation_id=corr
        )
    return OnboardingOutcome(
        "linked" if approval.autotask_id else "created",
        corr,
        autotask_id=autotask_contact_id,
        ghl_id=ghl_contact_id,
    )
