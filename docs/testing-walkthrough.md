# Stage-1 testing walkthrough (full sandbox wiring)

A phased, copy-paste guide to test Stage 1 on Windows — from a machine with neither
Docker nor Python 3.12, through to a live Contacts sync against your Autotask
sandbox. Do one phase, confirm its **✅ Checkpoint**, then continue.

> Run all terminal commands in the **VS Code integrated terminal** (PowerShell),
> opened in the project folder. After any install, open a **new** terminal so the
> PATH refreshes. `>>>` lines are what you type; everything assumes you're in the
> project root.

---

## Phase 1 — Install Docker Desktop

Docker runs the local Postgres database in a container so you don't install Postgres
by hand.

```powershell
winget install Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
```

Then:
1. **Reboot** if the installer asks (Docker needs the WSL2 / virtualization feature).
2. Launch **Docker Desktop** from the Start menu. Accept the service agreement.
3. If it prompts to install/update the **WSL2 kernel**, click through and let it.
4. Wait until the bottom-left status shows **"Engine running"** (green whale).

```powershell
docker --version
docker run --rm hello-world
```

**✅ Checkpoint:** `docker --version` prints a version and `hello-world` prints
"Hello from Docker!". If `docker` is "not recognized", open a new terminal; if the
engine won't start, ensure virtualization is enabled in BIOS and WSL2 is installed
(`wsl --install`).

> No-Docker fallback: if Docker won't cooperate, you can still run everything except
> the Postgres container by using SQLite — see the note at the end.

---

## Phase 2 — Install Python 3.12

```powershell
winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
```

Open a **new** terminal, then:

```powershell
py -3.12 --version
```

**✅ Checkpoint:** prints `Python 3.12.x`. (The `py` launcher lets you pick 3.12
explicitly even if other Pythons are installed.)

---

## Phase 3 — Project virtual environment + dependencies

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -Bypass -Force      # allow activating the venv this session
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Your prompt now starts with `(.venv)`.

**✅ Checkpoint:** `pip --version` shows a path inside `...\.venv\...`, and the
install finishes without red errors.

---

## Phase 4 — Start the database + create the schema

```powershell
docker compose up -d        # start Postgres in the background
docker compose ps           # should show service "db" as healthy/running
copy .env.example .env      # your local secrets file (git-ignored)
alembic upgrade head        # create every table
alembic current             # should print 0001_initial (head)
```

**✅ Checkpoint:** `docker compose ps` shows `db` running, and `alembic current`
prints `0001_initial`. (You can leave `.env` mostly blank for now — Phases 4–6 don't
need real credentials.)

---

## Phase 5 — Run the offline test suite

```powershell
pytest -q
```

**✅ Checkpoint:** `22 passed`. This proves the whole Contacts flow (dedupe →
conflict → approval → audit), Account-linkage guard, idempotency, and the
environment switch — all against in-process fakes, no external accounts.

---

## Phase 6 — Run the app and see the active environment

```powershell
uvicorn integration.api.main:app --reload --app-dir src
```

Watch the startup log for the banner: `ACTIVE ENVIRONMENT: 🟢 SANDBOX`. Then in a
browser:
- <http://localhost:8000/health> → JSON with `"environment": "sandbox"`.
- <http://localhost:8000/docs> → interactive API docs.

Leave this running in its own terminal. Open a **second** terminal (activate the
venv again with `.\.venv\Scripts\Activate.ps1`) for the next phases.

**✅ Checkpoint:** `/health` returns `"environment": "sandbox"` and the startup
banner says SANDBOX.

---

## Phase 7 — Get the sandbox credentials

Follow the two detailed guides, which give the exact portal clicks:
- Autotask sandbox API-only user → [setup/autotask-api-user.md](setup/autotask-api-user.md)
  → produces `AUTOTASK_USERNAME`, `AUTOTASK_SECRET`, `AUTOTASK_INTEGRATION_CODE`.
- GHL test sub-account + Marketplace app → [setup/ghl-oauth-app.md](setup/ghl-oauth-app.md)
  → produces `GHL_CLIENT_ID`, `GHL_CLIENT_SECRET`, `GHL_LOCATION_ID`,
  `GHL_WEBHOOK_SECRET`.

**✅ Checkpoint:** you have all the values above written down.

---

## Phase 8 — Fill in `.env`

Open `.env` and paste your values. At minimum for a live Contacts test:

```
ENVIRONMENT=sandbox
AUTOTASK_USERNAME=...
AUTOTASK_SECRET=...
AUTOTASK_INTEGRATION_CODE=...
GHL_CLIENT_ID=...
GHL_CLIENT_SECRET=...
GHL_LOCATION_ID=...
GHL_WEBHOOK_SECRET=pick-any-strong-string-for-local-testing
```

For local testing you can set `GHL_WEBHOOK_SECRET` to any value you choose — the
test script below signs with the same value. Restart the app (Ctrl+C in its
terminal, then re-run the `uvicorn` command) so it picks up the new `.env`.

**✅ Checkpoint:** the app restarts cleanly and `/health` still says sandbox.

---

## Phase 9 — Live smoke test: a clean new contact

This sends a signed contact webhook; the app authenticates to Autotask (zone
detection runs — watch the log), dedupes against your sandbox, and either creates or
raises an approval.

```powershell
py -3.12 scripts/send_test_contact.py --email "smoke1@example-newco.com" --company "Zzz Test Co 1"
```

Expected (a company that doesn't exist in your sandbox, no holding account set):
`{"action": "approval", ...}` with an `ACCOUNT_CREATE` approval — Accounts are never
auto-created (Spec §9.3). Then confirm it landed:

- <http://localhost:8000/approvals> → shows the pending approval.
- Bot command router: `Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/messages -ContentType application/json -Body '{"text":"/pending"}'`

Now test the **exact-match create** path: in Autotask sandbox, note an existing
Account's exact name, then:

```powershell
py -3.12 scripts/send_test_contact.py --email "smoke2@example.com" --company "<EXACT EXISTING ACCOUNT NAME>"
```

Expected: `{"action": "created", ...}` and a new Contact appears in Autotask under
that Account, with a row in `entity_mapping` and a before-state row in `audit_log`.

**✅ Checkpoint:** clean-miss → approval (nothing created); exact-account → contact
created and linked. Both appear in the transaction feed.

---

## Phase 10 — Inspect what happened

```powershell
# recent activity via the bot router:
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/messages -ContentType application/json -Body '{"text":"/transactions"}'
```

Or query the DB directly (psql inside the container):

```powershell
docker exec -it autotask_ghl_db psql -U app -d autotask_ghl -c "select id,status,operation,summary from transaction_log order by id desc limit 10;"
docker exec -it autotask_ghl_db psql -U app -d autotask_ghl -c "select id,approval_type,severity,status from approval_queue order by id desc limit 10;"
docker exec -it autotask_ghl_db psql -U app -d autotask_ghl -c "select id,operation,entity_type,entity_id from audit_log order by id desc limit 10;"
```

**✅ Checkpoint:** `transaction_log` shows your operations; `audit_log` has a
before-state row for every Autotask write; `approval_queue` holds the gated items.

---

## (Optional) Authorise GHL OAuth

Needed only to push Autotask → GHL or receive real GHL webhooks. With the app
running, browse to <http://localhost:8000/oauth/ghl/authorize>, pick your **test
location**, approve. The callback stores the rotating refresh token.

---

## Stopping / cleaning up

```powershell
# stop the app:  Ctrl+C in its terminal
docker compose down       # stop Postgres (keeps data)
docker compose down -v    # stop + wipe the database volume (fresh start)
```

---

## No-Docker fallback (if Docker can't run)

You can run Phases 3, 5, 6, 9, 10 without Postgres by using a local SQLite file —
good enough to exercise the flow:

```powershell
$env:DATABASE_URL = "sqlite+pysqlite:///./local_dev.db"
alembic upgrade head
uvicorn integration.api.main:app --reload --app-dir src
```

SQLite is for convenience only; the real local target is Postgres (matches Azure SQL
behaviour more closely).
