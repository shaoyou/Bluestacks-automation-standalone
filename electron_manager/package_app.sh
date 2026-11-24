#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  cat <<'EOF'
Usage: ./package_app.sh [mac|win|all] [--dir]

  mac    Build macOS DMG package.
  win    Build Windows NSIS installer package.
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
    return
  fi

  npm run build
  npx electron-builder --mac dmg --arm64 --publish never

  local release_dir="$SCRIPT_DIR/release"
  local version
  version="$(node -p "require('./package.json').version")"
  local wanted="$release_dir/熊熊乐园小助手-${version}-arm64.dmg"
  local dmg="$wanted"
  if [[ ! -f "$dmg" ]]; then
    dmg="$(find "$release_dir" -maxdepth 1 -type f -name "熊熊乐园小助手-${version}-arm64*.dmg" -print -quit)"
  fi
  if [[ -z "$dmg" || ! -f "$dmg" ]]; then
    echo "ERROR: DMG not found in $release_dir"
    exit 1
  fi
  if [[ "$dmg" != "$wanted" ]]; then
    mv "$dmg" "$wanted"
  fi
  # Keep only the requested DMG; remove the zip, blockmaps, latest-mac.yml
  # and the unpacked mac-arm64 directory.
  find "$release_dir" -mindepth 1 -maxdepth 1 ! -name "$(basename "$wanted")" -exec rm -rf {} +
  echo "DMG package: $wanted"
}

build_win() {
  echo "Building Windows package..."
  if [[ "$dir_only" == true ]]; then
    npm run package:win -- --dir
  else
    npm run package:win
  fi
}

case "$target" in
  mac) build_mac ;;
  win) build_win ;;
  all)
    build_mac
    build_win
    ;;
  *)
    usage
    exit 2
    ;;
esac

echo "Done. Packages are in: $SCRIPT_DIR/release"
