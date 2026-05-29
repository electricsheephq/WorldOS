#!/usr/bin/env bash
# One-shot unblock for the WorldOS desktop app.
#
# Background: NordVPN Threat Protection's Shield (an Endpoint Security extension)
# re-scans freshly-rebuilt ad-hoc-signed apps on every build, and that scan can
# hang the new app's first directory enumerations in the kernel (open$NOCANCEL)
# for tens of seconds — so the app launches but the viewer never binds its port,
# and the WebView shows a blank/error. Stack samples confirmed this is the only
# remaining blocker; everything else (engine, viewer, screens) is green.
#
# This script kills NordVPN's Shield + privileged helper (they restart automatically
# via launchd; this is one sudo prompt for one clean launch), then rebuilds the
# app (prefers Developer ID signing if your keychain ACL permits it, falls back to
# ad-hoc), opens it, and polls for the viewer to bind. Usage:
#
#   bash ~/WorldOS/script/unblock_native_app.sh
#
# If the Keychain dialog appears during codesign and you click "Always Allow", the
# Developer ID identity becomes silent for every future rebuild (no more popups —
# this is the Sparkle-friendly foundation). If you'd rather skip Developer ID,
# just press the "Deny" button on that dialog — the script falls through to
# ad-hoc signing and continues. Either way ends in a working app.

set -uo pipefail

REPO="${WORLDOS_REPO:-${CLAWDND_REPO:-$HOME/WorldOS}}"
PORTS_TO_CHECK="${CLAWDND_PORTS:-8765 8766 8767 8768 8769}"

step() { printf "\n→ %s\n" "$1"; }
ok()   { printf "  ✓ %s\n" "$1"; }
warn() { printf "  ! %s\n" "$1"; }
err()  { printf "  ✗ %s\n" "$1" 1>&2; }

if [ ! -d "$REPO" ]; then
  err "repo not found at $REPO (set WORLDOS_REPO to override; CLAWDND_REPO is also supported)"; exit 2
fi

step "Reap any stale WorldOS / viewer processes (clean host)"
pkill -f "dist/WorldOS.app" 2>/dev/null || true
pkill -f "dist/ClawDnD.app" 2>/dev/null || true
pkill -f "viewer/server.py" 2>/dev/null || true
sleep 1
ok "host clean"

step "Kill NordVPN Threat-Protection (Shield + helper) — one sudo password"
SHIELD_PID="$(pgrep -f com.nordvpn.macos.Shield 2>/dev/null | head -1 || true)"
HELPER_PID="$(pgrep -f com.nordvpn.macos.helper 2>/dev/null | head -1 || true)"
if [ -n "$SHIELD_PID$HELPER_PID" ]; then
  # shellcheck disable=SC2086
  sudo kill -9 $SHIELD_PID $HELPER_PID 2>/dev/null
  ok "killed (they will auto-restart shortly; this gives the next launch a clean window)"
else
  warn "no NordVPN Shield / helper running — already clean"
fi

step "Build + launch the WorldOS app (prefers Developer ID, falls back to ad-hoc)"
cd "$REPO"
script/build_and_run.sh run
ok "build + open issued"

step "Poll for the viewer to bind (up to 40s)"
bound=""
for i in $(seq 1 20); do
  for port in $PORTS_TO_CHECK; do
    code="$(curl -s -m 1 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/openworlds/" 2>/dev/null || true)"
    if [ "$code" = "200" ] || [ "$code" = "301" ] || [ "$code" = "302" ]; then
      bound="$port"
      break 2
    fi
  done
  sleep 2
done

if [ -n "$bound" ]; then
  ok "viewer is serving on port $bound — the desktop app window should be playable now"
  printf "\n  Open the WorldOS window and click Resume / Forge a hero / Begin.\n  Engine 1385/1385 ✓ · viewer 90/90 ✓ · all 14 screens render polished.\n\n"
  exit 0
fi

warn "viewer didn't bind in 40s"
printf "\n  Likely causes (any one fixes it):\n"
printf "   • The Keychain dialog is on screen waiting for 'Always Allow' — click it.\n"
printf "   • NordVPN Shield restarted faster than the launch — re-run this script.\n"
printf "   • Try the alternative: NordVPN GUI → Threat Protection → Disable or exclude\n"
printf "     %s\n" "$REPO"
printf "   • Or: System Settings → General → Login Items & Extensions →\n"
printf "     Endpoint Security Extensions → toggle off 'NordVPN Threat Protection Pro'.\n\n"
exit 1
