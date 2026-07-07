"""Test harness.

Offline: an in-memory SQLite database (created from the same models, proving the
schema is engine-portable per Spec §3.4) plus in-process fake connectors standing
in for Autotask/GHL. No live credentials or network needed — the entire Contacts
flow is exercised against recorded behaviour.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "sandbox")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("AUTOTASK_HOLDING_ACCOUNT_ID", "")
# Pin behaviour toggles so a developer's local .env can't change test outcomes.
os.environ.setdefault("AUTO_APPLY_ADDITIVE", "true")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from integration.db.base import Base
from integration.db import models  # noqa: F401  (register tables)


@pytest.fixture()
def session() -> Session:
    # StaticPool keeps a single in-memory connection so every session sees the schema.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    sess = maker()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
