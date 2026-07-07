# Setup: Autotask API-only user (SANDBOX)

You need a dedicated **API-only user** in your **Autotask sandbox** zone. This user
is what the integration authenticates as. ⚠️ Create it in **sandbox** first — all
Stage-1 work runs against sandbox (Spec §6).

> Autotask UI labels shift slightly between releases; the path below is current as
> of writing. If a menu name differs, search Admin for "API User".

## 1. Confirm you have a sandbox zone

Autotask sandboxes are provisioned by Datto. If you're unsure whether you have one,
ask your Autotask account team. The sandbox has its **own** login URL and its own
data — separate from production.

## 2. Create an API-only resource (user)

1. Log in to the **sandbox** Autotask.
2. Top-left **≡ menu → Admin → Account Settings & Users** (or **Admin → Resources/Users**).
3. Under **Resources/Users (HR)**, click **New → New API User**.
4. Fill in:
   - **First / Last name:** e.g. `Integration`, `GHL-Sync`.
   - **Email:** a monitored internal address.
   - **Security Level:** **API User (system)**.
5. In the **API Tracking Identifier** section:
   - Choose **Integration Vendor**, then select your vendor (or "Custom" / a generic
     one if you don't have a registered vendor). This produces the **Integration
     Code**.
6. Set **Credentials**:
   - **Username** (looks like an email) — this is `AUTOTASK_USERNAME`.
   - **Generate the secret** — this is `AUTOTASK_SECRET`. ⚠️ Copy it now; it's shown once.
7. **Save.**

## 3. Scope it to least privilege ✅

The API user should only touch the entities we sync. Under the user's **security
level / object permissions**, grant access to: **Contacts**, **Companies
(Accounts)**, and (for later stages) **Opportunities**, **Tickets**, **Ticket
Notes**. Deny the rest. This limits blast radius if the credential leaks.

## 4. Put the three values in `.env`

```
AUTOTASK_USERNAME=...        # the API username
AUTOTASK_SECRET=...          # the generated secret (shown once)
AUTOTASK_INTEGRATION_CODE=...# the Integration/Tracking code
```

## 5. Zone detection (automatic — nothing to configure)

On first call the integration hits the global zone-detection endpoint
(`zoneInformation?user=<username>`) to discover your account's API base URL, then
caches it. You'll see `Autotask zone detected (sandbox): https://webservicesN...`
in the logs. If you ever need to pin it manually, set `AUTOTASK_ZONE_OVERRIDE_URL`.

## 6. (Optional) Holding Account for unmatched contacts

If you want unmatched contacts parked rather than blocked while awaiting a linkage
decision (Spec §9.3), create/choose an Account like **`Prospects – Unassigned`** in
sandbox and put its id in `AUTOTASK_HOLDING_ACCOUNT_ID`. Leave blank to require an
explicit approval instead.

## Verify

With `.env` filled and the app running, the first Autotask call (e.g. a webhook or
`pytest` against sandbox) should authenticate and log the detected zone. A 401/403
means the username/secret/integration-code triple is wrong or the security level
isn't **API User (system)**.
