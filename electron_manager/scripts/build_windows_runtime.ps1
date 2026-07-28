param(
  [string]$Python = "python",
  [string]$PlatformToolsUrl = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$output = Join-Path $projectRoot "electron_manager/vendor/windows/x64"
$work = Join-Path $env:TEMP "bs-manager-windows-runtime"
$platformZip = Join-Path $work "platform-tools.zip"
$platformExtract = Join-Path $work "platform-tools"

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work, $output | Out-Null

& $Python -m pip install --upgrade pyinstaller numpy pillow
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
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  platform_tools_url = $PlatformToolsUrl
} | ConvertTo-Json)

Write-Host "Windows runtime ready: $output"
