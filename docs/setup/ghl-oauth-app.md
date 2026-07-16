# Setup: GoHighLevel OAuth Marketplace app (TEST location)

GHL has **no true sandbox**, so for the sandbox phase we use a dedicated **test
sub-account / location** and a Marketplace app scoped to least privilege (Spec §6,
§12.3).

## 1. Create a test sub-account (location)

1. Log in to your GHL **agency** account.
2. **Sub-Accounts → Create Sub-Account** (or use an existing throwaway one).
3. Name it clearly, e.g. `INTERLINKED — Integration Sandbox`.
4. Note its **Location ID** (Settings → Business Info, or the URL). This is
   `GHL_LOCATION_ID`.

## 2. Create a Marketplace app

1. Go to the **GHL Marketplace developer portal**:
   <https://marketplace.gohighlevel.com/> → sign in → **My Apps → Create App**.
2. **App details:** name (e.g. `INTERLINKED Autotask Sync v2`), listing **Private**
   (never listed on the public marketplace), **Distribution: Agency & Sub-Account**.
   ⚠️ Distribution type is locked at creation — to change it you must create a new
   app (which is why v2 exists). Agency & Sub-Account keeps the multi-sub-account
   path open (each sub-account has its own Autotask instance).
3. **Scopes** — everything the current flows call, plus Phase-3 scopes requested
   up front so installed locations don't need re-authorization later:
   - `contacts.readonly`, `contacts.write` (covers contact notes/tags/tasks endpoints)
   - `businesses.readonly`, `businesses.write` (companies)
   - `opportunities.readonly`, `opportunities.write` (deals + pipelines)
   - `calendars.readonly`, `calendars/events.readonly`, `calendars/events.write`
   - `locations.readonly`, `locations/customFields.readonly`, `locations/customFields.write`
   - `locations/tags.readonly`, `locations/tags.write`
   - `users.readonly`
4. **Redirect URI:** add `http://localhost:8000/oauth/crm/callback` for local dev.
   (Add your deployed App Service callback URL later for production.)
   ⚠️ The path must not contain "ghl", "highlevel", or "leadconnector" — the
   marketplace rejects redirect URIs that reference their brand ("The redirect uri
   contains a Highlevel reference").
5. Save. The portal shows your **Client ID** and **Client Secret**.

## 3. Webhooks (signatures)

1. In the app's **Webhooks** section, subscribe to **ContactCreate, ContactUpdate,
   OpportunityCreate, OpportunityUpdate, OpportunityStageUpdate, NoteCreate**.
2. Set the webhook URL to the unified dispatcher endpoint (`/webhooks/crm` —
   brand-neutral path, same restriction as the redirect URI). A marketplace app
   has ONE webhook URL for all event types; the dispatcher routes on the
   payload's `type` field. For local testing use a tunnel (e.g. `ngrok http 8000`)
   and use the tunnel URL.
3. **There is no signing secret to copy.** GHL signs marketplace webhooks with
   *their* private key; the connector verifies the `x-ghl-signature` header
   (Ed25519) against GHL's published public key, which is baked into
   `connectors/ghl.py`. The legacy `x-wh-signature` (RSA-SHA256, sunset
   2026-07-01) is also accepted during the transition window. ⚠️ Inbound webhooks
   are rejected unless a signature verifies (Spec §4).
4. `GHL_WEBHOOK_SECRET` in `.env` is **optional** — set it only so
   `scripts/send_test_contact.py` can send locally signed fake webhooks.

## 4. Put the values in `.env`

```
GHL_CLIENT_ID=...
GHL_CLIENT_SECRET=...
GHL_LOCATION_ID=...
GHL_WEBHOOK_SECRET=...
GHL_REDIRECT_URI=http://localhost:8000/oauth/crm/callback
GHL_SCOPES=contacts.readonly contacts.write businesses.readonly businesses.write opportunities.readonly opportunities.write calendars.readonly calendars/events.readonly calendars/events.write locations.readonly locations/customFields.readonly locations/customFields.write locations/tags.readonly locations/tags.write users.readonly
```

## 5. Authorise (one-time per environment)

1. Run the app (`uvicorn ...`).
2. Browse to `http://localhost:8000/oauth/ghl/authorize`.
3. Pick the **test location**, approve.
4. The callback stores the access + **rotating refresh token**. ✅ The integration
   rotates the refresh token automatically on each refresh (Spec §12.1).

## Notes

- ⚠️ Each environment (sandbox/production) authorises **separately** and has its own
  token. Authorising the test location never grants access to production data.
- In production the tokens live in **Key Vault**, not in process memory.
