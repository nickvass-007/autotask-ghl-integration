"""Interlinked Sync Control Centre — portal API (token-gated like /admin).

Long-running work (dry-runs, live syncs) is launched as background asyncio
tasks in this process (it owns the GHL token); the UI polls the jobs list.
Every mutation lands in the audit/transaction trail via the underlying engine.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from ..config.settings import get_settings
from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import ApprovalStatus, CanonicalEntityType
from ..db.models import (
    ApprovalQueue,
    EntityMapping,
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
                },
                "recent_jobs": jobs,
            }
        )


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
@router.get("/portal/api/jobs")
async def list_jobs(
    request: Request, profile_id: int | None = None, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        stmt = select(SyncJob).where(SyncJob.environment == get_settings().environment)
        if profile_id is not None:
            stmt = stmt.where(SyncJob.profile_id == profile_id)
        rows = session.execute(stmt.order_by(SyncJob.created_at.desc()).limit(100)).scalars()
        return JSONResponse({"jobs": [_job_dict(j) for j in rows]})


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
        rows = session.execute(
            select(EntityMapping)
            .where(
                EntityMapping.environment == get_settings().environment,
                EntityMapping.canonical_entity_type == CanonicalEntityType.COMPANY,
            )
            .order_by(EntityMapping.last_synced_at.desc())
            .limit(500)
        ).scalars()
        return JSONResponse(
            {
                "source": "synced companies",
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
    with session_scope() as session:
        settings = get_portal_settings(session)
        stmt = (
            select(EntityMapping)
            .where(
                EntityMapping.environment == get_settings().environment,
                EntityMapping.canonical_entity_type == CanonicalEntityType.CONTACT,
            )
            .order_by(EntityMapping.last_synced_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(session.execute(stmt).scalars())
        total = (
            session.query(EntityMapping)
            .filter_by(
                environment=get_settings().environment,
                canonical_entity_type=CanonicalEntityType.CONTACT,
            )
            .count()
        )

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
    request: Request, offset: int = 0, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        rows = session.execute(
            select(TransactionLog)
            .where(TransactionLog.environment == get_settings().environment)
            .order_by(TransactionLog.timestamp.desc())
            .offset(offset)
            .limit(100)
        ).scalars()
        return JSONResponse(
            {
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
