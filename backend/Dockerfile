# Cloud Run image for predicare-api.
#
# Unlike backend/api/Dockerfile (build context = backend/api, used by
# docker-compose which bind-mounts ml-models), this Dockerfile uses the
# parent `backend/` as its build context so it can bake BOTH the API and the
# ml-models package into the image. Without ml-models in the image the
# /assistant and /redistribution endpoints fail at request time with
# ModuleNotFoundError (the code lazy-imports from /app/ml-models/**).
#
# Build & deploy (from repo root):
#   gcloud run deploy predicare-api --source backend --region asia-south1 \
#     --project smart-health-501313
# or explicitly:
#   docker build -f backend/Dockerfile -t \
#     asia-south1-docker.pkg.dev/smart-health-501313/predicare/api:latest backend
#   docker push  asia-south1-docker.pkg.dev/smart-health-501313/predicare/api:latest
#   gcloud run deploy predicare-api --image <that> --region asia-south1 \
#     --project smart-health-501313

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ML_MODELS_DIR=/app/ml-models

WORKDIR /app

# System deps for asyncpg/psycopg and PostGIS client
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# API code -> /app ; ml-models -> /app/ml-models (imported at request time by
# assistant.py, redistribution.py, predict.py).
COPY api/ /app/
COPY ml-models/ /app/ml-models/

EXPOSE 8000

# Bind to $PORT when present (Cloud Run injects it, default 8080). Shell form expands the var.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}
