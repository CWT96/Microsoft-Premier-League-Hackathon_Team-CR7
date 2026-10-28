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

### Same app deployed as tested

- **Same files:** the image copies `frontend/premier-x/` (served at `/`) and `frontend/fan-app/` (`/classic/`).
- **Same versions:** Python 3.13 and `infra/constraints.txt`, the `pip freeze` of the venv the app was tested in. After changing `backend/requirements.txt`, regenerate it with `.venv/bin/pip freeze > infra/constraints.txt`.
- **No stale browser files:** the app's HTML and JS are sent with `Cache-Control: no-cache`, so a phone that opened the old version picks up the new `pitch.js` on the next load.
- **One replica:** rooms, scores and the match clock live in memory. Keep exactly 1 replica, with scale-to-zero off.
- **Build from a clean checkout of the pushed commit and tag it with the short SHA** (`git rev-parse --short HEAD`), so the deployed image maps to one commit.
- **After deploy:**
  - `/healthz` returns 200.
  - `/` shows The Premier X.
  - Play one full match as Arsenal and one as Chelsea at phone size.

Redeploy a new image (from the repo root):

```bash
docker build --platform linux/amd64 -f infra/Dockerfile -t matchdaycall46e7fe.azurecr.io/matchday-call:<tag> .
az acr login -n matchdaycall46e7fe && docker push matchdaycall46e7fe.azurecr.io/matchday-call:<tag>
az containerapp update -g rg-matchday-call -n matchday-call --image matchdaycall46e7fe.azurecr.io/matchday-call:<tag>
```

Tear down after judging: `az group delete -n rg-matchday-call`.

## AI call lines (Azure OpenAI)

The demo's per-call setup and explanation lines are written by `gpt-4.1-mini` on Azure OpenAI resource `aoai-matchday-39c125` (rg-matchday-call, East US, deployment `gpt-4.1-mini`) and committed to `backend/llm_cache/demo_lines/`. The deployed app only reads the cache (`LLM_MODE=cache`); no key is deployed. To regenerate (e.g. after editing `backend/agents/prompts/demo_lines.prompt`):

```bash
export AZURE_AI_ENDPOINT=https://aoai-matchday-39c125.openai.azure.com/openai/v1 AZURE_AI_DEPLOYMENT=gpt-4.1-mini LLM_MODE=live
export AZURE_AI_API_KEY=$(az cognitiveservices account keys list -n aoai-matchday-39c125 -g rg-matchday-call --query key1 -o tsv)
.venv/bin/python -m backend.demo.lines --drop-rejected && .venv/bin/python -m backend.demo.lines
```
