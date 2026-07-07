# Local development setup

Everything here runs against **sandbox / a GHL test location** — never production
(Spec §6). The active environment is printed on startup and shown at `/health`.

## Prerequisites

- **Python 3.12+** (the project targets 3.12). Check: `python --version`.
- **Docker Desktop** (for local Postgres). Check: `docker --version`.
- **Git** + **VS Code** (see the [README](../README.md) walkthrough if new).

## 1. Virtual environment + dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

A virtual environment keeps this project's packages isolated from the rest of your
system, so versions never clash between projects.

## 2. Start local Postgres

```powershell
docker compose up -d         # starts Postgres on localhost:5432
docker compose ps            # confirm it's healthy
```

The connection string is already set in `.env.example`
(`postgresql+psycopg://app:app@localhost:5432/autotask_ghl`).

## 3. Secrets file

```powershell
copy .env.example .env
```

Open `.env` and fill in your **sandbox** Autotask creds and **GHL test** creds.
See the guides under [docs/setup/](setup/) for how to obtain each value.
⚠️ `.env` is git-ignored — never commit it. `ENVIRONMENT` must stay `sandbox`.

## 4. Create the database schema

```powershell
alembic upgrade head
```

This runs the initial migration, creating all tables (the schema is defined once
and runs identically on Postgres here and Azure SQL in production — Spec §3.4).
To inspect: `alembic current` (shows `0001_initial`).

## 5. Run the app

```powershell
uvicorn integration.api.main:app --reload --app-dir src
```

- Startup logs print the **active environment banner** (should say 🟢 SANDBOX).
- `GET http://localhost:8000/health` → JSON including `"environment": "sandbox"`.
- `GET /docs` → interactive API docs (FastAPI/Swagger).

## 6. Authorise GoHighLevel (OAuth)

1. Visit `http://localhost:8000/oauth/ghl/authorize` in a browser.
2. Sign in to your **GHL test sub-account** and approve the app.
3. GHL redirects to `/oauth/crm/callback` (brand-neutral path — GHL rejects redirect
   URIs containing "ghl"/"highlevel"), which exchanges the code and stores the
   (rotating) refresh token. You'll see `{"status": "authorized"}`.

> Locally the token is held in process memory for convenience. In production it
> lives in **Key Vault** and is loaded via Managed Identity (Spec §12.3).

## 7. Exercise the Contacts flow

- **Via tests (no live creds needed):** `pytest` — runs the full Contacts flow
  (dedupe → conflict → approval → audit) against in-process fakes. 22 tests.
- **Via a simulated webhook:** POST a GHL contact payload to
  `/webhooks/crm/contact` (you'll need a valid `x-wh-signature` HMAC of the body
  using `GHL_WEBHOOK_SECRET`). The response reports the action taken
  (`created` / `approval` / `skipped`).
- **Inspect results** in the DB: `entity_mapping`, `approval_queue`, `audit_log`,
  `transaction_log`. Or hit `GET /approvals`.

## Useful commands

```powershell
pytest                      # run the test suite
pytest -q tests/test_contacts_flow.py
alembic upgrade head        # apply migrations
alembic downgrade -1        # roll back one migration
ruff check src              # lint
docker compose down         # stop Postgres (keeps data)
docker compose down -v      # stop + wipe the database volume
```

## Troubleshooting

- **`/health` says production** → your `.env` has `ENVIRONMENT=production`. Stop and
  fix it; production must be deliberate (Spec §6).
- **DB connection refused** → `docker compose ps`; make sure the `db` service is
  healthy and port 5432 isn't taken by another Postgres.
- **Autotask auth fails** → confirm the sandbox API-only user creds and that zone
  detection returned a URL (it's logged at startup of the first Autotask call).
