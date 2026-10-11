#!/bin/sh
# Apply database migrations, then exec the container command (uvicorn by default).
set -eu

echo "Running database migrations..."
alembic upgrade head

exec "$@"
