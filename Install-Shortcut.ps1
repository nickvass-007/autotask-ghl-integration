<#
  Puts an "Interlinked Sync" shortcut on your Desktop that launches the app.
  Run this once:   .\Install-Shortcut.ps1
  Then just double-click the Desktop icon whenever you want to start the app
  (e.g. after a PC restart).
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$target = Join-Path $PSScriptRoot "Start-Interlinked.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "Interlinked Sync.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($linkPath)
$sc.TargetPath = $target
$sc.WorkingDirectory = $PSScriptRoot
$sc.Description = "Start the Interlinked Autotask ⇄ GoHighLevel sync locally"
$sc.WindowStyle = 1
# Use the app's icon if present, else a generic terminal icon.
$icon = Join-Path $PSScriptRoot "assets\interlinked.ico"
if (Test-Path $icon) { $sc.IconLocation = $icon } else { $sc.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0" }
$sc.Save()

Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $linkPath" -ForegroundColor Cyan
Write-Host "Double-click 'Interlinked Sync' on your Desktop to start the app." -ForegroundColor White
