"""Add oauth_token_store — persist the GHL OAuth grant across API restarts.

Revision ID: 0004_oauth_token_store
Revises: 0003_sync_criteria
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from integration.db import models  # noqa: F401  (registers tables on Base.metadata)
from integration.db.models import OAuthTokenStore

revision: str = "0004_oauth_token_store"
down_revision: str | None = "0003_sync_criteria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    OAuthTokenStore.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    OAuthTokenStore.__table__.drop(bind=op.get_bind(), checkfirst=True)
