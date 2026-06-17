#!/usr/bin/env bash
# WorldOS QA/play REAPER — kill any harness process (a QA run or a play session) older than
# MAX_MIN minutes. A safety net against WEDGED runs that spin for hours: e.g. a dry-run
# play_party with no human input that loops a sleep forever, or a `claude -p` that never returns.
# (play_party/run_* now self-cap, but this catches anything that slips through or predates the fix.)
#
# It does NOT touch your main Claude session — only the harness scripts + their player_server
# children. macOS only (uses BSD `ps -o lstart` + `date -j`).
#
# Usage:
#   scripts/qa_reap.sh [max_minutes] [--dry-run]
#   scripts/qa_reap.sh            # kill harness procs older than 30 min
#   scripts/qa_reap.sh 60 --dry-run   # just LIST what's older than 60 min
set -uo pipefail

MAX_MIN="${1:-30}"
DRY=""; [ "${2:-}" = "--dry-run" ] && DRY=1
MAX_S=$(( MAX_MIN * 60 ))
# The harness entry points + their per-actor facade. NOT the `claude` binary itself (so this
# never reaps the interactive session), NOT the read-only viewer (harmless, and may be a live game).
PAT='play_party\.sh|run_party\.sh|run_duo\.sh|run_qa\.sh|scripts/play\.sh|player_server\.py'
now="$(date +%s)"
found=0

# pid + lstart (5 tokens: "Day Mon DD HH:MM:SS Year") + the command.
ps -Ao pid=,lstart=,command= 2>/dev/null | grep -E "$PAT" | grep -vE 'qa_reap|grep' | while read -r pid d1 d2 d3 d4 d5 cmd; do
  st="$(date -j -f "%a %b %e %T %Y" "$d1 $d2 $d3 $d4 $d5" +%s 2>/dev/null)" || continue
  age=$(( now - st ))
  [ "$age" -lt "$MAX_S" ] && continue
  found=1
  printf '[qa-reap] pid %s  age %dm  %s\n' "$pid" "$((age/60))" "$(printf '%.90s' "$cmd")"
  if [ -z "$DRY" ]; then
    pkill -TERM -P "$pid" 2>/dev/null   # children first (claude -p / viewer)
    kill -TERM "$pid" 2>/dev/null; sleep 2
    pkill -9 -P "$pid" 2>/dev/null
    kill -9 "$pid" 2>/dev/null
  fi
done

[ -n "$DRY" ] && echo "[qa-reap] dry-run — nothing killed (threshold ${MAX_MIN}m)." \
              || echo "[qa-reap] done (killed harness procs older than ${MAX_MIN}m; re-run to confirm clean)."
