"""Enumerations shared across the schema and sync engine.

These are stored as portable VARCHARs (Enum(native_enum=False)) rather than
Postgres-native ENUM types, so the same schema runs on Azure SQL (Spec §3.4).
"""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class System(StrEnum):
    AUTOTASK = "autotask"
    GHL = "ghl"


class CanonicalEntityType(StrEnum):
    CONTACT = "contact"
    COMPANY = "company"
    DEAL = "deal"
    SERVICE_ITEM = "service_item"
    # Forward-looking (documented seam, not implemented in v1):
    COMMUNICATION = "communication"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Severity(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class ApprovalType(StrEnum):
    # Flow 1 — Contacts (Stage 1)
    CONTACT_FIELD_CONFLICT = "contact_field_conflict"
    CONTACT_POSSIBLE_DUPLICATE = "contact_possible_duplicate"
    ACCOUNT_LINK = "account_link"
    ACCOUNT_CREATE = "account_create"
    # Flow 2 / Stage C — present so the queue schema is complete (logic is later stages)
    OPPORTUNITY_CREATE = "opportunity_create"
    SALES_OUTCOME_CHANGE = "sales_outcome_change"
    UNMAPPED_STAGE = "unmapped_stage"
    AMOUNT_CONFLICT = "amount_conflict"
    CUSTOMER_ONBOARDING = "customer_onboarding"


class Operation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"
    CONFLICT = "conflict"
    ERROR = "error"
    BLOCK = "block"


class TransactionStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    ERROR = "error"
    BLOCKED = "blocked"


class Direction(StrEnum):
    AUTOTASK_TO_GHL = "autotask_to_ghl"
    GHL_TO_AUTOTASK = "ghl_to_autotask"


class Actor(StrEnum):
    SYSTEM = "system"
    APPROVAL = "approval"
    ADMIN = "admin"
