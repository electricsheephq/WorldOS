#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="WorldOSApp"
DISPLAY_NAME="WorldOS"
# Bundle ID intentionally kept as dev.clawdnd.app: changing it orphans existing
# installs (no upgrade path). Revisit at v2.0 with a migration. See issue #295 (W0-B).
BUNDLE_ID="dev.clawdnd.app"
MIN_SYSTEM_VERSION="13.0"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ART_ROOT="${WORLDOS_ART_REPO_ROOT:-${CLAWDND_ART_REPO_ROOT:-$ROOT_DIR}}"
PREFER_LAUNCH_ROOTS="${WORLDOS_PREFER_LAUNCH_ROOTS:-1}"
ENABLE_SCRIPTED_PROVIDER="${WORLDOS_ENABLE_SCRIPTED_PROVIDER:-0}"
plist_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  printf '%s' "$value"
}
plist_bool() {
  local normalized
  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    1|true|yes|on) printf '<true/>' ;;
    *) printf '<false/>' ;;
  esac
}
ROOT_DIR_PLIST="$(plist_escape "$ROOT_DIR")"
ART_ROOT_PLIST="$(plist_escape "$ART_ROOT")"
PREFER_LAUNCH_ROOTS_PLIST="$(plist_bool "$PREFER_LAUNCH_ROOTS")"
ENABLE_SCRIPTED_PROVIDER_PLIST="$(plist_bool "$ENABLE_SCRIPTED_PROVIDER")"
PACKAGE_DIR="$ROOT_DIR/macos/WorldOSApp"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$DISPLAY_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_BINARY="$APP_MACOS/$APP_NAME"
INFO_PLIST="$APP_CONTENTS/Info.plist"

usage() {
  echo "usage: $0 [run|--verify|--debug|--logs|--telemetry|--release-check]" >&2
  echo "env: WORLDOS_NO_STOP_EXISTING=1 skips the global WorldOSApp kill" >&2
}

stop_existing() {
  if [ "${WORLDOS_NO_STOP_EXISTING:-0}" = "1" ]; then
    return 0
  fi
  pkill -x "$APP_NAME" >/dev/null 2>&1 || true
  pkill -f "$ROOT_DIR/viewer/server.py" >/dev/null 2>&1 || true
}

bundle_pid() {
  local pid cmd
  for pid in $(pgrep -x "$APP_NAME" 2>/dev/null || true); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    case "$cmd" in
      "$APP_BINARY"*) printf '%s\n' "$pid" ;;
    esac
  done
}

pid_in_list() {
  local needle="$1" haystack="${2:-}"
  case " $haystack " in
    *" $needle "*) return 0 ;;
    *) return 1 ;;
  esac
}

wait_for_bundle_pid() {
  local existing_pids="${1:-}" pid
  for _ in $(seq 1 50); do
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      pid_in_list "$pid" "$existing_pids" && continue
      printf '%s\n' "$pid"
      return 0
    done < <(bundle_pid)
    sleep 0.2
  done
  return 1
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
  local icon_src="$ROOT_DIR/assets/icon/WorldOS.icns"
  if [ -f "$icon_src" ]; then
    mkdir -p "$APP_CONTENTS/Resources"
    cp "$icon_src" "$APP_CONTENTS/Resources/WorldOS.icns"
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
  <string>WorldOS</string>
  <key>CFBundleIconName</key>
  <string>WorldOS</string>
  <key>LSMinimumSystemVersion</key>
  <string>$MIN_SYSTEM_VERSION</string>
  <key>WorldOSRepoRoot</key>
  <string>$ROOT_DIR_PLIST</string>
  <key>WorldOSArtRepoRoot</key>
  <string>$ART_ROOT_PLIST</string>
  <key>WorldOSPreferLaunchRoots</key>
  $PREFER_LAUNCH_ROOTS_PLIST
  <key>WorldOSEnableScriptedProvider</key>
  $ENABLE_SCRIPTED_PROVIDER_PLIST
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

  # Sign the local app bundle AD-HOC by default. A dev/QA/CI build must NEVER
  # auto-search the keychain for a "Developer ID Application" identity: a bare
  # `security find-identity` scans EVERY keychain in the search list, which on this
  # box includes an UNRELATED product's signing keychain on a removable volume
  # (evaos-release-signing on /Volumes/LEXAR). That auto-search fired a removable-
  # volume TCC prompt AND, when it matched, a keychain-password prompt on EVERY build
  # — a hard P0: it blocked unattended/CI builds, EVERY live GUI run, and any future
  # user's first launch (the popups can't be answered headlessly, so autonomy breaks).
  # Ad-hoc signing touches NO keychain.
  #
  # The stable-cdhash optimization (a Developer ID signature keeps the same cdhash
  # across rebuilds, so security software like NordVPN Threat Protection doesn't
  # re-scan the binary each launch) is now OPT-IN and EXPLICIT: export
  # WORLDOS_SIGN_IDENTITY="<your specific intended identity>" to sign with it. The
  # default (unset) ad-hoc signs with zero keychain access. We NEVER auto-pick an
  # identity from the keychain search list.
  if command -v codesign >/dev/null 2>&1; then
    if [ -n "${WORLDOS_SIGN_IDENTITY:-}" ]; then
      codesign --force --sign "$WORLDOS_SIGN_IDENTITY" "$APP_BUNDLE" >/dev/null 2>&1 \
        || codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
    else
      codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || true
    fi
  fi
}

open_app() {
  # Set BOTH names so the native app's RepositoryLocator resolves the repo root
  # whether it reads the new WORLDOS_* name or the legacy CLAWDND_* one (#295, W0-E).
  # Keep private art separately overridable: a Lexar worktree can launch app code from
  # $ROOT_DIR while reading gitignored _private art from the canonical checkout.
  WORLDOS_REPO_ROOT="$ROOT_DIR" CLAWDND_REPO_ROOT="$ROOT_DIR" \
  WORLDOS_ART_REPO_ROOT="$ART_ROOT" CLAWDND_ART_REPO_ROOT="$ART_ROOT" \
  WORLDOS_PREFER_LAUNCH_ROOTS="$PREFER_LAUNCH_ROOTS" \
    /usr/bin/open -n "$APP_BUNDLE"
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
    existing_pids="$(bundle_pid | tr '\n' ' ')"
    open_app
    pid="$(wait_for_bundle_pid "$existing_pids")"
    echo "$DISPLAY_NAME launched from $APP_BUNDLE (pid $pid)"
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
