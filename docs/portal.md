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

## Known limitations
- Notifications are placeholder settings only (`notify_email`,
  `notify_webhook_url`) — no delivery infra exists yet.
- Criteria/schedule editors are prompt-based (functional, not polished).
- Customer "already linked" status keys off COMPANY mappings, which populate
  once the businesses sync runs (needs `businesses.*` scopes).
- Opportunities/contracts/invoices pages (Phase 4) intentionally not built yet.
