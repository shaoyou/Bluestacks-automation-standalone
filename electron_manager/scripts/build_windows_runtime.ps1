param(
  [string]$Python = "python",
  [string]$PlatformToolsUrl = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
  [string]$OutputDirectory = "",
  [switch]$Windows7Legacy
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$output = if ($OutputDirectory) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  Join-Path $projectRoot "electron_manager/vendor/windows/x64"
}
$work = Join-Path $env:TEMP "bs-manager-windows-runtime"
$platformZip = Join-Path $work "platform-tools.zip"
$platformExtract = Join-Path $work "platform-tools"

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work, $output | Out-Null

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

Set-Content -Path (Join-Path $output "runtime-manifest.json") -Encoding UTF8 -Value (@{
  version = 1
  compatibility = if ($Windows7Legacy) { "windows-7-sp1-x64" } else { "windows-10-or-newer-x64" }
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  platform_tools_url = $PlatformToolsUrl
} | ConvertTo-Json)

Write-Host "Windows runtime ready: $output"
