"""Interlinked Sync Control Centre — portal API (token-gated like /admin).

Long-running work (dry-runs, live syncs) is launched as background asyncio
tasks in this process (it owns the GHL token); the UI polls the jobs list.
Every mutation lands in the audit/transaction trail via the underlying engine.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import ApprovalStatus, CanonicalEntityType, System, TransactionStatus
from ..db.models import (
    ApprovalQueue,
    EntityMapping,
    OAuthTokenStore,
    SyncCursor,
    SyncJob,
    SyncProfile,
    TransactionLog,
)
from ..db.session import session_scope
from ..jobs.scheduler import PRESETS, compute_next_run
from ..sync.profiles import (
    SETTING_DEFAULTS,
    approve_profile,
    criteria_hash,
    get_portal_settings,
    latest_snapshot,
    live_run_blockers,
    run_dry_run,
    run_live,
    set_portal_setting,
)
from .admin import _authorize, _enum_val
from .deps import get_autotask, get_ghl
from .portal_page import PORTAL_HTML

log = get_logger(__name__)
router = APIRouter()

_tasks: set[asyncio.Task] = set()


def _bg(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _profile_dict(session, p: SyncProfile, *, detail: bool = False) -> dict:
    snap = latest_snapshot(session, p)
    out = {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "enabled": p.enabled,
        "sync_type": p.sync_type,
        "criteria": p.criteria_json,
        "dry_run_required": p.dry_run_required,
        "requires_approval": p.requires_approval_before_live_sync,
        "review_state": p.review_state,
        "review_reason": p.review_reason,
        "schedule_enabled": p.schedule_enabled,
        "schedule_paused": p.schedule_paused,
        "schedule_type": p.schedule_type,
        "schedule_config": p.schedule_config,
        "last_run_at": _iso(p.last_run_at),
        "next_run_at": _iso(p.next_run_at),
        "approved_by": p.approved_by,
        "created_by": p.created_by,
        "updated_by": p.updated_by,
        "last_dry_run_job_id": p.last_dry_run_job_id,
        "last_live_sync_job_id": p.last_live_sync_job_id,
        "blockers": live_run_blockers(session, p),
        "dry_run_current": bool(
            snap and snap.criteria_hash == criteria_hash(p.criteria_json or {})
        ),
        "last_dry_run_summary": snap.summary_json if snap else None,
    }
    if detail and snap:
        out["snapshot"] = {
            "created_at": _iso(snap.created_at),
            "matched_customer_ids": (snap.matched_customer_ids or {}).get("ids", []),
            "matched_contact_count": snap.matched_contact_count,
        }
    return out


def _job_dict(j: SyncJob) -> dict:
    dur = None
    if j.started_at and j.ended_at:
        dur = round((j.ended_at - j.started_at).total_seconds(), 1)
    return {
        "id": j.id,
        "profile_id": j.profile_id,
        "kind": j.kind,
        "trigger": j.trigger,
        "started_by": j.started_by,
        "status": j.status,
        "scheduled_for": _iso(j.scheduled_for),
        "started_at": _iso(j.started_at),
        "ended_at": _iso(j.ended_at),
        "duration_s": dur,
        "summary": j.summary_json,
        "error": j.error,
        "correlation_id": j.correlation_id,
    }


@router.get("/portal", response_class=HTMLResponse)
async def portal_page() -> HTMLResponse:
    return HTMLResponse(PORTAL_HTML)


@router.get("/portal/api/overview")
async def overview(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    s = get_settings()
    env = s.environment
    with session_scope() as session:
        profiles = session.query(SyncProfile).filter_by(environment=env).count()
        pending = (
            session.query(ApprovalQueue)
            .filter_by(environment=env, status=ApprovalStatus.PENDING)
            .count()
        )
        contacts = (
            session.query(EntityMapping)
            .filter_by(environment=env, canonical_entity_type=CanonicalEntityType.CONTACT)
            .count()
        )
        companies = (
            session.query(EntityMapping)
            .filter_by(environment=env, canonical_entity_type=CanonicalEntityType.COMPANY)
            .count()
        )
        running = session.query(SyncJob).filter_by(environment=env, status="running").count()

        # Work-done digests from the transaction feed (24h and 7 days).
        from datetime import timedelta

        def _digest(hours: int) -> dict:
            since = utcnow() - timedelta(hours=hours)
            rows = session.execute(
                select(TransactionLog.operation, TransactionLog.status).where(
                    TransactionLog.environment == env, TransactionLog.timestamp >= since
                )
            ).all()
            out = {"created": 0, "updated": 0, "skipped": 0, "conflicts": 0, "errors": 0, "total": len(rows)}
            for op, status in rows:
                op_v, st_v = _enum_val(op), _enum_val(status)
                if st_v == "error":
                    out["errors"] += 1
                elif st_v == "conflict":
                    out["conflicts"] += 1
                elif op_v == "create":
                    out["created"] += 1
                elif op_v == "update":
                    out["updated"] += 1
                else:
                    out["skipped"] += 1
            return out

        decided = {
            "approved": session.query(ApprovalQueue)
            .filter_by(environment=env, status=ApprovalStatus.APPROVED).count(),
            "rejected": session.query(ApprovalQueue)
            .filter_by(environment=env, status=ApprovalStatus.REJECTED).count(),
        }
        jobs_done = (
            session.query(SyncJob).filter_by(environment=env, status="succeeded").count()
        )
        jobs = [
            _job_dict(j)
            for j in session.execute(
                select(SyncJob).where(SyncJob.environment == env)
                .order_by(SyncJob.created_at.desc()).limit(8)
            ).scalars()
        ]
        return JSONResponse(
            {
                "environment": env.value,
                "is_production": s.is_production,
                "deployment_label": s.deployment_label,
                "counts": {
                    "profiles": profiles,
                    "pending_approvals": pending,
                    "contacts_linked": contacts,
                    "companies_linked": companies,
                    "jobs_running": running,
                    "jobs_completed": jobs_done,
                },
                "work_24h": _digest(24),
                "work_7d": _digest(24 * 7),
                "approvals_decided": decided,
                "recent_jobs": jobs,
            }
        )


# ── System health ─────────────────────────────────────────────────────────────
_HEALTH_ENTITY_TYPES = (
    CanonicalEntityType.CONTACT,
    CanonicalEntityType.COMPANY,
    CanonicalEntityType.DEAL,
    CanonicalEntityType.SERVICE_ITEM,
)


@router.get("/portal/api/health")
async def system_health(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    """Fast, DB-only snapshot of the integration's health — safe to poll.

    No external API calls (use /health/ping for a live connectivity test)."""
    _authorize(request, x_admin_token)
    s = get_settings()
    env = s.environment
    with session_scope() as session:
        mappings = {
            et.value: session.query(EntityMapping)
            .filter_by(environment=env, canonical_entity_type=et)
            .count()
            for et in _HEALTH_ENTITY_TYPES
        }

        tok = session.execute(
            select(OAuthTokenStore).where(
                OAuthTokenStore.environment == env, OAuthTokenStore.system == System.GHL
            )
        ).scalar_one_or_none()
        ghl_authorized = tok is not None and bool(tok.access_token)
        ghl_updated = _iso(tok.updated_at) if tok is not None else None

        cursors = [
            {
                "entity_type": c.entity_type,
                "source_system": _enum_val(c.source_system),
                "cursor": c.cursor,
                "updated_at": _iso(c.updated_at),
            }
            for c in session.execute(
                select(SyncCursor).where(SyncCursor.environment == env)
            ).scalars()
        ]

        # Errors in the last hour = a quick "is something wrong right now" signal.
        from datetime import timedelta

        errors_1h = session.execute(
            select(func.count())
            .select_from(TransactionLog)
            .where(
                TransactionLog.environment == env,
                TransactionLog.timestamp >= utcnow() - timedelta(hours=1),
                TransactionLog.status == TransactionStatus.ERROR,
            )
        ).scalar_one()

    return JSONResponse(
        {
            "environment": env.value,
            "is_production": s.is_production,
            "connectors": {
                "ghl": {
                    "authorized": ghl_authorized,
                    "token_updated_at": ghl_updated,
                    "location_id": s.ghl_location_id or None,
                    "scopes": s.ghl_scope_list,
                },
                "autotask": {
                    "configured": bool(s.autotask_username),
                    "zone": s.autotask_zone_override_url or "auto-detected",
                },
            },
            "background": {
                "poller_enabled": s.enable_poller,
                "scheduler_enabled": s.enable_scheduler,
                "poll_interval_s": s.autotask_poll_interval_seconds,
                "reconciliation_interval_s": s.reconciliation_interval_seconds,
            },
            "mappings": mappings,
            "cursors": cursors,
            "errors_1h": errors_1h,
        }
    )


@router.post("/portal/api/health/ping")
async def system_health_ping(
    request: Request, x_admin_token: str = Header(default="")
) -> JSONResponse:
    """On-demand LIVE connectivity test against Autotask and GHL (read-only)."""
    _authorize(request, x_admin_token)

    async def _ghl() -> dict:
        try:
            ghl = get_ghl()
            await ghl.authenticate()
            try:
                pipelines = await ghl.get_pipelines()
            except Exception:
                # Access token may be stale — rotate once and retry (poller pattern).
                await ghl.refresh()
                pipelines = await ghl.get_pipelines()
            return {"ok": True, "detail": f"{len(pipelines)} pipeline(s) reachable"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:200]}

    async def _autotask() -> dict:
        try:
            at = await get_autotask()
            ok = await at.health()
            return {"ok": bool(ok), "detail": f"zone {at._base_url or 'unknown'}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:200]}

    ghl_res, at_res = await asyncio.gather(_ghl(), _autotask())
    return JSONResponse({"ghl": ghl_res, "autotask": at_res})


# ── Profiles ──────────────────────────────────────────────────────────────────
@router.get("/portal/api/profiles")
async def list_profiles(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        rows = session.execute(
            select(SyncProfile)
            .where(SyncProfile.environment == get_settings().environment)
            .order_by(SyncProfile.name)
        ).scalars()
        return JSONResponse({"profiles": [_profile_dict(session, p) for p in rows]})


@router.post("/portal/api/profiles")
async def create_profile(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    if not str(body.get("name", "")).strip():
        raise HTTPException(status_code=400, detail="name is required")
    with session_scope() as session:
        settings = get_portal_settings(session)
        p = SyncProfile(
            environment=get_settings().environment,
            name=body["name"].strip(),
            description=body.get("description", ""),
            sync_type=body.get("sync_type", "contacts"),
            criteria_json=body.get("criteria", {"rules": []}),
            dry_run_required=bool(
                body.get("dry_run_required", settings["default_dry_run_required"] == "true")
            ),
            requires_approval_before_live_sync=bool(
                body.get(
                    "requires_approval",
                    settings["require_approval_before_live_sync"] == "true",
                )
            ),
            created_by=body.get("operator", "portal"),
            updated_by=body.get("operator", "portal"),
        )
        p.criteria_hash = criteria_hash(p.criteria_json)
        session.add(p)
        session.flush()
        return JSONResponse(_profile_dict(session, p))


@router.get("/portal/api/profiles/{pid}")
async def get_profile(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        out = _profile_dict(session, p, detail=True)
        out["jobs"] = [
            _job_dict(j)
            for j in session.execute(
                select(SyncJob).where(SyncJob.profile_id == pid)
                .order_by(SyncJob.created_at.desc()).limit(20)
            ).scalars()
        ]
        return JSONResponse(out)


@router.put("/portal/api/profiles/{pid}")
async def update_profile(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        for field_name in ("name", "description", "sync_type"):
            if field_name in body:
                setattr(p, field_name, body[field_name])
        for flag in ("enabled", "dry_run_required"):
            if flag in body:
                setattr(p, flag, bool(body[flag]))
        if "requires_approval" in body:
            p.requires_approval_before_live_sync = bool(body["requires_approval"])
        if "criteria" in body:
            new_hash = criteria_hash(body["criteria"] or {})
            if new_hash != p.criteria_hash:
                # §6 rule 3: criteria change invalidates prior dry-runs/approval.
                p.criteria_json = body["criteria"]
                p.criteria_hash = new_hash
                p.review_state = "dry_run_required"
                p.review_reason = "criteria changed — new dry-run required"
                p.approved_at = None
                p.approved_by = None
        p.updated_by = body.get("operator", "portal")
        p.updated_at = utcnow()
        session.flush()
        return JSONResponse(_profile_dict(session, p))


@router.delete("/portal/api/profiles/{pid}")
async def delete_profile(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        session.delete(p)
    return JSONResponse({"deleted": pid})


@router.post("/portal/api/profiles/{pid}/duplicate")
async def duplicate_profile(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        copy = SyncProfile(
            environment=p.environment,
            name=f"{p.name} (copy)",
            description=p.description,
            sync_type=p.sync_type,
            criteria_json=p.criteria_json,
            criteria_hash=p.criteria_hash,
            dry_run_required=p.dry_run_required,
            requires_approval_before_live_sync=p.requires_approval_before_live_sync,
            created_by="portal",
        )
        session.add(copy)
        session.flush()
        return JSONResponse(_profile_dict(session, copy))


async def _bg_dry_run(pid: int, started_by: str) -> None:
    try:
        autotask = await get_autotask()
        with session_scope() as session:
            profile = session.get(SyncProfile, pid)
            if profile is not None:
                await run_dry_run(
                    session, profile=profile, autotask=autotask, started_by=started_by
                )
    except Exception:
        log.exception("Background dry-run for profile %s failed", pid)


async def _bg_live(pid: int, started_by: str) -> None:
    try:
        autotask = await get_autotask()
        ghl = get_ghl()
        await ghl.authenticate()
        with session_scope() as session:
            profile = session.get(SyncProfile, pid)
            if profile is not None:
                await run_live(
                    session, profile=profile, autotask=autotask, ghl=ghl,
                    started_by=started_by,
                )
    except Exception:
        log.exception("Background live sync for profile %s failed", pid)


@router.post("/portal/api/profiles/{pid}/dry-run")
async def start_dry_run(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    _bg(_bg_dry_run(pid, "portal-operator"))
    return JSONResponse({"started": True, "kind": "dry_run"})


@router.post("/portal/api/profiles/{pid}/run")
async def start_live(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        blockers = live_run_blockers(session, p)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
    _bg(_bg_live(pid, "portal-operator"))
    return JSONResponse({"started": True, "kind": "live"})


@router.post("/portal/api/profiles/{pid}/approve")
async def approve(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        try:
            approve_profile(session, p, approved_by=body.get("operator", "portal-operator"))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return JSONResponse(_profile_dict(session, p))


@router.post("/portal/api/profiles/{pid}/schedule")
async def set_schedule(pid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    with session_scope() as session:
        p = session.get(SyncProfile, pid)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        settings = get_portal_settings(session)
        if "schedule_type" in body:
            p.schedule_type = body["schedule_type"]
            p.schedule_config = body.get("schedule_config")
        for flag in ("schedule_enabled", "schedule_paused"):
            if flag in body:
                setattr(p, flag, bool(body[flag]))
        p.next_run_at = (
            compute_next_run(
                p.schedule_type,
                p.schedule_config,
                tz_name=settings["default_timezone"],
                allow_weekend=settings["allow_weekend_schedules"] == "true",
                allow_overnight=settings["allow_overnight_schedules"] == "true",
            )
            if p.schedule_enabled and not p.schedule_paused
            else None
        )
        session.flush()
        return JSONResponse(_profile_dict(session, p))


# ── Jobs ──────────────────────────────────────────────────────────────────────
def _page_params(offset: int, limit: int) -> tuple[int, int]:
    return max(0, offset), (limit if limit in (50, 100, 200) else 100)


@router.get("/portal/api/jobs")
async def list_jobs(
    request: Request,
    profile_id: int | None = None,
    offset: int = 0,
    limit: int = 100,
    x_admin_token: str = Header(default=""),
) -> JSONResponse:
    _authorize(request, x_admin_token)
    offset, limit = _page_params(offset, limit)
    from sqlalchemy import func

    with session_scope() as session:
        base = select(SyncJob).where(SyncJob.environment == get_settings().environment)
        if profile_id is not None:
            base = base.where(SyncJob.profile_id == profile_id)
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        rows = session.execute(
            base.order_by(SyncJob.created_at.desc()).offset(offset).limit(limit)
        ).scalars()
        return JSONResponse(
            {"jobs": [_job_dict(j) for j in rows], "total": total, "offset": offset, "limit": limit}
        )


@router.get("/portal/api/jobs/{jid}")
async def job_detail(jid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        j = session.get(SyncJob, jid)
        if j is None:
            raise HTTPException(status_code=404, detail="job not found")
        logs = [
            {
                "timestamp": _iso(t.timestamp),
                "operation": _enum_val(t.operation),
                "status": _enum_val(t.status),
                "entity_ref": t.entity_ref,
                "summary": t.summary,
            }
            for t in session.execute(
                select(TransactionLog)
                .where(TransactionLog.correlation_id == j.correlation_id)
                .order_by(TransactionLog.timestamp.desc())
                .limit(500)
            ).scalars()
        ]
        return JSONResponse({**_job_dict(j), "logs": logs})


@router.post("/portal/api/jobs/{jid}/cancel")
async def cancel_job(jid: int, request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        j = session.get(SyncJob, jid)
        if j is None:
            raise HTTPException(status_code=404, detail="job not found")
        if j.status != "running":
            raise HTTPException(status_code=409, detail=f"job is {j.status}")
        j.cancel_requested = True
    return JSONResponse({"cancelling": jid})


# ── Customers / Contacts ──────────────────────────────────────────────────────
@router.get("/portal/api/customers")
async def customers(
    request: Request, profile_id: int | None = None, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        settings = get_portal_settings(session)
        if profile_id is not None:
            p = session.get(SyncProfile, profile_id)
            snap = latest_snapshot(session, p) if p else None
            preview = (snap.summary_json or {}).get("matched_preview", []) if snap else []
            return JSONResponse(
                {"source": f"dry-run of {p.name}" if p else "?", "customers": preview,
                 "autotask_web_base": settings["autotask_web_base"]}
            )
        # Default view: every Account with a COMPANY mapping (i.e. synced).
        from sqlalchemy import func

        offset, limit = _page_params(
            int(request.query_params.get("offset", 0)),
            int(request.query_params.get("limit", 100)),
        )
        base = select(EntityMapping).where(
            EntityMapping.environment == get_settings().environment,
            EntityMapping.canonical_entity_type == CanonicalEntityType.COMPANY,
        )
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        rows = session.execute(
            base.order_by(EntityMapping.last_synced_at.desc()).offset(offset).limit(limit)
        ).scalars()
        return JSONResponse(
            {
                "source": "synced companies",
                "total": total,
                "offset": offset,
                "limit": limit,
                "customers": [
                    {"id": r.autotask_id, "ghl_id": r.ghl_id, "linked": True,
                     "last_synced_at": _iso(r.last_synced_at)}
                    for r in rows
                ],
                "autotask_web_base": settings["autotask_web_base"],
            }
        )


@router.get("/portal/api/contacts")
async def contacts(
    request: Request,
    q: str = "",
    offset: int = 0,
    limit: int = 100,
    x_admin_token: str = Header(default=""),
) -> JSONResponse:
    _authorize(request, x_admin_token)
    limit = limit if limit in (50, 100, 200) else 100
    offset = max(0, offset)

    # Search: resolve q → Autotask contact ids (name/email contains), then
    # restrict the mapping page to those ids.
    search_ids: list[str] | None = None
    if q.strip():
        try:
            autotask_s = await get_autotask()
            resp = await autotask_s._client.post(
                autotask_s._url("Contacts/query"),
                headers=autotask_s._auth_headers(),
                json={
                    "filter": [{
                        "op": "or",
                        "items": [
                            {"op": "contains", "field": f, "value": q.strip()}
                            for f in ("firstName", "lastName", "emailAddress")
                        ],
                    }],
                    "IncludeFields": ["id"],
                    "MaxRecords": 500,
                },
            )
            resp.raise_for_status()
            search_ids = [str(i["id"]) for i in resp.json().get("items", [])]
        except Exception as exc:
            log.warning("Contact search unavailable: %s", exc)
            search_ids = []

    with session_scope() as session:
        settings = get_portal_settings(session)
        base = select(EntityMapping).where(
            EntityMapping.environment == get_settings().environment,
            EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
        )
        if search_ids is not None:
            base = base.where(EntityMapping.autotask_id.in_(search_ids or ["-none-"]))
        stmt = base.order_by(EntityMapping.last_synced_at.desc()).offset(offset).limit(limit)
        rows = list(session.execute(stmt).scalars())
        from sqlalchemy import func

        total = session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar() or 0

    # Enrich the page live from Autotask (IDs only live in the mapping table):
    # one batched contact query + one batched company query per page.
    details: dict[str, dict] = {}
    company_names: dict[str, str] = {}
    try:
        autotask = await get_autotask()
        ids = [int(r.autotask_id) for r in rows if r.autotask_id]
        if ids:
            resp = await autotask._client.post(
                autotask._url("Contacts/query"),
                headers=autotask._auth_headers(),
                json={"filter": [{"op": "in", "field": "id", "value": ids}], "MaxRecords": 500},
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                details[str(item["id"])] = item
            company_ids = sorted(
                {int(i["companyID"]) for i in details.values() if i.get("companyID") is not None}
            )
            if company_ids:
                resp = await autotask._client.post(
                    autotask._url("Companies/query"),
                    headers=autotask._auth_headers(),
                    json={
                        "filter": [{"op": "in", "field": "id", "value": company_ids}],
                        "IncludeFields": ["id", "companyName"],
                        "MaxRecords": 500,
                    },
                )
                resp.raise_for_status()
                for item in resp.json().get("items", []):
                    company_names[str(item["id"])] = item.get("companyName") or ""
        # Ticket survey (CSAT) results per contact — averaged across all their
        # completed surveys. Field names vary by Autotask config; try the
        # common numeric candidates and degrade silently if the entity is off.
        try:
            resp = await autotask._client.post(
                autotask._url("SurveyResults/query"),
                headers=autotask._auth_headers(),
                json={"filter": [{"op": "in", "field": "contactID", "value": ids}], "MaxRecords": 500},
            )
            resp.raise_for_status()
            agg: dict[str, list[float]] = {}
            for item in resp.json().get("items", []):
                score = None
                for key in ("surveyGrade", "companyRating", "rating", "score"):
                    if isinstance(item.get(key), (int, float)):
                        score = float(item[key])
                        break
                if score is not None and item.get("contactID") is not None:
                    agg.setdefault(str(item["contactID"]), []).append(score)
            for cid, scores in agg.items():
                if cid in details:
                    details[cid]["_survey_avg"] = round(sum(scores) / len(scores), 1)
                    details[cid]["_survey_count"] = len(scores)
        except Exception as exc:
            log.info("Survey results unavailable: %s", exc)
    except Exception as exc:  # degrade to IDs-only rather than failing the page
        log.warning("Contacts page enrichment unavailable: %s", exc)

    def _row(r) -> dict:
        d = details.get(str(r.autotask_id), {})
        company_id = str(d["companyID"]) if d.get("companyID") is not None else None
        return {
            "autotask_id": r.autotask_id,
            "ghl_id": r.ghl_id,
            "last_synced_at": _iso(r.last_synced_at),
            "first_name": d.get("firstName"),
            "last_name": d.get("lastName"),
            "email": d.get("emailAddress"),
            "phone": d.get("phone") or d.get("mobilePhone"),
            "company_id": company_id,
            "company_name": company_names.get(company_id or "", None),
            "survey_avg": d.get("_survey_avg"),
            "survey_count": d.get("_survey_count", 0),
        }

    return JSONResponse(
        {
            "total": total,
            "offset": offset,
            "limit": limit,
            "contacts": [_row(r) for r in rows],
            "autotask_web_base": settings["autotask_web_base"],
        }
    )


# ── Logs & settings ───────────────────────────────────────────────────────────
@router.get("/portal/api/logs")
async def logs(
    request: Request, offset: int = 0, limit: int = 100, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    offset, limit = _page_params(offset, limit)
    from sqlalchemy import func

    with session_scope() as session:
        base = select(TransactionLog).where(
            TransactionLog.environment == get_settings().environment
        )
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        rows = session.execute(
            base.order_by(TransactionLog.timestamp.desc()).offset(offset).limit(limit)
        ).scalars()
        return JSONResponse(
            {
                "total": total,
                "offset": offset,
                "limit": limit,
                "logs": [
                    {
                        "timestamp": _iso(t.timestamp),
                        "direction": _enum_val(t.direction),
                        "operation": _enum_val(t.operation),
                        "status": _enum_val(t.status),
                        "entity_type": t.entity_type,
                        "entity_ref": t.entity_ref,
                        "summary": t.summary,
                        "correlation_id": t.correlation_id,
                    }
                    for t in rows
                ]
            }
        )


# ── Trends, detail views, exclusions, bulk actions ───────────────────────────
@router.get("/portal/api/trends")
async def trends(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    """Daily operation counts for the last 14 days (dashboard chart)."""
    _authorize(request, x_admin_token)
    from datetime import timedelta

    env = get_settings().environment
    since = utcnow() - timedelta(days=14)
    with session_scope() as session:
        rows = session.execute(
            select(TransactionLog.timestamp, TransactionLog.operation, TransactionLog.status)
            .where(TransactionLog.environment == env, TransactionLog.timestamp >= since)
        ).all()
    days: dict[str, dict] = {}
    for ts, op, status in rows:
        key = ts.date().isoformat()
        d = days.setdefault(key, {"created": 0, "updated": 0, "errors": 0, "other": 0})
        st, opv = _enum_val(status), _enum_val(op)
        if st == "error":
            d["errors"] += 1
        elif opv == "create":
            d["created"] += 1
        elif opv == "update":
            d["updated"] += 1
        else:
            d["other"] += 1
    out = []
    for i in range(13, -1, -1):
        day = (utcnow() - timedelta(days=i)).date().isoformat()
        out.append({"day": day, **days.get(day, {"created": 0, "updated": 0, "errors": 0, "other": 0})})
    return JSONResponse({"days": out})


@router.get("/portal/api/contacts/{autotask_id}/detail")
async def contact_detail(
    autotask_id: str, request: Request, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    from ..sync.criteria import is_excluded

    with session_scope() as session:
        link = session.execute(
            select(EntityMapping).where(
                EntityMapping.environment == get_settings().environment,
                EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
                EntityMapping.autotask_id == autotask_id,
            )
        ).scalar_one_or_none()
        history = [
            {
                "timestamp": _iso(t.timestamp),
                "direction": _enum_val(t.direction),
                "operation": _enum_val(t.operation),
                "status": _enum_val(t.status),
                "summary": t.summary,
            }
            for t in session.execute(
                select(TransactionLog)
                .where(TransactionLog.entity_ref.in_([autotask_id, link.ghl_id if link else ""]))
                .order_by(TransactionLog.timestamp.desc())
                .limit(50)
            ).scalars()
        ]
        excluded = is_excluded(session, "contact", autotask_id)
        settings = get_portal_settings(session)
    detail = None
    try:
        autotask = await get_autotask()
        contact = await autotask.get_contact(autotask_id)
        raw_account = (
            await autotask.get_account_raw(contact.company_id)
            if contact and contact.company_id
            else None
        )
        detail = {
            "first_name": contact.first_name if contact else None,
            "last_name": contact.last_name if contact else None,
            "email": contact.email if contact else None,
            "phone": contact.phone if contact else None,
            "company_id": contact.company_id if contact else None,
            "company_name": (raw_account or {}).get("companyName"),
        }
    except Exception as exc:
        log.warning("Contact detail enrichment unavailable: %s", exc)
    return JSONResponse(
        {
            "autotask_id": autotask_id,
            "ghl_id": link.ghl_id if link else None,
            "last_synced_at": _iso(link.last_synced_at) if link else None,
            "excluded": excluded,
            "detail": detail,
            "history": history,
            "autotask_web_base": settings["autotask_web_base"],
        }
    )


@router.get("/portal/api/approvals")
async def approvals_page(
    request: Request, offset: int = 0, limit: int = 100, x_admin_token: str = Header(default="")
) -> JSONResponse:
    """Pending approvals, paginated. Mirrors /admin/data's approval shape but
    with server-side offset/limit/total for the shared pager."""
    _authorize(request, x_admin_token)
    offset, limit = _page_params(offset, limit)
    from sqlalchemy import func

    env = get_settings().environment
    with session_scope() as session:
        base = select(ApprovalQueue).where(
            ApprovalQueue.environment == env,
            ApprovalQueue.status == ApprovalStatus.PENDING,
        )
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        rows = session.execute(
            base.order_by(ApprovalQueue.created_at.desc()).offset(offset).limit(limit)
        ).scalars()
        return JSONResponse(
            {
                "total": total,
                "offset": offset,
                "limit": limit,
                "decide_enabled": bool(get_settings().approval_callback_secret),
                "approvals": [
                    {
                        "id": a.id,
                        "type": _enum_val(a.approval_type),
                        "severity": _enum_val(a.severity),
                        "reason": a.detected_reason,
                        "autotask_id": a.autotask_id,
                        "ghl_id": a.ghl_id,
                        "proposed_change": a.proposed_change,
                        "created_at": _iso(a.created_at),
                    }
                    for a in rows
                ],
            }
        )


@router.get("/portal/api/customers/{account_id}/detail")
async def customer_detail(
    account_id: str, request: Request, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    from ..sync.criteria import is_excluded

    with session_scope() as session:
        settings = get_portal_settings(session)
        excluded = is_excluded(session, "account", account_id)
        company_link = session.execute(
            select(EntityMapping).where(
                EntityMapping.environment == get_settings().environment,
                EntityMapping.canonical_entity_type == CanonicalEntityType.COMPANY,
                EntityMapping.autotask_id == account_id,
            )
        ).scalar_one_or_none()
        history = [
            {
                "timestamp": _iso(t.timestamp),
                "operation": _enum_val(t.operation),
                "status": _enum_val(t.status),
                "summary": t.summary,
            }
            for t in session.execute(
                select(TransactionLog)
                .where(TransactionLog.entity_ref == account_id)
                .order_by(TransactionLog.timestamp.desc())
                .limit(25)
            ).scalars()
        ]
        contact_links = {
            m.autotask_id: m.ghl_id
            for m in session.execute(
                select(EntityMapping).where(
                    EntityMapping.environment == get_settings().environment,
                    EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
                )
            ).scalars()
        }

    account: dict = {}
    contacts: list[dict] = []
    try:
        autotask = await get_autotask()
        raw = await autotask.get_account_raw(account_id) or {}
        type_labels = await autotask.get_picklist_labels("Companies", "companyType")
        class_labels = await autotask.get_picklist_labels("Companies", "classification")
        account = {
            "name": raw.get("companyName"),
            "type": type_labels.get(str(raw.get("companyType")), raw.get("companyType")),
            "classification": class_labels.get(str(raw.get("classification")), None),
            "website": raw.get("webAddress"),
            "active": bool(raw.get("isActive")),
        }
        resp = await autotask._client.post(
            autotask._url("Contacts/query"),
            headers=autotask._auth_headers(),
            json={"filter": [{"op": "eq", "field": "companyID", "value": int(account_id)}],
                  "MaxRecords": 200},
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            contacts.append({
                "autotask_id": str(item["id"]),
                "name": f"{item.get('firstName') or ''} {item.get('lastName') or ''}".strip(),
                "email": item.get("emailAddress"),
                "active": bool(item.get("isActive")),
                "ghl_id": contact_links.get(str(item["id"])),
            })
    except Exception as exc:
        log.warning("Customer detail enrichment unavailable: %s", exc)

    return JSONResponse(
        {
            "autotask_id": account_id,
            "account": account,
            "excluded": excluded,
            "ghl_business_id": company_link.ghl_id if company_link else None,
            "contacts": contacts,
            "history": history,
            "autotask_web_base": settings["autotask_web_base"],
        }
    )


@router.post("/portal/api/exclusions")
async def add_exclusion(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    from ..db.models import SyncExclusion

    body = await request.json()
    entity_type = body.get("entity_type")
    autotask_id = str(body.get("autotask_id") or "")
    if entity_type not in ("contact", "account") or not autotask_id:
        raise HTTPException(status_code=400, detail="entity_type (contact|account) + autotask_id required")
    with session_scope() as session:
        session.add(
            SyncExclusion(
                environment=get_settings().environment,
                entity_type=entity_type,
                autotask_id=autotask_id,
                reason=body.get("reason", ""),
                created_by=body.get("operator", "portal"),
            )
        )
    return JSONResponse({"excluded": autotask_id})


@router.delete("/portal/api/exclusions/{entity_type}/{autotask_id}")
async def remove_exclusion(
    entity_type: str, autotask_id: str, request: Request, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    from ..db.models import SyncExclusion

    with session_scope() as session:
        row = session.execute(
            select(SyncExclusion).where(
                SyncExclusion.environment == get_settings().environment,
                SyncExclusion.entity_type == entity_type,
                SyncExclusion.autotask_id == autotask_id,
            )
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
    return JSONResponse({"removed": autotask_id})


@router.post("/portal/api/approvals/bulk-reject-echoes")
async def bulk_reject_echoes(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    """Reject pending GHL→AT contact approvals whose GHL contact is one WE
    synced (mapped) — the echo-storm leftovers. Autotask is untouched."""
    _authorize(request, x_admin_token)
    from ..db.enums import System

    env = get_settings().environment
    rejected = 0
    with session_scope() as session:
        pending = list(
            session.execute(
                select(ApprovalQueue).where(
                    ApprovalQueue.environment == env,
                    ApprovalQueue.status == ApprovalStatus.PENDING,
                    ApprovalQueue.source_system == System.GHL,
                    ApprovalQueue.ghl_id.is_not(None),
                )
            ).scalars()
        )
        mapped = {
            m.ghl_id
            for m in session.execute(
                select(EntityMapping).where(
                    EntityMapping.environment == env,
                    EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
                )
            ).scalars()
        }
        for a in pending:
            if a.ghl_id in mapped:
                a.status = ApprovalStatus.REJECTED
                a.decided_at = utcnow()
                a.decided_by = "portal:bulk-reject-echoes"
                rejected += 1
    return JSONResponse({"rejected": rejected})


@router.get("/portal/api/settings")
async def get_settings_api(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        return JSONResponse(
            {"settings": get_portal_settings(session), "presets": list(PRESETS)}
        )


@router.put("/portal/api/settings")
async def put_settings(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    with session_scope() as session:
        for key, value in (body.get("settings") or {}).items():
            try:
                set_portal_setting(session, key, str(value))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse({"settings": get_portal_settings(session)})
