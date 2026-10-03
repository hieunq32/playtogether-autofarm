$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
$SpecDir = Join-Path $BuildDir "spec"

New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null

py -3 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --workpath (Join-Path $BuildDir "work") `
  --distpath $DistDir `
  --specpath $SpecDir `
  --name "PlayTogetherAutoFarm" `
  --icon (Join-Path $ProjectRoot "assets\autofarm.ico") `
  --add-data "$(Join-Path $ProjectRoot 'config.json');." `
  --add-data "$(Join-Path $ProjectRoot 'templates');templates" `
  --hidden-import "win32timezone" `
  (Join-Path $ProjectRoot "main.py")

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE. Existing release files were left untouched."
}

$ReleaseDir = Join-Path $ProjectRoot "release\PlayTogetherAutoFarm_OneFile"
$ZipPath = Join-Path $ProjectRoot "release\PlayTogetherAutoFarm_OneFile_App_And_SetupImage.zip"
if (Test-Path -LiteralPath $ReleaseDir) {
  Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
if (Test-Path -LiteralPath $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Copy-Item -Force (Join-Path $ProjectRoot "dist\PlayTogetherAutoFarm.exe") (Join-Path $ReleaseDir "PlayTogetherAutoFarm.exe")
Copy-Item -Force (Join-Path $ProjectRoot "assets\bluestacks_alignment_wallpaper_1920x1080.png") $ReleaseDir
Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath $ZipPath -Force

# Keep the source tree small after the release files have been copied.
$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
if (Test-Path -LiteralPath $BuildDir) {
  Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
if (Test-Path -LiteralPath $DistDir) {
  Remove-Item -LiteralPath $DistDir -Recurse -Force
}

Write-Host ""
Write-Host "Onefile release complete:"
Write-Host $ReleaseDir
Write-Host $ZipPath
