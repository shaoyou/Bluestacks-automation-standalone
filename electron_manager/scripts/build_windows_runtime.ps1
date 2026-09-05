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

function Invoke-Native {
  param(
    [string]$FilePath,
    [string[]]$Arguments
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

if ($Windows7Legacy) {
  # Electron 22 and this Python toolchain are the final maintained baseline that can run on Windows 7 SP1.
  Invoke-Native $Python @("-m", "pip", "install", "--upgrade", "pyinstaller==5.13.2", "numpy==1.24.4", "pillow==9.5.0")
} else {
  Invoke-Native $Python @("-m", "pip", "install", "--upgrade", "pyinstaller==6.11.1", "numpy==2.0.2", "pillow==10.4.0")
}
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "adb_bot", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "adb_bot.py"))
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "record_touch", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "record_touch.py"))
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "device_discovery_diagnostic", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "device_discovery_diagnostic.py"))
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "hdc_device_diagnostic", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "hdc_device_diagnostic.py"))
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "chest_analyzer", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "chest_analyzer.py"))
Invoke-Native $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "data_store", "--distpath", (Join-Path $work "dist"), "--workpath", (Join-Path $work "build"), "--specpath", $work, (Join-Path $projectRoot "data_store.py"))
Copy-Item (Join-Path $work "dist/adb_bot.exe") $output -Force
Copy-Item (Join-Path $work "dist/record_touch.exe") $output -Force
Copy-Item (Join-Path $work "dist/device_discovery_diagnostic.exe") $output -Force
Copy-Item (Join-Path $work "dist/hdc_device_diagnostic.exe") $output -Force
Copy-Item (Join-Path $work "dist/chest_analyzer.exe") $output -Force
Copy-Item (Join-Path $work "dist/data_store.exe") $output -Force

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
  $hdcSourceDir = Split-Path -Parent $sourcePath
  $hdcSharedDll = Join-Path $hdcSourceDir "libusb_shared.dll"
  if (Test-Path $hdcSharedDll) {
    Copy-Item $hdcSharedDll (Join-Path $harmonyOutput "libusb_shared.dll") -Force
  } else {
    Write-Warning "libusb_shared.dll was not found next to hdc.exe; HDC may fail to start on Windows."
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
exit 0
