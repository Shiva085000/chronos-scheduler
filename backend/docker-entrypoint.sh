#!/bin/sh
# One image, two roles, selected by SERVICE_ROLE (api | worker) so a PaaS
# that can't override the command can still run the same build either way.
# Compose keeps overriding the worker command explicitly; both paths work.
#
# Only the API applies migrations — exactly one process runs DDL. Workers
# that boot before the first migration crash and are restarted by the
# platform until the schema exists (compose orders them after a healthy
# API instead).
set -e

if [ "${SERVICE_ROLE:-api}" = "worker" ]; then
  echo "starting worker..."
  exec python -m app.worker.main
fi

echo "running database migrations..."
alembic upgrade head

echo "starting api server..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
