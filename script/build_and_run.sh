#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="ClawDnDApp"
DISPLAY_NAME="ClawDnD"
BUNDLE_ID="dev.clawdnd.app"
MIN_SYSTEM_VERSION="13.0"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="$ROOT_DIR/macos/ClawDnDApp"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$DISPLAY_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_BINARY="$APP_MACOS/$APP_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"

usage() {
  echo "usage: $0 [run|--verify|--debug|--logs|--telemetry|--release-check]" >&2
}

stop_existing() {
  pkill -x "$APP_NAME" >/dev/null 2>&1 || true
}

build_bundle() {
  swift build --package-path "$PACKAGE_DIR"
  local bin_path
  bin_path="$(swift build --package-path "$PACKAGE_DIR" --show-bin-path)/$APP_NAME"

  rm -rf "$APP_BUNDLE"
  mkdir -p "$APP_MACOS"
  cp "$bin_path" "$APP_BINARY"
  chmod +x "$APP_BINARY"

  cat >"$INFO_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleName</key>
  <string>$DISPLAY_NAME</string>
  <key>CFBundleDisplayName</key>
  <string>$DISPLAY_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_SYSTEM_VERSION</string>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST

  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
  fi
}

open_app() {
  CLAWDND_REPO_ROOT="$ROOT_DIR" /usr/bin/open -n "$APP_BUNDLE"
}

release_check() {
  build_bundle
  echo "Bundle: $APP_BUNDLE"
  echo
  echo "Codesign identities:"
  security find-identity -p codesigning -v || true
  echo
  echo "codesign --verify --deep --strict:"
  codesign --verify --deep --strict "$APP_BUNDLE"
  echo
  echo "spctl -a -vv:"
  spctl -a -vv "$APP_BUNDLE" || true
}

stop_existing

case "$MODE" in
  run)
    build_bundle
    open_app
    ;;
  --verify|verify)
    build_bundle
    open_app
    sleep 2
    pgrep -x "$APP_NAME" >/dev/null
    echo "$DISPLAY_NAME launched from $APP_BUNDLE"
    ;;
  --debug|debug)
    build_bundle
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    build_bundle
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    build_bundle
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --release-check|release-check)
    release_check
    ;;
  *)
    usage
    exit 2
    ;;
esac
