"""SQLAlchemy declarative base and engine-portable column helpers.

Portability constraint (Spec §3.4): the SAME models must generate the schema on
local Postgres AND on Azure SQL in production. We therefore avoid engine-specific
types:

- ``JSONColumn``  -> SQLAlchemy generic ``JSON``: maps to ``jsonb``-compatible
  storage on Postgres and ``NVARCHAR(MAX)`` (JSON) on Azure SQL.
- ``TimestampTZ`` -> ``DateTime(timezone=True)``; we always store **UTC**.
- Enumerations use ``Enum(..., native_enum=False)`` so they become a portable
  ``VARCHAR`` + ``CHECK`` constraint instead of a Postgres-only ``ENUM`` type.
- Primary keys are plain auto-increment integers (``Identity``) — portable and
  index-friendly on both engines.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Identity, Integer, JSON
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import TypeEngine


def utcnow() -> datetime:
    """Timezone-aware current time in UTC. Used as the default for all timestamps."""
    return datetime.now(UTC)


# Generic JSON: portable across Postgres (jsonb) and Azure SQL (NVARCHAR/JSON).
JSONColumn: type[TypeEngine] = JSON
# Timezone-aware timestamp; we always persist UTC.
TimestampTZ = DateTime(timezone=True)

# Portable auto-increment PK: BIGINT IDENTITY on Postgres/Azure SQL; INTEGER
# (rowid autoincrement) on SQLite, which is used for the offline test smoke.
_BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def id_column():
    return mapped_column(_BigIntPK, Identity(), primary_key=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def created_at_column():
    return mapped_column(TimestampTZ, default=utcnow, nullable=False)


def updated_at_column():
    return mapped_column(TimestampTZ, default=utcnow, onupdate=utcnow, nullable=False)
