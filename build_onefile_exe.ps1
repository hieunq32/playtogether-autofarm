$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

py -3 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name "PlayTogetherAutoFarm" `
  --icon "assets\autofarm.ico" `
  --add-data "config.json;." `
  --add-data "templates;templates" `
  --hidden-import "win32timezone" `
  "main.py"

$ReleaseDir = Join-Path $ProjectRoot "release\PlayTogetherAutoFarm_OneFile"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Get-Process | Where-Object { $_.ProcessName -like "PlayTogetherAutoFarm*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Copy-Item -Force (Join-Path $ProjectRoot "dist\PlayTogetherAutoFarm.exe") (Join-Path $ReleaseDir "PlayTogetherAutoFarm.exe")

Write-Host ""
Write-Host "Onefile release complete:"
Write-Host $ReleaseDir
