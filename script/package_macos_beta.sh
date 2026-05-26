#!/usr/bin/env bash
set -euo pipefail

# Build and package the ClawDnD macOS beta channel.
#
# This script intentionally keeps every generated artifact under
# /Volumes/LEXAR/Codex/clawdnd-beta-channel and reads release signing material
# from /Volumes/LEXAR/Codex/clawdnd-release-secrets. It never prints key
# contents and does not mutate source files.

DEFAULT_VERSION="0.3.0"
DEFAULT_BUILD="2026052601"
DEFAULT_CHANNEL="local-beta"
DEFAULT_PRERELEASE="beta.1"

APP_NAME="ClawDnD"
EXECUTABLE_NAME="ClawDnDApp"
BUNDLE_ID="dev.clawdnd.app"
SIGNING_IDENTITY="Developer ID Application: Andrew Ryan (TC6MS3T6NN)"

OUTPUT_ROOT="${BETA_OUTPUT_DIR:-/Volumes/LEXAR/Codex/clawdnd-beta-channel}"
SECRETS_DIR="/Volumes/LEXAR/Codex/clawdnd-release-secrets"
PRIVATE_KEY_FILE="${SECRETS_DIR}/sparkle-ed25519-private-key.base64"

usage() {
  cat <<'USAGE'
Usage: script/package_macos_beta.sh [--version VERSION] [--build BUILD] [--channel CHANNEL] [--prerelease SUFFIX]

Defaults:
  --version  0.3.0
  --build    2026052601
  --channel  local-beta
  --prerelease beta.1

Environment:
  BETA_OUTPUT_DIR               Output directory for the local beta channel.
                                Defaults to /Volumes/LEXAR/Codex/clawdnd-beta-channel
  CLAWDND_FEED_URL              Sparkle feed URL written into Info.plist.
                                Defaults to http://127.0.0.1:8765/appcast.xml
  CLAWDND_DOWNLOAD_URL_PREFIX   Optional URL prefix passed to Sparkle generate_appcast.
                                Defaults to http://127.0.0.1:8765/
USAGE
}

log() {
  printf '[package_macos_beta] %s\n' "$*"
}

fail() {
  printf '[package_macos_beta] error: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  local label="$2"
  [[ -f "$path" ]] || fail "missing ${label}: ${path}"
}

require_dir() {
  local path="$1"
  local label="$2"
  [[ -d "$path" ]] || fail "missing ${label}: ${path}"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  printf '%s' "$value"
}

copy_sparkle_framework() {
  local package_dir="$1"
  local frameworks_dir="$2"
  local sparkle_framework=""

  sparkle_framework="$(find "${package_dir}/.build/artifacts" -path '*/Sparkle.framework' -type d -print -quit 2>/dev/null || true)"
  [[ -n "$sparkle_framework" ]] || fail "Sparkle.framework was not found under SwiftPM artifacts; run SwiftPM resolution/build first"

  mkdir -p "$frameworks_dir"
  ditto "$sparkle_framework" "${frameworks_dir}/Sparkle.framework"
}

add_app_framework_rpath() {
  local binary_path="$1"
  if otool -l "$binary_path" | grep -q '@executable_path/../Frameworks'; then
    return
  fi
  install_name_tool -add_rpath '@executable_path/../Frameworks' "$binary_path"
}

find_swiftpm_executable() {
  local package_dir="$1"
  local executable_path=""

  if [[ -x "${package_dir}/.build/release/${EXECUTABLE_NAME}" ]]; then
    executable_path="${package_dir}/.build/release/${EXECUTABLE_NAME}"
  fi

  if [[ -z "$executable_path" ]]; then
    executable_path="$(find "${package_dir}/.build" -type f -path "*/release/${EXECUTABLE_NAME}" -perm -111 -print 2>/dev/null | head -n 1 || true)"
  fi

  [[ -n "$executable_path" ]] || fail "release executable was not found in ${package_dir}/.build"
  printf '%s' "$executable_path"
}

find_generate_appcast() {
  local package_dir="$1"
  local tool_path=""

  tool_path="$(find "${package_dir}/.build/artifacts" -type f -path '*/bin/generate_appcast' -perm -111 -print -quit 2>/dev/null || true)"
  if [[ -z "$tool_path" ]]; then
    tool_path="$(find "${package_dir}/.build/checkouts" -type f -name generate_appcast -perm -111 -print -quit 2>/dev/null || true)"
  fi

  [[ -n "$tool_path" ]] || fail "Sparkle generate_appcast was not found in SwiftPM build artifacts"
  printf '%s' "$tool_path"
}

write_info_plist() {
  local plist_path="$1"
  local version="$2"
  local build="$3"
  local channel="$4"
  local public_key="$5"
  local feed_url="$6"
  local beta_channel_root="$7"

  cat >"$plist_path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>${EXECUTABLE_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$(xml_escape "$version")</string>
  <key>CFBundleVersion</key>
  <string>$(xml_escape "$build")</string>
  <key>ClawDnDUpdateChannel</key>
  <string>$(xml_escape "$channel")</string>
  <key>ClawDnDLocalBetaChannelPath</key>
  <string>$(xml_escape "$beta_channel_root")</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key>
  <true/>
  <key>SUEnableInstallerLauncherService</key>
  <true/>
  <key>SUFeedURL</key>
  <string>$(xml_escape "$feed_url")</string>
  <key>SUPublicEDKey</key>
  <string>$(xml_escape "$public_key")</string>
</dict>
</plist>
PLIST
}

sign_bundle_contents() {
  local app_path="$1"

  log "Signing nested Sparkle helpers, framework, executable, and app bundle"
  while IFS= read -r nested; do
    codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" "$nested"
  done < <(find "${app_path}/Contents/Frameworks/Sparkle.framework" \
    \( -name '*.xpc' -o -name '*.app' -o -name '*.dylib' -o -name Autoupdate \) -print 2>/dev/null | sort)

  codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" \
    "${app_path}/Contents/Frameworks/Sparkle.framework"
  codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" \
    "${app_path}/Contents/MacOS/${EXECUTABLE_NAME}"
  codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" "$app_path"
}

write_release_notes() {
  local notes_path="$1"
  local version="$2"
  local build="$3"
  local channel="$4"

  cat >"$notes_path" <<NOTES
# ClawDnD ${version} (${build})

Channel: ${channel}

- Beta macOS package assembled from the SwiftPM release build.
- Includes the bundled OpenWorlds viewer resources.
- Signed with Developer ID Application and Sparkle EdDSA appcast metadata.
NOTES
}

write_checksums() {
  local checksums_path="$1"
  shift

  : >"$checksums_path"
  for artifact in "$@"; do
    shasum -a 256 "$artifact" >>"$checksums_path"
  done
}

write_validation_report() {
  local report_path="$1"
  local app_path="$2"
  local zip_path="$3"
  local dmg_path="$4"
  local appcast_path="$5"
  local version="$6"
  local build="$7"
  local channel="$8"

  {
    printf '# ClawDnD Beta Packaging Validation\n\n'
    printf -- '- Version: `%s`\n' "$version"
    printf -- '- Build: `%s`\n' "$build"
    printf -- '- Channel: `%s`\n' "$channel"
    printf -- '- App bundle: `%s`\n' "$app_path"
    printf -- '- ZIP: `%s`\n' "$zip_path"
    printf -- '- DMG: `%s`\n' "$dmg_path"
    printf -- '- Appcast: `%s`\n\n' "$appcast_path"
    printf '## Checks\n\n'
    printf '```text\n'
    codesign --verify --deep --strict --verbose=2 "$app_path" 2>&1
    spctl --assess --type execute --verbose=2 "$app_path" 2>&1 || true
    printf '```\n'
  } >"$report_path"
}

main() {
  local version="$DEFAULT_VERSION"
  local build="$DEFAULT_BUILD"
  local channel="$DEFAULT_CHANNEL"
  local prerelease="${PRERELEASE:-$DEFAULT_PRERELEASE}"

  while (($#)); do
    case "$1" in
      --version)
        [[ $# -ge 2 ]] || fail "--version requires a value"
        version="$2"
        shift 2
        ;;
      --build)
        [[ $# -ge 2 ]] || fail "--build requires a value"
        build="$2"
        shift 2
        ;;
      --channel)
        [[ $# -ge 2 ]] || fail "--channel requires a value"
        channel="$2"
        shift 2
        ;;
      --prerelease)
        [[ $# -ge 2 ]] || fail "--prerelease requires a value"
        prerelease="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
  done

  require_command swift
  require_command ditto
  require_command codesign
  require_command hdiutil
  require_command install_name_tool
  require_command otool
  require_command shasum
  require_command spctl
  prerelease="$(printf '%s' "$prerelease" | tr -d '[:space:]')"
  [[ -n "$prerelease" ]] || fail "pre-release suffix must not be empty"

  local script_dir repo_root package_dir public_key_file feed_url download_url_prefix
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "${script_dir}/.." && pwd)"
  package_dir="${repo_root}/macos/ClawDnDApp"
  public_key_file="${package_dir}/SparklePublicKey.txt"
  download_url_prefix="${CLAWDND_DOWNLOAD_URL_PREFIX:-http://127.0.0.1:8765/}"
  feed_url="${CLAWDND_FEED_URL:-${download_url_prefix%/}/appcast.xml}"

  [[ "$repo_root" == /Volumes/LEXAR/repos/* ]] || fail "repo must be under /Volumes/LEXAR/repos; found ${repo_root}"
  [[ "$OUTPUT_ROOT" == /Volumes/LEXAR/Codex/* ]] || fail "output root must be under /Volumes/LEXAR/Codex"

  require_dir "$package_dir" "SwiftPM package"
  require_dir "${repo_root}/viewer/openworlds" "OpenWorlds viewer resources"
  require_file "$public_key_file" "Sparkle public key"
  require_file "$PRIVATE_KEY_FILE" "Sparkle private key file"

  local public_key
  public_key="$(tr -d '\r\n[:space:]' <"$public_key_file")"
  [[ -n "$public_key" ]] || fail "Sparkle public key file is empty"

  log "Building SwiftPM release package at ${package_dir}"
  swift build -c release --package-path "$package_dir"

  local executable_path generate_appcast_path
  executable_path="$(find_swiftpm_executable "$package_dir")"
  generate_appcast_path="$(find_generate_appcast "$package_dir")"

  local staging_dir app_path channel_app_path zip_path dmg_path dmg_src appcast_src release_notes_path
  local appcast_path checksums_path validation_report_path artifact_stem
  staging_dir="${OUTPUT_ROOT}/staging"
  app_path="${staging_dir}/${APP_NAME}.app"
  channel_app_path="${OUTPUT_ROOT}/${APP_NAME}.app"
  artifact_stem="${APP_NAME}-${version}-${prerelease}"
  zip_path="${OUTPUT_ROOT}/${artifact_stem}.zip"
  dmg_path="${OUTPUT_ROOT}/${artifact_stem}.dmg"
  dmg_src="${staging_dir}/dmg"
  appcast_src="${staging_dir}/appcast"
  release_notes_path="${OUTPUT_ROOT}/RELEASE_NOTES.md"
  appcast_path="${OUTPUT_ROOT}/appcast.xml"
  checksums_path="${OUTPUT_ROOT}/CHECKSUMS.txt"
  validation_report_path="${OUTPUT_ROOT}/validation-report.md"

  log "Assembling ${app_path}"
  rm -rf "$staging_dir"
  mkdir -p "${app_path}/Contents/MacOS" "${app_path}/Contents/Resources" "${app_path}/Contents/Frameworks"
  install -m 755 "$executable_path" "${app_path}/Contents/MacOS/${EXECUTABLE_NAME}"
  write_info_plist "${app_path}/Contents/Info.plist" "$version" "$build" "$channel" "$public_key" "$feed_url" "$OUTPUT_ROOT"
  plutil -lint "${app_path}/Contents/Info.plist" >/dev/null
  copy_sparkle_framework "$package_dir" "${app_path}/Contents/Frameworks"
  ditto "${repo_root}/viewer/openworlds" "${app_path}/Contents/Resources/openworlds"
  add_app_framework_rpath "${app_path}/Contents/MacOS/${EXECUTABLE_NAME}"

  sign_bundle_contents "$app_path"
  rm -rf "$channel_app_path"
  ditto "$app_path" "$channel_app_path"

  log "Writing ZIP, DMG, release notes, appcast, checksums, and validation report"
  rm -f "$zip_path" "$dmg_path" "$release_notes_path" "${zip_path%.zip}.md" "${dmg_path%.dmg}.md" "$appcast_path" "$checksums_path" "$validation_report_path"
  ditto -c -k --keepParent "$app_path" "$zip_path"

  mkdir -p "$dmg_src"
  ditto "$app_path" "${dmg_src}/${APP_NAME}.app"
  hdiutil create -volname "${APP_NAME} ${version}" -srcfolder "$dmg_src" -ov -format UDZO "$dmg_path" >/dev/null

  write_release_notes "$release_notes_path" "$version" "$build" "$channel"
  cp "$release_notes_path" "${zip_path%.zip}.md"
  cp "$release_notes_path" "${dmg_path%.dmg}.md"
  mkdir -p "$appcast_src"
  cp "$zip_path" "$appcast_src/"
  cp "${zip_path%.zip}.md" "$appcast_src/"

  local appcast_args=("$generate_appcast_path" --ed-key-file "$PRIVATE_KEY_FILE" -o "$appcast_path")
  appcast_args+=(--download-url-prefix "$download_url_prefix")
  appcast_args+=("$appcast_src")
  "${appcast_args[@]}" >/dev/null

  write_checksums "$checksums_path" "$zip_path" "$dmg_path" "$appcast_path" "$release_notes_path"
  write_validation_report "$validation_report_path" "$channel_app_path" "$zip_path" "$dmg_path" "$appcast_path" "$version" "$build" "$channel"

  log "Done: ${OUTPUT_ROOT}"
}

main "$@"
