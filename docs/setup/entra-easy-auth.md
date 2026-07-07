# Setup: Entra ID SSO for the admin tool (Easy Auth)

⚠️ This secures **our admin tool only** — it does **not** authenticate to Autotask
or GHL, which keep their own credential models. Do not conflate the two (Spec §12.3).

Easy Auth lets the App Service require an Entra (Microsoft) login with almost no
code, and **App Roles** distinguish admins from viewers.

## 1. Register the app in Entra

Portal path: **Entra ID → App registrations → New registration**.

1. **Name:** `Autotask-GHL Admin`.
2. **Supported account types:** *Accounts in this organizational directory only*.
3. **Redirect URI:** Web → `https://<APP>.azurewebsites.net/.auth/login/aad/callback`.
4. **Register.** Note the **Application (client) ID** and **Directory (tenant) ID**.
5. **Certificates & secrets → New client secret** → copy the value.

## 2. Define App Roles (Spec §12.3)

**App registrations → your app → App roles → Create app role**, twice:

| Display name | Value | Allowed member types |
|---|---|---|
| Integration Admin | `Integration.Admin` | Users/Groups |
| Integration Viewer | `Integration.Viewer` | Users/Groups |

Then assign people: **Entra ID → Enterprise applications → your app → Users and
groups → Add user/group**, picking the role for each.

## 3. Turn on Easy Auth on the App Service

Portal path: **App Service → Settings → Authentication → Add identity provider**.

1. **Identity provider:** Microsoft.
2. **App registration type:** *Pick an existing app registration* → select the one
   above (paste client id/secret if prompted).
3. **Restrict access:** *Require authentication*.
4. **Unauthenticated requests:** *HTTP 302 redirect to log in*.
5. **Save.**

Now every request to the admin UI requires an Entra login. The signed-in user's
roles arrive in the `X-MS-CLIENT-PRINCIPAL` header; gate admin actions on
`Integration.Admin`.

## 4. ✅ Conditional Access MFA

In **Entra ID → Protection → Conditional Access**, create a policy that requires
**MFA** for this app (or for all admin users). This is what makes admin access to
the tool strong.

## What this does and doesn't do

- ✅ Secures who can open the admin UI and approve/override changes.
- ❌ Does **not** log in to Autotask or GHL — those use the API-only user and the
  OAuth Marketplace app respectively (separate guides).
