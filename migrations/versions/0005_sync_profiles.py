"""Portal: sync profiles, jobs, dry-run snapshots, settings.

Revision ID: 0005_sync_profiles
Revises: 0004_oauth_token_store
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db import models  # noqa: F401
from integration.db.models import PortalSetting, SyncJob, SyncProfile, SyncProfileSnapshot

revision: str = "0005_sync_profiles"
down_revision: str | None = "0004_oauth_token_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (SyncProfile, SyncJob, SyncProfileSnapshot, PortalSetting)


def upgrade() -> None:
    bind = op.get_bind()
    for model in _TABLES:
        model.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(_TABLES):
        model.__table__.drop(bind=bind, checkfirst=True)
