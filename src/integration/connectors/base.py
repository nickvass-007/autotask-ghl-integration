"""The connector contract (Spec §7.2).

Each external system implements this common interface plus a declared field schema
+ capabilities (read/write, supports-webhooks, rate limits). The core sync engine
talks **only** to this interface and the canonical model, never to a vendor SDK
directly — that's what lets system N plug in without rewriting the core.

Stage 1 implements ``authenticate()`` fully for Autotask and GHL, and the Contacts
read/write/dedupe paths used by the Contacts flow. ``fetch_changes()`` /
``handle_webhook()`` for Opportunities/Tickets are defined here but realised in
later stages.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..canonical.entities import CanonicalContact, CanonicalEntity
from ..db.enums import CanonicalEntityType, Environment, System


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Declared rate limits so the engine can pace itself (Spec §12.1)."""

    max_requests: int
    per_seconds: int
    daily_cap: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    """What a connector can do — persisted to ``connector_registry`` (Spec §7.2)."""

    key: str  # "autotask" | "ghl" | ...
    display_name: str
    can_read: bool
    can_write: bool
    supports_webhooks: bool
    supported_entities: tuple[CanonicalEntityType, ...]
    rate_limit: RateLimit


@dataclass(slots=True)
class ChangeSet:
    """A page of changes pulled from a system during polling (Spec §4)."""

    entities: list[CanonicalEntity] = field(default_factory=list)
    cursor: str | None = None  # opaque continuation (e.g. Autotask "id > lastId")
    has_more: bool = False


@dataclass(slots=True)
class PushResult:
    """Outcome of writing one entity to a target system."""

    ok: bool
    external_id: str | None = None
    detail: str = ""


class Connector(ABC):
    """Common contract every external system implements."""

    capabilities: ConnectorCapabilities

    def __init__(self, environment: Environment) -> None:
        self.environment = environment

    @property
    @abstractmethod
    def system(self) -> System:  # pragma: no cover - trivial
        ...

    # ── Auth (Stage 1) ──
    @abstractmethod
    async def authenticate(self) -> None:
        """Establish credentials/session. Autotask: zone detection + API-only user.
        GHL: OAuth 2.0 with refresh-token rotation. Raises on failure."""

    @abstractmethod
    async def health(self) -> bool:
        """Cheap call proving the connector is authenticated and reachable."""

    # ── Contacts read/dedupe (Stage 1) ──
    @abstractmethod
    async def find_contacts(self, *, email: str | None = None) -> list[CanonicalContact]:
        """Look up candidate contacts for dedupe (Spec §9.1). Connectors that don't
        own contacts return an empty list."""

    @abstractmethod
    async def get_contact(self, external_id: str) -> CanonicalContact | None:
        """Fetch a single contact by this system's id (for before-state capture)."""

    # ── Write (Stage 1: Contacts) ──
    @abstractmethod
    async def create_contact(self, contact: CanonicalContact) -> PushResult:
        ...

    @abstractmethod
    async def update_contact(
        self, external_id: str, changes: dict[str, object]
    ) -> PushResult:
        """Apply only the given field changes (post-gating) to an existing contact."""

    # ── Polling / webhooks (defined now; Opportunity/Ticket realisation is Stage 2) ──
    @abstractmethod
    async def fetch_changes(
        self, entity_type: CanonicalEntityType, *, cursor: str | None = None
    ) -> ChangeSet:
        ...

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify an inbound webhook signature (Spec §4, §12.1). Autotask has no
        comprehensive webhooks, so its implementation returns False (poll instead)."""
