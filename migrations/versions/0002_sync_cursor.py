"""Add sync_cursor — polling positions for the Autotask sweep (Flow 2).

Revision ID: 0002_sync_cursor
Revises: 0001_initial
Create Date: 2026-07-07

``checkfirst=True`` keeps this idempotent for databases created fresh from the
metadata-based baseline (0001 already emits every current table there).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db import models  # noqa: F401  (registers tables on Base.metadata)
from integration.db.models import SyncCursor

revision: str = "0002_sync_cursor"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    SyncCursor.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SyncCursor.__table__.drop(bind=op.get_bind(), checkfirst=True)
