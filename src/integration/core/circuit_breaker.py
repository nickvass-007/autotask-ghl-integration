"""Circuit breaker for Autotask writes (Spec §5.5).

If conflict volume or write-failure rate crosses a threshold within a rolling
window, **pause all Autotask writes** and alert admins. Defaults are configurable
(``CIRCUIT_BREAKER_*``). This is a protect-the-record control: when the system
looks like it's misbehaving, it stops writing rather than risk damage.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import System, TransactionStatus
from ..db.models import CircuitBreakerState, TransactionLog

log = get_logger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when a write is attempted while the breaker is tripped."""


def _state(session: Session, target: System) -> CircuitBreakerState:
    env = get_settings().environment
    stmt = select(CircuitBreakerState).where(
        CircuitBreakerState.environment == env,
        CircuitBreakerState.target_system == target,
    )
    state = session.execute(stmt).scalar_one_or_none()
    if state is None:
        state = CircuitBreakerState(environment=env, target_system=target, tripped=False)
        session.add(state)
        session.flush()
    return state


def is_tripped(session: Session, target: System = System.AUTOTASK) -> bool:
    return _state(session, target).tripped


def assert_writable(session: Session, target: System = System.AUTOTASK) -> None:
    if _state(session, target).tripped:
        raise CircuitOpenError(
            f"Circuit breaker is OPEN for {target} — writes are paused (Spec §5.5)."
        )


def trip(session: Session, reason: str, target: System = System.AUTOTASK) -> None:
    state = _state(session, target)
    state.tripped = True
    state.tripped_at = utcnow()
    state.reason = reason
    log.error("CIRCUIT BREAKER TRIPPED for %s: %s", target, reason)


def reset(session: Session, target: System = System.AUTOTASK) -> None:
    state = _state(session, target)
    state.tripped = False
    state.reason = None
    log.warning("Circuit breaker reset for %s", target)


def evaluate(session: Session, target: System = System.AUTOTASK) -> bool:
    """Check rolling-window conflict/failure counts and trip if a threshold is crossed.

    Returns True if the breaker is (now) tripped. Called after each write attempt."""
    settings = get_settings()
    window_start = utcnow() - timedelta(minutes=settings.circuit_breaker_window_minutes)

    recent = (
        session.execute(
            select(TransactionLog.status).where(
                TransactionLog.environment == settings.environment,
                TransactionLog.timestamp >= window_start,
            )
        )
        .scalars()
        .all()
    )
    if not recent:
        return _state(session, target).tripped

    conflicts = sum(1 for s in recent if s == TransactionStatus.CONFLICT)
    failures = sum(1 for s in recent if s == TransactionStatus.ERROR)
    failure_rate = failures / len(recent)

    if conflicts >= settings.circuit_breaker_max_conflicts:
        trip(session, f"{conflicts} conflicts in window", target)
    elif failure_rate >= settings.circuit_breaker_max_failure_rate:
        trip(session, f"{failure_rate:.0%} write-failure rate in window", target)

    return _state(session, target).tripped
