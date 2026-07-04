#!/bin/sh
# API entrypoint: run migrations, then serve.
# Workers don't run migrations — compose orders them after a healthy API,
# so exactly one process applies DDL and there is no migration race.
set -e

echo "running database migrations..."
alembic upgrade head

echo "starting api server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
