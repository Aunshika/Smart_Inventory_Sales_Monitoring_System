#!/bin/sh
set -eu
if [ "${1:-}" = "prod" ]; then
  if [ ! -f .env.production ]; then
    echo "Missing .env.production. Copy .env.production.example and fill real values." >&2
    exit 1
  fi
  docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
else
  docker compose up -d --build
fi
