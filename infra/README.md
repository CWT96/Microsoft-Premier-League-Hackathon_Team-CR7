# infra/

**Owners:** backend. Zero-cost deployment only.

| Item | Plan | Tier |
|---|---|---|
| `.env.example` | All environment variables; copy to repo-root `.env` | P0 |
| `Dockerfile` | One image: FastAPI serves the API, WebSocket and the built frontend | P1 |
| Azure Container Apps | Consumption plan, inside the monthly free grant. 1 replica, scale-to-zero off during judging. | P1 |
| LLM | `LLM_MODE=cache` in the deployed app, so judges cost nothing. Live calls only during development. | P0 |

No Cosmos DB, Web PubSub or paid services. State lives in memory; restarting the app restarts the match.

## Deploy (done 2026-10-27)

Live at https://matchday-call.livelyforest-089ca771.eastus.azurecontainerapps.io (`/healthz`, `/api/*`, `/ws`).
Subscription "Azure subscription 1" (free trial), resource group `rg-matchday-call`, East US: registry `matchdaycall46e7fe`, environment `cae-matchday-call` (logs off), app `matchday-call` (1 replica, 0.5 CPU / 1 GiB, env `LLM_MODE=cache`, `TRUST_PROXY=true`, `PUBLIC_BASE_URL`).

Redeploy a new image (from the repo root):

```bash
docker build --platform linux/amd64 -f infra/Dockerfile -t matchdaycall46e7fe.azurecr.io/matchday-call:<tag> .
az acr login -n matchdaycall46e7fe && docker push matchdaycall46e7fe.azurecr.io/matchday-call:<tag>
az containerapp update -g rg-matchday-call -n matchday-call --image matchdaycall46e7fe.azurecr.io/matchday-call:<tag>
```

Tear down after judging: `az group delete -n rg-matchday-call`.
