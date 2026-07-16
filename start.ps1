<#
  Interlinked Sync — one-command local launcher.

  Double-click Start-Interlinked.bat, or run:  .\start.ps1
  It brings the whole stack up from a cold PC:
    1. starts Docker Desktop if it isn't running
    2. starts the local Postgres container
    3. repairs the Python venv if OneDrive has corrupted it
    4. applies database migrations
    5. opens the portal in your browser
    6. runs the API (this window stays open showing logs)

  Switches:
    -Dev       enable auto-reload (restarts the API when you edit code)
    -NoBrowser don't open the browser automatically
    -Rebuild   force a clean reinstall of dependencies before starting
#>
[CmdletBinding()]
param(
  [switch]$Dev,
  [switch]$NoBrowser,
  [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($msg, $color = "Cyan") { Write-Host "  $msg" -ForegroundColor $color }
function Step($n, $msg) { Write-Host "`n[$n/6] $msg" -ForegroundColor White }

Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host "  INTERLINKED SYNC — local launcher" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor DarkCyan

# ── Preflight: required tooling + .env ───────────────────────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "uv is not installed or not on PATH. Install from https://docs.astral.sh/uv/ and re-run." "Red"
  Read-Host "Press Enter to close"; exit 1
}
if (-not (Test-Path ".env")) {
  Say ".env not found. Copy .env.example to .env and fill in your credentials first." "Red"
  Read-Host "Press Enter to close"; exit 1
}

# ── 1. Docker Desktop ────────────────────────────────────────────────────────
Step 1 "Checking Docker..."
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Say "Docker isn't running — starting Docker Desktop (this can take a minute)..." "Yellow"
  $dockerExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
  if (Test-Path $dockerExe) { Start-Process $dockerExe } else { Say "Docker Desktop not found at the default path; start it manually." "Yellow" }
  $deadline = (Get-Date).AddMinutes(3)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 4
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host "." -NoNewline -ForegroundColor DarkGray
  }
  Write-Host ""
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Say "Docker still isn't ready. Start Docker Desktop, then re-run." "Red"; Read-Host "Press Enter to close"; exit 1 }
}
Say "Docker is running." "Green"

# ── 2. Postgres container ────────────────────────────────────────────────────
Step 2 "Starting local Postgres..."
docker compose up -d db *> $null
$deadline = (Get-Date).AddMinutes(2)
$dbReady = $false
while ((Get-Date) -lt $deadline) {
  $status = (docker inspect -f '{{.State.Health.Status}}' autotask_ghl_db 2>$null)
  if ($status -eq "healthy") { $dbReady = $true; break }
  Start-Sleep -Seconds 3
}
if (-not $dbReady) { Say "Postgres didn't report healthy in time. Check 'docker compose ps'." "Red"; Read-Host "Press Enter to close"; exit 1 }
Say "Postgres is healthy on localhost:5432." "Green"

# ── 3. Python environment (self-heals OneDrive venv corruption) ──────────────
Step 3 "Verifying Python environment..."
if ($Rebuild) {
  Say "Rebuilding the virtual environment (-Rebuild)..." "Yellow"
  if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
  uv sync --extra dev
} else {
  uv run --quiet python -c "import integration" *> $null
  if ($LASTEXITCODE -ne 0) {
    Say "Environment looks broken (OneDrive can corrupt .venv) — reinstalling..." "Yellow"
    if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
    uv sync --extra dev
  }
}
Say "Python environment ready." "Green"

# ── 4. Database migrations ───────────────────────────────────────────────────
Step 4 "Applying database migrations..."
uv run --quiet alembic upgrade head
if ($LASTEXITCODE -ne 0) { Say "Migrations failed — see the error above." "Red"; Read-Host "Press Enter to close"; exit 1 }
Say "Database schema is up to date." "Green"

# ── 5. Open the portal once the API answers ──────────────────────────────────
if (-not $NoBrowser) {
  Step 5 "Opening the portal when the API is ready..."
  Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 40; $i++) {
      try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { Start-Process "http://localhost:8000/portal"; break }
      } catch { Start-Sleep -Milliseconds 750 }
    }
  } | Out-Null
} else {
  Step 5 "Skipping browser (-NoBrowser)."
}

# ── 6. Run the API in the foreground ─────────────────────────────────────────
Step 6 "Starting the API — leave this window open. Press Ctrl+C to stop."
Write-Host "  Portal:  http://localhost:8000/portal" -ForegroundColor Cyan
Write-Host "  Health:  http://localhost:8000/health" -ForegroundColor DarkGray
Write-Host ""
$uvicornArgs = @("run", "uvicorn", "integration.api.main:app", "--host", "127.0.0.1", "--port", "8000")
if ($Dev) { $uvicornArgs += "--reload" }
& uv @uvicornArgs
