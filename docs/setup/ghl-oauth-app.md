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
2. **App details:** name (e.g. `Autotask Integration`), choose **Distribution: 
   Sub-Account** (it installs per location).
3. **Scopes** — request only what Stage 1 needs (least privilege ✅):
   - `contacts.readonly`
   - `contacts.write`
   *(Opportunities scopes come in Stage 2.)*
4. **Redirect URI:** add `http://localhost:8000/oauth/crm/callback` for local dev.
   (Add your deployed App Service callback URL later for production.)
   ⚠️ The path must not contain "ghl", "highlevel", or "leadconnector" — the
   marketplace rejects redirect URIs that reference their brand ("The redirect uri
   contains a Highlevel reference").
5. Save. The portal shows your **Client ID** and **Client Secret**.

## 3. Webhooks (signatures)

1. In the app's **Webhooks** section, subscribe to **Contact Create** and **Contact
   Update**.
2. Set the webhook URL to your endpoint (`/webhooks/crm/contact` — brand-neutral
   path, same restriction as the redirect URI). For local testing
   use a tunnel (e.g. `ngrok http 8000`) and use the tunnel URL.
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
GHL_SCOPES=contacts.readonly contacts.write
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
