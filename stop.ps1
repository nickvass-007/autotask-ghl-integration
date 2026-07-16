<#
  Stop the local Interlinked Sync stack.

    .\stop.ps1            stop the API (frees port 8000)
    .\stop.ps1 -Db        also stop the Postgres container (data is kept)

  Note: normally you just press Ctrl+C in the launcher window. This is for when
  the API is running detached, or a stray process is holding port 8000.
#>
[CmdletBinding()]
param([switch]$Db)

$ErrorActionPreference = "SilentlyContinue"
Set-Location -Path $PSScriptRoot

Write-Host "Stopping the API on port 8000..." -ForegroundColor Cyan
$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
  $procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $procIds) {
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) { Write-Host "  Stopping $($p.ProcessName) (PID $procId)" -ForegroundColor Yellow; Stop-Process -Id $procId -Force }
  }
  Write-Host "  API stopped." -ForegroundColor Green
} else {
  Write-Host "  Nothing is listening on port 8000." -ForegroundColor DarkGray
}

if ($Db) {
  Write-Host "Stopping Postgres (data is preserved in the volume)..." -ForegroundColor Cyan
  docker compose down
  Write-Host "  Postgres stopped." -ForegroundColor Green
}
