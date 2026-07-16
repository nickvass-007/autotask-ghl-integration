@echo off
REM Double-click this to start the Interlinked Sync app locally.
REM It just launches start.ps1 with the right execution policy.

cd /d "%~dp0"

REM Prefer PowerShell 7 (pwsh) if installed, else fall back to Windows PowerShell.
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
)

REM If the launcher exits with an error, keep the window open so it can be read.
if %errorlevel% neq 0 (
  echo.
  echo The launcher exited with an error ^(code %errorlevel%^).
  pause
)
