param(
    [switch]$Prod
)

$ErrorActionPreference = "Stop"

if ($Prod) {
    if (-not (Test-Path ".env.production")) {
        throw "Missing .env.production. Copy .env.production.example to .env.production and fill real values."
    }
    docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
} else {
    docker compose up -d --build
}
