"""System-agnostic canonical entities (Spec §7.1).

These are the *hub*. A GHL contact and an Autotask Contact+Account both map to
``CanonicalContact`` (+ ``CanonicalCompany``); neither connector knows about the
other. ``Deal`` and ``ServiceItem`` are defined now for Flow 2 (Stage 2) and an
extensible base supports a future ``Communication`` entity (Spec §8.4) — but only
Contact/Company are exercised by the Stage-1 Contacts flow.

Each entity carries a ``source_system`` + ``source_id`` (provenance) and a free
``extra`` bag for connector-specific custom fields that are config-mapped rather
than first-class here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.enums import CanonicalEntityType, System


@dataclass(slots=True)
class CanonicalEntity:
    """Base for all canonical entities (extensible — e.g. future Communication)."""

    source_system: System
    source_id: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    #: Subclasses set this; used for entity_mapping + routing.
    entity_type: CanonicalEntityType = field(init=False, default=CanonicalEntityType.CONTACT)


@dataclass(slots=True)
class CanonicalCompany(CanonicalEntity):
    """Maps to Autotask Account / the GHL contact's company text field."""

    name: str | None = None
    website: str | None = None
    phone: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    company_id: str | None = None  # resolved Autotask Account id (for linkage)

    def __post_init__(self) -> None:
        self.entity_type = CanonicalEntityType.COMPANY


@dataclass(slots=True)
class CanonicalContact(CanonicalEntity):
    """Maps to Autotask Contact (+ linked Account) and the flat GHL contact."""

    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    mobile: str | None = None
    address1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    title: str | None = None
    company_name: str | None = None
    website: str | None = None  # maps to Account.webAddress (gated, Spec §370 mapping)
    company_id: str | None = None  # resolved link to a CanonicalCompany / Autotask Account
    date_of_birth: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str | None = None  # create-only provenance (e.g. "ghl")

    def __post_init__(self) -> None:
        self.entity_type = CanonicalEntityType.CONTACT

    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


@dataclass(slots=True)
class CanonicalDeal(CanonicalEntity):
    """Covers Autotask Opportunity / GHL Opportunity (Flow 2 — Stage 2, schema only)."""

    name: str | None = None
    monetary_value: float | None = None
    stage: str | None = None
    status: str | None = None
    contact_id: str | None = None
    account_id: str | None = None
    owner: str | None = None
    close_date: str | None = None

    def __post_init__(self) -> None:
        self.entity_type = CanonicalEntityType.DEAL


@dataclass(slots=True)
class CanonicalServiceItem(CanonicalEntity):
    """Covers Autotask Ticket (Flow 2 — Stage 2, schema only)."""

    title: str | None = None
    status: str | None = None
    queue: str | None = None
    account_id: str | None = None
    contact_id: str | None = None

    def __post_init__(self) -> None:
        self.entity_type = CanonicalEntityType.SERVICE_ITEM
