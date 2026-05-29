#!/usr/bin/env bash
# Build the nmdsuite image on a remote Docker host (via SSH) and bring up
# a local-test instance. Run from the repo root on the dev box.
#
# Required env:
#   DOCKER_HOST            ssh://user@dockerhost (or a configured docker context)
#   NMD_LOCAL_DATA_DIR     Absolute path on the remote where SQLite + uploads live
#                          (e.g. /home/youruser/docker/nmdsuite-local/data)
#
# Optional env:
#   NMD_BASE_URL           Public URL used in outgoing emails / absolute links.
#                          Default: http://localhost:5005
#   SKIP_MIGRATE=1         Skip the migrate step (e.g. for a pure rebuild check).
#
# Example:
#   export DOCKER_HOST=ssh://kohler@dockerdev.local
#   export NMD_LOCAL_DATA_DIR=/home/kohler/docker/nmdsuite-local/data
#   ./deploy-local.sh

set -euo pipefail

: "${DOCKER_HOST:?DOCKER_HOST must be set, e.g. ssh://user@dockerhost}"
: "${NMD_LOCAL_DATA_DIR:?NMD_LOCAL_DATA_DIR must be set to the data dir path on the remote}"

COMPOSE="docker compose -f docker-compose.local.yml"

echo ">>> Target daemon: $DOCKER_HOST"
echo ">>> Data dir on remote: $NMD_LOCAL_DATA_DIR"

echo ">>> Building image..."
$COMPOSE build

echo ">>> Starting container..."
$COMPOSE up -d

if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
    echo ">>> Running migrations..."
    $COMPOSE exec -T nmdsuite python manage.py migrate --noinput
fi

echo ">>> Done. App is listening on port 5005 of the remote docker host."
