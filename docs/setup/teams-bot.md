# Setup: Teams Ops Bot (Azure Bot Service)

⚠️ A Teams **incoming webhook** can post messages but **cannot** handle the
Approve/Reject button callbacks — interactive Adaptive Cards require a real **bot**
(Azure Bot Service). That's what this sets up (Spec §11).

## Two transports — pick one or run both

| | **Workflow webhook** (5-minute setup) | **Azure Bot** (full experience) |
|---|---|---|
| Approval cards | ✅ posted, with a **Review in Sync Control Centre** deep-link | ✅ posted, with working **Approve/Reject/Override buttons** |
| Transaction feed (BLOCKED/ERROR) | ✅ | — (use the webhook for the feed) |
| Slash commands (`/pending`, `/status`, …) | — | ✅ |
| Setup | Teams channel → **Workflows** → *"Post to a channel when a webhook request is received"* → paste the URL into `TEAMS_WORKFLOW_WEBHOOK_URL` | steps 0–4 below |

Both are best-effort: a Teams outage never breaks or slows the sync path.

## 0. Install the bot SDK extra

The CloudAdapter wiring needs the optional dependency:

```powershell
pip install -e ".[teams]"
```

## 1. Register the bot

Portal path: **Create a resource → Azure Bot**.

1. **Bot handle:** `autotask-ghl-ops`.
2. **Type of App:** *Single Tenant* (or User-Assigned Managed Identity).
3. **Creation type:** *Create new Microsoft App ID*.
4. Create. Then open the bot → **Configuration**:
   - **Messaging endpoint:** `https://<APP>.azurewebsites.net/api/messages`
   - Note the **Microsoft App ID** → `TEAMS_BOT_APP_ID`.
5. **Manage Password / Certificates & secrets** → new client secret →
   `TEAMS_BOT_APP_PASSWORD`. Note the tenant → `TEAMS_BOT_TENANT_ID`.

## 2. Enable the Teams channel

Bot → **Channels → Microsoft Teams → Apply/Save.**

## 3. Build the Teams app package + install to the Ops channel

1. Create a Teams app manifest (Teams Developer Portal → **Apps → New app**), set the
   bot id to your **Microsoft App ID**, scope = **Team**.
2. Download the app package (zip) and in Teams: **Apps → Manage your apps → Upload a
   custom app → Upload to a specific team** → choose your **Integration Ops** team /
   channel (Spec §11).

## 4. Wire the callback verification secret + say hello

Set `APPROVAL_CALLBACK_SECRET` (in `.env` locally / Key Vault in prod). The
Adaptive Card Approve/Reject buttons include this token; both the
`POST /approvals/{id}/decide` endpoint and the bot's card handler reject any
callback whose token doesn't match (Spec §11.1 "verify the caller").

Then **send the bot any message in the Ops channel** (e.g. `/status`). The bot
stores that conversation reference and uses it as the target for **proactive
approval cards** — until it has been spoken to once, it has nowhere to post.

## 5. Commands available (Stage 1)

In the Ops channel, message the bot:

- `/status` — shows the **active environment** (Spec §6).
- `/pending` — pending approvals.
- `/transactions` — recent sync transaction feed (Spec §11.2).
- `/digest` — 24-hour summary.
- `/audit <entity_type> <id>` — "what happened to this contact?" (Spec §11.4).

Approval cards post automatically whenever ANY flow raises one — contacts,
deals, customer onboarding (Stage C), and unmapped stages. HIGH-severity
approvals and circuit-breaker trips additionally email the admins.

## Backup email channel

High-severity items also email admins via **Microsoft Graph** (Spec §11) — set
`GRAPH_*` and `ADMIN_ALERT_EMAILS`. This is best-effort and never blocks sync.

## Local testing without the Azure Bot channel

With `TEAMS_BOT_APP_ID` unset, `POST /api/messages` degrades to the SDK-free
command router, so you can test commands with a plain HTTP POST
`{"text": "/pending"}` while developing, before the Teams channel is wired.
With `TEAMS_BOT_APP_ID` set, the endpoint runs the full botbuilder CloudAdapter
path (JWT-verified activities from the Bot Framework service).

⚠️ The proactive-card path (`continue_conversation`) needs a live verification
pass against your botbuilder version the first time you wire it — SDK
signatures drifted across 4.x, and the code carries a compatibility fallback.
