# Autotask ⇄ GoHighLevel Integration

An **in-house, production-grade** integration between **Autotask PSA** (Datto) and
**GoHighLevel (GHL / LeadConnector)**, hosted on **Azure**. No third-party iPaaS sits in the
runtime path — the mechanics are ours to control.

> **Status: Stage 3.** Foundation + connectors + **Contacts, Companies, Opportunities, Tickets
> and Notes flows** (Flow 2 inert until configured), the **Sync Control Centre portal**, **Teams
> approvals/feed** (bot + Workflow webhook), and the **Fabric export seam**. Production cutover
> and the Fabric hub repoint come in later stages. See [Stage boundaries](#stage-boundaries).

---

## The mental model (read this first)

This is **not** two equal systems mirroring each other. It is a customer **lifecycle with a
handoff boundary**:

| System | Role | Owns |
|---|---|---|
| **GoHighLevel** | **System of engagement** | New business / prospecting, and marketing to existing customers |
| **Autotask** | **System of record** (protected source of truth) | Service delivery, contracts, the authoritative customer record |

The integration keeps the **engagement surface (GHL)** correctly informed by the **record
(Autotask)**, lets new business originate in GHL and flow **into** Autotask at the handoff point,
and — above all — **protects Autotask from being silently damaged**.

**The protection rules (Spec §5), which every flow obeys:**

- **Autotask → GHL** is free-flowing. Autotask wins.
- **GHL → Autotask** is **gated**: a clean dedupe miss is safe to create; a fuzzy match raises an
  approval; a conflict with a *populated* Autotask field is **never** auto-overwritten; filling an
  *empty* Autotask field is additive and logged.
- Autotask **Accounts are never auto-created or auto-renamed**.
- The integration **never deletes** Autotask records.
- The **before-state is captured to an immutable audit log before every Autotask write**, so any
  change is manually revertible.
- A **circuit breaker** pauses all Autotask writes if conflict volume or write-failure rate spikes.

---

## Brand-new to all of this? Start here (Spec §3.2)

This walkthrough assumes you have used a terminal a little but have **not** set up Git, GitHub, or
a Python project before. Follow it top to bottom.

### What Git, a commit, a branch, and a push actually are (60-second model)

- **Git** is a tool that takes snapshots of your project folder over time.
- A **commit** is one labelled snapshot ("here's what the project looked like, with a note about
  what changed"). Commits are local to your machine until you push them.
- A **branch** is a movable label pointing at a line of commits. `main` is the default branch. You
  can make other branches to try things without disturbing `main`.
- A **push** uploads your local commits to GitHub so they're backed up and shareable.
- **GitHub** is the website that hosts the repository (the project's Git history) in the cloud.

### 1. Install Git (Windows)

1. Download from <https://git-scm.com/download/win>. The download starts automatically.
2. Run the installer. The defaults are fine — keep clicking **Next**, then **Install**, then **Finish**.
3. Open a **new** terminal (PowerShell) and verify:
   ```powershell
   git --version
   ```
   You should see something like `git version 2.4x.x`. If "command not found", close and reopen
   the terminal so it picks up the new install.

### 2. Install VS Code + the Claude Code extension

1. Download VS Code from <https://code.visualstudio.com/> and install (defaults are fine).
2. Open VS Code. Click the **Extensions** icon in the left sidebar (four squares).
3. Search **Claude Code**, click **Install** on the official Anthropic extension.

### 3. Create the GitHub repository

1. Create a free account at <https://github.com/> if you don't have one.
2. Click the **+** (top-right) → **New repository**.
3. On the *Create a new repository* screen, set:
   - **Repository name:** `autotask-ghl-integration`
   - **Visibility:** **Private** ⚠️ (this code references credentials and internal systems — never make it public)
   - **Initialize this repository with:** leave **everything unticked** — *no* README, *no*
     `.gitignore`, *no* license. We create those locally so the very first file is our own
     `.gitignore` (see step 6). GitHub adding a README first would get in the way.
4. Click **Create repository**. GitHub shows a "…or push an existing repository from the command
   line" box — keep that tab open; you'll use the URL it shows.

### 4. Authenticate Git to GitHub

The simplest path on Windows is to let **VS Code sign you in**:

1. In VS Code, open the **Accounts** menu (the person icon, bottom-left) → **Sign in with GitHub**.
2. A browser opens; approve the authorization. VS Code now holds your GitHub credentials.

*(Alternative — Personal Access Token (PAT):* if you prefer the terminal, create a token at
GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**, grant
it **Contents: Read and write** on this repo, and paste it when Git prompts for a password on your
first push. The VS Code sign-in is recommended because you don't have to manage token expiry by
hand.)

### 5. Get this project onto your machine

This project folder **already exists locally** and is already a Git repository. You just need to
connect it to the GitHub repo you created and push:

```powershell
# from inside the project folder:
git remote add origin https://github.com/<your-username>/autotask-ghl-integration.git
git branch -M main
```

*(If you were starting from scratch instead, you'd `git clone <url>` into a projects folder via
VS Code's **Clone Repository** command. Here the folder is already set up.)*

### 6. Your first commit and push

The first file in any repo must be `.gitignore` so secrets can never be committed — that file is
**already created** here (open `.gitignore` to see it). Now stage everything, commit, and push:

```powershell
git add .
git commit -m "Stage 1 scaffold: foundation, schema, connectors, Contacts flow"
git push -u origin main
```

⚠️ **Before your first push, confirm `.env` is *not* in the list.** Run `git status`. You should
see `.env.example` (the safe template) but **never** `.env` (your real secrets). If you ever see
`.env` staged, stop and remove it: `git rm --cached .env`.

---

## Run it locally (Stage 1)

> Full detail in [docs/local-dev.md](docs/local-dev.md). Quick version:

```powershell
# 1. Create and activate a Python virtual environment (isolates this project's packages)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the project + dev tools
pip install -e ".[dev]"

# 3. Start the local Postgres database (needs Docker Desktop running)
docker compose up -d

# 4. Create your local secrets file from the template, then fill it in
copy .env.example .env
#    -> open .env and paste your SANDBOX Autotask + GHL TEST creds (see docs/setup/)

# 5. Create the database schema (runs the Alembic migration)
alembic upgrade head

# 6. Run the API + bot endpoint
uvicorn integration.api.main:app --reload --app-dir src
```

Visit <http://localhost:8000/health> — it reports the **active environment** (should say
`sandbox`). The active environment is also printed as a banner in the startup log. ⚠️ If it ever
says `production` on your dev machine, stop and check your `.env`.

Run the test suite (validates the Contacts flow against mocked Autotask/GHL responses — no live
credentials needed):

```powershell
pytest
```

---

## Why this stack (Spec §12.1)

- **Python 3.12 + FastAPI** — greenfield, fastest to stand up, and one language covers the API,
  the polling jobs, and the Teams bot, so there's no context-switching across the system.
- **SQLAlchemy + Alembic** — the schema is defined once as ORM models and migrated identically onto
  **local Postgres** and **Azure SQL** in production, so we develop locally for free and avoid
  engine-specific SQL (Spec §3.4).
- **httpx + tenacity** — async HTTP with exponential backoff + jitter for Autotask/GHL rate limits.
- **Azure Bot Service (botbuilder)** — a *real* bot is required for interactive Approve/Reject
  buttons; a Teams incoming webhook cannot handle button callbacks (Spec §11).

See [docs/architecture.md](docs/architecture.md) for the full component & data-flow diagram and the
justification for each Azure service.

---

## Repository layout

```
.
├── .gitignore                  # first file — keeps secrets out of Git (§3.3)
├── .env.example                # documented template; copy to .env (git-ignored)
├── docker-compose.yml          # local Postgres only
├── pyproject.toml              # deps + tooling
├── alembic.ini                 # migration config
├── config/                     # field-mapping + source-of-truth YAML (§5, §9)
│   ├── contacts.mapping.yaml
│   ├── opportunities.mapping.yaml   # schema present; logic is Stage 2
│   └── stage_mapping.yaml
├── docs/
│   ├── architecture.md         # Mermaid diagram + Azure service justification
│   ├── local-dev.md
│   ├── cutover-checklist.md    # sandbox → production (§6)
│   └── setup/                  # click-by-click portal + az CLI guides (§13.1.10)
├── migrations/                 # Alembic environment + versions
├── src/integration/
│   ├── config/                 # settings (env switch) + mapping loader
│   ├── db/                     # SQLAlchemy models (full schema, §3.4)
│   ├── canonical/              # system-agnostic hub entities (§7.1)
│   ├── connectors/             # base contract + Autotask + GHL (§7.2)
│   ├── sync/                   # matching, conflicts, approvals, audit, contacts, reconcile
│   ├── teams/                  # Ops bot: approval cards + transaction feed (§11)
│   ├── core/                   # circuit breaker, idempotency, logging
│   └── api/                    # FastAPI app (API + bot endpoint + health)
└── tests/
```

---

## Stage boundaries

**Stage 1 (this delivery):** repo foundation; full schema migrated locally; canonical model +
connector interface; Autotask & GHL connectors authenticating against sandbox; **Contacts flow
end-to-end** (dedupe → conflict → approval → audit) with Account-linkage guarding; Teams bot for
Contacts approvals + transaction feed; environment switch defaulting to sandbox and visible
everywhere.

**Stage 2 (code complete, needs configuration):** Flow 2 Opportunities/Tickets — Autotask→GHL
mirrors (Sales + read-only Service pipelines), the gated GHL→Autotask Opportunity direction,
Stage C conversion handoff (closed-won by default, configurable earlier via
`conversion_stage_ids`), Stage E classification sync, and the Autotask polling sweep — which now
also mirrors **Accounts → GHL Businesses** and **Ticket Notes → GHL contact notes**, with the
`/webhooks/crm/note` endpoint carrying GHL notes back onto mirrored tickets. All of it is
**inert until configured** — follow [docs/flow2-setup.md](docs/flow2-setup.md) (GHL scopes,
pipelines, `config/stage_mapping.yaml`, `ENABLE_POLLER`).

**Stage 3 (this delivery):** Teams for real — proactive Adaptive-Card approvals with working
Approve/Reject buttons (Azure Bot / CloudAdapter, `pip install -e ".[teams]"`), a
zero-infrastructure Workflow-webhook transport for cards + the BLOCKED/ERROR transaction feed,
Graph email alerts for HIGH approvals and circuit-breaker trips
([docs/setup/teams-bot.md](docs/setup/teams-bot.md)); plus the **Microsoft Fabric ingestion
seam** — token-gated incremental `/export/{transactions,mappings,approvals}` feeds and the
hub repoint plan in [docs/fabric-roadmap.md](docs/fabric-roadmap.md).

**Later stages (NOT built yet):** live Azure provisioning + production cutover; the Fabric
repoint itself (Phases F1–F3 in the roadmap); additional connectors (e.g. 3CX). The **seams**
exist (full schema, canonical hub, connector contract, config-driven mapping) so they plug in
without a rewrite — see [CONTRIBUTING.md](CONTRIBUTING.md).
