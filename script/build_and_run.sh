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

  # App icon (the brass d20 + claw-scratch mark). Copy the prebuilt .icns into
  # Contents/Resources and reference it via CFBundleIconFile so Finder / Dock /
  # the title bar show a real icon instead of the generic executable placeholder.
  local icon_src="$ROOT_DIR/assets/icon/ClawDnD.icns"
  if [ -f "$icon_src" ]; then
    mkdir -p "$APP_CONTENTS/Resources"
    cp "$icon_src" "$APP_CONTENTS/Resources/ClawDnD.icns"
  fi

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
  <key>CFBundleIconFile</key>
  <string>ClawDnD</string>
  <key>CFBundleIconName</key>
  <string>ClawDnD</string>
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

  # Sign with a Developer ID identity if available so the signature is STABLE
  # across rebuilds. Ad-hoc signatures (`--sign -`) produce a NEW cdhash on each
  # build, which causes security software (e.g. NordVPN Threat Protection's file
  # scanner) to re-evaluate the binary every time — that re-evaluation can hang
  # the freshly-launched app's first directory enumerations in the kernel
  # (open$NOCANCEL) for tens of seconds. A Developer ID signature is stable AND
  # generally pre-trusted, so the scan is cached/skipped after the first launch.
  if command -v codesign >/dev/null 2>&1; then
    SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
    if [ -n "$SIGN_IDENTITY" ]; then
      codesign --force --sign "$SIGN_IDENTITY" "$APP_BUNDLE" >/dev/null 2>&1 \
        || codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
    else
      codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
    fi
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
