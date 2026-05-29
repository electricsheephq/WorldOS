#!/usr/bin/env bash
# Point the OpenWorlds preview at the LIVE session — the campaign whose snapshot.json was
# MOST RECENTLY written across the QA state dirs. "Always live": run this (or loop it) and the
# preview follows whatever is currently playing. The viewer is read-only + auto-refreshes off
# the snapshot mtime, so once pointed it tracks the running session beat-by-beat.
#
# Always serves the CANONICAL checkout's code (this repo) + its _private art, on a fresh
# process (so the per-request asset-version stamp reflects current .jsx → no stale browser cache).
#
# Usage: qa/preview_live.sh [port]   (default 8799)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8799}"

SNAP="$(ls -t "$ROOT"/qa/state/*/campaigns/*/snapshot.json 2>/dev/null | head -1 || true)"
if [ -z "${SNAP:-}" ]; then
  echo "preview_live: no campaign snapshot under $ROOT/qa/state/*/campaigns/ — start a session first." >&2
  exit 1
fi
CAMP="$(basename "$(dirname "$SNAP")")"
STATE_DIR="$(dirname "$(dirname "$(dirname "$SNAP")")")"   # .../qa/state/<run>/campaigns/<camp>/snapshot.json -> .../<run>

# Free the port (kill whatever viewer holds it) so the new process recomputes the version stamp.
for p in $(lsof -ti tcp:"$PORT" 2>/dev/null || true); do kill -TERM "$p" 2>/dev/null || true; done
sleep 1

WORLDOS_STATE_DIR="$STATE_DIR" CLAWDND_STATE_DIR="$STATE_DIR" \
WORLDOS_REPO_ROOT="$ROOT" CLAWDND_REPO_ROOT="$ROOT" \
  nohup python3 "$ROOT/viewer/server.py" "$CAMP" "$PORT" > "/tmp/preview_live_$PORT.log" 2>&1 &
echo "preview :$PORT -> LIVE campaign $CAMP"
echo "  state: $STATE_DIR"
echo "  open:  http://127.0.0.1:$PORT/openworlds/  (hard-refresh once: Cmd+Shift+R)"
