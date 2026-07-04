FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# requirements.txt/-dev.txt are the human-edited sources; the lock is the
# reproducible install (regenerate with: pip freeze > requirements.lock).
COPY requirements.txt requirements-dev.txt requirements.lock ./
RUN pip install -r requirements.lock

COPY alembic.ini pytest.ini ./
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# API by default; the worker service overrides the command.
CMD ["./docker-entrypoint.sh"]
