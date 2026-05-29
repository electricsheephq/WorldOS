#!/usr/bin/env bash
# WorldOS — CAMPAIGN MONITOR. DOUBLE-CLICK this file to open a live page that shows EVERY
# campaign running — your own games AND every parallel QA test run — in one place, auto-
# refreshing. Jump between them, watch how each is going at a glance (party, location, day,
# scores). It is READ-ONLY: it only watches; it never changes a campaign.
#
# Close this window (or press Ctrl-C) to stop. Optional arg: a port (default 8770, chosen so it
# coexists with a play session's dashboard on 8765 — so you can watch WHILE you play).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

PORT="${1:-8770}"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "Python 3 was not found on PATH. Install Python 3 and try again."
  echo "(press Return to close)"; read -r _; exit 1
fi
URL="http://127.0.0.1:${PORT}/monitor"

echo "WorldOS campaign monitor → ${URL}"
echo "(read-only; watches every play + QA campaign. Ctrl-C or close this window to stop.)"
echo

# The monitor needs ONLY the stdlib HTTP viewer — no claude, no uv, no voice backend — so this
# starts reliably.
ready() { curl -fsS -o /dev/null --max-time 1 "http://127.0.0.1:${PORT}/monitor.json" 2>/dev/null; }

# If a monitor is ALREADY running on this port, reuse it — DON'T spawn a second server that
# fails to bind while the browser silently attaches to the stale one ("frozen on old data").
if ready; then
  echo "A monitor is already running on ${PORT} — opening it (close that window first if you want a fresh one)."
  command -v open >/dev/null 2>&1 && open "$URL" || echo "Open this in your browser:  $URL"
  exit 0
fi

"$PY" viewer/server.py '' "$PORT" &
SV=$!
trap 'kill "$SV" 2>/dev/null' EXIT INT TERM
# Open the browser ONLY once the server is actually accepting connections — never point it at a
# port that never came up. Bail loudly if the server died (e.g. the port is taken by something else).
( for _ in $(seq 1 40); do
    if ready; then
      command -v open >/dev/null 2>&1 && open "$URL" || echo "Open this in your browser:  $URL"
      exit 0
    fi
    kill -0 "$SV" 2>/dev/null || { echo "Monitor failed to start (is port ${PORT} already in use by something else?)."; exit 1; }
    sleep 0.25
  done
  echo "Monitor didn't come up on ${PORT} within 10s — check for errors above." ) &
wait "$SV"
