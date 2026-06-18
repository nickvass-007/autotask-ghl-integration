# Build Specification — Autotask ⇄ GoHighLevel Integration

> **For the build agent (Claude Code):** This is a complete Stage-1 specification. Read it
> in full before writing any code. Deliver **Stage 1 only** (defined at the end), then
> **stop and confirm it works** before proceeding to later stages. The operator is an
> intermediate terminal user but **not** an Azure or cloud-infrastructure expert — for any
> step performed in a web portal (Azure Portal, Entra, Autotask admin, GHL Marketplace,
> GitHub, Teams) provide **click-by-click** instructions; for anything scriptable, prefer
> pasteable `az` CLI or shell commands. Never assume a step is "obvious." Explain the
> reasoning behind each design decision, not just the code.

---

## 1. Objective & System Model

Build an **in-house, production-grade API integration** between **Autotask PSA (Datto)** and
**GoHighLevel (GHL / LeadConnector)**, hosted on **Azure**. This is owned end to end — **no
third-party iPaaS** (e.g. Flozic/Zapier) sits in the runtime path. We replicate the relevant
capabilities ourselves so the mechanics are ours to control.

**The primary objective is the GoHighLevel integration.** Everything else (platform seams for
future connectors) is scaffolding that must not slow down or complicate the core GHL↔Autotask
delivery.

### The system model — read this first; it frames every rule below

This is **not** symmetric record-mirroring between two equal systems. It implements a customer
**lifecycle with a defined handoff boundary**, and two systems with **different jobs**:

- **GoHighLevel = SYSTEM OF ENGAGEMENT.**
  - Primary home for **new logos / new business**: the sales team works prospects here from
    first touch through to close.
  - **After** a prospect converts, GHL **remains** the marketing & sales engagement surface
    for existing customers: newsletters, cross-sell, upsell, re-engagement, and campaigns
    targeted at specific customer classifications/segments.
- **Autotask = SYSTEM OF RECORD** (the **protected source of truth**).
  - Becomes the operational home once a prospect **converts** to a customer: service
    delivery, contracts, tickets, account management all live here.

A **converted customer exists in both systems at once**, with different roles: Autotask is the
authoritative record + service delivery; GHL is marketing/engagement + ongoing sales motions.
The integration's job is to keep the **engagement surface (GHL)** correctly informed by the
**record (Autotask)**, while letting **new business originate in GHL** and flow **into**
Autotask at the handoff point.

---

## 2. Operator Context & Working Method

- Operator: intermediate terminal user; **not** an Azure expert. Willing to learn and become
  more proficient — favour explanations that build understanding.
- **Staged delivery:** provide Stage 1 only, then stop and confirm it works before proceeding.
- For portal steps: **click-by-click**. For scriptable steps: pasteable CLI/shell.
- Mark security-critical notes with ⚠️ and recommended best practices with ✅.
- Output: professional, maintainable, suitable for ongoing development.

---

## 3. Repository & Database Foundation

This project — and all future projects — is tracked in **Git**, hosted on **GitHub** (private
repos), edited in **VS Code**, and built with the **Claude Code** extension running inside
VS Code.

### 3.1 Tooling stack (locked)

| Layer | Choice |
|---|---|
| Editor | VS Code |
| AI builder | Claude Code (VS Code extension) |
| Version control | Git |
| Repo hosting | GitHub — **private** repository |
| Repo structure | **One repo per project** (this integration is its own repo) |
| Local database | PostgreSQL (run locally during development) |
| Production database | Azure SQL Database (provisioned at deployment, not before) |
| Schema portability | **Required** — avoid engine-specific SQL so the same schema runs on Postgres locally and Azure SQL in production (see 3.4) |

### 3.2 Beginner setup walkthrough (write this out click-by-click in the delivered README)

Provide, in the repo's `README.md`, a genuinely beginner-friendly walkthrough covering:

1. **Install Git** (Windows installer steps) and verify with `git --version`.
2. **Install VS Code** and the **Claude Code** extension (Extensions panel → search → install).
3. **Create a GitHub account** (if needed) and create a **new private repository** named
   `autotask-ghl-integration` — *do not* initialise with a README from GitHub's side (we
   create it locally). Show the exact GitHub screen choices.
4. **Authenticate Git to GitHub** (GitHub sign-in via VS Code, or a Personal Access Token —
   explain both, recommend the VS Code sign-in for simplicity).
5. **Clone the repo** into a local projects folder via VS Code.
6. **First commit:** create `.gitignore` **first** (see 3.3), then `README.md`, then
   `git add`, `git commit -m "Initial commit: project scaffold and gitignore"`, `git push`.
7. Explain — briefly and in plain language — what a commit, branch, and push are, so the
   operator builds a mental model rather than copying blindly.

### 3.3 `.gitignore` — the literal first file in the repo

⚠️ **The single most important rule from commit #1: never commit secrets.** API keys, the
Autotask secret/integration code, GHL OAuth client secret and tokens, and database connection
strings must **never** enter Git history. They live in a git-ignored `.env` file locally, and
in Azure Key Vault in production.

The first file created in the repository is `.gitignore`, containing at minimum:

```
# Secrets & environment
.env
.env.*
*.key
*.pem

# Python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/

# IDE / OS
.vscode/
.DS_Store

# Local DB / logs
*.sqlite3
*.log
```

Ship a committed **`.env.example`** with every required variable **name** present and all
values blank/placeholder, so the structure is documented without leaking secrets.

### 3.4 Database — full schema designed up front, built locally first

**Approach:** design the **complete schema now** (every table the Stage-1 system needs, plus
forward-looking tables for later stages), run it against **local Postgres** during the entire
build, and provision **Azure SQL** only at the deployment stage. ✅ This satisfies "all
databases built from the beginning" (the *schema* is complete from day one) while honouring
sandbox-first development (no cloud DB cost until deploy).

⚠️ **Portability constraint:** keep SQL engine-neutral. Use an ORM (**SQLAlchemy**) with
migrations (**Alembic**) so the same models generate the schema on both Postgres and Azure SQL.
Avoid Postgres-only or SQL-Server-only constructs; where a difference is unavoidable, isolate
it behind the ORM. Use UTC timestamps everywhere.

**Schema to design and create from the start** (define columns, types, PKs/FKs, indexes;
implement via SQLAlchemy models + an initial Alembic migration):

- **`entity_mapping`** — the cross-system identity spine. Columns: id, canonical_entity_type
  (contact/company/deal/service_item/...), autotask_entity_type, autotask_id, ghl_entity_type,
  ghl_id, environment (sandbox/production), created_at, updated_at, last_synced_at. Unique
  constraints prevent duplicate links. **This table is what stops duplicate record creation.**
- **`approval_queue`** — every gated/ambiguous change awaiting a human decision. Columns: id,
  status (pending/approved/rejected/expired), severity (low/med/high), approval_type,
  canonical_entity_type, source_system, target_system, autotask_id (nullable), ghl_id
  (nullable), proposed_change (JSON: before/after), detected_reason, created_at,
  decided_at, decided_by, expires_at, environment, correlation_id.
- **`audit_log`** — immutable before-state capture for every write to Autotask (and ideally
  all writes). Columns: id, timestamp, environment, correlation_id, operation
  (create/update/skip/conflict/error), target_system, entity_type, entity_id,
  before_state (JSON), after_state (JSON), actor (system/approval/admin), result. **Append-only
  — never updated or deleted.** This is what makes any change manually revertible.
- **`transaction_log`** — the queryable feed of EVERY sync operation surfaced by the Teams bot.
  Columns: id, timestamp, environment, correlation_id, direction, operation, entity_type,
  entity_ref, status (success/skipped/conflict/error), summary, detail (JSON).
  *(May be a view over audit_log + processing events, or its own table — implementer's call;
  it must support `/transactions` and `/pending` style queries and a daily digest.)*
- **`stage_mapping`** — pipeline/stage translation (see Flow 2). Columns: id, environment,
  ghl_pipeline_id, ghl_stage_id, autotask_entity (opportunity/ticket),
  autotask_status_value, direction, active, last_validated_at.
- **`classification_sync`** *(forward-looking, Stage 3)* — tracks Autotask→GHL classification
  attribute pushes (customer type, tier, contract status, owner) so segmented campaigns work.
- **`connector_registry`** *(forward-looking, platform)* — declared connectors and their
  capabilities, so future systems (e.g. 3CX) plug in without schema changes.
- **`processed_events`** — idempotency ledger. Columns: id, event_id, entity_version,
  source_system, processed_at, environment. Unique on (event_id, source_system) to guarantee
  exactly-once handling.

---

## 4. Reference APIs & Confirmed Capability Surface

- **Autotask REST API:** https://www.autotask.net/help/developerhelp/Content/APIs/REST/REST_API_Home.htm
- **GoHighLevel API v2:** https://help.gohighlevel.com/support/solutions/articles/48001060529-highlevel-api-documentation
- **Capability reference only — replicate, do NOT integrate with:**
  https://www.flozic.ai/integrate/apps/autotask/integrations/gohighlevel

The following operations are **confirmed achievable on both APIs** (verified against the
capability reference) and are the surface this build relies on:

- **Autotask triggers (we obtain via webhook where available, else polling):** New/Updated
  Contact, New Account, Updated Account, New Opportunity, Updated Opportunity, New Ticket,
  Updated Ticket, New/Updated Ticket Note.
- **Autotask actions:** Create Contact, Update Contact, Create Account, Update Account,
  Create Opportunity, Update Opportunity, Create Ticket, Update Ticket, Create Ticket Note.
- **GHL triggers:** New Contact, Updated Contact, New Opportunity, Updated Opportunity.
- **GHL actions:** Create/Update Contact, Create Opportunity, Update Opportunity, Create Notes,
  Add Tags on Contact.

⚠️ **Autotask has no comprehensive outbound webhook system.** Outbound Autotask changes must be
**polled** (query by `LastActivityDate`/`id > lastId` per entity). This shapes the architecture
— budget for timer-driven polling and reconciliation. GHL inbound webhooks are used where
available and **must have their signatures verified**.

---

## 5. Protected Source-of-Truth Rules (Autotask)

Autotask data is authoritative and must **never be silently damaged**. These rules apply to
**every** flow below.

1. **Autotask → GHL:** free-flowing. Autotask values win and may overwrite GHL.
2. **GHL → Autotask:** **GATED.**
   - New record, no Autotask match → run dedupe first. **Clean miss** = safe to create.
     **Fuzzy/possible match** = **do not create**, raise an approval.
   - Existing record, GHL field conflicts with a **populated** Autotask field → **never**
     overwrite automatically; raise a discrepancy for human decision.
   - GHL value fills an **empty** Autotask field → additive; may auto-apply (configurable,
     default on, always logged).
3. The integration **never deletes** Autotask records. Deletes are approval-only or blocked.
4. Before **any** write to Autotask, capture the **before-state** to the immutable `audit_log`
   so a human can manually revert.
5. **Circuit breaker:** if conflict volume or write-failure rate crosses a threshold, **pause
   all Autotask writes** and alert admins. Recommend sensible defaults (e.g. pause if >N
   conflicts or >M% write failures within a rolling window) and make them configurable.

Source-of-truth precedence is **configurable per entity/field**, shipping with
**Autotask-protective defaults**.

---

## 6. Environment Strategy — Sandbox First (Mandatory)

We have an **Autotask sandbox zone**. **All** development, testing, and Stage-1 validation runs
against sandbox before **any** connection to the production Autotask zone.

- Environment is a **runtime config switch** (`sandbox` | `production`), **never hardcoded**.
- Sandbox and production each have their **own** Autotask API-only user, zone URL (from zone
  detection), credentials, and Key Vault secrets. One environment can **never** read the
  other's credentials.
- GHL has no true sandbox: use a dedicated **GHL test sub-account/location** for the sandbox
  phase, switched the same way.
- The active environment must **always** be visible: a startup log line, the admin UI, and the
  Teams bot status command all state which environment is live. ⚠️ Operating against production
  must be a deliberate, obvious act — never a default.
- Deliver a documented **sandbox → production cutover checklist** (swap creds, re-auth OAuth,
  smoke test, rollback plan) as part of Stage 1.

---

## 7. Build as a Platform — Seams for Future Connectors

This is **GHL↔Autotask first**, but future systems (e.g. **3CX** call-transcription ingest —
**illustrative only, not built in v1**) must plug in **without rewriting the core**. Design
these seams now; implement only Autotask + GHL + Contacts in Stage 1.

1. **Canonical internal model (hub-and-spoke, not point-to-point).** Define system-agnostic
   internal entities — `Contact`, `Company`, `Deal`, `ServiceItem`, and an extensible base for
   future types (e.g. `Communication`/`CallTranscript`). Every connector maps **its** schema to
   the canonical model only. Adding system *N* is one set of mappings to the hub, not *N*
   mappings to every other system.
2. **Connector interface.** Each external system implements a common contract:
   `authenticate()`, `fetch_changes()`, `push(entity)`, `handle_webhook()`, plus a declared
   field schema + capabilities (read/write, supports-webhooks, rate limits). Autotask and GHL
   are the first two implementations.
3. **Event backbone.** Service Bus carries **canonical** events typed by entity. Connectors
   publish/subscribe; routing is config-driven.
4. **Per-connector config.** Each connector ships its own mapping YAML + source-of-truth rules
   against the canonical model. Precedence is configurable per entity/field —
   Autotask-protective defaults today; a different system could be authoritative for a
   different entity tomorrow.
5. **Pluggable matching.** The dedupe / identity-resolution engine is generic and reusable
   (e.g. 3CX would match a recording to a Contact by phone number, then attach a canonical
   `Communication` — reusing the **same** matching + approval pipeline).

Document explicitly, in a `CONTRIBUTING`/extending guide, how a developer adds a third
connector end to end.

---

## 8. Lifecycle Model — Prospect → Conversion → Engagement

Every sync rule respects the system roles from §1.

### 8.1 Stage P — Prospect (pre-conversion, GHL-led)
- New logos originate in GHL; sales works them via the Sales pipeline (Flow 2).
- Early-stage prospects do **not** need full Autotask records. **Do not** auto-create Autotask
  records for unqualified leads — only at/after conversion, or via the gated dedupe→approval
  path if a match to an existing Account is detected. ⚠️ This keeps the protected system of
  record free of unqualified leads.

### 8.2 Stage C — Conversion / Handoff (the boundary event)
- **Trigger:** a GHL Opportunity in the Sales pipeline reaches the designated **closed-won**
  stage (configured in the stage-map; this stage **is** the handoff signal).
- **Customer onboarding handoff:**
  1. Dedupe the GHL contact/company against Autotask Accounts (reuse Flow 1 matching).
  2. **Clean miss** → approval to **create** the Autotask Account + Contact (Accounts are never
     auto-created — onboarding into the system of record is a deliberate, approved act).
     **Possible match** → approval to **link** to the existing Account.
  3. On approval: create/link Autotask records, write the `entity_mapping` row, capture audit,
     and **stamp the GHL contact** as `Converted — Managed in Autotask` (status flag/tag,
     configurable) so the engagement layer knows the record now lives in Autotask.
- Post-handoff, operational fields become Autotask-authoritative (all gating applies); GHL
  retains the customer for engagement (Stage 3).
- **Teams bot:** new approval card `New customer onboarding — create/link Autotask Account?`
  showing the won deal, proposed Account match, value, and Approve / Link / Reject.

### 8.3 Stage E — Existing-customer engagement (post-conversion, both systems)
To enable **segmented campaigns** (newsletters, cross-sell, upsell to specific customer types),
push **classification data** Autotask → GHL:
- Sync selected Autotask Account/Contact **classification attributes** → GHL tags/custom
  fields: customer type/classification, service tier, account manager/owner, contract status
  (active/lapsing), industry/vertical, lifecycle = `Customer`. Field set is config-driven; ship
  a sensible default.
- **Direction: Autotask → GHL, free-flowing and authoritative** (classifications are owned by
  the system of record). They populate the GHL fields the sales team filters on to build
  campaign audiences. ✅ This is what makes "mail-out to classified customers of a certain type"
  possible.
- The integration does **not** run campaigns — it ensures GHL holds accurate, current
  classification data so GHL-native campaign tools target correctly.
- **GHL → Autotask for classification fields is BLOCKED** (marketing must not rewrite the
  record's classification). Engagement artifacts (campaign membership, opens, etc.) stay in GHL
  and are **not** pushed to Autotask in v1.

### 8.4 Future seam (document, do NOT build in v1) — Communication sync
Meetings, follow-ups, calls, and communication history may later synchronize to live in **both**
systems. Design the canonical model to accommodate a future `Communication`/`Activity` entity
(meeting, call, follow-up, email) with configurable directionality, but **do not** implement
bidirectional comms sync in v1. Note it explicitly as a planned extension.

⚠️ **Watch the lifecycle tension:** a converted customer stays in GHL for engagement but should
**not** be re-worked as a new logo. The `Converted — Managed in Autotask` stamp is the hook the
GHL pipeline setup uses to keep new-business prospects visually separate from existing-customer
engagement.

---

## 9. Flow 1 — Contacts (Autotask ⇄ GHL)

GHL has a **flat** contact (company is a text field); Autotask splits **Contact** and **Company
(Account)** into two linked entities. A GHL contact therefore maps to **two** Autotask objects,
and the company link is the dangerous part.

### 9.1 Matching precedence (dedupe order)

| # | Rule | Result |
|---|---|---|
| 1 | `email` exact | Confident match (primary key) |
| 2 | `firstName + lastName + companyID` exact | Confident match |
| 3 | phone/mobile exact + name similarity | **Possible → approval** |
| 4 | fuzzy name + company | **Possible → approval** |
| 5 | nothing | Clean miss (safe to create Contact) |

### 9.2 Field behaviour

"Add" = write only if the Autotask field is empty (additive, logged, default-on). "Approval" =
conflict on a **populated** Autotask field → queue, never auto-overwrite.

| GHL field | Autotask target | AT→GHL | GHL→AT (AT empty) | GHL→AT (conflict) |
|---|---|---|---|---|
| email | Contact.emailAddress | overwrite | add | **Approval — HIGH** (match key) |
| firstName | Contact.firstName | overwrite | add | Approval — LOW |
| lastName | Contact.lastName | overwrite | add | Approval — LOW |
| phone | Contact.phone | overwrite | add | Approval — MED |
| mobile | Contact.mobilePhone | overwrite | add | Approval — MED |
| address1 | Contact.addressLine | overwrite | add | Approval — LOW |
| city | Contact.city | overwrite | add | Approval — LOW |
| state | Contact.state | overwrite | add | Approval — LOW |
| postalCode | Contact.zipCode | overwrite | add | Approval — LOW |
| country | Contact.country | overwrite | add | Approval — LOW |
| title | Contact.title | overwrite | add | Approval — LOW |
| companyName | Account.companyName (via link) | overwrite (read-only to GHL) | see 9.3 | **Approval — HIGH; NEVER auto-rename an Account** |
| website | Account.webAddress | overwrite | add | Approval — LOW |
| dateOfBirth | custom field (if mapped) | overwrite | add | Approval — LOW |
| tags | custom field / skip v1 | one-way AT→GHL or skip | additive only | n/a v1 |
| source | create-only | — | set on create | never update |
| customFields[] | config-mapped | per config | per config | Approval (default) |

**Do NOT sync** (Autotask-managed): `id`, `companyID` (owned by linkage logic), lifecycle
status, `createDate`, `lastActivityDate`. Lifecycle/deactivation is a separate guarded flow,
never a field overwrite.

### 9.3 Account (Company) linkage — stricter than Contact creation

⚠️ A GHL company-name typo must never rename an Account (attached to many contacts, tickets,
contracts) or spawn a duplicate. When a GHL contact arrives with company "X":
- Exact Account name match → link `contact.companyID`, done.
- One close fuzzy candidate → **approval**: "Link to existing Account 'Y'?"
- Multiple candidates → **approval** with a pick-list.
- No candidate → **approval**: "Create new Autotask Account 'X'?" — ✅ Accounts are **never**
  auto-created in v1; they carry billing/ownership weight.
- ✅ Unmatched contacts attach to a configurable holding Account (e.g.
  `Prospects – Unassigned`) so nothing floats orphaned while awaiting a decision.

---

## 10. Flow 2 — Opportunities & Tickets (Autotask ⇄ GHL)

Autotask remains the protected source of truth; all GHL→Autotask writes obey §5.

### 10.1 Resolve the three-way entity mismatch first
- Autotask **Opportunity** = pre-sale deal, linked to an Account; has amount/stage/probability.
- Autotask **Ticket** = post-sale service work, linked to Account/Contact; has status/queue.
- GHL **Opportunity** = a card in a Pipeline at a Stage (the only "deal-like" object in GHL).

Map to canonical: `canonical.Deal` (covers Autotask Opportunity), `canonical.ServiceItem`
(covers Autotask Ticket). A GHL Opportunity represents **either**, disambiguated by **which GHL
Pipeline it sits in** (10.3). ⚠️ **Never** let one GHL pipeline write to both Autotask entities —
that is the primary duplication risk.

### 10.2 Directionality
- **Autotask Opportunity → GHL Opportunity (Sales pipeline):** free-flowing. Autotask
  authoritative for amount, stage, close date, probability; Autotask wins on conflict.
- **Autotask Ticket → GHL Opportunity (Service pipeline):** free-flowing, **read-only mirror**
  for visibility. Autotask Ticket status/queue authoritative; overwrites the GHL card.
- **GHL Opportunity → Autotask:** **gated.**
  - GHL deal in a designated new-business stage with **no** linked Autotask Opportunity →
    dedupe against existing Opportunities for that Account; clean miss = create; possible match
    = **approval**.
  - GHL stage change that would move an existing Autotask Opportunity **backwards**, change a
    closed-won/closed-lost outcome, or alter amount on a populated field → **approval** (never
    auto-overwrite a sales outcome).
  - GHL must **never** create or mutate Autotask **Tickets** in v1 — any attempt is **blocked
    and logged**, not queued.

### 10.3 Pipeline & stage mapping (the translation layer)
Autotask stages/statuses are **picklists with IDs**; GHL stages are free-form per location.
Hardcoding either side is forbidden.
- Build a **stage-mapping config** (the `stage_mapping` table + YAML) keyed by GHL
  `pipelineId + stageId` ↔ Autotask opportunity stage value (or ticket status value). **Two
  separate maps:** Sales pipeline ↔ Opportunity stages; Service pipeline ↔ Ticket statuses.
- On startup, **fetch both sides' picklists/pipelines via API and validate** every mapping
  entry resolves to a real ID on each end. Unmapped stage at runtime = route to approval as
  "unmapped stage," never guess.
- Provide an admin + Teams bot command to list current stage mappings and flag any that no
  longer resolve (someone renamed a GHL stage).

### 10.4 Field mapping — `canonical.Deal` (Autotask Opportunity ⇄ GHL Opportunity)

| GHL | Autotask | Conflict rule |
|---|---|---|
| opp.name | Opportunity.title | AT wins |
| opp.monetaryValue | Opportunity.amount | AT wins; GHL→AT **approval** |
| opp.pipelineStageId | Opportunity.stage | via 10.3 map |
| opp.status (open/won/lost) | Opportunity.status | closed states = **approval** |
| opp.contactId | Opportunity.contactID | resolve via Flow 1 map |
| (Account link) | Opportunity.accountID | **must** resolve to existing Account; else approval |
| opp.assignedTo | Opportunity.ownerResourceID | map users; unmapped = skip |
| close date | Opportunity.projectedCloseDate | AT wins |
| notes/activity | Create Notes / Create Ticket Note | additive only |

**Do NOT sync:** Autotask-managed IDs, `createDate`, `lastActivityDate`, probability
auto-calcs.

### 10.5 Notes sync (additive, low-risk)
- Autotask Ticket Note (new/updated) → append to the linked GHL Opportunity as a Note, tagged
  with source + Autotask note ID (idempotent; never double-post).
- GHL Note on a synced Opportunity → append as Autotask Ticket Note **only if** a linked
  Autotask Ticket already exists; otherwise hold (do not create a ticket). Notes are never
  edited or deleted across the boundary.

### 10.6 Build order for Flow 2
Implement **read-only Autotask→GHL mirroring first** (both Opportunity→Sales-pipeline and
Ticket→Service-pipeline), validate in sandbox, **then** enable the gated GHL→Autotask
Opportunity direction. Tickets stay one-way (AT→GHL) for the entirety of v1.

---

## 11. Teams Ops Bot (Azure Bot Service + Adaptive Cards, Python)

⚠️ A simple Teams *incoming webhook* can post messages but **cannot** handle button callbacks —
interactive Approve/Reject requires a proper **bot** (Azure Bot Service). Spec it as a Bot
Service app, not a webhook.

Delivered into a private **Integration Ops** Teams channel for system admins. Must provide:

1. **Approval cards** — every ambiguous change posts an Adaptive Card showing source, entity
   link, before/after values, with **Approve / Reject / Override** buttons that call back into
   the integration API (**verify the caller**). Card types span Flow 1 (contact/account
   conflicts, account linkage), Flow 2 (create opportunity, sales-outcome change, unmapped
   stage, amount conflict), and Stage C (new-customer onboarding).
   - **Approved** → apply to Autotask + audit. **Rejected** → discard, leave Autotask
     untouched, optionally push the authoritative Autotask value back to GHL to resolve drift.
     **Expired** (configurable window) → default to **protect Autotask** (no change), re-notify.
2. **Transaction feed** — a queryable log of **every** sync operation (create/update/skip/
   conflict/error) with status, entity, timestamp, correlation ID. Support commands (e.g.
   `/transactions`, `/pending`) and a **daily digest**.
3. **Alerts** — errors, circuit-breaker trips, token/credential expiry.
4. **Audit lookup** — "what happened to this contact/company?"

Also send **email to admins via Microsoft Graph** for high-severity items as a backup channel.

A periodic **reconciliation sweep** compares both systems to catch drift even when no event
fired, feeding the same approval queue.

---

## 12. Tech Stack & Azure Architecture

### 12.1 Stack (locked)
- **Python 3.12 + FastAPI.** Greenfield, fastest to stand up, one language covers the API,
  polling jobs, and the Teams bot. Justify briefly in the README, then proceed.
- **Autotask auth:** API-only user (username + secret + integration code); call the
  **zone-detection endpoint first** to get the correct API base URL.
- **GHL auth:** OAuth 2.0 Marketplace app with **refresh-token rotation**.
- Handle Autotask **threadless pagination** (`id > lastId`, 500-record page cap) and **rate
  limits** (Autotask per-DB threshold, GHL burst + daily) with **exponential backoff + jitter**.
- **Idempotent processing:** dedupe on event ID + entity version (`processed_events`).
  `entity_mapping` prevents duplicate creation.
- **Inbound GHL webhooks:** verify signatures.
- **ORM/migrations:** SQLAlchemy + Alembic (portable schema, §3.4).

### 12.2 Azure services (recommend final shape; justify each in the README)

| Concern | Service | Why |
|---|---|---|
| Compute / API / bot endpoint / admin UI | **Azure App Service (Web App)** | Hosts FastAPI + admin config UI + bot messaging endpoint; **Entra "Easy Auth"** gives admin-tool SSO with almost no code |
| Polling & reconciliation | **Azure Functions (Timer-triggered)** | Autotask has no comprehensive webhooks → outbound changes **must** be polled; reconciliation sweep runs here |
| Durable event queue | **Azure Service Bus** | Dead-letter + retry guarantees no event is lost — directly supports "cannot be damaged" |
| Database | **Azure SQL Database** | `entity_mapping`, `approval_queue`, immutable `audit_log`, stage maps, etc. (Postgres locally → Azure SQL on deploy, §3.4) |
| Secrets | **Azure Key Vault** | All API creds, OAuth tokens, signing secrets; app reads via **Managed Identity** (no secrets stored for Azure itself) |
| Bot registration | **Azure Bot Service** | Teams bot |
| Observability | **Application Insights + Log Analytics** | Structured logs, correlation IDs, alerting |
| Ingress (optional) | **Azure API Management** | Single front door for inbound GHL webhooks; throttling/WAF |

**Data flow:** GHL webhook → (API Management →) App Service → Service Bus → worker →
`entity_mapping` lookup → Autotask REST. Reverse direction via Timer-triggered Functions polling
Autotask (limited native webhooks).

### 12.3 Permissions & SSO
- **Admin-tool login (your team):** **Entra ID SSO** via Easy Auth, with **App Roles**
  (`Integration.Admin`, `Integration.Viewer`) and **Conditional Access MFA**. ⚠️ This secures
  **our tool only** — it does **not** authenticate to Autotask or GHL, which keep their own
  credential models. Do not conflate the two.
- **Service-to-service:** **Managed Identity** for the App Service → Key Vault (no stored
  secrets for Azure resources themselves).
- **Autotask:** dedicated **API-only resource** scoped to least privilege (only synced
  entities).
- **GHL:** **OAuth Marketplace app** scoped only to required scopes (contacts, opportunities,
  etc.).
- **Secrets rotation:** Key Vault rotation policies; GHL refresh-token rotation handled in code.

---

## 13. Deliverables & Stage-1 Scope

### 13.1 Deliverables
1. Architecture diagram (Mermaid) — components, data flow, Azure services.
2. Repo structure + the beginner setup walkthrough (§3.2) in `README.md`.
3. `.gitignore` (first file) + `.env.example`.
4. Full database schema as SQLAlchemy models + initial Alembic migration (§3.4), runnable on
   local Postgres.
5. Field-mapping + source-of-truth config schema (YAML) for Contacts and Opportunities/Tickets,
   plus the stage-mapping config.
6. Core sync engine with the **Contacts flow fully implemented** as the reference, including
   dedupe, conflict detection, approval routing, and Autotask before-state capture.
7. Approval queue + reconciliation sweep.
8. Teams Ops Bot wired for **Contacts approvals** and the **transaction feed**.
9. Dead-letter / retry handling.
10. **Step-by-step setup guides** (click-by-click + `az` CLI) for: Azure resource creation;
    Entra app registration + Easy Auth; Key Vault wiring via Managed Identity; Autotask API
    user creation (sandbox); GHL OAuth app creation (test sub-account); Bot registration +
    Teams channel install.
11. Local dev setup + Azure deployment guide + observability/alerting setup.
12. Sandbox → production cutover checklist.
13. `CONTRIBUTING`/extending guide: how to add a third connector.

### 13.2 Stage 1 — build this, then STOP and confirm before proceeding
- GitHub repo initialised (with `.gitignore` first), beginner README, local Postgres running.
- Full schema designed and migrated locally.
- Canonical model + connector interface skeleton, with **Autotask and GHL connectors**
  implemented far enough to authenticate (Autotask zone detection; GHL OAuth) **against
  sandbox / GHL test location**.
- **Contacts flow end-to-end** in sandbox: dedupe → conflict → approval → audit logging, with
  Account-linkage guarding.
- **Teams bot** wired for Contacts approvals + the transaction feed.
- Environment switch defaulting to **sandbox**, with active-environment visible everywhere.

**Then stop.** Confirm Stage 1 works in sandbox before building Flow 2 (Opportunities/Tickets),
Stage C (conversion handoff), Stage E (classification sync), and production cutover.

---

*End of Stage-1 specification.*
