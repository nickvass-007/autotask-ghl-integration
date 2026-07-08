# Interlinked Sync Control Centre (/portal)

The operator portal for the Autotask ⇄ GHL sync. Token-gated (same
`APPROVAL_CALLBACK_SECRET` as the API); set the token bottom-left in the sidebar.

## Pages
- **Dashboard** — environment banner, link counts, running jobs, recent jobs.
- **Customers** — synced companies, or any profile's dry-run match list, with
  Autotask deep links (`autotask_web_base` setting controls the zone host).
- **Contacts** — every linked contact (AT id ↔ GHL id, last synced).
- **Sync Profiles** — saved audiences. Create → dry-run → review breakdown →
  approve → run live/schedule. Criteria are AND-ed rules over Account fields.
- **Sync Jobs** — every run (dry/live, manual/scheduled), stoppable while
  running, with a live-polling per-job log fed from the transaction feed.
- **Approvals / Logs / Settings** — approval queue with approve/reject,
  transaction feed, and portal settings (thresholds, timezone, caps).

## Safety model (enforced server-side, not just UI)
1. `dry_run_required`: no live run until a dry-run exists **for the current
   criteria** (criteria edits invalidate it via hash comparison).
2. `requires_approval_before_live_sync`: operator must Approve after review.
3. Significant-change detection between consecutive dry-runs (>20% audience
   change, conflict growth, >10 extra creates, new customer type in scope)
   flips the profile to `review_required` and pauses its schedule.
4. Scheduled runs that fail the gates degrade to a **dry-run** instead of
   silently going live. Runs never overlap per profile; error threshold aborts.
5. Every job records trigger, operator, timings, result; every touched record
   lands in the audit/transaction trail under the job's correlation id.

## Scheduling
`schedule_type`: manual | preset | interval | daily | weekdays | weekly | once.
Presets: weekday_7am, weekday_6pm, saturday_9am, sunday_9am,
every_4h_business, overnight, hourly, daily_7am. Timezone defaults to
Australia/Sydney (portal setting). Weekend/overnight suppression settings
shift slots to the next allowed time. `ENABLE_SCHEDULER=false` disables the
loop entirely.

## Later additions (2026-07-08 evening)
- Contact detail view (click a row): profile, sync history, Autotask link,
  and per-record **Enable/Disable sync** (``sync_exclusions``, migration 0006 —
  enforced in the outbound filter for contacts AND whole accounts).
- Contacts search (name/email via Autotask contains-query) + pagination.
- Dashboard: Work Done digests (24h/7d), 14-day stacked activity chart,
  approve/reject tallies.
- Form-based criteria editor (picklist labels) and schedule editor.
- ``POST /portal/api/approvals/bulk-reject-echoes`` — clears echo approvals.
- Webhook notifications: set the ``notify_webhook_url`` setting to a Teams
  incoming-webhook URL; fires on live-sync completion/failure and
  review-pauses. Email delivery still not wired.

## Known limitations
- Customer "already linked" status keys off COMPANY mappings, which populate
  once the businesses sync runs (needs `businesses.*` scopes).
- Opportunities/contracts/invoices pages (Phase 4) intentionally not built yet.
- Survey scores read Autotask ``SurveyResults`` and degrade silently if the
  entity is not enabled on the account.
