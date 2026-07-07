"""Initial schema — full Stage-1 + forward-looking tables (Spec §3.4).

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-18

The baseline migration creates the entire schema from the single source of truth
(the SQLAlchemy models' metadata). Building the baseline from metadata keeps the
models and the migration from drifting, and is engine-portable: the same call
emits Postgres DDL locally and Azure SQL DDL in production. Subsequent migrations
are explicit, incremental ``alembic revision --autogenerate`` diffs against these
models.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db.base import Base
from integration.db import models  # noqa: F401  (registers all tables on Base.metadata)

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
