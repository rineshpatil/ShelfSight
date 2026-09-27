# Start the ShelfSight bridge that n8n calls (http://host.docker.internal:8765).
# Reads secrets from the git-ignored .env at the repo root, and makes sure SearxNG is up first.
#   powershell -ExecutionPolicy Bypass -File scripts\start_bridge.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .env)) { throw ".env not found at $(Get-Location). See README for the variables it needs." }
docker compose -f infra/searxng/docker-compose.yml up -d | Out-Null
uv run --env-file .env shelfsight serve
