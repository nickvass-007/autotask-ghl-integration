# Setup: Key Vault + Managed Identity

All API credentials, OAuth tokens, and signing secrets live in **Azure Key Vault**.
The App Service reads them via its **Managed Identity** — so there are **no secrets
stored for Azure itself** (Spec §12.3). Locally you use `.env`; in production the
app loads the same names from Key Vault.

## 1. Create the vault and store secrets

```bash
az keyvault create -g $RG -n $KV -l $LOC --enable-rbac-authorization true

# Store each secret (names mirror the .env variable names):
az keyvault secret set --vault-name $KV --name AUTOTASK-USERNAME --value "..."
az keyvault secret set --vault-name $KV --name AUTOTASK-SECRET --value "..."
az keyvault secret set --vault-name $KV --name AUTOTASK-INTEGRATION-CODE --value "..."
az keyvault secret set --vault-name $KV --name GHL-CLIENT-ID --value "..."
az keyvault secret set --vault-name $KV --name GHL-CLIENT-SECRET --value "..."
az keyvault secret set --vault-name $KV --name GHL-WEBHOOK-SECRET --value "..."
az keyvault secret set --vault-name $KV --name DATABASE-URL --value "mssql+pyodbc://..."
az keyvault secret set --vault-name $KV --name APPROVAL-CALLBACK-SECRET --value "..."
# ...and the Teams/Graph secrets as needed.
```

⚠️ Sandbox and production use **separate vaults** (or at least separate secret
names) so one environment can never read the other's credentials (Spec §6).

## 2. Give the App Service a Managed Identity

```bash
az webapp identity assign -g $RG -n $APP
# capture the principalId it prints:
PRINCIPAL=$(az webapp identity show -g $RG -n $APP --query principalId -o tsv)
```

## 3. Grant that identity read access to the vault (RBAC)

```bash
KVID=$(az keyvault show -n $KV --query id -o tsv)
az role assignment create --assignee $PRINCIPAL \
  --role "Key Vault Secrets User" --scope $KVID
```

✅ "Key Vault Secrets User" is read-only on secret values — least privilege.

## 4. Reference secrets from App Service settings

Two common patterns:

**a) Key Vault references in app settings** (simplest):

```bash
az webapp config appsettings set -g $RG -n $APP --settings \
  AUTOTASK_SECRET="@Microsoft.KeyVault(VaultName=$KV;SecretName=AUTOTASK-SECRET)" \
  GHL_CLIENT_SECRET="@Microsoft.KeyVault(VaultName=$KV;SecretName=GHL-CLIENT-SECRET)" \
  DATABASE_URL="@Microsoft.KeyVault(VaultName=$KV;SecretName=DATABASE-URL)" \
  ENVIRONMENT="production"
```

The platform resolves these at runtime using the Managed Identity, and they appear
to the app as ordinary environment variables — so `Settings` loads them exactly as
it loads `.env` locally. No app code change needed.

**b)** Or load them in code via `azure-identity` + `azure-keyvault-secrets` with
`DefaultAzureCredential()` (Managed Identity in Azure, your `az login` locally).

## 5. Secret rotation ✅

- Enable **Key Vault rotation policies** on long-lived secrets.
- The **GHL refresh token rotates in code** on every refresh (Spec §12.1); persist
  the new refresh token back to Key Vault when you wire production token storage.
