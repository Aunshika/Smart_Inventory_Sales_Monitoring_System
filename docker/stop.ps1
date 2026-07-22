param(
    [switch]$Prod
)

$ErrorActionPreference = "Stop"

if ($Prod) {
    docker compose -f docker-compose.prod.yml --env-file .env.production down
} else {
    docker compose down
}
