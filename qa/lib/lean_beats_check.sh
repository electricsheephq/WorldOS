#!/usr/bin/env bash
# lean_beats_check.sh — A/B verify the CLAWDND_LEAN_BEATS perf flag on the REAL solo play
# loop (scripts/play.sh — the exact backend the built .app and the 5-persona gate run).
#
# WHY: every beat the DM turn normally `--resume`s its growing claude -p session, replaying
# the full transcript, so prefill (and wall-time) grows each beat — the latency a narrative
# persona quit over. CLAWDND_LEAN_BEATS=1 makes beats 2+ start a fresh session that
# re-grounds from the engine (scene_context bundles state/threads/arcs + the recent
# narration tail) instead of replaying the transcript. This harness measures whether that
# (a) flattens per-beat latency and (b) keeps story continuity across the lean boundary.
#
# WHAT IT DOES: launches play.sh on a free port with NULL voice/image (gateway-free), waits
# for the cold open, then injects the SAME scripted moves into the dashboard move-sink twice
# — once flag-OFF (baseline), once flag-ON (lean) — timing each beat from its per-turn
# dm.<ts>.jsonl file. Then it prints the recent persisted narration for a human continuity
# spot-check (does the lean run's late beat still reference earlier NPCs / choices?).
#
# HARD CONSTRAINTS (16GB host, heavy swap): runs ONE play.sh (one claude -p DM) at a time,
# strictly sequential OFF then ON — never parallel. Keep BEATS small (3-4). The orchestrator
# runs the full 5-persona sweep; this is the focused latency+continuity check.
#
# SKIP-CLEAN: if claude/uv/jq/curl or play.sh are missing, prints why and exits 0 (so it's a
# no-op in sandboxes without the model and never red-fails CI).
#
# Usage: qa/lib/lean_beats_check.sh [world] [beats] [port] [budget]
#   world  default baldurs-gate   beats default 4   port default 8884   budget default 2.00
set -uo pipefail

WORLD="${1:-baldurs-gate}"
BEATS="${2:-4}"
PORT="${3:-8884}"
BUDGET="${4:-2.00}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLAY="$ROOT/scripts/play.sh"

note() { printf '[lean-check] %s\n' "$*"; }

# --- Preflight: only proceed if a REAL run is possible ------------------------
for bin in claude uv jq curl python3; do
  command -v "$bin" >/dev/null 2>&1 || { note "SKIP: missing '$bin' — cannot run a real solo session. Run on a host with the model."; exit 0; }
done
[ -f "$PLAY" ] || { note "SKIP: $PLAY not found."; exit 0; }

# The scripted player moves (beat 1 is the DM's cold open; these drive beats 2..N+1). Kept
# generic so they fit any world's opening: react, ask a question, commit, press on.
MOVES_TEXT=(
  "say:I study the room and the person who just spoke to me, then ask them plainly what they want from me."
  "do:I weigh what they've offered, name the one thing that worries me most about it, and press them on it."
  "say:All right — I'm in. But I want to know your name, and I want to know who else is counting on this."
  "do:I move with them toward whatever comes next, staying alert for the first sign this turns dangerous."
)

now_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }
post_move() {  # $1 state_dir, $2 "kind:text"
  local sink="$1/player_moves.jsonl" kind="${2%%:*}" text="${2#*:}"
  python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"kind":sys.argv[2],"text":sys.argv[3]})+"\n")' \
    "$sink" "$kind" "$text"
}
count_turns() { ls -1 "$1"/dm.*.jsonl 2>/dev/null | grep -vc '\.err$' || echo 0; }

# Wait until a NEW dm.<ts>.jsonl appears (the DM finished a beat) or timeout. Echoes the ms
# the beat took (wall-clock between the move post and the new turn file), or "TIMEOUT".
wait_for_beat() {  # $1 state_dir, $2 baseline_turn_count, $3 t0_ms, $4 deadline_s
  local dir="$1" base="$2" t0="$3" deadline="$4" waited=0
  while [ "$waited" -lt "$deadline" ]; do
    if [ "$(count_turns "$dir")" -gt "$base" ]; then echo $(( $(now_ms) - t0 )); return 0; fi
    sleep 2; waited=$((waited + 2))
  done
  echo "TIMEOUT"; return 1
}

run_phase() {  # $1 label, $2 lean(0/1) ; echoes "ms1 ms2 ..." and leaves state in REPLY_DIR
  local label="$1" lean="$2" run port state base t0 ms i
  run="leanchk-$( [ "$lean" = 1 ] && echo on || echo off )-$(date +%s)"
  port=$((PORT + lean))   # OFF and ON on different ports so they never collide
  state="$ROOT/play-state/$run"
  note "=== $label : launching play.sh (port=$port, world=$WORLD, run=$run) ==="
  rm -rf "$state"
  ( cd "$ROOT" && CLAWDND_LEAN_BEATS="$lean" CLAWDND_DM_MODEL="${CLAWDND_DM_MODEL:-sonnet}" \
      CLAWDND_PLAY_BUDGET="$BUDGET" CLAWDND_PLAY_MAX_IDLE=120 CLAWDND_PLAY_PORT="$port" \
      bash "$PLAY" "$WORLD" "$run" "$port" >"$state.driver.log" 2>&1 ) &
  local pid=$!

  # Wait for the cold open (the FIRST dm turn file) — up to 240s.
  local opened=0 w=0
  while [ "$w" -lt 240 ]; do
    [ "$(count_turns "$state")" -ge 1 ] && { opened=1; break; }
    kill -0 "$pid" 2>/dev/null || { note "$label: play.sh exited before opening (see $state.driver.log)"; break; }
    sleep 3; w=$((w + 3))
  done
  if [ "$opened" != 1 ]; then
    note "$label: no cold open within 240s — aborting this phase."; kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    REPLY_MS=""; REPLY_DIR="$state"; return 1
  fi
  note "$label: cold open done. Injecting $BEATS scripted move(s), timing each beat…"

  local results=()
  for ((i=0; i<BEATS && i<${#MOVES_TEXT[@]}; i++)); do
    base="$(count_turns "$state")"; t0="$(now_ms)"
    post_move "$state" "${MOVES_TEXT[$i]}"
    ms="$(wait_for_beat "$state" "$base" "$t0" 240)"
    note "$label beat $((i+2)): ${ms} ms"   # beat numbering: cold open = beat 1
    results+=("$ms")
  done

  # Stop this phase's loop cleanly before starting the next (ONE claude -p at a time).
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  pkill -f "viewer/server.py \"\" $port" 2>/dev/null || true
  REPLY_MS="${results[*]}"; REPLY_DIR="$state"
}

continuity_dump() {  # $1 state_dir — print the recent persisted narration for human review
  local snap; snap="$(find "$1/campaigns" -name snapshot.json 2>/dev/null | head -n1)"
  [ -f "$snap" ] || { note "(no snapshot to spot-check in $1)"; return; }
  python3 - "$snap" <<'PY'
import json,sys
snap=json.load(open(sys.argv[1]))
log=snap.get("session_log") or []
rows=[e for e in log if e.get("kind") in ("narration","dialogue")]
for e in rows[-8:]:
    sp=(" ["+e["speaker"]+"]") if e.get("speaker") else ""
    print(f"  ({e.get('kind')}{sp}) {e.get('text','')[:400]}")
PY
}

note "world=$WORLD beats=$BEATS base-port=$PORT budget=\$$BUDGET — SEQUENTIAL OFF then ON"
run_phase "flag-OFF (baseline)" 0; OFF_MS="$REPLY_MS"; OFF_DIR="$REPLY_DIR"
run_phase "flag-ON  (lean)"     1; ON_MS="$REPLY_MS";  ON_DIR="$REPLY_DIR"

echo
note "================= RESULT ================="
note "per-beat ms (cold open excluded), beats 2..$((BEATS+1)):"
note "  flag-OFF: ${OFF_MS:-<none>}"
note "  flag-ON : ${ON_MS:-<none>}"
note "Expect flag-OFF to GROW across beats and flag-ON to stay ~flat near beat-2 latency."
echo
note "--- continuity spot-check: flag-ON recent narration (does the LATE beat still"
note "    reference earlier NPCs / the scene / prior choices? human judgement) ---"
[ -n "${ON_DIR:-}" ] && continuity_dump "$ON_DIR"
echo
note "A flat latency curve that DROPS story quality is NOT a pass — story is the North Star."
note "(state dirs kept for inspection: OFF=$OFF_DIR  ON=$ON_DIR — both under play-state/, gitignored)"
