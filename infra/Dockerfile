# One image: FastAPI serves the API, the WebSocket and the frontend (premier-x at /, fan-app at /classic/).
# Build from the repo root:  docker build -f infra/Dockerfile -t matchday-call .
# Same Python as the venv the app was tested on (the pinned numpy needs 3.12+).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_MODE=cache

WORKDIR /app

# The exact versions the app was tested with locally (pip freeze of the dev venv), so the
# deployed app does not pick up a newer FastAPI/Starlette/uvicorn than the one we played on.
COPY backend/requirements.txt backend/requirements.txt
COPY infra/constraints.txt infra/constraints.txt
RUN pip install --no-cache-dir -r backend/requirements.txt -c infra/constraints.txt

# Runtime needs only the backend, the contracts (event validation) and the match data.
COPY contracts/ contracts/
COPY data/source/ data/source/
COPY backend/ backend/
COPY frontend/fan-app/ frontend/fan-app/
COPY frontend/premier-x/ frontend/premier-x/
COPY frontend/ops-dashboard/ frontend/ops-dashboard/
COPY frontend/director/ frontend/director/

EXPOSE 8000

# Single worker: rooms, scores and sign-ups live in this process's memory.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "4096"]
