"""Hub export API — the Microsoft Fabric ingestion seam.

Fabric (or any warehouse) pulls the integration's state incrementally with an
id watermark: call with ``since_id`` = the highest id you've stored, page until
``count < limit``. Three feeds cover the hub's needs:

- ``/export/transactions`` — every sync operation (the event stream)
- ``/export/mappings``     — the identity spine (Autotask id ⇄ GHL id per entity)
- ``/export/approvals``    — the human-decision trail

All read-only, token-gated like the portal, and environment-scoped. This is the
Stage-"repoint" seam: when Fabric becomes the hub, a FabricConnector implements
the same connector contract the Autotask/GHL connectors use, and these feeds
are how Fabric bootstraps + audits the mirror. See docs/fabric-roadmap.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..config.settings import get_settings
from ..db.models import ApprovalQueue, EntityMapping, TransactionLog
from ..db.session import session_scope
from .admin import _authorize

router = APIRouter(prefix="/export", tags=["export"])

_MAX_LIMIT = 1000


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _page(session, model, since_id: int, limit: int):
    env = get_settings().environment
    stmt = (
        select(model)
        .where(model.environment == env, model.id > since_id)
        .order_by(model.id.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def _envelope(items: list[dict]) -> dict:
    return {
        "items": items,
        "count": len(items),
        "next_since_id": items[-1]["id"] if items else None,
    }


def _val(v):
    return getattr(v, "value", v)


@router.get("/transactions")
async def export_transactions(
    request: Request,
    since_id: int = 0,
    limit: int = 500,
    x_admin_token: str = Header(default=""),
) -> JSONResponse:
    _authorize(request, x_admin_token)
    limit = min(max(1, limit), _MAX_LIMIT)
    with session_scope() as session:
        rows = _page(session, TransactionLog, since_id, limit)
        items = [
            {
                "id": r.id,
                "timestamp": _iso(r.timestamp),
                "correlation_id": r.correlation_id,
                "direction": _val(r.direction),
                "operation": _val(r.operation),
                "entity_type": r.entity_type,
                "entity_ref": r.entity_ref,
                "status": _val(r.status),
                "summary": r.summary,
                "detail": r.detail,
            }
            for r in rows
        ]
    return JSONResponse(_envelope(items))


@router.get("/mappings")
async def export_mappings(
    request: Request,
    since_id: int = 0,
    limit: int = 500,
    x_admin_token: str = Header(default=""),
) -> JSONResponse:
    _authorize(request, x_admin_token)
    limit = min(max(1, limit), _MAX_LIMIT)
    with session_scope() as session:
        rows = _page(session, EntityMapping, since_id, limit)
        items = [
            {
                "id": r.id,
                "canonical_entity_type": _val(r.canonical_entity_type),
                "autotask_entity_type": r.autotask_entity_type,
                "autotask_id": r.autotask_id,
                "ghl_entity_type": r.ghl_entity_type,
                "ghl_id": r.ghl_id,
                "last_synced_at": _iso(r.last_synced_at),
            }
            for r in rows
        ]
    return JSONResponse(_envelope(items))


@router.get("/approvals")
async def export_approvals(
    request: Request,
    since_id: int = 0,
    limit: int = 500,
    x_admin_token: str = Header(default=""),
) -> JSONResponse:
    _authorize(request, x_admin_token)
    limit = min(max(1, limit), _MAX_LIMIT)
    with session_scope() as session:
        rows = _page(session, ApprovalQueue, since_id, limit)
        items = [
            {
                "id": r.id,
                "created_at": _iso(r.created_at),
                "status": _val(r.status),
                "severity": _val(r.severity),
                "approval_type": _val(r.approval_type),
                "canonical_entity_type": _val(r.canonical_entity_type),
                "autotask_id": r.autotask_id,
                "ghl_id": r.ghl_id,
                "detected_reason": r.detected_reason,
                "decided_at": _iso(r.decided_at),
                "decided_by": r.decided_by,
                "correlation_id": r.correlation_id,
            }
            for r in rows
        ]
    return JSONResponse(_envelope(items))
