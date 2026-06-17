#!/usr/bin/env bash
# The recursive-improvement LOOP GATE — the standing fitness check.
#
# Runs K duo playtests CONCURRENTLY (each isolated), each scored on BOTH lenses
# (story-craft + mechanical) AND run through the behavioral gate, then PASS/FAILs the
# batch against the north-star thresholds. This is how "loop testing" becomes
# "recursive improvement after release": run it on a push or a schedule; a RED means a
# regression to fix BEFORE new feature work — the loop never declares the game "done".
#
# run_duo.sh already does the per-run work (constrained-player duo + dual-lens score +
# behavioral gate, echoing `behavioral=GREEN|RED`); this wraps K of them and gates.
#
# Usage: qa/loop.sh [runs] [world] [persona] [beats] [budget-per-call]
# Env:   WORLDOS_STORY_MIN (default 4.3)  WORLDOS_MECH_MIN (default 4.5)
# Exit:  0 = all runs clear the bar; 1 = at least one below (RED); 2 = nothing ran.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 2

RUNS="${1:-2}"; WORLD="${2:-baldurs-gate}"; PERSONA="${3:-qa/play_player_duo.txt}"
BEATS="${4:-8}"; BUDGET="${5:-1.20}"
STORY_MIN="${WORLDOS_STORY_MIN:-${WORLDOS_STORY_MIN:-4.3}}"; MECH_MIN="${WORLDOS_MECH_MIN:-${WORLDOS_MECH_MIN:-4.5}}"
STAMP="$(date +%y%m%d-%H%M%S)"; T="qa/transcripts"
mkdir -p "$T"

pids=(); runs=()
for i in $(seq 1 "$RUNS"); do
  RUN="loop-$STAMP-$i"; runs+=("$RUN")
  echo "[loop] launching $RUN (world=$WORLD beats=$BEATS budget=$BUDGET)"
  qa/run_duo.sh "$RUN" "$WORLD" "$PERSONA" "$BEATS" "$BUDGET" > "$T/$RUN.runlog" 2>&1 &
  pids+=("$!")
  sleep 8  # stagger so the scoring phases don't all collide
done
[ "${#pids[@]}" -eq 0 ] && { echo "[loop] nothing to run" >&2; exit 2; }
echo "[loop] ${#runs[@]} run(s) in flight — waiting…"
for p in "${pids[@]}"; do wait "$p" || true; done

echo "[loop] ===== scorecard (bar: story>=$STORY_MIN  mech>=$MECH_MIN  behavioral=GREEN) ====="
fail=0
for RUN in "${runs[@]}"; do
  story="$(jq -r '.overall // 0' "$T/$RUN.tolkien.json" 2>/dev/null || echo 0)"
  mech="$(jq -r '.overall // 0' "$T/$RUN.score.json" 2>/dev/null || echo 0)"
  gate="$(grep -o 'behavioral=[A-Z]*' "$T/$RUN.runlog" 2>/dev/null | tail -1 | cut -d= -f2)"; gate="${gate:-RED}"
  bad=""
  awk -v s="$story" -v m="$STORY_MIN" 'BEGIN{exit !(s+0 < m+0)}' && bad="$bad story=$story<$STORY_MIN"
  awk -v s="$mech"  -v m="$MECH_MIN"  'BEGIN{exit !(s+0 < m+0)}' && bad="$bad mech=$mech<$MECH_MIN"
  [ "$gate" != "GREEN" ] && bad="$bad behavioral=$gate"
  if [ -n "$bad" ]; then
    fail=$((fail + 1)); echo "  [BELOW] $RUN: story=$story mech=$mech gate=$gate —$bad"
    v="$(jq -r '.verdict // ""' "$T/$RUN.tolkien.json" 2>/dev/null)"; [ -n "$v" ] && echo "          \"$v\""
  else
    echo "  [OK]    $RUN: story=$story mech=$mech gate=$gate"
  fi
done
echo "[loop] $fail/${#runs[@]} run(s) below the bar."
[ "$fail" -eq 0 ] && exit 0 || exit 1
