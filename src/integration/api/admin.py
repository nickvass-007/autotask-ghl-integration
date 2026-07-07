"""Minimal ops web UI (local dev convenience; the Teams bot is the primary ops
surface per Spec §11).

GET /admin       — self-contained HTML dashboard (no data embedded)
GET /admin/data  — JSON: environment, pending approvals, recent transactions

⚠️ The app is reachable through the public dev tunnel (ngrok), so /admin/data is
protected: if APPROVAL_CALLBACK_SECRET is configured the caller must present it
in `x-admin-token`; if it is NOT configured, tunnel traffic (identified by the
X-Forwarded-For header the tunnel adds) is rejected and only direct localhost
requests are served. Approve/Reject actions reuse POST /approvals/{id}/decide,
which independently verifies the same secret (Spec §11.1).
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from ..config.settings import get_settings
from ..db.enums import ApprovalStatus
from ..db.models import ApprovalQueue, SyncCriteria, TransactionLog
from ..db.session import session_scope
from ..sync.criteria import FILTERABLE_FIELDS, OPERATORS, load_rules

router = APIRouter()


def _enum_val(v: object) -> str:
    return getattr(v, "value", str(v))


def _authorize(request: Request, token: str) -> None:
    """Shared admin auth: token when configured; localhost-only via tunnel otherwise."""
    settings = get_settings()
    if settings.approval_callback_secret:
        if token != settings.approval_callback_secret:
            raise HTTPException(status_code=401, detail="bad or missing x-admin-token")
    elif "x-forwarded-for" in {k.lower() for k in request.headers}:
        # No secret configured -> serve only direct localhost requests, not tunnel traffic.
        raise HTTPException(status_code=401, detail="set APPROVAL_CALLBACK_SECRET for tunnel access")


@router.get("/admin/data")
async def admin_data(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    settings = get_settings()
    _authorize(request, x_admin_token)

    env = settings.environment
    with session_scope() as session:
        approvals = list(
            session.execute(
                select(ApprovalQueue)
                .where(ApprovalQueue.environment == env, ApprovalQueue.status == ApprovalStatus.PENDING)
                .order_by(ApprovalQueue.created_at.desc())
                .limit(100)
            ).scalars()
        )
        transactions = list(
            session.execute(
                select(TransactionLog)
                .where(TransactionLog.environment == env)
                .order_by(TransactionLog.timestamp.desc())
                .limit(50)
            ).scalars()
        )
        return JSONResponse(
            {
                "environment": env.value,
                "is_production": settings.is_production,
                "deployment_label": settings.deployment_label,
                "decide_enabled": bool(settings.approval_callback_secret),
                "approvals": [
                    {
                        "id": a.id,
                        "type": _enum_val(a.approval_type),
                        "severity": _enum_val(a.severity),
                        "reason": a.detected_reason,
                        "autotask_id": a.autotask_id,
                        "ghl_id": a.ghl_id,
                        "proposed_change": a.proposed_change,
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in approvals
                ],
                "transactions": [
                    {
                        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                        "direction": _enum_val(t.direction),
                        "operation": _enum_val(t.operation),
                        "entity_ref": t.entity_ref,
                        "status": _enum_val(t.status),
                        "summary": t.summary,
                    }
                    for t in transactions
                ],
                "criteria": [
                    {
                        "id": r.id,
                        "field": r.field,
                        "operator": r.operator,
                        "value": r.value,
                    }
                    for r in load_rules(session)
                ],
            }
        )


# ── Customer sync criteria (which Autotask customers flow to GHL) ─────────────
@router.get("/admin/criteria/fields")
async def criteria_fields(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    """Filterable Account fields with live picklist labels so operators pick by
    name. Falls back to plain text inputs when Autotask is unreachable."""
    _authorize(request, x_admin_token)
    fields: dict[str, dict] = {}
    autotask = None
    for name, meta in FILTERABLE_FIELDS.items():
        entry: dict = {"label": meta["label"], "options": None}
        if meta["picklist"]:
            try:
                if autotask is None:
                    from .deps import get_autotask

                    autotask = await get_autotask()
                entry["options"] = await autotask.get_picklist_labels("Companies", name)
            except Exception:
                entry["options"] = None
        fields[name] = entry
    return JSONResponse({"fields": fields, "operators": list(OPERATORS)})


@router.post("/admin/criteria")
async def add_criteria(request: Request, x_admin_token: str = Header(default="")) -> JSONResponse:
    _authorize(request, x_admin_token)
    body = await request.json()
    field_name = str(body.get("field", "")).strip()
    operator = str(body.get("operator", "eq")).strip()
    value = str(body.get("value", "")).strip()
    if field_name not in FILTERABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"field must be one of {list(FILTERABLE_FIELDS)}")
    if operator not in OPERATORS:
        raise HTTPException(status_code=400, detail=f"operator must be one of {list(OPERATORS)}")
    if not value:
        raise HTTPException(status_code=400, detail="value is required")
    with session_scope() as session:
        row = SyncCriteria(
            environment=get_settings().environment,
            entity_type="contact",
            field=field_name,
            operator=operator,
            value=value,
        )
        session.add(row)
        session.flush()
        return JSONResponse({"id": row.id, "field": field_name, "operator": operator, "value": value})


@router.delete("/admin/criteria/{rule_id}")
async def delete_criteria(
    rule_id: int, request: Request, x_admin_token: str = Header(default="")
) -> JSONResponse:
    _authorize(request, x_admin_token)
    with session_scope() as session:
        row = session.get(SyncCriteria, rule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="rule not found")
        session.delete(row)
    return JSONResponse({"deleted": rule_id})


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autotask ⇄ GHL — Ops</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --muted:#667085;
          --line:#e4e7ec; --accent:#2563eb; --ok:#067647; --warn:#b54708; --bad:#b42318; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#101318; --card:#181c23; --ink:#e6e8eb; --muted:#98a2b3; --line:#2a3038; }
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--ink); font:14px/1.5 system-ui,Segoe UI,sans-serif; padding:24px; }
  .wrap { max-width:1100px; margin:0 auto; display:grid; gap:16px; }
  .banner { padding:10px 16px; border-radius:8px; font-weight:600; letter-spacing:.02em; }
  .banner.prod { background:#b42318; color:#fff; }
  .banner.sandbox { background:#067647; color:#fff; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
  h2 { font-size:15px; margin-bottom:10px; }
  .muted { color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  .pill { display:inline-block; padding:1px 8px; border-radius:99px; font-size:12px; font-weight:600; }
  .pill.high, .pill.error, .pill.conflict { background:#fee4e2; color:var(--bad); }
  .pill.med, .pill.skipped { background:#fef0c7; color:var(--warn); }
  .pill.low, .pill.success { background:#d1fadf; color:var(--ok); }
  .approval { border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:10px; }
  .approval .head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px; }
  .btn { border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:6px;
         padding:5px 12px; cursor:pointer; font-weight:600; }
  .btn.approve { background:var(--ok); border-color:var(--ok); color:#fff; }
  .btn.reject { background:var(--bad); border-color:var(--bad); color:#fff; }
  .btn:disabled { opacity:.5; cursor:not-allowed; }
  input[type=text], input[type=password] { border:1px solid var(--line); border-radius:6px;
         padding:5px 8px; background:var(--bg); color:var(--ink); }
  pre { background:var(--bg); border-radius:6px; padding:8px; overflow-x:auto; font-size:12px; }
  details summary { cursor:pointer; color:var(--muted); }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  #status { min-height:1.2em; }
</style>
</head>
<body>
<div class="wrap">
  <div id="banner" class="banner">Loading…</div>
  <div class="card">
    <div class="row">
      <label for="token"><b>Ops token</b> <span class="muted">(APPROVAL_CALLBACK_SECRET)</span></label>
      <input type="password" id="token" placeholder="paste token" size="34">
      <button class="btn" onclick="saveToken()">Save</button>
      <button class="btn" onclick="load()">Refresh</button>
      <span id="status" class="muted"></span>
    </div>
  </div>
  <div class="card">
    <h2>Customer sync criteria <span class="muted">— which Autotask customers flow to GHL</span></h2>
    <div id="rules" class="muted">Loading…</div>
    <div class="row" style="margin-top:10px">
      <select id="crit-field"></select>
      <select id="crit-op">
        <option value="eq">is</option><option value="ne">is not</option>
        <option value="in">is any of</option><option value="not_in">is none of</option>
      </select>
      <span id="crit-value-slot"><input type="text" id="crit-value" placeholder="value" size="18"></span>
      <button class="btn" onclick="addRule()">Add rule</button>
    </div>
    <div class="muted" style="margin-top:6px">Rules AND together against the contact's Account.
      No rules = every customer syncs. Applies to the outbound mirror + classification push only —
      inbound GHL webhooks still go through the normal gated pipeline.</div>
  </div>
  <div class="card"><h2>Pending approvals <span id="acount" class="muted"></span></h2><div id="approvals"></div></div>
  <div class="card"><h2>Recent transactions</h2><div style="overflow-x:auto"><table id="tx">
    <thead><tr><th>Time</th><th>Op</th><th>Status</th><th>Entity</th><th>Summary</th></tr></thead>
    <tbody></tbody></table></div></div>
</div>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function token() { return localStorage.getItem("opsToken") || ""; }
function saveToken() { localStorage.setItem("opsToken", $("#token").value.trim()); load(); }

async function load() {
  $("#status").textContent = "loading…";
  try {
    const r = await fetch("/admin/data", { headers: { "x-admin-token": token() } });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    render(await r.json());
    $("#status").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) { $("#status").textContent = "error: " + e.message; }
}

function render(d) {
  const b = $("#banner");
  b.className = "banner " + (d.is_production ? "prod" : "sandbox");
  b.textContent = "ACTIVE ENVIRONMENT: " + d.environment.toUpperCase() + "  (" + d.deployment_label + ")";
  loadCriteriaFields().then(() => renderRules(d.criteria || []));
  $("#acount").textContent = "(" + d.approvals.length + ")";
  $("#approvals").innerHTML = d.approvals.length ? d.approvals.map(a => {
    const cands = (a.proposed_change?.candidates || []).map(c =>
      `<button class="btn" onclick="pickId(${a.id}, '${esc(c.id ?? c.source_id ?? "")}')">use ${esc(c.name || c.id)}</button>`).join(" ");
    return `<div class="approval" id="ap-${a.id}">
      <div class="head">#${a.id} <span class="pill ${esc(a.severity)}">${esc(a.severity)}</span>
        <b>${esc(a.type)}</b> <span class="muted">${esc(a.created_at || "")}</span></div>
      <div>${esc(a.reason)}</div>
      <div class="muted">GHL: ${esc(a.ghl_id || "—")} · Autotask: ${esc(a.autotask_id || "—")}</div>
      <details><summary>proposed change</summary><pre>${esc(JSON.stringify(a.proposed_change, null, 2))}</pre></details>
      <div class="row" style="margin-top:8px">
        ${d.decide_enabled ? "" : '<span class="pill med">read-only: set APPROVAL_CALLBACK_SECRET to decide</span>'}
        <input type="text" id="chosen-${a.id}" placeholder="chosen id (optional)" size="18">
        ${cands}
        <button class="btn approve" ${d.decide_enabled ? "" : "disabled"} onclick="decide(${a.id}, 'approve')">Approve</button>
        <button class="btn reject" ${d.decide_enabled ? "" : "disabled"} onclick="decide(${a.id}, 'reject')">Reject</button>
      </div></div>`;
  }).join("") : '<span class="muted">Queue is empty.</span>';
  $("#tx tbody").innerHTML = d.transactions.map(t =>
    `<tr><td>${esc((t.timestamp || "").replace("T", " ").slice(0, 19))}</td>
     <td>${esc(t.operation)}</td><td><span class="pill ${esc(t.status)}">${esc(t.status)}</span></td>
     <td>${esc(t.entity_ref || "—")}</td><td>${esc(t.summary)}</td></tr>`).join("");
}

function pickId(id, val) { $("#chosen-" + id).value = val; }

// ── Customer sync criteria ──────────────────────────────────────────────────
let critFields = null;

async function loadCriteriaFields() {
  if (critFields) return critFields;
  const r = await fetch("/admin/criteria/fields", { headers: { "x-admin-token": token() } });
  if (!r.ok) return null;
  critFields = (await r.json()).fields;
  const sel = $("#crit-field");
  sel.innerHTML = Object.entries(critFields).map(([k, v]) =>
    `<option value="${esc(k)}">${esc(v.label)}</option>`).join("");
  sel.onchange = renderValueInput;
  renderValueInput();
  return critFields;
}

function renderValueInput() {
  const meta = critFields?.[$("#crit-field").value];
  const slot = $("#crit-value-slot");
  if (meta?.options && Object.keys(meta.options).length) {
    slot.innerHTML = `<select id="crit-value">` + Object.entries(meta.options).map(([v, l]) =>
      `<option value="${esc(v)}">${esc(l)}</option>`).join("") + `</select>`;
  } else {
    slot.innerHTML = `<input type="text" id="crit-value" placeholder="value" size="18">`;
  }
}

function renderRules(rules) {
  const labelFor = (f, v) => {
    const opts = critFields?.[f]?.options;
    if (!opts) return v;
    return v.split(",").map(x => opts[x.trim()] || x.trim()).join(", ");
  };
  const opText = { eq: "is", ne: "is not", in: "is any of", not_in: "is none of" };
  $("#rules").innerHTML = rules.length ? rules.map(r =>
    `<div class="row" style="margin-bottom:4px">
       <span class="pill low">${esc(critFields?.[r.field]?.label || r.field)}</span>
       <span>${esc(opText[r.operator] || r.operator)}</span>
       <b>${esc(labelFor(r.field, r.value))}</b>
       <button class="btn" onclick="deleteRule(${r.id})">✕</button>
     </div>`).join("")
    : '<span class="muted">No rules — every customer syncs.</span>';
}

async function addRule() {
  const body = {
    field: $("#crit-field").value,
    operator: $("#crit-op").value,
    value: $("#crit-value").value.trim(),
  };
  if (!body.value) { alert("Pick or enter a value first."); return; }
  const r = await fetch("/admin/criteria", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-admin-token": token() },
    body: JSON.stringify(body),
  });
  if (!r.ok) { alert("Failed: " + ((await r.json()).detail || r.status)); return; }
  load();
}

async function deleteRule(id) {
  if (!confirm("Remove this rule? (Removing rules widens the sync audience.)")) return;
  const r = await fetch("/admin/criteria/" + id, {
    method: "DELETE", headers: { "x-admin-token": token() },
  });
  if (!r.ok) { alert("Failed: " + ((await r.json()).detail || r.status)); return; }
  load();
}

async function decide(id, decision) {
  if (!confirm(decision.toUpperCase() + " approval #" + id + "?")) return;
  const chosen = $("#chosen-" + id).value.trim();
  const body = { decision, decided_by: "admin-ui" };
  if (chosen) body.chosen_id = chosen;
  try {
    const r = await fetch(`/approvals/${id}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-approval-token": token() },
      body: JSON.stringify(body),
    });
    const out = await r.json();
    if (!r.ok) throw new Error(out.detail || r.status);
    alert("#" + id + " → " + out.action);
    load();
  } catch (e) { alert("Failed: " + e.message); }
}

$("#token").value = token();
load();
setInterval(load, 30000);
</script>
</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    # The page itself embeds no data; everything loads via /admin/data with the token.
    return HTMLResponse(_PAGE)
