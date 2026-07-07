# Sandbox → Production cutover checklist

⚠️ Operating against production must be a **deliberate, obvious act** (Spec §6).
Work top to bottom; do not skip the smoke test or the rollback plan. Nothing here
runs until Stage 1 is confirmed working in sandbox and later stages are signed off.

## Pre-flight (still in sandbox)

- [ ] All tests green (`pytest`), Contacts flow validated end-to-end in sandbox.
- [ ] `entity_mapping`, `approval_queue`, `audit_log`, `transaction_log` reviewed —
      data looks correct for real scenarios.
- [ ] Circuit-breaker thresholds (`CIRCUIT_BREAKER_*`) agreed with admins.
- [ ] Field-mapping + source-of-truth YAML reviewed; Autotask-protective defaults
      confirmed (Spec §5).
- [ ] Approval expiry behaviour confirmed (expired ⇒ protect Autotask, Spec §11.1).

## Provision production Azure (separate from sandbox)

- [ ] Resource group, App Service, Azure SQL, Service Bus, Key Vault, App Insights,
      Bot — see [setup/azure-resources.md](setup/azure-resources.md).
- [ ] **Separate Key Vault / secret set** for production — one environment can never
      read the other's credentials (Spec §6).
- [ ] Run `alembic upgrade head` against **Azure SQL** (`DATABASE_URL` = the prod
      mssql+pyodbc string). Confirm `alembic current` = latest.

## Swap credentials (production)

- [ ] Create a **production** Autotask API-only user (least privilege) — repeat
      [setup/autotask-api-user.md](setup/autotask-api-user.md) in the **production**
      zone. New username/secret/integration-code into the **production** Key Vault.
- [ ] **Re-authorise GHL OAuth** for the **production** location (the sandbox token
      does not work for production) — repeat the OAuth flow against the prod redirect
      URI; confirm the rotating refresh token is persisted to Key Vault.
- [ ] New **GHL webhook signing secret** for the production app; subscribe prod
      webhooks to the prod endpoint.
- [ ] Production **Teams bot** messaging endpoint points at the prod App Service;
      new `APPROVAL_CALLBACK_SECRET`.

## Flip the switch

- [ ] Set `ENVIRONMENT=production` (App Service setting / Key Vault).
- [ ] Restart the app. **Confirm the startup banner says 🔴 PRODUCTION** and
      `/health` shows `"environment": "production"`.
- [ ] `/status` in Teams confirms PRODUCTION.

## Smoke test (production, low blast radius)

- [ ] Send one **non-destructive** GHL contact event (a clean new prospect with a
      known company). Confirm: dedupe ran, before-state audit row written, expected
      action (create/approval) — and **no unexpected Autotask writes**.
- [ ] Force one conflict and confirm it routes to an **approval** (not an overwrite).
- [ ] Approve one item from Teams; confirm the audit + transaction rows.
- [ ] Verify the daily digest / transaction feed populate.

## Rollback plan (have this ready BEFORE flipping)

- [ ] **Immediate stop:** trip the circuit breaker (pause all Autotask writes) or
      set `ENVIRONMENT=sandbox` and restart — production writes cease.
- [ ] **Revert a bad write:** every Autotask write has a `before_state` in the
      immutable `audit_log` (Spec §5.4). Use it to restore field values manually.
- [ ] **Replay safety:** `processed_events` makes re-delivered events no-ops, so a
      restart won't double-apply.
- [ ] Keep the sandbox config intact so you can fall back instantly.

## Post-cutover

- [ ] Watch App Insights for errors / circuit-breaker trips for the first 24–48h.
- [ ] Confirm GHL refresh-token rotation is persisting to Key Vault (no auth
      failures after the first token expiry).
