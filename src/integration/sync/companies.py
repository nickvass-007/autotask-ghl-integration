"""Company mirror — Autotask Account → GHL Business (Autotask-authoritative).

Free-flowing AT→GHL like the contact mirror: the GHL Business is a read-mostly
representation of the protected Autotask Account. The COMPANY row in
``entity_mapping`` is the identity spine; contacts link to their Business via
``businessId`` on create/update (see GHLConnector._payload).

⚠️ Nothing here ever writes Accounts back into Autotask — Account creation
remains approval-only (Spec §9.3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.entities import CanonicalCompany
from ..config.settings import get_settings
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import (
    CanonicalEntityType,
    Direction,
    Operation,
    System,
    TransactionStatus,
)
from ..db.models import EntityMapping
from .audit import record_transaction

log = get_logger(__name__)


def company_mapping_by_autotask(session: Session, account_id: str) -> EntityMapping | None:
    env = get_settings().environment
    stmt = select(EntityMapping).where(
        EntityMapping.environment == env,
        EntityMapping.canonical_entity_type == CanonicalEntityType.COMPANY,
        EntityMapping.autotask_id == account_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def company_from_account_raw(raw: dict) -> CanonicalCompany:
    company = CanonicalCompany(
        source_system=System.AUTOTASK,
        source_id=str(raw.get("id")),
        name=raw.get("companyName"),
        website=raw.get("webAddress"),
        phone=raw.get("phone"),
        address1=raw.get("address1"),
        address2=raw.get("address2"),
        city=raw.get("city"),
        state=raw.get("state"),
        postal_code=raw.get("postalCode"),
    )
    company.company_id = str(raw.get("id"))
    return company


async def mirror_autotask_account(
    session: Session,
    *,
    company: CanonicalCompany,
    ghl,
    correlation_id: str | None = None,
) -> tuple[str, str | None]:
    """Upsert the GHL Business for an Autotask Account.

    Returns (action, ghl_business_id). Adopts an existing same-named Business
    when present so re-runs and pre-existing data don't duplicate."""
    correlation_id = correlation_id or new_correlation_id()
    account_id = company.source_id or company.company_id

    link = company_mapping_by_autotask(session, account_id or "")
    if link and link.ghl_id:
        await ghl.update_business(link.ghl_id, ghl.business_fields(company))
        link.last_synced_at = utcnow()
        action, business_id = "updated", link.ghl_id
    else:
        existing = await ghl.find_businesses(company.name or "")
        if existing:
            business_id = str(existing[0]["id"])
            action = "adopted"
        else:
            result = await ghl.create_business(company)
            business_id = result.external_id
            action = "created"
        session.add(
            EntityMapping(
                canonical_entity_type=CanonicalEntityType.COMPANY,
                autotask_entity_type="Account",
                autotask_id=account_id,
                ghl_entity_type="business",
                ghl_id=business_id,
                environment=get_settings().environment,
                last_synced_at=utcnow(),
            )
        )
        session.flush()

    record_transaction(
        session,
        correlation_id=correlation_id,
        direction=Direction.AUTOTASK_TO_GHL,
        operation=Operation.UPDATE if action == "updated" else Operation.CREATE,
        entity_type="company",
        entity_ref=business_id,
        status=TransactionStatus.SUCCESS,
        summary=f"Autotask Account {account_id} -> GHL Business {action} ({company.name!r})",
    )
    return action, business_id
