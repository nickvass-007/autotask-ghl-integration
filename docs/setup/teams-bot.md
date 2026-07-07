# Setup: Teams Ops Bot (Azure Bot Service)

⚠️ A Teams **incoming webhook** can post messages but **cannot** handle the
Approve/Reject button callbacks — interactive Adaptive Cards require a real **bot**
(Azure Bot Service). That's what this sets up (Spec §11).

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

## 4. Wire the callback verification secret

Set `APPROVAL_CALLBACK_SECRET` (in `.env` locally / Key Vault in prod). The
Adaptive Card Approve/Reject buttons include this token; the
`POST /approvals/{id}/decide` endpoint rejects any callback whose token doesn't
match (Spec §11.1 "verify the caller").

## 5. Commands available (Stage 1)

In the Ops channel, message the bot:

- `/status` — shows the **active environment** (Spec §6).
- `/pending` — pending approvals.
- `/transactions` — recent sync transaction feed (Spec §11.2).
- `/digest` — 24-hour summary.
- `/audit <entity_type> <id>` — "what happened to this contact?" (Spec §11.4).

Approval cards post automatically whenever the Contacts flow raises one.

## Backup email channel

High-severity items also email admins via **Microsoft Graph** (Spec §11) — set
`GRAPH_*` and `ADMIN_ALERT_EMAILS`. This is best-effort and never blocks sync.

## Local testing without the Azure Bot channel

The `POST /api/messages` endpoint exposes the same command router, so you can test
commands with a plain HTTP POST `{"text": "/pending"}` while developing, before the
Teams channel is wired.
