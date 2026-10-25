# infra/

**Owners:** backend. Zero-cost deployment only.

| Item | Plan | Tier |
|---|---|---|
| `.env.example` | All environment variables; copy to repo-root `.env` | P0 |
| `Dockerfile` | One image: FastAPI serves the API, WebSocket and the built frontend | P1 |
| Azure Container Apps | Consumption plan, inside the monthly free grant. 1 replica, scale-to-zero off during judging. | P1 |
| LLM | `LLM_MODE=cache` in the deployed app, so judges cost nothing. Live calls only during development. | P0 |

No Cosmos DB, Web PubSub or paid services. State lives in memory; restarting the app restarts the match.
