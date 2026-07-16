"""Interlinked Sync Control Centre — single-file portal UI.

No build tooling: one HTML shell, hash routing, brand tokens as CSS variables
(swap the :root block to rebrand — nothing brand-specific lives in components).
"""

PORTAL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Interlinked Sync Control Centre</title>
<style>
:root{
  /* ── Interlinked brand tokens (edit here to rebrand) ── */
  --brand-primary:#2f6fed; --brand-secondary:#1b2a4a; --brand-accent:#37c2ff;
  --brand-danger:#e5484d; --brand-success:#30a46c; --brand-warning:#f5a524;
  --brand-bg:#0d1117; --brand-panel:#161b23; --brand-panel-2:#1c2330;
  --brand-border:#2a3242; --brand-text:#e6e9ef; --brand-muted:#8b95a7;
}
*{box-sizing:border-box;margin:0}
body{background:var(--brand-bg);color:var(--brand-text);font:14px/1.5 "Segoe UI",system-ui,sans-serif;display:flex;min-height:100vh}
aside{width:220px;background:var(--brand-secondary);padding:16px 0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:0 16px 14px;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:10px}
.logo b{font-size:17px;letter-spacing:.06em}
.logo span{display:block;font-size:11px;color:var(--brand-accent);letter-spacing:.14em;margin-top:2px}
nav a{display:block;padding:9px 18px;color:#cdd6e4;text-decoration:none;font-weight:500;border-left:3px solid transparent}
nav a.active,nav a:hover{background:rgba(255,255,255,.06);color:#fff;border-left-color:var(--brand-accent)}
main{flex:1;padding:20px 26px;min-width:0}
.banner{padding:9px 16px;border-radius:8px;font-weight:700;margin-bottom:16px;letter-spacing:.03em}
.banner.prod{background:var(--brand-danger);color:#fff}
.banner.sandbox{background:var(--brand-success);color:#fff}
.card{background:var(--brand-panel);border:1px solid var(--brand-border);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:15px;margin-bottom:10px} h3{font-size:13px;margin:12px 0 6px;color:var(--brand-muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--brand-border);vertical-align:top}
th{color:var(--brand-muted);font-weight:600;white-space:nowrap}
tr:hover td{background:var(--brand-panel-2)}
.muted{color:var(--brand-muted)} .right{text-align:right}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:11.5px;font-weight:700;white-space:nowrap}
.pill.ok,.pill.success,.pill.succeeded,.pill.approved,.pill.enabled,.pill.healthy{background:rgba(48,164,108,.18);color:var(--brand-success)}
.pill.warn,.pill.pending,.pill.review_required,.pill.dry_run_required,.pill.paused,.pill.skipped,.pill.conflict{background:rgba(245,165,36,.16);color:var(--brand-warning)}
.pill.bad,.pill.failed,.pill.error,.pill.blocked,.pill.cancelled,.pill.disabled{background:rgba(229,72,77,.16);color:var(--brand-danger)}
.pill.info,.pill.running,.pill.dry_run,.pill.live,.pill.scheduled,.pill.manual{background:rgba(47,111,237,.18);color:var(--brand-accent)}
.btn{border:1px solid var(--brand-border);background:var(--brand-panel-2);color:var(--brand-text);border-radius:7px;padding:6px 13px;cursor:pointer;font-weight:600;font-size:13px}
.btn:hover{border-color:var(--brand-primary)}
.btn.primary{background:var(--brand-primary);border-color:var(--brand-primary);color:#fff}
.btn.danger{background:var(--brand-danger);border-color:var(--brand-danger);color:#fff}
.btn.success{background:var(--brand-success);border-color:var(--brand-success);color:#fff}
.btn:disabled{opacity:.45;cursor:not-allowed}
input,select,textarea{border:1px solid var(--brand-border);border-radius:7px;padding:6px 9px;background:var(--brand-bg);color:var(--brand-text);font:inherit}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.stat{background:var(--brand-panel-2);border:1px solid var(--brand-border);border-radius:9px;padding:12px}
.stat b{font-size:22px;display:block}
.scroll{overflow-x:auto}
a{color:var(--brand-accent)} pre{background:var(--brand-bg);border-radius:7px;padding:9px;overflow-x:auto;font-size:12px}
#toast{position:fixed;bottom:18px;right:18px;background:var(--brand-panel-2);border:1px solid var(--brand-border);border-radius:9px;padding:10px 16px;display:none;max-width:420px}
th.sortable{cursor:pointer;user-select:none} th.sortable:hover{color:var(--brand-text)}
.skel{height:14px;border-radius:5px;background:linear-gradient(90deg,var(--brand-panel-2) 25%,var(--brand-border) 50%,var(--brand-panel-2) 75%);background-size:200% 100%;animation:sh 1.2s infinite;margin:9px 0}
@keyframes sh{from{background-position:200% 0}to{background-position:-200% 0}}
#modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:50}
#modal{background:var(--brand-panel);border:1px solid var(--brand-border);border-radius:12px;padding:20px;max-width:440px;width:92%}
@media (max-width:900px){
  body{flex-direction:column}
  aside{width:100%;padding:8px 0;position:sticky;top:0;z-index:40}
  .logo{padding:4px 14px 8px;margin-bottom:4px}
  nav{display:flex;overflow-x:auto;white-space:nowrap}
  nav a{padding:8px 12px;border-left:none;border-bottom:3px solid transparent}
  nav a.active,nav a:hover{border-left:none;border-bottom-color:var(--brand-accent)}
  aside>div:last-child{display:none}
  main{padding:14px}
  .grid{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
}
</style>
</head>
<body>
<aside>
  <div class="logo"><b>INTERLINKED</b><span>SYNC CONTROL CENTRE</span></div>
  <nav id="nav">
    <a href="#dashboard">Dashboard</a><a href="#customers">Customers</a>
    <a href="#contacts">Contacts</a><a href="#profiles">Sync Profiles</a>
    <a href="#jobs">Sync Jobs</a><a href="#approvals">Approvals</a>
    <a href="#logs">Logs</a><a href="#settings">Settings</a>
  </nav>
  <div style="margin-top:auto;padding:12px 16px" class="muted">
    <label style="font-size:11px">OPS TOKEN</label><br>
    <input type="password" id="token" size="14" style="margin:4px 0">
    <button class="btn" onclick="saveToken()">Set</button>
  </div>
</aside>
<main><div id="banner" class="banner">…</div><div id="page"></div></main>
<div id="toast"></div>
<div id="modal-bg"><div id="modal"><div id="modal-msg" style="margin-bottom:14px"></div>
  <div class="row" style="justify-content:flex-end"><button class="btn" id="modal-no">Cancel</button>
  <button class="btn danger" id="modal-yes">Confirm</button></div></div></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const token=()=>localStorage.getItem("opsToken")||"";
function saveToken(){localStorage.setItem("opsToken",$("#token").value.trim());route();}
function toast(msg){const t=$("#toast");t.textContent=msg;t.style.display="block";setTimeout(()=>t.style.display="none",4200);}
function confirmBox(msg){return new Promise(res=>{const bg=$("#modal-bg");
  $("#modal-msg").textContent=msg;bg.style.display="flex";
  $("#modal-yes").onclick=()=>{bg.style.display="none";res(true);};
  $("#modal-no").onclick=()=>{bg.style.display="none";res(false);};});}
const skeleton=`<div class="card">${'<div class="skel"></div>'.repeat(6)}</div>`;
function makeSortable(){document.querySelectorAll("#page table").forEach(tbl=>{
  const head=tbl.rows[0];if(!head)return;
  [...head.cells].forEach((th,i)=>{th.classList.add("sortable");th.title="Click to sort";
    th.onclick=()=>{const dir=th.dataset.d==="1"?-1:1;th.dataset.d=dir===1?"1":"-1";
      const rows=[...tbl.rows].slice(1);
      rows.sort((a,b)=>{const x=a.cells[i]?.innerText.trim()||"",y=b.cells[i]?.innerText.trim()||"";
        const nx=parseFloat(x.replace(/[^\d.-]/g,"")),ny=parseFloat(y.replace(/[^\d.-]/g,""));
        return (!isNaN(nx)&&!isNaN(ny)?nx-ny:x.localeCompare(y))*dir;});
      rows.forEach(r=>tbl.tBodies[0]?tbl.tBodies[0].appendChild(r):tbl.appendChild(r));};});});}
async function api(path,opts={}){
  const r=await fetch(path,{...opts,headers:{"Content-Type":"application/json","x-admin-token":token(),...(opts.headers||{})}});
  if(!r.ok){const d=await r.json().catch(()=>({detail:r.status}));throw new Error(d.detail||r.status);}
  return r.json();
}
const fmtDt=s=>s?s.replace("T"," ").slice(0,16):"—";
const pill=(v)=>`<span class="pill ${esc(String(v||"").toLowerCase())}">${esc(v??"—")}</span>`;
let AT_BASE="https://ww29.autotask.net";
// Shared pagination state + footer. Every list page registers under a key.
const pg={};
function pgState(key){return pg[key]??=({offset:0,limit:100});}
function pager(key,d,reload){
  const st=pgState(key);st.offset=d.offset;st.limit=d.limit;
  const pages=Math.max(1,Math.ceil(d.total/d.limit));
  const page=Math.floor(d.offset/d.limit)+1;
  const from=d.total?d.offset+1:0,to=Math.min(d.offset+d.limit,d.total);
  window[`pgNav_${key}`]=(dir)=>{st.offset=Math.max(0,st.offset+dir*st.limit);reload();};
  window[`pgSize_${key}`]=(n)=>{st.limit=Number(n);st.offset=0;reload();};
  return `<div class="row" style="margin-top:12px;justify-content:space-between">
    <span class="muted">Showing <b>${from}–${to}</b> of <b>${d.total}</b> records</span>
    <span class="row">
      <button class="btn" ${page<=1?"disabled":""} onclick="pgNav_${key}(-1)">‹ Prev</button>
      <span>Page <b>${page}</b> of <b>${pages}</b></span>
      <button class="btn" ${page>=pages?"disabled":""} onclick="pgNav_${key}(1)">Next ›</button>
      <select onchange="pgSize_${key}(this.value)">
        ${[50,100,200].map(n=>`<option value="${n}" ${n===d.limit?"selected":""}>${n} / page</option>`).join("")}
      </select></span></div>`;}
const atLink=id=>id?`<a target="_blank" href="${AT_BASE}/Mvc/CRM/AccountDetail.mvc?accountId=${esc(id)}">${esc(id)}</a>`:"—";

async function banner(){try{const d=await api("/portal/api/overview");
  const b=$("#banner");b.className="banner "+(d.is_production?"prod":"sandbox");
  b.textContent=(d.is_production?"⚠ PRODUCTION — live Autotask data":"SANDBOX")+"  ·  "+d.deployment_label;
  return d;}catch(e){$("#banner").textContent="Auth needed — set the ops token (bottom left). "+e.message;return null;}}

// ── Pages ──
async function pageDashboard(){const d=await api("/portal/api/overview");const c=d.counts;
  const w=d.work_24h||{},w7=d.work_7d||{},ad=d.approvals_decided||{};
  $("#page").innerHTML=`
  <div class="grid">
    <div class="stat"><b>${c.contacts_linked}</b>Contacts linked</div>
    <div class="stat"><b>${c.companies_linked}</b>Companies linked</div>
    <div class="stat"><b>${c.profiles}</b>Sync profiles</div>
    <div class="stat"><b>${c.pending_approvals}</b>Pending approvals</div>
    <div class="stat"><b>${c.jobs_running}</b>Jobs running</div>
    <div class="stat"><b>${c.jobs_completed}</b>Jobs completed</div>
  </div>
  <div class="card"><h2>Work done</h2><div class="scroll"><table>
    <tr><th>Window</th><th class=right>Records created</th><th class=right>Updated</th><th class=right>Skipped</th><th class=right>Conflicts → approvals</th><th class=right>Errors</th><th class=right>Total operations</th></tr>
    <tr><td>Last 24 hours</td><td class=right><b>${w.created??0}</b></td><td class=right>${w.updated??0}</td><td class=right>${w.skipped??0}</td><td class=right>${w.conflicts??0}</td><td class=right>${w.errors??0}</td><td class=right>${w.total??0}</td></tr>
    <tr><td>Last 7 days</td><td class=right><b>${w7.created??0}</b></td><td class=right>${w7.updated??0}</td><td class=right>${w7.skipped??0}</td><td class=right>${w7.conflicts??0}</td><td class=right>${w7.errors??0}</td><td class=right>${w7.total??0}</td></tr>
  </table></div>
  <div class="row" style="margin-top:8px"><span class="muted">Human decisions:</span>
    ${pill("approved")} <b>${ad.approved??0}</b> <span class="muted">·</span>
    ${pill("failed")} <b>${ad.rejected??0}</b> rejected</div></div>
  <div class="card"><div class="row" style="justify-content:space-between">
    <h2>System health</h2>
    <button class="btn" id="pingbtn" onclick="pingConnections()">Test connections</button></div>
    <div id="health" class="muted">loading…</div></div>
  <div class="card"><h2>14-day activity</h2><div id="trend" class="muted">loading…</div></div>
  <div class="card"><h2>Recent jobs</h2><div class="scroll"><table>
    <tr><th>#</th><th>Profile</th><th>Kind</th><th>Trigger</th><th>Status</th><th>Started</th><th>Duration</th><th>Result</th></tr>
    ${d.recent_jobs.map(j=>`<tr onclick="location.hash='job/${j.id}'" style="cursor:pointer">
      <td>${j.id}</td><td>${j.profile_id??"—"}</td><td>${pill(j.kind)}</td><td>${pill(j.trigger)}</td>
      <td>${pill(j.status)}</td><td>${fmtDt(j.started_at)}</td><td>${j.duration_s??"—"}s</td>
      <td class="muted">${esc(JSON.stringify(j.summary||{}).slice(0,80))}</td></tr>`).join("")}
  </table></div></div>`;
  renderHealth();renderTrend();}

async function renderHealth(){try{
  const h=await api("/portal/api/health");const m=h.mappings||{},bg=h.background||{},cn=h.connectors||{};
  const dot=ok=>`<span style="color:${ok?'var(--brand-success)':'var(--brand-danger)'}">●</span>`;
  const onoff=v=>v?pill("enabled"):pill("disabled");
  const cursorRows=(h.cursors||[]).map(c=>`<tr><td>${esc(c.entity_type)}</td><td class="muted">${esc(c.source_system||"—")}</td>
    <td class="muted">${esc(c.cursor||"—")}</td><td>${fmtDt(c.updated_at)}</td></tr>`).join("")
    ||`<tr><td colspan=4 class=muted>No polling cursors yet — the poller hasn't swept.</td></tr>`;
  $("#health").innerHTML=`
    <div class="grid" style="margin-bottom:10px">
      <div class="stat"><b>${m.contact??0}</b>Contacts mapped</div>
      <div class="stat"><b>${m.company??0}</b>Companies mapped</div>
      <div class="stat"><b>${m.deal??0}</b>Deals mapped</div>
      <div class="stat"><b>${m.service_item??0}</b>Tickets mapped</div>
      <div class="stat"><b>${h.errors_1h??0}</b>Errors (last hour)</div>
    </div>
    <div class="row" style="gap:18px;flex-wrap:wrap">
      <span>${dot(cn.ghl&&cn.ghl.authorized)} <b>GoHighLevel</b> ${cn.ghl&&cn.ghl.authorized?"authorised":"not authorised"}
        <span class="muted">${cn.ghl&&cn.ghl.scopes?`· ${cn.ghl.scopes.length} scopes`:""}</span></span>
      <span>${dot(cn.autotask&&cn.autotask.configured)} <b>Autotask</b> ${cn.autotask&&cn.autotask.configured?"configured":"not configured"}
        <span class="muted">${cn.autotask?`· ${esc(cn.autotask.zone)}`:""}</span></span>
    </div>
    <div class="row" style="gap:18px;margin-top:8px;flex-wrap:wrap">
      <span>Poller ${onoff(bg.poller_enabled)} <span class="muted">every ${bg.poll_interval_s}s</span></span>
      <span>Scheduler ${onoff(bg.scheduler_enabled)}</span>
      <span class="muted">Reconciliation every ${bg.reconciliation_interval_s}s</span>
    </div>
    <div id="pingresult" style="margin-top:8px"></div>
    <h3>Polling cursors</h3><div class="scroll"><table>
      <tr><th>Entity</th><th>Source</th><th>Cursor</th><th>Updated</th></tr>${cursorRows}</table></div>`;
}catch(e){$("#health").textContent="health unavailable: "+e.message;}}

async function pingConnections(){const btn=$("#pingbtn");const out=$("#pingresult");
  btn.disabled=true;btn.textContent="Testing…";out.innerHTML="";
  try{const r=await api("/portal/api/health/ping",{method:"POST"});
    const line=(name,x)=>`<div>${x.ok?pill("ok"):pill("failed")} <b>${name}</b> <span class="muted">${esc(x.detail||"")}</span></div>`;
    out.innerHTML=line("GoHighLevel",r.ghl)+line("Autotask",r.autotask);
  }catch(e){out.innerHTML=`<span class="pill failed">error</span> ${esc(e.message)}`;}
  finally{btn.disabled=false;btn.textContent="Test connections";}}

async function renderTrend(){try{
  const t=await api("/portal/api/trends");const days=t.days;
  const max=Math.max(1,...days.map(d=>d.created+d.updated+d.errors+d.other));
  const W=720,H=120,bw=Math.floor(W/days.length)-6;
  const bars=days.map((d,i)=>{
    const total=d.created+d.updated+d.errors+d.other;
    let y=H-18;const segs=[["created","var(--brand-success)"],["updated","var(--brand-primary)"],["other","#5a6478"],["errors","var(--brand-danger)"]]
      .map(([k,col])=>{const h=Math.round((d[k]/max)*(H-30));y-=h;
        return h?`<rect x="${i*(bw+6)}" y="${y}" width="${bw}" height="${h}" fill="${col}" rx="2"><title>${d.day}: ${d[k]} ${k}</title></rect>`:"";}).join("");
    const lbl=i%2===0?`<text x="${i*(bw+6)+bw/2}" y="${H-4}" font-size="9" fill="var(--brand-muted)" text-anchor="middle">${d.day.slice(5)}</text>`:"";
    return segs+lbl+(total?`<text x="${i*(bw+6)+bw/2}" y="${y-3}" font-size="9" fill="var(--brand-muted)" text-anchor="middle">${total}</text>`:"");}).join("");
  $("#trend").innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px">${bars}</svg>
  <div class="row muted" style="font-size:12px"><span style="color:var(--brand-success)">■</span> created
  <span style="color:var(--brand-primary)">■</span> updated <span style="color:#5a6478">■</span> other
  <span style="color:var(--brand-danger)">■</span> errors</div>`;
}catch(e){$("#trend").textContent="trend unavailable: "+e.message;}}

async function pageCustomers(){const profiles=(await api("/portal/api/profiles")).profiles;
  const sel=location.hash.split("?p=")[1]||"";const st=pgState("cust");
  let d=await api("/portal/api/customers"+(sel?`?profile_id=${sel}`:`?offset=${st.offset}&limit=${st.limit}`));
  AT_BASE=d.autotask_web_base;
  if(sel){ // dry-run preview arrives whole — paginate client-side
    const all=d.customers;
    d={...d,total:all.length,offset:Math.min(st.offset,Math.max(0,all.length-1)),limit:st.limit,
       customers:all.slice(st.offset,st.offset+st.limit)};}
  $("#page").innerHTML=`<div class="card"><div class="row"><h2>Customers</h2>
    <select id="cprof" onchange="location.hash='customers?p='+this.value">
      <option value="">Synced companies (linked)</option>
      ${profiles.map(p=>`<option value="${p.id}" ${String(p.id)===sel?"selected":""}>Dry-run: ${esc(p.name)}</option>`).join("")}
    </select><span class="muted">source: ${esc(d.source)}</span></div>
    <div class="scroll"><table><tr><th>Autotask ID</th><th>Name</th><th>Type</th><th>Classification</th><th>Status</th></tr>
    ${d.customers.map(c=>`<tr onclick="location.hash='customer/${esc(c.id)}'" style="cursor:pointer"><td>${atLink(c.id)}</td><td>${esc(c.name||"—")}</td><td>${esc(c.type||"—")}</td>
      <td>${esc(c.classification||"—")}</td><td>${c.linked?pill("linked"):pill("new")}${c.ghl_id?` <span class="muted">GHL ${esc(c.ghl_id)}</span>`:""}</td></tr>`).join("")||"<tr><td colspan=5 class=muted>No rows — run a dry-run on a profile or sync companies.</td></tr>"}
    </table></div>${pager("cust",d,pageCustomers)}</div>`;}

let contactsQ="";
async function pageContacts(){
  const st=pgState("contacts");const q=contactsQ;
  const d=await api(`/portal/api/contacts?offset=${st.offset}&limit=${st.limit}&q=${encodeURIComponent(q)}`);AT_BASE=d.autotask_web_base;
  $("#page").innerHTML=`<div class="card"><div class="row"><h2>Synced contacts <span class="muted">(${d.total}${q?" matching":" linked"})</span></h2>
    <input id="csearch" placeholder="Search name or email…" value="${esc(q)}" size="26"
      onkeydown="if(event.key==='Enter')contactsSearch(this.value)">
    <button class="btn" onclick="contactsSearch($('#csearch').value)">Search</button>
    ${q?`<button class="btn" onclick="contactsSearch('')">Clear</button>`:""}</div>
  <div class="scroll"><table><tr><th>First name</th><th>Last name</th><th>Email</th><th>Phone</th><th>Company</th><th>Survey</th><th>Autotask ID</th><th>GHL ID</th><th>Last synced</th></tr>
  ${d.contacts.map(c=>`<tr onclick="showContact('${esc(c.autotask_id)}')" style="cursor:pointer">
    <td>${esc(c.first_name||"—")}</td><td>${esc(c.last_name||"—")}</td>
    <td>${c.email?`<a href="mailto:${esc(c.email)}">${esc(c.email)}</a>`:"—"}</td>
    <td>${esc(c.phone||"—")}</td>
    <td>${c.company_id?`<a target="_blank" href="${AT_BASE}/Mvc/CRM/AccountDetail.mvc?accountId=${esc(c.company_id)}">${esc(c.company_name||c.company_id)}</a>`:"—"}</td>
    <td>${c.survey_avg!=null?`<b>${c.survey_avg}</b> ★ <span class="muted">(${c.survey_count})</span>`:'<span class="muted">—</span>'}</td>
    <td><a target="_blank" href="${AT_BASE}/Mvc/CRM/ContactDetail.mvc?contactId=${esc(c.autotask_id)}">${esc(c.autotask_id)}</a></td>
    <td class="muted">${esc(c.ghl_id)}</td><td>${fmtDt(c.last_synced_at)}</td></tr>`).join("")}
  </table></div>
  ${pager("contacts",d,pageContacts)}</div>`;}
function contactsSearch(q){contactsQ=q.trim();pgState("contacts").offset=0;pageContacts();}

async function showContact(id){
  const d=await api(`/portal/api/contacts/${id}/detail`);AT_BASE=d.autotask_web_base;
  const c=d.detail||{};
  $("#page").innerHTML=`<div class="card"><div class="row">
    <button class="btn" onclick="pageContacts()">← Back</button>
    <h2>${esc(c.first_name||"")} ${esc(c.last_name||"")}</h2>
    ${d.excluded?pill("disabled")+" sync disabled":pill("enabled")+" syncing"}</div>
    <div class="grid" style="margin-top:10px">
      <div class="stat"><b>${esc(c.email||"—")}</b>Email</div>
      <div class="stat"><b>${esc(c.phone||"—")}</b>Phone</div>
      <div class="stat"><b>${c.company_id?`<a target="_blank" href="${AT_BASE}/Mvc/CRM/AccountDetail.mvc?accountId=${esc(c.company_id)}">${esc(c.company_name||c.company_id)}</a>`:"—"}</b>Company</div>
      <div class="stat"><b>${fmtDt(d.last_synced_at)}</b>Last synced</div>
    </div>
    <div class="row" style="margin-top:10px">
      <a class="btn" target="_blank" href="${AT_BASE}/Mvc/CRM/ContactDetail.mvc?contactId=${esc(d.autotask_id)}">Open in Autotask</a>
      <span class="muted">GHL id: ${esc(d.ghl_id||"—")}</span>
      ${d.excluded
        ?`<button class="btn success" onclick="api('/portal/api/exclusions/contact/${esc(d.autotask_id)}',{method:'DELETE'}).then(()=>showContact('${esc(d.autotask_id)}'))">Enable sync</button>`
        :`<button class="btn danger" onclick="api('/portal/api/exclusions',{method:'POST',body:JSON.stringify({entity_type:'contact',autotask_id:'${esc(d.autotask_id)}',reason:'portal detail view'})}).then(()=>showContact('${esc(d.autotask_id)}'))">Disable sync</button>`}
    </div></div>
  <div class="card"><h2>Sync history</h2><div class="scroll"><table>
    <tr><th>Time</th><th>Direction</th><th>Op</th><th>Status</th><th>Summary</th></tr>
    ${d.history.map(h=>`<tr><td>${fmtDt(h.timestamp)}</td><td class="muted">${esc(h.direction)}</td>
      <td>${esc(h.operation)}</td><td>${pill(h.status)}</td><td>${esc(h.summary)}</td></tr>`).join("")||"<tr><td colspan=5 class=muted>No history.</td></tr>"}
  </table></div></div>`;}

const RULE_FIELDS={companyType:"Customer type",classification:"Classification",isActive:"Account active",ownerResourceID:"Account owner",marketSegmentID:"Market segment"};
function ruleRows(criteria){return (criteria?.rules||[]).map(r=>`${RULE_FIELDS[r.field]||r.field} ${r.operator} ${r.value}`).join(" AND ")||"(no rules — everything matches)";}

async function pageProfiles(){const d=await api("/portal/api/profiles");
  $("#page").innerHTML=`<div class="card"><div class="row"><h2>Sync Profiles</h2>
    <button class="btn primary" onclick="createProfile()">+ New profile</button></div>
    <div class="scroll"><table>
    <tr><th>Profile</th><th>Type</th><th>Criteria</th><th>Schedule</th><th>Last run</th><th>Next run</th><th>Status</th><th></th></tr>
    ${d.profiles.map(p=>{
      const st=!p.enabled?"disabled":p.schedule_paused?"paused":p.review_state;
      return `<tr><td><a href="#profile/${p.id}"><b>${esc(p.name)}</b></a><br><span class="muted">${esc(p.description)}</span></td>
      <td>${esc(p.sync_type)}</td><td class="muted">${esc(ruleRows(p.criteria))}</td>
      <td>${p.schedule_enabled?esc(p.schedule_type)+(p.schedule_paused?" (paused)":""):"manual"}</td>
      <td>${fmtDt(p.last_run_at)}</td><td>${fmtDt(p.next_run_at)}</td><td>${pill(st)}</td>
      <td><a href="#profile/${p.id}">open</a></td></tr>`;}).join("")||"<tr><td colspan=8 class=muted>No profiles yet — create one to define a sync audience.</td></tr>"}
    </table></div></div>`;}

async function createProfile(){const name=prompt("Profile name (e.g. Active Managed Customers → GHL):");if(!name)return;
  const p=await api("/portal/api/profiles",{method:"POST",body:JSON.stringify({name,criteria:{rules:[{field:"isActive",operator:"eq",value:"true"},{field:"companyType",operator:"eq",value:"1"}]}})});
  location.hash="profile/"+p.id;}

function breakdownTable(rows){return `<div class="scroll"><table><tr><th>Customer type</th><th>Classification</th><th class=right>Count</th><th>Action</th></tr>
  ${(rows||[]).map(r=>`<tr><td>${esc(r.type)}</td><td>${esc(r.classification)}</td><td class=right>${r.count}</td><td>${pill(r.action==="Sync"?"ok":"skipped")} ${esc(r.action)}</td></tr>`).join("")}</table></div>`;}

async function pageProfile(id){const p=await api(`/portal/api/profiles/${id}`);const s=p.last_dry_run_summary;
  const st=!p.enabled?"disabled":p.schedule_paused?"paused":p.review_state;
  const nextKind=p.blockers.length? (p.schedule_enabled?"Dry run only (blocked from live: "+p.blockers.join("; ")+")":"Blocked: "+p.blockers.join("; ")) : (p.schedule_enabled?"LIVE sync":"Manual only");
  $("#page").innerHTML=`
  <div class="card"><div class="row"><h2>${esc(p.name)}</h2>${pill(st)}
    <span class="muted">${esc(p.description)}</span></div>
    <div class="row" style="margin:10px 0">
      <button class="btn" onclick="dryRun(${p.id})">▶ Run dry-run</button>
      <button class="btn success" ${p.blockers.length?"disabled title='"+esc(p.blockers.join("; "))+"'":""} onclick="liveRun(${p.id})">⚡ Run live sync now</button>
      <button class="btn primary" ${p.review_state==="pending"||p.review_state==="review_required"?"":"disabled"} onclick="approveProfile(${p.id})">✔ Approve for live</button>
      <button class="btn" onclick="editCriteria(${p.id})">Edit criteria</button>
      <button class="btn" onclick="editSchedule(${p.id})">Edit schedule</button>
      <button class="btn" onclick="api('/portal/api/profiles/${p.id}/duplicate',{method:'POST'}).then(x=>location.hash='profile/'+x.id)">Duplicate</button>
      <button class="btn danger" onclick="confirmBox('Delete this profile and its schedule?').then(ok=>ok&&api('/portal/api/profiles/${p.id}',{method:'DELETE'}).then(()=>location.hash='profiles'))">Delete</button>
    </div>
    ${p.review_reason?`<div class="pill warn" style="margin-bottom:8px">⚠ ${esc(p.review_reason)}</div>`:""}
    <div class="grid">
      <div class="stat"><b>${esc(p.review_state)}</b>Review state</div>
      <div class="stat"><b>${p.dry_run_current?"current":"stale"}</b>Dry-run vs criteria</div>
      <div class="stat"><b>${p.schedule_enabled?esc(p.schedule_type):"manual"}</b>Schedule ${p.schedule_paused?"(paused)":""}</div>
      <div class="stat"><b>${fmtDt(p.next_run_at)}</b>Next run → ${esc(nextKind)}</div>
    </div>
    <h3>Criteria</h3><div class="muted">${esc(ruleRows(p.criteria))}</div></div>
  ${s?`<div class="card"><h2>Latest dry-run (Would Sync)</h2>
    <div class="grid">
      <div class="stat"><b>${s.customers_matched}</b>Matching customers</div>
      <div class="stat"><b>${s.customers_linked}</b>Already linked</div>
      <div class="stat"><b>${s.customers_new}</b>New to create/link</div>
      <div class="stat"><b>${s.customers_excluded}</b>Excluded</div>
      <div class="stat"><b>${s.contacts_matched}</b>Contacts matched</div>
      <div class="stat"><b>${s.warnings}</b>Warnings</div>
      <div class="stat"><b>${s.conflicts}</b>Conflicts</div>
    </div>
    <h3>What would sync, by customer type</h3>${breakdownTable(s.type_breakdown)}
    <h3>Matched customers (first ${Math.min((s.matched_preview||[]).length,500)}) — <a href="#customers?p=${p.id}">full view</a></h3>
    <h3>Excluded examples</h3><div class="scroll"><table><tr><th>ID</th><th>Name</th><th>Why excluded</th></tr>
    ${(s.excluded_examples||[]).map(e=>`<tr><td>${atLink(e.id)}</td><td>${esc(e.name||"—")}</td><td class="muted">${esc((e.reasons||[]).join(", ")||"—")}</td></tr>`).join("")}</table></div>
    <div class="row" style="margin-top:8px"><button class="btn" onclick="exportDryRun(${p.id})">Export dry-run report</button></div>
  </div>`:`<div class="card muted">No dry-run yet — run one to preview what this profile would sync.</div>`}
  <div class="card"><h2>Run history (Has Synced)</h2><div class="scroll"><table>
    <tr><th>#</th><th>Kind</th><th>Trigger</th><th>By</th><th>Status</th><th>Started</th><th>Duration</th><th>Result</th></tr>
    ${p.jobs.map(j=>`<tr onclick="location.hash='job/${j.id}'" style="cursor:pointer"><td>${j.id}</td><td>${pill(j.kind)}</td>
      <td>${pill(j.trigger)}</td><td>${esc(j.started_by||"—")}</td><td>${pill(j.status)}</td>
      <td>${fmtDt(j.started_at)}</td><td>${j.duration_s??"—"}s</td>
      <td class="muted">${esc(JSON.stringify(j.summary||{}).slice(0,90))}</td></tr>`).join("")||"<tr><td colspan=8 class=muted>Never run.</td></tr>"}
  </table></div></div>`;}

async function showCustomer(id){
  const d=await api(`/portal/api/customers/${id}/detail`);AT_BASE=d.autotask_web_base;
  const a=d.account||{};
  $("#page").innerHTML=`<div class="card"><div class="row">
    <button class="btn" onclick="location.hash='customers'">← Back</button>
    <h2>${esc(a.name||id)}</h2>${a.active?pill("enabled")+" active":pill("disabled")+" inactive"}
    ${d.excluded?pill("disabled")+" sync disabled":pill("enabled")+" syncing"}</div>
    <div class="grid" style="margin-top:10px">
      <div class="stat"><b>${esc(a.type||"—")}</b>Customer type</div>
      <div class="stat"><b>${esc(a.classification||"—")}</b>Classification</div>
      <div class="stat"><b>${a.website?`<a target="_blank" href="//${esc(a.website)}">${esc(a.website)}</a>`:"—"}</b>Website</div>
      <div class="stat"><b>${esc(d.ghl_business_id||"not yet synced")}</b>GHL Business</div>
    </div>
    <div class="row" style="margin-top:10px">
      <a class="btn" target="_blank" href="${AT_BASE}/Mvc/CRM/AccountDetail.mvc?accountId=${esc(d.autotask_id)}">Open in Autotask</a>
      ${d.excluded
        ?`<button class="btn success" onclick="api('/portal/api/exclusions/account/${esc(d.autotask_id)}',{method:'DELETE'}).then(()=>showCustomer('${esc(d.autotask_id)}'))">Enable sync</button>`
        :`<button class="btn danger" onclick="confirmBox('Disable sync for this whole account? None of its contacts will mirror.').then(ok=>ok&&api('/portal/api/exclusions',{method:'POST',body:JSON.stringify({entity_type:'account',autotask_id:'${esc(d.autotask_id)}',reason:'portal customer view'})}).then(()=>showCustomer('${esc(d.autotask_id)}')))">Disable sync</button>`}
    </div></div>
  <div class="card"><h2>Contacts (${d.contacts.length})</h2><div class="scroll"><table>
    <tr><th>Name</th><th>Email</th><th>Active</th><th>Synced to GHL</th><th></th></tr>
    ${d.contacts.map(c=>`<tr><td>${esc(c.name||"—")}</td><td>${esc(c.email||"—")}</td>
      <td>${c.active?pill("ok"):pill("disabled")}</td><td>${c.ghl_id?pill("linked"):pill("new")}</td>
      <td><a href="#" onclick="event.preventDefault();showContact('${esc(c.autotask_id)}')">detail</a></td></tr>`).join("")||"<tr><td colspan=5 class=muted>No contacts.</td></tr>"}
  </table></div></div>
  <div class="card"><h2>Sync history</h2><div class="scroll"><table>
    <tr><th>Time</th><th>Op</th><th>Status</th><th>Summary</th></tr>
    ${d.history.map(h=>`<tr><td>${fmtDt(h.timestamp)}</td><td>${esc(h.operation)}</td><td>${pill(h.status)}</td><td>${esc(h.summary)}</td></tr>`).join("")||"<tr><td colspan=4 class=muted>No history yet.</td></tr>"}
  </table></div></div>`;makeSortable();}

async function dryRun(id){await api(`/portal/api/profiles/${id}/dry-run`,{method:"POST"});toast("Dry-run started — refresh in a few seconds.");setTimeout(()=>route(),6000);}
async function liveRun(id){if(!await confirmBox("Run LIVE sync now? This writes to GHL."))return;
  try{await api(`/portal/api/profiles/${id}/run`,{method:"POST"});toast("Live sync started.");setTimeout(()=>route(),4000);}catch(e){toast("Blocked: "+e.message);}}
async function approveProfile(id){try{await api(`/portal/api/profiles/${id}/approve`,{method:"POST",body:"{}"});toast("Approved for live sync.");route();}catch(e){toast(e.message);}}
let critFieldOpts=null;
async function editCriteria(id){const p=await api(`/portal/api/profiles/${id}`);
  if(!critFieldOpts){try{critFieldOpts=(await api("/admin/criteria/fields")).fields;}catch(e){critFieldOpts={};}}
  let rules=(p.criteria?.rules||[]).map(r=>({...r}));
  const fieldSel=(v)=>`<select class="cr-field" onchange="critValSlot(this)">${Object.keys(RULE_FIELDS).map(f=>`<option value="${f}" ${f===v?"selected":""}>${RULE_FIELDS[f]}</option>`).join("")}</select>`;
  const opSel=(v)=>`<select class="cr-op">${[["eq","is"],["ne","is not"],["in","is any of"],["not_in","is none of"]].map(([o,l])=>`<option value="${o}" ${o===v?"selected":""}>${l}</option>`).join("")}</select>`;
  const valInput=(f,v)=>{const opts=critFieldOpts?.[f]?.options;
    return opts&&Object.keys(opts).length?`<select class="cr-val">${Object.entries(opts).map(([ov,l])=>`<option value="${ov}" ${String(ov)===String(v)?"selected":""}>${l}</option>`).join("")}</select>`
    :`<input class="cr-val" value="${esc(v??"")}" size="12">`;};
  const rowHtml=(r)=>`<div class="row cr-row" style="margin:4px 0">${fieldSel(r.field)}${opSel(r.operator)}<span class="cr-slot">${valInput(r.field,r.value)}</span><button class="btn danger" onclick="this.parentElement.remove()">✕</button></div>`;
  $("#page").insertAdjacentHTML("afterbegin",`<div class="card" id="crit-editor"><h2>Edit criteria — ${esc(p.name)}</h2>
    <div class="muted">Rules AND together against the contact's Autotask Account. Saving invalidates prior dry-runs.</div>
    <div id="crit-rows">${rules.map(rowHtml).join("")}</div>
    <div class="row" style="margin-top:8px">
      <button class="btn" onclick="$('#crit-rows').insertAdjacentHTML('beforeend',${JSON.stringify(rowHtml({field:"companyType",operator:"eq",value:"1"})).replace(/"/g,"&quot;")})">+ Add rule</button>
      <button class="btn primary" onclick="saveCriteria(${p.id})">Save criteria</button>
      <button class="btn" onclick="$('#crit-editor').remove()">Cancel</button>
    </div></div>`);
  window.critValSlot=(sel)=>{const row=sel.closest(".cr-row");row.querySelector(".cr-slot").innerHTML=valInput(sel.value,"");};}
async function saveCriteria(id){
  const rules=[...document.querySelectorAll("#crit-editor .cr-row")].map(row=>({
    field:row.querySelector(".cr-field").value,
    operator:row.querySelector(".cr-op").value,
    value:row.querySelector(".cr-val").value}));
  await api(`/portal/api/profiles/${id}`,{method:"PUT",body:JSON.stringify({criteria:{rules}})});
  toast("Criteria saved — a new dry-run is now required.");route();}

async function editSchedule(id){const p=await api(`/portal/api/profiles/${id}`);
  const cfg=p.schedule_config||{};
  $("#page").insertAdjacentHTML("afterbegin",`<div class="card" id="sched-editor"><h2>Edit schedule — ${esc(p.name)}</h2>
    <div class="row" style="margin:8px 0">
      <label>Type</label>
      <select id="sch-type" onchange="schedSlots()">
        ${["manual","preset","interval","daily","weekdays","weekly","once"].map(t=>`<option ${t===p.schedule_type?"selected":""}>${t}</option>`).join("")}
      </select>
      <span id="sch-slots"></span>
    </div>
    <div class="row"><label><input type="checkbox" id="sch-enabled" ${p.schedule_enabled?"checked":""}> Enabled</label>
      <label><input type="checkbox" id="sch-paused" ${p.schedule_paused?"checked":""}> Paused</label>
      <span class="muted">Times are Australia/Sydney. Live runs still require dry-run + approval.</span></div>
    <div class="row" style="margin-top:8px"><button class="btn primary" onclick="saveSchedule(${p.id})">Save schedule</button>
      <button class="btn" onclick="$('#sched-editor').remove()">Cancel</button></div></div>`);
  window.schedSlots=()=>{const t=$("#sch-type").value;let html="";
    if(t==="preset")html=`<select id="sch-preset">${["weekday_7am","weekday_6pm","saturday_9am","sunday_9am","every_4h_business","overnight","hourly","daily_7am"].map(x=>`<option ${x===cfg.preset?"selected":""}>${x}</option>`).join("")}</select>`;
    else if(t==="interval")html=`every <input id="sch-hours" size="3" value="${cfg.every_hours||4}"> hours`;
    else if(["daily","weekdays","weekly"].includes(t))html=`at <input id="sch-time" size="5" value="${cfg.time||"07:00"}">`+
      (t==="weekly"?` on ${["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map((d,i)=>`<label style="margin-right:4px"><input type="checkbox" class="sch-day" value="${i}" ${(cfg.days||[0,1,2,3,4]).includes(i)?"checked":""}>${d}</label>`).join("")}`:"");
    else if(t==="once")html=`at <input id="sch-at" size="17" value="${cfg.at||""}" placeholder="2026-07-10T07:00">`;
    $("#sch-slots").innerHTML=html;};
  schedSlots();}
async function saveSchedule(id){const t=$("#sch-type").value;let config=null;
  if(t==="preset")config={preset:$("#sch-preset").value};
  else if(t==="interval")config={every_hours:Number($("#sch-hours").value)};
  else if(["daily","weekdays","weekly"].includes(t)){config={time:$("#sch-time").value};
    if(t==="weekly")config.days=[...document.querySelectorAll(".sch-day:checked")].map(c=>Number(c.value));}
  else if(t==="once")config={at:$("#sch-at").value};
  await api(`/portal/api/profiles/${id}/schedule`,{method:"POST",body:JSON.stringify({
    schedule_type:t,schedule_config:config,
    schedule_enabled:$("#sch-enabled").checked,schedule_paused:$("#sch-paused").checked})});
  toast("Schedule saved.");route();}
async function exportDryRun(id){const p=await api(`/portal/api/profiles/${id}`);
  const blob=new Blob([JSON.stringify({profile:p.name,summary:p.last_dry_run_summary,exported:new Date().toISOString()},null,2)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=`dryrun-${p.name.replace(/\W+/g,"-")}.json`;a.click();}

async function pageJobs(){const st=pgState("jobs");
  const d=await api(`/portal/api/jobs?offset=${st.offset}&limit=${st.limit}`);
  $("#page").innerHTML=`<div class="card"><h2>Sync Jobs</h2><div class="scroll"><table>
  <tr><th>#</th><th>Profile</th><th>Kind</th><th>Trigger</th><th>By</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr>
  ${d.jobs.map(j=>`<tr><td>${j.id}</td><td>${j.profile_id??"—"}</td><td>${pill(j.kind)}</td><td>${pill(j.trigger)}</td>
    <td>${esc(j.started_by||"—")}</td><td>${pill(j.status)}</td><td>${fmtDt(j.started_at)}</td><td>${j.duration_s??"—"}s</td>
    <td><a href="#job/${j.id}">logs</a>${j.status==="running"?` <button class="btn danger" onclick="cancelJob(${j.id})">Stop</button>`:""}</td></tr>`).join("")}
  </table></div>${pager("jobs",d,pageJobs)}</div>`;}
async function cancelJob(id){await api(`/portal/api/jobs/${id}/cancel`,{method:"POST"});toast("Cancel requested.");setTimeout(()=>route(),2500);}

let jobTimer=null;
async function pageJob(id){const j=await api(`/portal/api/jobs/${id}`);
  $("#page").innerHTML=`<div class="card"><div class="row"><h2>Job #${j.id}</h2>${pill(j.kind)}${pill(j.status)}
    ${j.status==="running"?`<button class="btn danger" onclick="cancelJob(${j.id})">Stop job</button>`:""}
    <a href="#profile/${j.profile_id}">profile</a></div>
    <div class="grid"><div class="stat"><b>${esc(j.trigger)}</b>Trigger</div>
      <div class="stat"><b>${esc(j.started_by||"—")}</b>Started by</div>
      <div class="stat"><b>${fmtDt(j.started_at)}</b>Started</div>
      <div class="stat"><b>${j.duration_s??"…"}s</b>Duration</div></div>
    ${j.error?`<pre style="color:var(--brand-danger)">${esc(j.error)}</pre>`:""}
    <h3>Result</h3><pre>${esc(JSON.stringify(j.summary||{},null,2))}</pre></div>
  <div class="card"><h2>Log (${j.logs.length} entries${j.status==="running"?", live":""})</h2>
    <div class="scroll"><table><tr><th>Time</th><th>Op</th><th>Status</th><th>Entity</th><th>Summary</th></tr>
    ${j.logs.map(l=>`<tr><td>${fmtDt(l.timestamp)}</td><td>${esc(l.operation)}</td><td>${pill(l.status)}</td>
      <td class="muted">${esc(l.entity_ref||"—")}</td><td>${esc(l.summary)}</td></tr>`).join("")}
    </table></div></div>`;
  if(j.status==="running"){clearTimeout(jobTimer);jobTimer=setTimeout(()=>{if(location.hash===`#job/${id}`)pageJob(id);},3000);}}

async function pageApprovals(){const st=pgState("appr");
  const d=await api(`/portal/api/approvals?offset=${st.offset}&limit=${st.limit}`);
  $("#page").innerHTML=`<div class="card"><h2>Pending approvals <span class="muted">(${d.total} total)</span></h2>
  ${!d.decide_enabled?'<div class="pill warn" style="margin-bottom:8px">read-only: set APPROVAL_CALLBACK_SECRET to decide</div>':""}
  ${d.approvals.map(a=>`<div class="card" style="margin:8px 0"><div class="row">#${a.id} ${pill(a.severity)} <b>${esc(a.type)}</b> <span class="muted">${fmtDt(a.created_at)}</span></div>
    <div>${esc(a.reason)}</div><div class="muted">GHL ${esc(a.ghl_id||"—")} · AT ${esc(a.autotask_id||"—")}</div>
    <details><summary class="muted">proposed change</summary><pre>${esc(JSON.stringify(a.proposed_change,null,2))}</pre></details>
    <div class="row" style="margin-top:6px"><input id="ch-${a.id}" placeholder="chosen id (optional)" size="16">
      <button class="btn success" ${d.decide_enabled?"":"disabled"} onclick="decide(${a.id},'approve')">Approve</button>
      <button class="btn danger" ${d.decide_enabled?"":"disabled"} onclick="decide(${a.id},'reject')">Reject</button></div></div>`).join("")||"<span class=muted>Queue is empty.</span>"}
  ${pager("appr",d,pageApprovals)}</div>`;}
async function decide(id,decision){const chosen=$(`#ch-${id}`).value.trim();
  const body={decision,decided_by:"portal-operator"};if(chosen)body.chosen_id=chosen;
  try{const r=await fetch(`/approvals/${id}/decide`,{method:"POST",headers:{"Content-Type":"application/json","x-approval-token":token()},body:JSON.stringify(body)});
  const out=await r.json();if(!r.ok)throw new Error(out.detail);toast(`#${id} → ${out.action}`);route();}catch(e){toast("Failed: "+e.message);}}

async function pageLogs(){const st=pgState("logs");
  const d=await api(`/portal/api/logs?offset=${st.offset}&limit=${st.limit}`);
  $("#page").innerHTML=`<div class="card"><h2>Transaction log</h2><div class="scroll"><table>
  <tr><th>Time</th><th>Dir</th><th>Op</th><th>Status</th><th>Entity</th><th>Summary</th></tr>
  ${d.logs.map(l=>`<tr><td>${fmtDt(l.timestamp)}</td><td class="muted">${esc(l.direction)}</td><td>${esc(l.operation)}</td>
    <td>${pill(l.status)}</td><td class="muted">${esc(l.entity_type)} ${esc(l.entity_ref||"")}</td><td>${esc(l.summary)}</td></tr>`).join("")}
  </table></div>${pager("logs",d,pageLogs)}</div>`;}

async function pageSettings(){const d=await api("/portal/api/settings");const s=d.settings;
  const fields=Object.entries(s).map(([k,v])=>`<tr><td>${esc(k)}</td><td><input id="set-${esc(k)}" value="${esc(v)}" size="34"></td></tr>`).join("");
  $("#page").innerHTML=`<div class="card"><h2>Settings</h2><div class="scroll"><table><tr><th>Key</th><th>Value</th></tr>${fields}</table></div>
  <div class="row" style="margin-top:10px"><button class="btn primary" onclick="saveSettings(${JSON.stringify(Object.keys(s)).replace(/"/g,"&quot;")})">Save settings</button>
  <span class="muted">Timezone default Australia/Sydney · notification keys are placeholders (no notification infra yet)</span></div></div>`;}
async function saveSettings(keys){const settings={};keys.forEach(k=>settings[k]=$(`#set-${k}`).value);
  await api("/portal/api/settings",{method:"PUT",body:JSON.stringify({settings})});toast("Settings saved.");}

// ── Router ──
const routes={dashboard:pageDashboard,customers:pageCustomers,contacts:pageContacts,profiles:pageProfiles,jobs:pageJobs,approvals:pageApprovals,logs:pageLogs,settings:pageSettings};
let autoTimer=null;
async function route(){await banner();const h=(location.hash||"#dashboard").slice(1);
  document.querySelectorAll("#nav a").forEach(a=>a.classList.toggle("active",a.getAttribute("href")==="#"+h.split("/")[0].split("?")[0]));
  $("#page").innerHTML=skeleton;
  try{
    if(h.startsWith("profile/"))await pageProfile(Number(h.split("/")[1]));
    else if(h.startsWith("job/"))await pageJob(Number(h.split("/")[1]));
    else if(h.startsWith("customer/"))await showCustomer(h.split("/")[1]);
    else await (routes[h.split("?")[0]]||pageDashboard)();
  }catch(e){$("#page").innerHTML=`<div class="card"><span class="pill bad">error</span> ${esc(e.message)}</div>`;}
  makeSortable();
  // Live refresh: dashboard + jobs re-poll every 30s while visible.
  clearInterval(autoTimer);const base=h.split("/")[0].split("?")[0];
  if(["dashboard","jobs",""].includes(base))autoTimer=setInterval(async()=>{
    if(document.hidden)return;
    try{if(base==="jobs")await pageJobs();else await pageDashboard();makeSortable();}catch(e){}},30000);}
window.addEventListener("hashchange",route);
$("#token").value=token();route();
</script>
</body>
</html>"""
