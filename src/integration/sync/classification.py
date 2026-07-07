"""Stage E — classification sync, Autotask → GHL (Spec §8.3).

Pushes selected Autotask Account classification attributes onto the linked GHL
contacts as tags / custom-field values, so GHL-native campaign tools can build
audiences ("mail-out to classified customers of a certain type").

- Direction: **Autotask → GHL only, authoritative.** GHL→Autotask for these
  fields is BLOCKED by construction — nothing here reads from GHL.
- Config-driven (``config/classification.yaml``): which attributes, whether they
  land as tags (``at:<attribute>/<value>``) and/or custom fields.
- ``classification_sync`` rows make the push idempotent: an attribute is only
  re-pushed when its value changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.mapping import _load_yaml
from ..config.settings import get_settings
from ..core.logging import get_logger, new_correlation_id
from ..db.base import utcnow
from ..db.enums import CanonicalEntityType, Direction, Operation, TransactionStatus
from ..db.models import ClassificationSync, EntityMapping
from .audit import record_transaction

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClassificationAttr:
    attribute: str
    autotask_entity: str
    autotask_field: str
    as_tag: bool
    ghl_custom_field: str


@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    tag_prefix: str
    lifecycle_tag: str
    attributes: tuple[ClassificationAttr, ...]


@lru_cache
def load_classification_config() -> ClassificationConfig:
    raw = _load_yaml("classification.yaml")
    defaults = raw.get("defaults", {})
    attrs = tuple(
        ClassificationAttr(
            attribute=a["attribute"],
            autotask_entity=a.get("autotask_entity", "Company"),
            autotask_field=a.get("autotask_field", ""),
            as_tag=bool(a.get("as_tag", False)),
            ghl_custom_field=str(a.get("ghl_custom_field") or ""),
        )
        for a in raw.get("attributes", [])
    )
    return ClassificationConfig(
        tag_prefix=defaults.get("tag_prefix", "at:"),
        lifecycle_tag=defaults.get("lifecycle_tag", "customer"),
        attributes=attrs,
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _state_row(
    session: Session, *, autotask_id: str, attribute: str
) -> ClassificationSync | None:
    env = get_settings().environment
    stmt = select(ClassificationSync).where(
        ClassificationSync.environment == env,
        ClassificationSync.autotask_entity_type == "Company",
        ClassificationSync.autotask_id == autotask_id,
        ClassificationSync.attribute == attribute,
    )
    return session.execute(stmt).scalar_one_or_none()


async def sync_classifications(
    session: Session,
    *,
    autotask,
    ghl,
    limit: int = 200,
) -> dict:
    """Sweep mapped contacts and push changed classification attributes to GHL.

    Idempotent per (account, attribute, value) via ``classification_sync``.
    Returns a summary dict for the daily digest."""
    cfg = load_classification_config()
    env = get_settings().environment
    correlation_id = new_correlation_id()

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

    # Picklist labels are fetched once per (entity, field) per sweep.
    label_cache: dict[tuple[str, str], dict[str, str]] = {}

    async def labels_for(attr: ClassificationAttr) -> dict[str, str]:
        key = ("Companies", attr.autotask_field)
        if key not in label_cache:
            label_cache[key] = await autotask.get_picklist_labels(*key)
        return label_cache[key]

    checked = pushed = 0
    for row in rows:
        checked += 1
        at_contact = await autotask.get_contact(row.autotask_id)
        if at_contact is None or at_contact.company_id is None:
            continue
        account = await autotask.get_account_raw(at_contact.company_id)
        if account is None:
            continue

        tags: list[str] = [cfg.lifecycle_tag]
        custom_fields: dict[str, object] = {}
        changed_attrs: list[str] = []

        for attr in cfg.attributes:
            if not attr.autotask_field:
                continue  # derived attributes land in a later stage
            raw_value = account.get(attr.autotask_field)
            if raw_value in (None, ""):
                continue
            labels = await labels_for(attr)
            label = labels.get(str(raw_value), str(raw_value))

            state = _state_row(session, autotask_id=at_contact.company_id, attribute=attr.attribute)
            if state is not None and state.value == label and state.pushed_at is not None:
                continue  # unchanged — idempotent skip
            if state is None:
                state = ClassificationSync(
                    environment=env,
                    autotask_entity_type="Company",
                    autotask_id=at_contact.company_id,
                    attribute=attr.attribute,
                )
                session.add(state)
            state.ghl_id = row.ghl_id
            state.value = label
            state.pushed_at = utcnow()
            changed_attrs.append(f"{attr.attribute}={label}")

            if attr.as_tag:
                tags.append(f"{cfg.tag_prefix}{_slug(attr.attribute)}/{_slug(label)}")
            if attr.ghl_custom_field:
                custom_fields[attr.ghl_custom_field] = label

        if not changed_attrs:
            continue
        # Tags are ADDITIVE in GHL; custom fields overwrite (AT-authoritative, §8.3).
        await ghl.add_tags(row.ghl_id, tags)
        if custom_fields:
            await ghl.update_custom_fields(row.ghl_id, custom_fields)
        pushed += 1
        record_transaction(
            session,
            correlation_id=correlation_id,
            direction=Direction.AUTOTASK_TO_GHL,
            operation=Operation.UPDATE,
            entity_type="contact",
            entity_ref=row.ghl_id,
            status=TransactionStatus.SUCCESS,
            summary=f"Classification push: {', '.join(changed_attrs)}",
            detail={"attributes": changed_attrs},
        )

    return {
        "correlation_id": correlation_id,
        "contacts_checked": checked,
        "contacts_pushed": pushed,
    }
