#!/bin/sh
set -eu
if [ "${1:-}" = "prod" ]; then
  docker compose -f docker-compose.prod.yml --env-file .env.production down
else
  docker compose down
fi
