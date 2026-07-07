"""Full database schema (Spec §3.4).

The *complete* schema is defined up front — every table Stage 1 needs, plus the
forward-looking tables for later stages — so "all databases built from the
beginning" is satisfied while only the Contacts flow is wired in Stage 1.

All models are engine-portable (see ``base.py``): they run on local Postgres now
and Azure SQL at deploy time with no changes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    JSONColumn,
    TimestampTZ,
    created_at_column,
    id_column,
    updated_at_column,
    utcnow,
)
from .enums import (
    Actor,
    ApprovalStatus,
    ApprovalType,
    CanonicalEntityType,
    Direction,
    Environment,
    Operation,
    Severity,
    System,
    TransactionStatus,
)


def _enum(py_enum, name: str):
    """Portable enum column: stored as VARCHAR + CHECK (native_enum=False)."""
    return Enum(py_enum, native_enum=False, validate_strings=True, name=name, length=40)


# ─────────────────────────────────────────────────────────────────────────────
# entity_mapping — the cross-system identity spine. (Spec §3.4)
# This table is what stops duplicate record creation.
# ─────────────────────────────────────────────────────────────────────────────
class EntityMapping(Base):
    __tablename__ = "entity_mapping"

    id: Mapped[int] = id_column()
    canonical_entity_type: Mapped[str] = mapped_column(
        _enum(CanonicalEntityType, "canonical_entity_type"), nullable=False
    )
    autotask_entity_type: Mapped[str | None] = mapped_column(String(60))
    autotask_id: Mapped[str | None] = mapped_column(String(80))
    ghl_entity_type: Mapped[str | None] = mapped_column(String(60))
    ghl_id: Mapped[str | None] = mapped_column(String(80))
    environment: Mapped[str] = mapped_column(_enum(Environment, "environment"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    last_synced_at: Mapped[datetime | None] = mapped_column(TimestampTZ)

    __table_args__ = (
        # A given Autotask record links once per canonical type per environment, and
        # likewise for GHL — these uniqueness rules prevent duplicate links.
        UniqueConstraint(
            "environment",
            "canonical_entity_type",
            "autotask_id",
            name="uq_entity_mapping_autotask",
        ),
        UniqueConstraint(
            "environment",
            "canonical_entity_type",
            "ghl_id",
            name="uq_entity_mapping_ghl",
        ),
        Index("ix_entity_mapping_lookup", "environment", "canonical_entity_type"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# approval_queue — every gated/ambiguous change awaiting a human decision. (§5, §11)
# ─────────────────────────────────────────────────────────────────────────────
class ApprovalQueue(Base):
    __tablename__ = "approval_queue"

    id: Mapped[int] = id_column()
    status: Mapped[str] = mapped_column(
        _enum(ApprovalStatus, "approval_status"),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    severity: Mapped[str] = mapped_column(_enum(Severity, "severity"), nullable=False)
    approval_type: Mapped[str] = mapped_column(_enum(ApprovalType, "approval_type"), nullable=False)
    canonical_entity_type: Mapped[str] = mapped_column(
        _enum(CanonicalEntityType, "approval_entity_type"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(_enum(System, "approval_source_system"), nullable=False)
    target_system: Mapped[str] = mapped_column(_enum(System, "approval_target_system"), nullable=False)
    autotask_id: Mapped[str | None] = mapped_column(String(80))
    ghl_id: Mapped[str | None] = mapped_column(String(80))
    # JSON: {"before": {...}, "after": {...}, plus matcher context like candidates}
    proposed_change: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    detected_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    decided_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    decided_by: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    environment: Mapped[str] = mapped_column(_enum(Environment, "approval_environment"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_approval_queue_pending", "environment", "status"),
        Index("ix_approval_queue_correlation", "correlation_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# audit_log — immutable before-state capture for every Autotask write. (Spec §5.4)
# APPEND-ONLY: never updated or deleted. This is what makes any change revertible.
# ─────────────────────────────────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = id_column()
    timestamp: Mapped[datetime] = mapped_column(TimestampTZ, default=utcnow, nullable=False)
    environment: Mapped[str] = mapped_column(_enum(Environment, "audit_environment"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(_enum(Operation, "audit_operation"), nullable=False)
    target_system: Mapped[str] = mapped_column(_enum(System, "audit_target_system"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(80))
    before_state: Mapped[dict | None] = mapped_column(JSONColumn)
    after_state: Mapped[dict | None] = mapped_column(JSONColumn)
    actor: Mapped[str] = mapped_column(_enum(Actor, "audit_actor"), nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (
        Index("ix_audit_log_entity", "environment", "entity_type", "entity_id"),
        Index("ix_audit_log_correlation", "correlation_id"),
        Index("ix_audit_log_timestamp", "timestamp"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# transaction_log — queryable feed of EVERY sync operation (Teams /transactions). (§3.4, §11)
# ─────────────────────────────────────────────────────────────────────────────
class TransactionLog(Base):
    __tablename__ = "transaction_log"

    id: Mapped[int] = id_column()
    timestamp: Mapped[datetime] = mapped_column(TimestampTZ, default=utcnow, nullable=False)
    environment: Mapped[str] = mapped_column(_enum(Environment, "tx_environment"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(_enum(Direction, "tx_direction"), nullable=False)
    operation: Mapped[str] = mapped_column(_enum(Operation, "tx_operation"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_ref: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(_enum(TransactionStatus, "tx_status"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONColumn)

    __table_args__ = (
        Index("ix_transaction_log_feed", "environment", "timestamp"),
        Index("ix_transaction_log_status", "environment", "status"),
        Index("ix_transaction_log_correlation", "correlation_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# stage_mapping — pipeline/stage translation (Flow 2). Schema present in Stage 1;
# the Opportunity/Ticket logic that uses it is a later stage. (Spec §3.4, §10.3)
# ─────────────────────────────────────────────────────────────────────────────
class StageMapping(Base):
    __tablename__ = "stage_mapping"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "stage_environment"), nullable=False)
    ghl_pipeline_id: Mapped[str] = mapped_column(String(80), nullable=False)
    ghl_stage_id: Mapped[str] = mapped_column(String(80), nullable=False)
    autotask_entity: Mapped[str] = mapped_column(String(40), nullable=False)  # opportunity|ticket
    autotask_status_value: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(_enum(Direction, "stage_direction"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(TimestampTZ)

    __table_args__ = (
        UniqueConstraint(
            "environment",
            "ghl_pipeline_id",
            "ghl_stage_id",
            "direction",
            name="uq_stage_mapping",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# classification_sync — forward-looking (Stage 3). Tracks Autotask→GHL
# classification attribute pushes so segmented campaigns work. (Spec §3.4, §8.3)
# ─────────────────────────────────────────────────────────────────────────────
class ClassificationSync(Base):
    __tablename__ = "classification_sync"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "class_environment"), nullable=False)
    autotask_entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    autotask_id: Mapped[str] = mapped_column(String(80), nullable=False)
    ghl_id: Mapped[str | None] = mapped_column(String(80))
    attribute: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. customer_type, tier
    value: Mapped[str | None] = mapped_column(Text)
    pushed_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        Index("ix_classification_sync_entity", "environment", "autotask_entity_type", "autotask_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# connector_registry — forward-looking (platform). Declared connectors and their
# capabilities so future systems plug in without schema changes. (Spec §3.4, §7)
# ─────────────────────────────────────────────────────────────────────────────
class ConnectorRegistry(Base):
    __tablename__ = "connector_registry"

    id: Mapped[int] = id_column()
    connector_key: Mapped[str] = mapped_column(String(60), nullable=False)  # autotask|ghl|3cx|...
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(_enum(Environment, "connector_environment"), nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONColumn, nullable=False)  # read/write, webhooks, rate limits
    supported_entities: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("environment", "connector_key", name="uq_connector_registry"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# processed_events — idempotency ledger. Unique on (event_id, source_system) to
# guarantee exactly-once handling. (Spec §3.4, §12.1)
# ─────────────────────────────────────────────────────────────────────────────
class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    id: Mapped[int] = id_column()
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_version: Mapped[str | None] = mapped_column(String(80))
    source_system: Mapped[str] = mapped_column(_enum(System, "pe_source_system"), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(TimestampTZ, default=utcnow, nullable=False)
    environment: Mapped[str] = mapped_column(_enum(Environment, "pe_environment"), nullable=False)

    __table_args__ = (
        UniqueConstraint("event_id", "source_system", name="uq_processed_events"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# sync_cursor — per-entity polling positions for the Autotask sweep (Spec §4,
# §12.1: threadless pagination). One row per (environment, system, entity).
# ─────────────────────────────────────────────────────────────────────────────
class SyncCursor(Base):
    __tablename__ = "sync_cursor"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "sc_environment"), nullable=False)
    source_system: Mapped[str] = mapped_column(_enum(System, "sc_source_system"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)  # contact|deal|service_item
    cursor: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("environment", "source_system", "entity_type", name="uq_sync_cursor"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# sync_criteria — operator-defined filters for WHICH Autotask customers are
# mirrored to GHL (contacts + classification push). Rules AND together against
# the contact's Account fields; no active rules = sync everything. Managed via
# the /admin UI.
# ─────────────────────────────────────────────────────────────────────────────
class SyncCriteria(Base):
    __tablename__ = "sync_criteria"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "crit_environment"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, default="contact")
    field: Mapped[str] = mapped_column(String(80), nullable=False)   # Account field, e.g. companyType
    operator: Mapped[str] = mapped_column(String(10), nullable=False)  # eq|ne|in|not_in
    value: Mapped[str] = mapped_column(Text, nullable=False)         # comma-separated for in/not_in
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        Index("ix_sync_criteria_env", "environment", "entity_type", "active"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Portal: saved sync profiles, job records, dry-run snapshots, settings.
# A PROFILE is a saved criteria+schedule configuration; every execution (dry or
# live, manual or scheduled) is a JOB row; every dry-run stores a SNAPSHOT so
# the safety rules can compare what changed between runs.
# ─────────────────────────────────────────────────────────────────────────────
class SyncProfile(Base):
    __tablename__ = "sync_profiles"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "sp_environment"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_type: Mapped[str] = mapped_column(String(40), nullable=False, default="contacts")
    criteria_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    criteria_hash: Mapped[str | None] = mapped_column(String(64))
    selected_customer_ids: Mapped[dict | None] = mapped_column(JSONColumn)  # explicit include list
    selected_contact_ids: Mapped[dict | None] = mapped_column(JSONColumn)
    dry_run_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_approval_before_live_sync: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    approved_by: Mapped[str | None] = mapped_column(String(120))
    # pending | dry_run_required | review_required | approved
    review_state: Mapped[str] = mapped_column(String(30), nullable=False, default="dry_run_required")
    review_reason: Mapped[str | None] = mapped_column(Text)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_type: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    schedule_config: Mapped[dict | None] = mapped_column(JSONColumn)
    last_dry_run_job_id: Mapped[int | None] = mapped_column(Integer)
    last_live_sync_job_id: Mapped[int | None] = mapped_column(Integer)
    last_run_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    next_run_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    created_by: Mapped[str | None] = mapped_column(String(120))
    updated_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (UniqueConstraint("environment", "name", name="uq_sync_profiles_name"),)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "sj_environment"), nullable=False)
    profile_id: Mapped[int | None] = mapped_column(Integer)  # null = ad-hoc job
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # dry_run | live
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # manual|scheduled|retry|system
    started_by: Mapped[str | None] = mapped_column(String(120))
    scheduled_for: Mapped[datetime | None] = mapped_column(TimestampTZ)
    started_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    ended_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict | None] = mapped_column(JSONColumn)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_sync_jobs_profile", "environment", "profile_id", "created_at"),)


class SyncProfileSnapshot(Base):
    __tablename__ = "sync_profile_snapshots"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "sps_environment"), nullable=False)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    criteria_snapshot: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    matched_customer_ids: Mapped[dict] = mapped_column(JSONColumn, nullable=False)   # {"ids": []}
    matched_contact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_ids: Mapped[dict | None] = mapped_column(JSONColumn)
    summary_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_sps_profile", "environment", "profile_id", "created_at"),)


class SyncExclusion(Base):
    """Per-record sync disable: an excluded account/contact is never mirrored
    outbound, regardless of criteria. Managed from the portal detail views."""

    __tablename__ = "sync_exclusions"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "se_environment"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # account | contact
    autotask_id: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        UniqueConstraint("environment", "entity_type", "autotask_id", name="uq_sync_exclusions"),
    )


class PortalSetting(Base):
    __tablename__ = "portal_settings"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "ps_environment"), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (UniqueConstraint("environment", "key", name="uq_portal_settings"),)


# ─────────────────────────────────────────────────────────────────────────────
# oauth_token_store — persisted OAuth tokens for LOCAL DEV so the GHL grant
# survives API restarts (GHL rotates the refresh token on every refresh; losing
# it forces a manual re-auth). ⚠️ In production this moves to Azure Key Vault
# via Managed Identity (Spec §12.3) — this table is the local stand-in.
# ─────────────────────────────────────────────────────────────────────────────
class OAuthTokenStore(Base):
    __tablename__ = "oauth_token_store"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "tok_environment"), nullable=False)
    system: Mapped[str] = mapped_column(_enum(System, "tok_system"), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("environment", "system", name="uq_oauth_token_store"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# circuit_breaker_state — supports the Spec §5.5 circuit breaker. Single row per
# (environment, target_system); tripped writes are paused until reset.
# ─────────────────────────────────────────────────────────────────────────────
class CircuitBreakerState(Base):
    __tablename__ = "circuit_breaker_state"

    id: Mapped[int] = id_column()
    environment: Mapped[str] = mapped_column(_enum(Environment, "cb_environment"), nullable=False)
    target_system: Mapped[str] = mapped_column(_enum(System, "cb_target_system"), nullable=False)
    tripped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tripped_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("environment", "target_system", name="uq_circuit_breaker"),
    )
