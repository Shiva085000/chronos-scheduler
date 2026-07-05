# Repo-root-context variant of backend/Dockerfile for PaaS deploys whose
# build context is the repository root (selected per service via
# RAILWAY_DOCKERFILE_PATH). Keep in sync with backend/Dockerfile.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements-dev.txt backend/requirements.lock ./
RUN pip install -r requirements.lock

COPY backend/alembic.ini backend/pytest.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# API by default; SERVICE_ROLE=worker switches the entrypoint's role.
CMD ["./docker-entrypoint.sh"]
