"""Field-mapping + source-of-truth loader (Spec §5, §9.2).

Loads ``config/contacts.mapping.yaml`` into typed rules the sync engine uses for
conflict detection and routing. Nothing about field behaviour is hardcoded in
Python — change the YAML, change the behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ..db.enums import Severity

# config/ lives at the repo root: <repo>/config/*.yaml
_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@dataclass(frozen=True, slots=True)
class FieldRule:
    canonical: str
    ghl: str
    autotask: str
    at_to_ghl: str            # overwrite | skip
    ghl_to_at_empty: str      # add | skip | link
    ghl_to_at_conflict: str   # approval | never | n/a
    severity: Severity
    create_only: bool = False
    optional: bool = False
    notes: str = ""

    @property
    def autotask_entity(self) -> str:
        """'Contact' or 'Account' — the object this field lives on."""
        return self.autotask.split(".", 1)[0]

    @property
    def autotask_field(self) -> str:
        """The field name without the entity prefix."""
        return self.autotask.split(".", 1)[1]


@dataclass(frozen=True, slots=True)
class ContactsMapping:
    auto_apply_additive: bool
    conflict_action: str
    fields: tuple[FieldRule, ...]
    do_not_sync: frozenset[str]

    def by_canonical(self, name: str) -> FieldRule | None:
        return next((f for f in self.fields if f.canonical == name), None)

    def by_ghl(self, name: str) -> FieldRule | None:
        return next((f for f in self.fields if f.ghl == name), None)


def _load_yaml(filename: str) -> dict:
    path = _CONFIG_DIR / filename
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def load_contacts_mapping() -> ContactsMapping:
    raw = _load_yaml("contacts.mapping.yaml")
    defaults = raw.get("defaults", {})
    fields = tuple(
        FieldRule(
            canonical=f["canonical"],
            ghl=f["ghl"],
            autotask=f["autotask"],
            at_to_ghl=f["at_to_ghl"],
            ghl_to_at_empty=f["ghl_to_at_empty"],
            ghl_to_at_conflict=f["ghl_to_at_conflict"],
            severity=Severity(f.get("severity", "low")),
            create_only=f.get("create_only", False),
            optional=f.get("optional", False),
            notes=f.get("notes", ""),
        )
        for f in raw.get("fields", [])
    )
    return ContactsMapping(
        auto_apply_additive=defaults.get("auto_apply_additive", True),
        conflict_action=defaults.get("conflict_action", "approval"),
        fields=fields,
        do_not_sync=frozenset(raw.get("do_not_sync", [])),
    )


@lru_cache
def load_opportunities_mapping() -> ContactsMapping:
    """canonical.Deal field rules (Spec §10.4) — same rule shape as Contacts, so
    conflict detection is reused verbatim across flows."""
    raw = _load_yaml("opportunities.mapping.yaml")
    defaults = raw.get("defaults", {})
    fields = tuple(
        FieldRule(
            canonical=f["canonical"],
            ghl=f["ghl"],
            autotask=f["autotask"],
            at_to_ghl=f["at_to_ghl"],
            ghl_to_at_empty=f["ghl_to_at_empty"],
            ghl_to_at_conflict=f["ghl_to_at_conflict"],
            severity=Severity(f.get("severity", "low")),
            create_only=f.get("create_only", False),
            optional=f.get("optional", False),
            notes=f.get("notes", ""),
        )
        for f in raw.get("fields", [])
    )
    return ContactsMapping(
        auto_apply_additive=defaults.get("auto_apply_additive", True),
        conflict_action=defaults.get("conflict_action", "approval"),
        fields=fields,
        do_not_sync=frozenset(raw.get("do_not_sync", [])),
    )


@lru_cache
def load_stage_mapping() -> dict:
    """Raw stage-mapping config (Spec §10.3). Consumed by Flow 2 in Stage 2; loaded
    here so startup validation can confirm the file parses."""
    return _load_yaml("stage_mapping.yaml")
