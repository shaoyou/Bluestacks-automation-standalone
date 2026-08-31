param(
  [string]$Python = "python",
  [string]$PlatformToolsUrl = "",
  [string]$OutputDirectory = "",
  [string]$HdcPath = "",
  [switch]$Windows7Legacy
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$defaultPlatformToolsUrl = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
$windows7PlatformToolsUrl = "https://dl.google.com/android/repository/platform-tools_r34.0.4-windows.zip"
if (-not $PlatformToolsUrl) {
  $PlatformToolsUrl = if ($Windows7Legacy) { $windows7PlatformToolsUrl } else { $defaultPlatformToolsUrl }
}
$output = if ($OutputDirectory) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  Join-Path $projectRoot "electron_manager/vendor/windows/x64"
}
$harmonyOutput = Join-Path $projectRoot "electron_manager/vendor/harmony/windows/x64"
$harmonyTarget = Join-Path $harmonyOutput "hdc.exe"
$work = Join-Path $env:TEMP "bs-manager-windows-runtime"
$platformZip = Join-Path $work "platform-tools.zip"
$platformExtract = Join-Path $work "platform-tools"

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work, $output | Out-Null
New-Item -ItemType Directory -Force $harmonyOutput | Out-Null

if ($Windows7Legacy) {
  # Electron 22 and this Python toolchain are the final maintained baseline that can run on Windows 7 SP1.
  & $Python -m pip install --upgrade "pyinstaller==5.13.2" "numpy==1.24.4" "pillow==9.5.0"
} else {
  & $Python -m pip install --upgrade pyinstaller numpy pillow
}
& $Python -m PyInstaller --noconfirm --clean --onefile --name adb_bot --distpath (Join-Path $work "dist") --workpath (Join-Path $work "build") --specpath $work (Join-Path $projectRoot "adb_bot.py")
& $Python -m PyInstaller --noconfirm --clean --onefile --name record_touch --distpath (Join-Path $work "dist") --workpath (Join-Path $work "build") --specpath $work (Join-Path $projectRoot "record_touch.py")
Copy-Item (Join-Path $work "dist/adb_bot.exe") $output -Force
Copy-Item (Join-Path $work "dist/record_touch.exe") $output -Force

Invoke-WebRequest -Uri $PlatformToolsUrl -OutFile $platformZip
Expand-Archive -Path $platformZip -DestinationPath $platformExtract -Force
$tools = Join-Path $platformExtract "platform-tools"
foreach ($file in @("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "fastboot.exe")) {
  Copy-Item (Join-Path $tools $file) $output -Force
}

function Resolve-HdcSource {
  param([string]$ExplicitPath)
  $bundledPath = Join-Path $projectRoot "electron_manager/vendor/harmony/windows/x64/hdc.exe"
  if (Test-Path $bundledPath) {
    return (Resolve-Path $bundledPath).Path
  }
  if ($ExplicitPath) {
    if (Test-Path $ExplicitPath) { return (Resolve-Path $ExplicitPath).Path }
    throw "HDC source not found: $ExplicitPath"
  }
  if ($env:HDC_PATH) {
    if (Test-Path $env:HDC_PATH) { return (Resolve-Path $env:HDC_PATH).Path }
    throw "HDC_PATH does not exist: $env:HDC_PATH"
  }
  $command = Get-Command hdc.exe -ErrorAction SilentlyContinue
  if ($command -and $command.Source) { return $command.Source }
  return $null
}

$hdcSource = Resolve-HdcSource -ExplicitPath $HdcPath
if ($hdcSource) {
  $sourcePath = [System.IO.Path]::GetFullPath($hdcSource)
  $targetPath = [System.IO.Path]::GetFullPath($harmonyTarget)
  if ($sourcePath -ne $targetPath) {
    Copy-Item $sourcePath $harmonyTarget -Force
  }
} else {
  Write-Warning "HDC was not found on this build machine, so the Windows package will not bundle it."
}

Set-Content -Path (Join-Path $output "runtime-manifest.json") -Encoding UTF8 -Value (@{
  version = 1
  compatibility = if ($Windows7Legacy) { "windows-7-sp1-x64" } else { "windows-10-or-newer-x64" }
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  platform_tools_url = $PlatformToolsUrl
  hdc_source = $hdcSource
} | ConvertTo-Json)

Write-Host "Windows runtime ready: $output"
