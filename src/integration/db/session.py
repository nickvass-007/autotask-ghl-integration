"""Engine and session factory.

A single engine is created from ``DATABASE_URL`` (Postgres locally, Azure SQL in
production — the only thing that differs between environments). ``session_scope``
gives a transactional context manager used by the sync engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    # pool_pre_ping avoids stale connections after idle periods (matters on Azure SQL).
    # Generous pool: webhook handlers hold a session across slow connector calls,
    # so bursts (e.g. GHL retry storms) need headroom; fail fast rather than
    # wedging the event loop for 30s when it's truly exhausted.
    # SQLite (tests) uses SingletonThreadPool, which rejects pool sizing kwargs.
    pool_kwargs = (
        {}
        if settings.database_url.startswith("sqlite")
        else {"pool_size": 15, "max_overflow": 25, "pool_timeout": 10}
    )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        **pool_kwargs,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
