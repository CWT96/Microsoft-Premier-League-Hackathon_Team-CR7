# One image: FastAPI serves the API and the WebSocket (and later the built frontend).
# Build from the repo root:  docker build -f infra/Dockerfile -t matchday-call .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_MODE=cache

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Runtime needs only the backend, the contracts (event validation) and the match data.
COPY contracts/ contracts/
COPY data/source/ data/source/
COPY backend/ backend/
COPY frontend/fan-app/ frontend/fan-app/

EXPOSE 8000

# Single worker: rooms, scores and sign-ups live in this process's memory.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "4096"]
