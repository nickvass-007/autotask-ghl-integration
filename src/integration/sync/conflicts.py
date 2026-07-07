"""Field-level conflict detection for GHL -> Autotask (Spec §5.2, §9.2).

Given an incoming GHL contact and the existing Autotask contact, classify each
mapped field into:

- **additive** — Autotask field is empty; GHL fills it (auto-apply if configured,
  always logged).
- **conflict** — Autotask field is populated and differs; NEVER auto-overwritten →
  routed to approval, carrying the per-field severity from the mapping.
- **no-op** — equal, or GHL has nothing to contribute, or rule forbids the write.

``company_name`` is intentionally excluded here — it is handled by the Account
linkage guard (Spec §9.3), never as a plain field write.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..canonical.entities import CanonicalContact
from ..config.mapping import ContactsMapping
from ..db.enums import Severity


@dataclass(slots=True)
class FieldConflict:
    canonical: str
    ghl_field: str
    before: object
    after: object
    severity: Severity


@dataclass(slots=True)
class FieldChanges:
    additive: dict[str, object]          # canonical field -> new value (fills empty AT field)
    conflicts: list[FieldConflict]       # populated AT field differs -> approval

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


def _empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _norm(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def detect_changes(
    incoming: CanonicalContact,
    existing: CanonicalContact,
    mapping: ContactsMapping,
) -> FieldChanges:
    additive: dict[str, object] = {}
    conflicts: list[FieldConflict] = []

    for rule in mapping.fields:
        # company_name -> linkage guard; create_only fields are never updated.
        if rule.canonical == "company_name" or rule.create_only:
            continue

        new_value = getattr(incoming, rule.canonical, None)
        if _empty(new_value):
            continue  # GHL has nothing to contribute for this field

        old_value = getattr(existing, rule.canonical, None)

        if _empty(old_value):
            # Autotask field empty -> additive (Spec §5.2), if the rule allows it.
            if rule.ghl_to_at_empty == "add":
                additive[rule.canonical] = new_value
            continue

        if _norm(old_value) == _norm(new_value):
            continue  # already equal

        # Populated and differs -> never auto-overwrite (Spec §5.2).
        if rule.ghl_to_at_conflict == "approval":
            conflicts.append(
                FieldConflict(
                    canonical=rule.canonical,
                    ghl_field=rule.ghl,
                    before=old_value,
                    after=new_value,
                    severity=rule.severity,
                )
            )
        # 'never' / 'n/a' -> silently skip (logged at a higher level)

    return FieldChanges(additive=additive, conflicts=conflicts)
