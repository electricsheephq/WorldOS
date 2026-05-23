#!/usr/bin/env bash
# ClawDnD dashboard — DOUBLE-CLICK this file to open the play/test view in your
# browser (no terminal typing needed). It auto-watches the most recent QA run so you
# can see tests as they happen. Optional arg: a run id (e.g. `duo1`) to watch a
# specific run, or `play` to watch your own live ~/.clawdnd game instead.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
PORT="${CLAWDND_DASH_PORT:-8765}"
ARG="${1:-}"

has_campaign() { ls "qa/state/$1/campaigns/"*/snapshot.json >/dev/null 2>&1; }
newest_run() {  # newest qa run id whose state actually holds a campaign
  for tx in $(ls -t qa/transcripts/*.jsonl 2>/dev/null); do
    r="$(basename "$tx" .jsonl)"; if has_campaign "$r"; then echo "$r"; return; fi
  done
}

if [ "$ARG" = "play" ]; then
  unset CLAWDND_VIEWER_TRANSCRIPT CLAWDND_STATE_DIR
  echo "Watching your live game (~/.clawdnd/state)…"
else
  RUN="${ARG:-$(newest_run)}"
  if [ -n "${RUN:-}" ] && has_campaign "$RUN"; then
    export CLAWDND_STATE_DIR="$PWD/qa/state/$RUN"
    [ -f "qa/transcripts/$RUN.jsonl" ] && export CLAWDND_VIEWER_TRANSCRIPT="$PWD/qa/transcripts/$RUN.jsonl"
    echo "Watching QA run: $RUN"
  else
    echo "No QA run found — watching your live game (~/.clawdnd/state)…"
  fi
fi

URL="http://127.0.0.1:$PORT/dashboard"
# open the browser shortly after the server comes up (macOS `open`, Linux `xdg-open`)
( sleep 1.2; (command -v open >/dev/null 2>&1 && open "$URL") \
            || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL") \
            || echo "Open $URL in your browser." ) &
echo "ClawDnD dashboard → $URL   (close this window to stop)"
exec python3 viewer/server.py "" "$PORT"
