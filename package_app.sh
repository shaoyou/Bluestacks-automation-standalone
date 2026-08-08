#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWIFT_DIR="$ROOT_DIR/swiftui_manager"
DIST_DIR="$ROOT_DIR/dist"
PACK_HOME="$ROOT_DIR/.pack_home"
APP_NAME="BSManagerApp"
APP_VERSION="1.1.0"
APP_BUILD="2"
APP_BUNDLE="$DIST_DIR/${APP_NAME}.app"
APP_EXEC="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
BUNDLE_RUNTIME_DIR="$APP_BUNDLE/Contents/Resources/Runtime"
RELEASE_BIN="$SWIFT_DIR/.build/release/$APP_NAME"
STANDALONE_BIN="$DIST_DIR/$APP_NAME"
STANDALONE_RUNTIME_DIR="$DIST_DIR/runtime"

if ! command -v swift >/dev/null 2>&1; then
  echo "ERROR: swift not found. Please install Xcode Command Line Tools."
  exit 1
fi

copy_runtime_tree() {
  local dest_dir="$1"
  rm -rf "$dest_dir"
  mkdir -p "$dest_dir"

  cp "$ROOT_DIR/adb_bot.py" "$dest_dir/adb_bot.py"
  cp "$ROOT_DIR/record_touch.py" "$dest_dir/record_touch.py"
  cp "$ROOT_DIR/chest_analyzer.py" "$dest_dir/chest_analyzer.py"
  cp -R "$ROOT_DIR/plans" "$dest_dir/plans"
  cp -R "$ROOT_DIR/image_templates" "$dest_dir/image_templates"
  mkdir -p "$dest_dir/diagnostics"
  mkdir -p "$dest_dir/recording_profiles"
}

echo "[1/5] Building release binary..."
cd "$SWIFT_DIR"
mkdir -p "$PACK_HOME/.cache" "$SWIFT_DIR/.build/clang-module-cache"
HOME="$PACK_HOME" \
XDG_CACHE_HOME="$PACK_HOME/.cache" \
CLANG_MODULE_CACHE_PATH="$SWIFT_DIR/.build/clang-module-cache" \
swift build --configuration release --product "$APP_NAME"

if [[ ! -f "$RELEASE_BIN" ]]; then
  echo "ERROR: release binary not found: $RELEASE_BIN"
  exit 1
fi

echo "[2/5] Preparing dist folder..."
mkdir -p "$DIST_DIR"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

echo "[3/5] Creating .app bundle..."
cp "$RELEASE_BIN" "$APP_EXEC"
chmod +x "$APP_EXEC"

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>BSManagerApp</string>
  <key>CFBundleDisplayName</key>
  <string>BSManagerApp</string>
  <key>CFBundleIdentifier</key>
  <string>local.playground.bsmanager</string>
  <key>CFBundleVersion</key>
  <string>${APP_BUILD}</string>
  <key>CFBundleShortVersionString</key>
  <string>${APP_VERSION}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>BSManagerApp</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

echo "[4/5] Bundling runtime resources..."
copy_runtime_tree "$BUNDLE_RUNTIME_DIR"
copy_runtime_tree "$STANDALONE_RUNTIME_DIR"

echo "[5/5] Exporting standalone executable..."
cp "$RELEASE_BIN" "$STANDALONE_BIN"
chmod +x "$STANDALONE_BIN"

echo ""
echo "Done."
echo "App bundle: $APP_BUNDLE"
echo "Executable: $STANDALONE_BIN"
echo "Run app: open \"$APP_BUNDLE\""
