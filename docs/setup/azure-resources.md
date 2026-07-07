# Setup: Azure resources (deployment)

> These resources are **provisioned at deployment time**, not during Stage-1 local
> dev (Spec §3.4). This guide is here so the path is documented and ready. The
> `az` CLI commands are pasteable; portal equivalents are noted where it helps.

## 0. One-time: install + sign in

1. Install the Azure CLI: <https://aka.ms/installazurecli> (Windows MSI).
2. Sign in and pick your subscription:
   ```bash
   az login
   az account set --subscription "<YOUR-SUBSCRIPTION-NAME-OR-ID>"
   ```

## 1. Variables (edit, then paste the block)

```bash
RG=rg-autotask-ghl
LOC=australiaeast
APP=autotask-ghl-api            # must be globally unique (App Service name)
PLAN=plan-autotask-ghl
SQL=sql-autotask-ghl-$RANDOM    # globally unique
SQLDB=autotask_ghl
KV=kv-autotask-ghl-$RANDOM      # globally unique, <=24 chars
SBNS=sb-autotask-ghl-$RANDOM    # Service Bus namespace, globally unique
```

## 2. Resource group

```bash
az group create -n $RG -l $LOC
```

## 3. Azure SQL (the production database, Spec §12.2)

```bash
az sql server create -g $RG -n $SQL -l $LOC \
  --admin-user sqladmin --admin-password "<STRONG-PASSWORD>"
az sql db create -g $RG -s $SQL -n $SQLDB --service-objective S0
# Allow Azure services (tighten with VNet/Private Endpoint for real production):
az sql server firewall-rule create -g $RG -s $SQL -n AllowAzure \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
```

The connection string (`DATABASE_URL`) becomes:
`mssql+pyodbc://sqladmin:<PW>@<server>.database.windows.net/autotask_ghl?driver=ODBC+Driver+18+for+SQL+Server`
(Install the `azuresql` extra: `pip install ".[azuresql]"` so `pyodbc` is present.)

## 4. App Service (hosts FastAPI + bot endpoint + admin UI, Spec §12.2)

```bash
az appservice plan create -g $RG -n $PLAN --is-linux --sku B1
az webapp create -g $RG -p $PLAN -n $APP --runtime "PYTHON:3.12"
# Start command:
az webapp config set -g $RG -n $APP \
  --startup-file "uvicorn integration.api.main:app --host 0.0.0.0 --port 8000 --app-dir src"
```

## 5. Service Bus (durable event queue, Spec §12.2)

```bash
az servicebus namespace create -g $RG -n $SBNS -l $LOC --sku Standard
az servicebus queue create -g $RG --namespace-name $SBNS -n canonical-events \
  --enable-dead-lettering-on-message-expiration true
```

## 6. Key Vault (secrets, Spec §12.3)

See [keyvault-managed-identity.md](keyvault-managed-identity.md) — create the vault,
store secrets, and grant the App Service Managed Identity read access.

## 7. Observability (App Insights, Spec §12.2)

```bash
az monitor app-insights component create -g $RG -a appi-autotask-ghl -l $LOC
# Wire the connection string into the web app settings (APPLICATIONINSIGHTS_CONNECTION_STRING).
```

## 8. (Optional) API Management front door

For a single throttled/WAF'd ingress for GHL webhooks (Spec §12.2). Optional for
first deploy; add when you want centralised ingress control.

## 9. Functions (Timer) for polling + reconciliation

Autotask has no comprehensive webhooks, so outbound changes are polled and the
reconciliation sweep runs on a timer (Spec §4, §12.2). Create a Function App and
deploy the polling/reconciliation jobs (added in a later stage; the sync functions
are already written to be callable from a timer trigger).

## Next

- [Entra app registration + Easy Auth](entra-easy-auth.md)
- [Key Vault + Managed Identity](keyvault-managed-identity.md)
- [Teams bot registration](teams-bot.md)
- Then follow the [cutover checklist](../cutover-checklist.md).
