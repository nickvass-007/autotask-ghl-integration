"""Scheduled sync runner (portal §5–§6, §10).

Profiles carry their own schedule (type + config). Every minute the loop finds
due profiles and launches them — but a schedule can NEVER cause a live sync
unless the profile's safety gates pass (dry-run for current criteria +
approval). A due-but-blocked profile runs a **dry-run instead** and flips to
the review workflow, so schedules degrade safely rather than silently writing.

Times are computed in the configured timezone (default Australia/Sydney) and
stored UTC. Runs never overlap per profile (live_run_blockers enforces it).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.models import SyncProfile
from ..db.session import session_scope
from ..sync.profiles import get_portal_settings, live_run_blockers, run_dry_run, run_live

log = get_logger(__name__)

# Business-friendly presets → concrete schedule configs (§5).
PRESETS: dict[str, dict] = {
    "weekday_7am": {"type": "weekly", "days": [0, 1, 2, 3, 4], "time": "07:00"},
    "weekday_6pm": {"type": "weekly", "days": [0, 1, 2, 3, 4], "time": "18:00"},
    "saturday_9am": {"type": "weekly", "days": [5], "time": "09:00"},
    "sunday_9am": {"type": "weekly", "days": [6], "time": "09:00"},
    "every_4h_business": {"type": "hours_window", "every_hours": 4, "start": "08:00", "end": "18:00",
                          "days": [0, 1, 2, 3, 4]},
    "overnight": {"type": "weekly", "days": [0, 1, 2, 3, 4, 5, 6], "time": "01:00"},
    "hourly": {"type": "interval", "every_hours": 1},
    "daily_7am": {"type": "weekly", "days": [0, 1, 2, 3, 4, 5, 6], "time": "07:00"},
}


def _resolve_config(schedule_type: str, config: dict | None) -> dict | None:
    if schedule_type == "manual" or not schedule_type:
        return None
    if schedule_type == "preset":
        return PRESETS.get((config or {}).get("preset", ""), None)
    if schedule_type == "interval":
        return {"type": "interval", "every_hours": float((config or {}).get("every_hours", 24))}
    if schedule_type in ("daily", "weekly", "weekdays"):
        days = (config or {}).get(
            "days", [0, 1, 2, 3, 4] if schedule_type == "weekdays" else [0, 1, 2, 3, 4, 5, 6]
        )
        return {"type": "weekly", "days": days, "time": (config or {}).get("time", "07:00")}
    if schedule_type == "once":
        return {"type": "once", "at": (config or {}).get("at")}
    return config  # already concrete (e.g. hours_window)


def compute_next_run(
    schedule_type: str,
    config: dict | None,
    *,
    tz_name: str = "Australia/Sydney",
    after: datetime | None = None,
    allow_weekend: bool = True,
    allow_overnight: bool = True,
) -> datetime | None:
    """Next run as UTC, or None for manual/finished schedules."""
    cfg = _resolve_config(schedule_type, config)
    if cfg is None:
        return None
    tz = ZoneInfo(tz_name)
    now = (after or utcnow()).astimezone(tz)

    def _ok(candidate: datetime) -> bool:
        if not allow_weekend and candidate.weekday() >= 5:
            return False
        if not allow_overnight and not (6 <= candidate.hour < 22):
            return False
        return True

    kind = cfg.get("type")
    if kind == "once":
        at = cfg.get("at")
        if not at:
            return None
        dt = datetime.fromisoformat(at)
        dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
        return dt.astimezone(timezone.utc) if dt > now.astimezone(timezone.utc) else None

    if kind == "interval":
        candidate = now + timedelta(hours=float(cfg.get("every_hours", 24)))
        for _ in range(21 * 24):
            if _ok(candidate):
                return candidate.astimezone(timezone.utc)
            candidate += timedelta(hours=1)
        return None

    if kind == "weekly":
        hh, mm = (cfg.get("time") or "07:00").split(":")
        target = time(int(hh), int(mm))
        days = set(cfg.get("days", [0, 1, 2, 3, 4, 5, 6]))
        for offset in range(0, 15):
            day = (now + timedelta(days=offset)).date()
            candidate = datetime.combine(day, target, tzinfo=tz)
            if candidate <= now or day.weekday() not in days:
                continue
            if _ok(candidate):
                return candidate.astimezone(timezone.utc)
        return None

    if kind == "hours_window":
        every = float(cfg.get("every_hours", 4))
        sh, sm = (cfg.get("start") or "08:00").split(":")
        eh, em = (cfg.get("end") or "18:00").split(":")
        days = set(cfg.get("days", [0, 1, 2, 3, 4]))
        for offset in range(0, 15):
            day = (now + timedelta(days=offset)).date()
            if day.weekday() not in days:
                continue
            slot = datetime.combine(day, time(int(sh), int(sm)), tzinfo=tz)
            end = datetime.combine(day, time(int(eh), int(em)), tzinfo=tz)
            while slot <= end:
                if slot > now and _ok(slot):
                    return slot.astimezone(timezone.utc)
                slot += timedelta(hours=every)
        return None
    return None


async def scheduler_tick(*, autotask_factory, ghl_factory) -> int:
    """One pass: launch every due profile. Returns number launched."""
    launched = 0
    with session_scope() as session:
        settings = get_portal_settings(session)
        due = list(
            session.execute(
                select(SyncProfile).where(
                    SyncProfile.environment == get_settings().environment,
                    SyncProfile.enabled.is_(True),
                    SyncProfile.schedule_enabled.is_(True),
                    SyncProfile.schedule_paused.is_(False),
                    SyncProfile.next_run_at.is_not(None),
                    SyncProfile.next_run_at <= utcnow(),
                )
            ).scalars()
        )
        ids = [p.id for p in due]
    for pid in ids:
        with session_scope() as session:
            profile = session.get(SyncProfile, pid)
            if profile is None:
                continue
            try:
                autotask = await autotask_factory()
                blockers = live_run_blockers(session, profile)
                allow_live = settings["allow_live_scheduled_syncs"] == "true"
                if not blockers and allow_live:
                    ghl = ghl_factory()
                    await ghl.authenticate()
                    await run_live(
                        session, profile=profile, autotask=autotask, ghl=ghl,
                        started_by="scheduler", trigger="scheduled",
                    )
                else:
                    # Blocked schedule degrades to a DRY RUN + review workflow (§6).
                    log.info(
                        "Scheduled profile %s blocked from live (%s) — running dry-run instead",
                        profile.name, "; ".join(blockers) or "live scheduled syncs disabled",
                    )
                    await run_dry_run(
                        session, profile=profile, autotask=autotask,
                        started_by="scheduler", trigger="scheduled",
                    )
                launched += 1
            except Exception:
                log.exception("Scheduled run for profile %s failed", pid)
            finally:
                profile = session.get(SyncProfile, pid)
                if profile is not None:
                    profile.next_run_at = compute_next_run(
                        profile.schedule_type,
                        profile.schedule_config,
                        tz_name=settings["default_timezone"],
                        allow_weekend=settings["allow_weekend_schedules"] == "true",
                        allow_overnight=settings["allow_overnight_schedules"] == "true",
                    )
    return launched


async def run_scheduler(*, autotask_factory, ghl_factory, stop: asyncio.Event) -> None:
    log.warning("Portal scheduler ENABLED (60s tick)")
    while not stop.is_set():
        try:
            await scheduler_tick(autotask_factory=autotask_factory, ghl_factory=ghl_factory)
        except Exception:
            log.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass
