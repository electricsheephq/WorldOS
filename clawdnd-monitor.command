#!/usr/bin/env bash
# ClawDnD — CAMPAIGN MONITOR. DOUBLE-CLICK this file to open a live page that shows EVERY
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

echo "ClawDnD campaign monitor → ${URL}"
echo "(read-only; watches every play + QA campaign. Ctrl-C or close this window to stop.)"
echo

# The monitor needs ONLY the stdlib HTTP viewer — no claude, no uv, no voice backend — so this
# starts reliably. Launch it, then open the browser to the monitor page once the port is up.
"$PY" viewer/server.py '' "$PORT" &
SV=$!
trap 'kill "$SV" 2>/dev/null' EXIT INT TERM
( sleep 1.5
  if command -v open >/dev/null 2>&1; then open "$URL"
  else echo "Open this in your browser:  $URL"; fi ) &
wait "$SV"
