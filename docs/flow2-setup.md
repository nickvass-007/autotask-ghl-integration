# Flow 2 setup — Opportunities & Tickets (Spec §10), Stage C & E

Everything in Flow 2 ships **disabled/inert until configured**: the stage map has
placeholder IDs, the poller is off, and the gated GHL→Autotask direction only
acts on webhooks you subscribe. Work through this top to bottom.

## 1. Update the GHL app scopes (developer portal)

Stage 1 requested only contacts scopes. Flow 2 needs opportunities too:

1. <https://marketplace.gohighlevel.com/> → **My Apps** → your app → **Scopes**.
2. Add: `opportunities.readonly` and `opportunities.write`. Save.
3. Update `.env`:
   ```
   GHL_SCOPES=contacts.readonly contacts.write opportunities.readonly opportunities.write
   ```
4. **Re-authorise** (scope changes always need a fresh grant):
   browse to `http://localhost:8000/oauth/ghl/authorize` and approve again.

## 2. Create the two GHL pipelines (test sub-account)

⚠️ TWO separate pipelines — one may never write to both Autotask entities (§10.1):

1. In the sub-account: **Opportunities → Pipelines → Create Pipeline**.
2. `Sales` — your new-business stages, e.g. *New Lead → Qualified → Proposal →
   Closed Won / Closed Lost*.
3. `Service — Autotask mirror` — mirrors Ticket statuses, e.g. *New → In
   Progress → Waiting → Complete*. Treat it as **read-only** in GHL: the sweep
   overwrites these cards from Autotask.

## 3. Fill in `config/stage_mapping.yaml`

You need the GHL pipeline + stage IDs and the Autotask picklist values:

- **GHL IDs:** with the app authorised, `GET http://localhost:8000/admin/stage-map`
  reports problems including what it *did* find; or call the GHL API
  `GET /opportunities/pipelines` from `/docs`. (IDs are long random strings.)
- **Autotask values:** Opportunity **stage** and Ticket **status** picklists —
  Autotask admin → picklists, or ask the integration:
  `Opportunities/entityInformation/fields` via the sandbox API.
- Replace every `REPLACE_WITH_…` placeholder. The stage list **order matters**
  for the sales pipeline: it defines what counts as a *backwards* move (§10.2).
- Set `closed_won_stage_id` — reaching this stage IS the Stage-C conversion
  trigger (§8.2).

Validate: `GET /admin/stage-map` → `problems: []` means every entry resolves.

## 4. Subscribe the opportunity webhooks

In the app's **Webhooks** section add the opportunity events (Create, Update /
Stage Update, Status Update) pointing at:

```
https://<your-tunnel>/webhooks/crm/opportunity
```

Same Ed25519 signature verification as contacts — nothing to configure.

## 5. (Optional) classification custom fields — Stage E

Tags work with zero setup (`at:customer-type/managed-services`, `customer`).
To ALSO write values into GHL custom fields for campaign filters:

1. Sub-account → **Settings → Custom Fields** → create e.g. `AT Customer Type`.
2. Paste each field's ID into `config/classification.yaml` under
   `ghl_custom_field:`.

## 6. Enable the sweep (poller)

Autotask has no webhooks, so the AT→GHL mirrors and Stage-E push are pulled:

```
# .env
ENABLE_POLLER=true
AUTOTASK_POLL_INTERVAL_SECONDS=300
RECONCILIATION_INTERVAL_SECONDS=3600
```

Restart the API and complete the OAuth grant; the poller logs one summary line
per sweep. ⚠️ The first sweep backfills from `id > 0` — on a large Autotask
database seed the cursor first (set `sync_cursor.cursor` to `id:<current-max>`)
if you don't want a full historical mirror.

## 7. What's gated vs automatic (quick reference)

| Event | Result |
|---|---|
| AT Opportunity/Ticket change | mirrored to GHL automatically (AT wins) |
| AT stage/status not in map | `unmapped_stage` approval — never guessed |
| GHL deal, clean dedupe miss, account resolves | Autotask Opportunity created |
| GHL deal, possible duplicate / account unresolved | approval |
| GHL forward stage move on linked deal | applied (audited) |
| GHL **backwards** move / won / lost / amount overwrite | approval |
| GHL edit on a Service-pipeline card | **BLOCKED and logged** (never queued) |
| Sales deal reaches closed-won | Stage-C `customer_onboarding` approval; on approve: Account+Contact created/linked, GHL contact stamped `Converted — Managed in Autotask` |
| AT classification change | GHL tags/custom fields updated on next sweep |
