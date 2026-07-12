# Microsoft Fabric as the hub — the repoint roadmap

> **Target state:** Microsoft Fabric is the **single source of truth** for
> customer/company/contact data. GHL (engagement) and Autotask (service
> delivery) both sync **to and from Fabric**, instead of to each other.
> **Current state:** GHL ⇄ Autotask sync directly (this repo), and Fabric can
> already ingest everything the integration knows via the `/export/*` feeds.

## Why the current build survives the repoint

The integration was deliberately built around three seams that make Fabric a
plug-in, not a rewrite:

1. **The canonical model** (`src/integration/canonical/`) — every entity is
   translated into a system-agnostic hub shape (`CanonicalContact`,
   `CanonicalCompany`, `CanonicalDeal`, …) before any sync logic runs. Fabric's
   entity tables adopt this shape as-is.
2. **The connector contract** (`src/integration/connectors/base.py`) — Autotask
   and GHL are both just `Connector` implementations. Fabric becomes a third
   `FabricConnector` implementing the same contract (read/write canonical
   entities, cursor-based `fetch_changes`).
3. **The identity spine** (`entity_mapping`) — every record's Autotask id ⇄ GHL
   id pairing is already recorded per entity type. Fabric inherits this table
   as its cross-system key map on day one; nothing has to be re-matched.

## Phases

### Phase F0 — ingestion (available now)
Fabric pulls the integration's state incrementally via the token-gated export
feeds (id-watermark pagination — store the highest `id`, pass it back as
`since_id`):

| Feed | Contents |
|---|---|
| `GET /export/transactions` | every sync operation (the event stream) |
| `GET /export/mappings` | the identity spine (Autotask ⇄ GHL ids) |
| `GET /export/approvals` | the human-decision trail |

Recommended Fabric side: a **Data Pipeline** (or notebook) on a 15-minute
schedule → Lakehouse `bronze_` tables → dbt/notebook transforms to a
`silver_customers` dimensional model keyed by the mapping spine.

Additionally, the runtime database is designed to run on **Azure SQL** in
production — Fabric can **mirror the Azure SQL database natively** (Fabric
Mirroring), which gives near-real-time replicas of every table (mappings,
transactions, approvals, cursors) with zero pipeline code. That is the
preferred F0 path once the app is hosted on Azure.

### Phase F1 — Fabric as the authoritative customer master (read model)
- Build the `dim_company` / `dim_contact` gold tables in Fabric, sourced from
  Autotask (authoritative today) + GHL engagement attributes.
- Downstream consumers (BI, AssetPilot, finance) read from Fabric, not from
  either operational system.
- **No write-path changes** in this repo. Validation phase: prove Fabric's
  master matches what the sync engine believes (the mapping spine makes this a
  join, not a fuzzy match).

### Phase F2 — the repoint (Fabric in the write path)
- Implement `FabricConnector(Connector)` — reads/writes the gold entity tables
  (via the SQL analytics endpoint / Fabric REST, or a thin API in front).
- Change the flows' wiring, not their logic:
  - **Today:** GHL →(gated)→ Autotask, Autotask →(free)→ GHL.
  - **After:** GHL →(gated)→ Fabric →(gated)→ Autotask, and Autotask →(free)→
    Fabric →(free)→ GHL.
- The protection rules (Spec §5) stay exactly where they are — they gate
  *writes toward the system of record*, which is Fabric after the repoint,
  with Autotask as a protected downstream mirror.
- The conversion/handoff trigger (`conversion_stage_ids`) becomes "promote the
  lead's company/contact into the Fabric master", which then flows to Autotask.

### Phase F3 — retire point-to-point
- Delete the direct GHL⇄Autotask paths once both spoke syncs run clean for a
  full billing cycle. The approval workflow, Teams surfaces, portal, and audit
  trail carry over unchanged — they operate on the canonical layer.

## What NOT to do
- ⚠️ Don't write from GHL straight into Fabric gold tables — everything keeps
  going through the gated canonical flows so dedupe/approval/audit still apply.
- ⚠️ Don't stand up a second matching engine in Fabric. The mapping spine is
  the one source of identity; Fabric consumes it.
