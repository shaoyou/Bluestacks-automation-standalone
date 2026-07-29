#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  cat <<'EOF'
Usage: ./package_app.sh [mac|win|win7|all] [--dir]

  mac    Build macOS DMG and ZIP packages.
  win    Build Windows NSIS installer and ZIP packages.
  win7   Build the Windows 7 SP1 x64 legacy NSIS installer and ZIP packages.
  all    Build both macOS and Windows packages.
  --dir  Build unpacked application directories only (faster verification build).
EOF
}

target="${1:-}"
if [[ -z "$target" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "Build target (mac/win/all): " target
  else
    usage
    exit 2
  fi
fi

if [[ "$target" == "-h" || "$target" == "--help" ]]; then
  usage
  exit 0
fi

dir_only=false
if [[ "${2:-}" == "--dir" ]]; then
  dir_only=true
elif [[ -n "${2:-}" ]]; then
  usage
  exit 2
fi

build_mac() {
  echo "Building macOS package..."
  if [[ "$dir_only" == true ]]; then
    npm run package:mac -- --dir
  else
    npm run package:mac
  fi
}

build_win() {
  echo "Building Windows package..."
  local runtime_dir="$SCRIPT_DIR/vendor/windows/x64"
  local required=(adb_bot.exe record_touch.exe adb.exe AdbWinApi.dll AdbWinUsbApi.dll)
  for file in "${required[@]}"; do
    if [[ ! -f "$runtime_dir/$file" ]]; then
      echo "Missing Windows runtime: $runtime_dir/$file" >&2
      echo "Run scripts/build_windows_runtime.ps1 on Windows first, then package the app." >&2
      exit 1
    fi
  done
  if [[ "$dir_only" == true ]]; then
    npm run package:win -- --dir
  else
    npm run package:win
  fi
}

build_win7() {
  echo "Building Windows 7 SP1 x64 legacy package..."
  local runtime_dir="$SCRIPT_DIR/vendor/windows/win7-x64"
  local required=(adb_bot.exe record_touch.exe adb.exe AdbWinApi.dll AdbWinUsbApi.dll)
  for file in "${required[@]}"; do
    if [[ ! -f "$runtime_dir/$file" ]]; then
      echo "Missing Windows 7 legacy runtime: $runtime_dir/$file" >&2
      echo "Run scripts/build_windows_runtime.ps1 -Windows7Legacy on Windows first, then package the app." >&2
      exit 1
    fi
  done
  if [[ "$dir_only" == true ]]; then
    npm run package:win7 -- --dir
  else
    npm run package:win7
  fi
}

case "$target" in
  mac) build_mac ;;
  win) build_win ;;
  win7) build_win7 ;;
  all)
    build_mac
    build_win
    build_win7
    ;;
  *)
    usage
    exit 2
    ;;
esac

echo "Done. Packages are in: $SCRIPT_DIR/release"
