"""Per-record sync exclusions (portal detail views).

Revision ID: 0006_sync_exclusions
Revises: 0005_sync_profiles
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db import models  # noqa: F401
from integration.db.models import SyncExclusion

revision: str = "0006_sync_exclusions"
down_revision: str | None = "0005_sync_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    SyncExclusion.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SyncExclusion.__table__.drop(bind=op.get_bind(), checkfirst=True)
