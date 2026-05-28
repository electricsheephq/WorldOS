#!/usr/bin/env bash
# Headless screenshot of an OpenWorlds screen via its URL-hash deep-link.
# Usage: qa/owshot.sh <screen-hash> <out.png> [port]
#
# screen-hash is one of: launcher, table, combat, dialogue, map, character,
# inventory, forge, relations, journal, bestiary, acts, merchant, create,
# seed, settings — plus aliases battle/parley/chronicles/market/stash.
#
# Used by qa/screen_coverage.py + qa/run_openworlds_session.sh for per-beat
# screen capture during release sweeps. Headless Chrome loads a localhost
# URL only (never enumerates the gated external volume), so this is safe to
# run autonomously in CI / cross-disk clones / on any host.
set -u
HASH="${1:-table}"
OUT="${2:-/tmp/ow-$HASH.png}"
PORT="${3:-8799}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="/tmp/chrome-ow-profile-${PORT}"
rm -f "$OUT"
timeout 25 "$CHROME" \
  --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1512,982 --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check --disable-background-networking \
  --disable-component-update --disable-default-apps --disable-sync \
  --virtual-time-budget=9000 --timeout=12000 \
  --screenshot="$OUT" "http://127.0.0.1:${PORT}/openworlds/#${HASH}" \
  >/dev/null 2>&1
pkill -f "user-data-dir=${PROFILE}" 2>/dev/null
[ -s "$OUT" ] && echo "OK $OUT ($(wc -c <"$OUT") bytes)" || echo "FAIL $OUT"
