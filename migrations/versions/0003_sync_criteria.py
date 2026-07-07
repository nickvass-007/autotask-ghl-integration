"""Add sync_criteria — operator-defined customer sync filters (admin UI).

Revision ID: 0003_sync_criteria
Revises: 0002_sync_cursor
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db import models  # noqa: F401  (registers tables on Base.metadata)
from integration.db.models import SyncCriteria

revision: str = "0003_sync_criteria"
down_revision: str | None = "0002_sync_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    SyncCriteria.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SyncCriteria.__table__.drop(bind=op.get_bind(), checkfirst=True)
