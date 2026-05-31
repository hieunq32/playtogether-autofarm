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
$ZipPath = Join-Path $ProjectRoot "release\PlayTogetherAutoFarm_OneFile_App_And_SetupImage.zip"
if (Test-Path -LiteralPath $ReleaseDir) {
  Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
if (Test-Path -LiteralPath $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Get-Process | Where-Object { $_.ProcessName -like "PlayTogetherAutoFarm*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Copy-Item -Force (Join-Path $ProjectRoot "dist\PlayTogetherAutoFarm.exe") (Join-Path $ReleaseDir "PlayTogetherAutoFarm.exe")
Copy-Item -Force (Join-Path $ProjectRoot "assets\bluestacks_alignment_wallpaper_1920x1080.png") $ReleaseDir
Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath $ZipPath -Force

# Keep the source tree small after the release files have been copied.
$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
$SpecPath = Join-Path $ProjectRoot "PlayTogetherAutoFarm.spec"
if (Test-Path -LiteralPath $BuildDir) {
  Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
if (Test-Path -LiteralPath $DistDir) {
  Remove-Item -LiteralPath $DistDir -Recurse -Force
}
if (Test-Path -LiteralPath $SpecPath) {
  Remove-Item -LiteralPath $SpecPath -Force
}

Write-Host ""
Write-Host "Onefile release complete:"
Write-Host $ReleaseDir
Write-Host $ZipPath
