#!/usr/bin/env bash
# Launch several ClawDnD QA playtests CONCURRENTLY (each isolated under qa/state/<run>
# with its own MCP config), then wait and print every scorecard. Each run is scored on
# BOTH lenses (mechanical + Tolkien story-craft). This is the velocity lever: 2-3 signals
# per cycle instead of one. The `claude -p` runs are API-bound, not the memory-heavy
# pytest workers — safe to run in parallel.
#
# Usage: qa/run_parallel.sh <budget-per-run> <prompt1> <rubric1> [<prompt2> <rubric2> ...]
# Example (3 story-first runs in parallel):
#   qa/run_parallel.sh 3.00 \
#     qa/play_prompt_story.txt qa/rubric_story.md \
#     qa/play_prompt_story.txt qa/rubric_story.md \
#     qa/play_prompt_story.txt qa/rubric_story.md
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

BUDGET="${1:-3.00}"; shift || true
STAMP="$(date +%H%M%S)"
i=0; pids=(); runs=()
while [ "$#" -ge 2 ]; do
  i=$((i + 1)); PROMPT="$1"; RUBRIC="$2"; shift 2
  RUN="p${STAMP}-$i"
  runs+=("$RUN")
  echo "[parallel] launching $RUN: prompt=$PROMPT rubric=$RUBRIC"
  qa/run_qa.sh "$RUN" "$BUDGET" "$PROMPT" "$RUBRIC" > "qa/transcripts/$RUN.run.log" 2>&1 &
  pids+=("$!")
  [ "$#" -ge 2 ] && sleep 8  # stagger starts so scoring phases don't all collide
done

if [ "${#pids[@]}" -eq 0 ]; then
  echo "usage: qa/run_parallel.sh <budget> <prompt1> <rubric1> [<prompt2> <rubric2> ...]" >&2
  exit 2
fi

echo "[parallel] ${#pids[@]} runs in flight: ${runs[*]} — waiting…"
for p in "${pids[@]}"; do wait "$p"; done

echo "[parallel] ===== all done — overall scores ====="
for RUN in "${runs[@]}"; do
  TOLK="$(jq -r '.overall // "?"' "qa/transcripts/$RUN.tolkien.json" 2>/dev/null || echo '?')"
  MECH="$(jq -r '.overall // "?"' "qa/transcripts/$RUN.score.json" 2>/dev/null || echo '?')"
  VERD="$(jq -r '.verdict // ""' "qa/transcripts/$RUN.tolkien.json" 2>/dev/null || echo '')"
  echo "  $RUN: story-craft=$TOLK  mechanical=$MECH"
  [ -n "$VERD" ] && echo "      \"$VERD\""
done
