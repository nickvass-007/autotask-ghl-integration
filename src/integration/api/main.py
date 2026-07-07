"""FastAPI application — the App Service host (Spec §12.2).

Endpoints (Stage 1):
  GET  /health                 -> liveness + ACTIVE ENVIRONMENT (always visible, §6)
  GET  /oauth/ghl/authorize    -> redirect an admin to GHL to grant the app
  GET  /oauth/crm/callback     -> exchange code, store rotating refresh token
                                  (path is brand-neutral: GHL's marketplace rejects
                                  redirect URIs containing "ghl"/"highlevel")
  POST /webhooks/crm/contact   -> verify signature, run the gated Contacts flow (§9)
                                  (brand-neutral path, same reason as the OAuth callback)
  GET  /admin                  -> minimal ops web UI (approvals + transaction feed)
  GET  /approvals              -> list pending approvals
  POST /approvals/{id}/decide  -> Approve/Reject/Override (verifies caller, §11.1)
  POST /api/messages           -> Teams bot messaging endpoint

The startup banner prints the active environment (Spec §6). ⚠️ If it says
PRODUCTION unexpectedly, stop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

import asyncio

from ..canonical.entities import CanonicalContact, CanonicalDeal
from ..config.settings import get_settings
from ..config.stages import load_stage_map, sync_stage_map_to_db
from ..core.logging import configure_logging, get_logger, new_correlation_id
from ..db.enums import ApprovalType, System
from ..db.session import session_scope
from ..jobs.poller import run_poller
from ..sync import contacts as contacts_flow
from ..sync import deals as deals_flow
from ..sync.approvals import get_approval, list_pending
from ..teams.bot import handle_command
from .admin import router as admin_router
from .deps import get_autotask, get_ghl, set_ghl_token

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    # Unmissable active-environment banner on startup (Spec §6).
    log.warning("=" * 72)
    log.warning(settings.environment_banner())
    log.warning("=" * 72)
    if settings.is_production:
        log.warning("⚠️  PRODUCTION is live — every Autotask write affects real data.")
    # Flow 2: surface the stage map in the DB for ops inspection (best-effort —
    # placeholder IDs are fine here; validation runs against live APIs on demand).
    try:
        with session_scope() as session:
            sync_stage_map_to_db(session, settings.environment)
    except Exception as exc:
        log.warning("Stage map not synced to DB: %s", exc)

    stop = asyncio.Event()
    poller_task: asyncio.Task | None = None
    if settings.enable_poller:
        poller_task = asyncio.create_task(
            run_poller(autotask_factory=get_autotask, ghl_factory=get_ghl, stop=stop)
        )
    yield
    if poller_task is not None:
        stop.set()
        poller_task.cancel()


app = FastAPI(title="Autotask ⇄ GoHighLevel Integration", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)


@app.get("/health")
async def health() -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "environment": s.environment.value,  # always visible (Spec §6)
            "is_production": s.is_production,
            "deployment_label": s.deployment_label,
            "banner": s.environment_banner(),
        }
    )


# ── GHL OAuth (Spec §12.1) ───────────────────────────────────────────────────
@app.get("/oauth/ghl/authorize")
async def ghl_authorize() -> RedirectResponse:
    ghl = get_ghl()
    state = new_correlation_id()
    return RedirectResponse(ghl.authorize_url(state))


@app.get("/oauth/crm/callback")
async def ghl_callback(code: str = "", state: str = "") -> JSONResponse:
    if not code:
        raise HTTPException(status_code=400, detail="missing authorization code")
    ghl = get_ghl()
    token = await ghl.exchange_code(code)
    # ⚠️ In production persist the rotating refresh token to Key Vault, not memory.
    set_ghl_token(token)
    return JSONResponse({"status": "authorized", "environment": get_settings().environment.value})


# ── GHL inbound webhook -> gated Contacts flow (Spec §4, §9) ──────────────────
@app.post("/webhooks/crm/contact")
async def ghl_contact_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    ghl = get_ghl()
    # Signatures MUST be verified; an unverified webhook is rejected (Spec §4).
    if not ghl.verify_webhook(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    payload = await request.json()
    event_id = str(payload.get("eventId") or payload.get("id") or new_correlation_id())
    data = payload.get("contact", payload)

    incoming = CanonicalContact(
        source_system=System.GHL,
        source_id=str(data.get("id")) if data.get("id") else None,
        email=data.get("email"),
        first_name=data.get("firstName"),
        last_name=data.get("lastName"),
        phone=data.get("phone"),
        mobile=data.get("mobilePhone") or data.get("mobile"),
        city=data.get("city"),
        state=data.get("state"),
        postal_code=data.get("postalCode"),
        country=data.get("country"),
        company_name=data.get("companyName"),
        website=data.get("website"),
    )

    autotask = await get_autotask()
    with session_scope() as session:
        outcome = await contacts_flow.process_ghl_contact(
            session, ghl_contact=incoming, event_id=event_id, autotask=autotask
        )
    return JSONResponse(
        {
            "action": outcome.action,
            "correlation_id": outcome.correlation_id,
            "autotask_id": outcome.autotask_id,
            "approval_ids": outcome.approval_ids,
            "detail": outcome.detail,
        }
    )


# ── GHL opportunity webhook -> gated Flow-2 pipeline (Spec §10.2) ─────────────
@app.post("/webhooks/crm/opportunity")
async def ghl_opportunity_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    ghl = get_ghl()
    if not ghl.verify_webhook(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    payload = await request.json()
    event_id = str(payload.get("eventId") or payload.get("id") or new_correlation_id())
    data = payload.get("opportunity", payload)

    deal = CanonicalDeal(
        source_system=System.GHL,
        source_id=str(data.get("id")) if data.get("id") else None,
    )
    deal.name = data.get("name")
    deal.monetary_value = data.get("monetaryValue")
    deal.status = data.get("status")
    deal.contact_id = str(data.get("contactId")) if data.get("contactId") else None
    deal.extra["pipeline_id"] = str(data.get("pipelineId") or "")
    deal.extra["stage_id"] = str(data.get("pipelineStageId") or "")

    autotask = await get_autotask()
    with session_scope() as session:
        outcome = await deals_flow.process_ghl_opportunity(
            session,
            ghl_deal=deal,
            event_id=event_id,
            autotask=autotask,
            ghl=ghl,
            stage_map=load_stage_map(),
        )
    return JSONResponse(
        {
            "action": outcome.action,
            "correlation_id": outcome.correlation_id,
            "autotask_id": outcome.autotask_id,
            "approval_ids": outcome.approval_ids,
            "detail": outcome.detail,
        }
    )


# ── Stage-map inspection (Spec §10.3: list + flag entries that don't resolve) ──
@app.get("/admin/stage-map")
async def stage_map_status() -> JSONResponse:
    smap = load_stage_map()
    problems: list[str] = []
    try:
        from ..config.stages import validate_stage_map

        autotask = await get_autotask()
        ghl = get_ghl()
        await ghl.authenticate()
        problems = await validate_stage_map(autotask=autotask, ghl=ghl)
        validated = True
    except Exception as exc:
        problems = [f"live validation unavailable: {exc}"]
        validated = False
    return JSONResponse(
        {
            "validated": validated,
            "problems": problems,
            "sales": {
                "pipeline": smap.sales.ghl_pipeline_id,
                "closed_won_stage": smap.sales.closed_won_stage_id,
                "stages": [
                    {
                        "ghl_stage": s.ghl_stage_id,
                        "autotask_value": s.autotask_status_value,
                        "direction": s.direction.value,
                        "active": s.active,
                    }
                    for s in smap.sales.stages
                ],
            },
            "service": {
                "pipeline": smap.service.ghl_pipeline_id,
                "stages": [
                    {
                        "ghl_stage": s.ghl_stage_id,
                        "autotask_value": s.autotask_status_value,
                        "direction": s.direction.value,
                        "active": s.active,
                    }
                    for s in smap.service.stages
                ],
            },
        }
    )


# ── Approvals (Spec §11.1) ────────────────────────────────────────────────────
@app.get("/approvals")
async def approvals() -> JSONResponse:
    with session_scope() as session:
        rows = list_pending(session)
        return JSONResponse(
            [
                {
                    "id": r.id,
                    "type": str(r.approval_type),
                    "severity": str(r.severity),
                    "reason": r.detected_reason,
                    "autotask_id": r.autotask_id,
                    "ghl_id": r.ghl_id,
                }
                for r in rows
            ]
        )


@app.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: int,
    request: Request,
    x_approval_token: str = Header(default=""),
) -> JSONResponse:
    """Apply a Teams card decision. ⚠️ The caller is verified via a shared token so
    a spoofed callback cannot approve a change (Spec §11.1)."""
    settings = get_settings()
    body = await request.json()
    token = x_approval_token or body.get("token", "")
    if not settings.approval_callback_secret or token != settings.approval_callback_secret:
        raise HTTPException(status_code=401, detail="unverified approval caller")

    decision = body.get("decision", "")
    approve = decision in ("approve", "override")
    decided_by = body.get("decided_by", "teams-bot")
    autotask = await get_autotask()
    with session_scope() as session:
        # Route by approval family: Flow-2 / Stage-C types apply via the deals
        # handler; everything else is the Flow-1 Contacts handler.
        row = get_approval(session, approval_id)
        is_deal = row is not None and ApprovalType(row.approval_type) in deals_flow.DEAL_APPROVAL_TYPES
        if is_deal:
            outcome = await deals_flow.apply_deal_decision(
                session,
                approval_id=approval_id,
                approve=approve,
                decided_by=decided_by,
                autotask=autotask,
                ghl=get_ghl(),
                chosen_account_id=body.get("chosen_id"),
            )
        else:
            outcome = await contacts_flow.apply_decision(
                session,
                approval_id=approval_id,
                approve=approve,
                decided_by=decided_by,
                autotask=autotask,
                chosen_account_id=body.get("chosen_id"),
                chosen_autotask_contact_id=body.get("chosen_id"),
            )
    return JSONResponse({"action": outcome.action, "correlation_id": outcome.correlation_id})


# ── Teams bot messaging endpoint (Spec §11) ───────────────────────────────────
@app.post("/api/messages")
async def teams_messages(request: Request) -> Response:
    """Minimal bot endpoint. In production this is wired to the botbuilder
    CloudAdapter with the Bot's app id/password; here we expose the command router
    so the feed/commands are testable without the Azure Bot channel."""
    payload = await request.json()
    text = payload.get("text", "")
    return JSONResponse({"reply": handle_command(text)})
