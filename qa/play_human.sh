#!/usr/bin/env bash
# Play WorldOS yourself (It.2): YOU are the player — you act through OpenWorlds'
# action palette / input, and a DM AGENT (claude -p, full plugin) responds, turn by
# turn, live in the same window. This is the human-in-the-loop version of the duo
# harness: OpenWorlds' /move endpoint appends your moves to $MOVES (exactly the
# facade's move format), and this loop reads each new move, runs a DM turn, logs it to
# the chat OpenWorlds renders, then waits for your next move.
#
# Gateway-free (one claude -p DM session + the local viewer). Open the page it
# launches, pick/confirm a character, and play. Ctrl-C to stop.
#
# Usage: qa/play_human.sh [world-id] [run-id] [port]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
WORLD="${1:-baldurs-gate}"; RUN="${2:-play-$(date +%H%M%S)}"; PORT="${3:-8765}"
BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"            # per DM turn
SESSION_BUDGET="${CLAWDND_PLAY_SESSION_BUDGET:-15.00}"  # M8: aggregate ceiling for the whole session
MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-40}"        # M8: hard turn cap (worst case = MAX_TURNS×BUDGET)
DM_TURNS=0
T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$RUN"
mkdir -p "$T" "$STATE_DIR"; rm -rf "$STATE_DIR/campaigns" 2>/dev/null
DM_CFG="$STATE_DIR/dm.mcp.json"; MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"
CHAT="$T/$RUN.chat.jsonl"; : > "$CHAT"; COMBINED="$T/$RUN.jsonl"; : > "$COMBINED"

python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); cfg["mcpServers"]["clawdnd-engine"]["env"]["CLAWDND_STATE_DIR"] = sys.argv[2]
json.dump(cfg, open(sys.argv[3], "w"))
PY

DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
DM_BRIEF="$(cat qa/play_dm_duo.txt)"
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }

# One DM turn (claude -p, full plugin, resumed across the session). Echoes the reply.
dm_turn() {
  local first="$1" msg="$2" out resume=()
  [ "$first" = "0" ] && resume=(--resume "$DSID") || resume=(--session-id "$DSID")
  out="$T/$RUN.dm.$(date +%s%N).jsonl"
  claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model sonnet --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
    --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
  cat "$out" >> "$COMBINED"
  jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
}

# Launch OpenWorlds pointed at THIS game; the human acts via its palette (/move
# appends to $MOVES) and watches the chat live.
CLAWDND_STATE_DIR="$STATE_DIR" CLAWDND_VIEWER_CHAT="$CHAT" CLAWDND_PLAYER_MOVES="$MOVES" \
  python3 viewer/server.py "" "$PORT" > "$T/$RUN.viewer.log" 2>&1 &
VIEWER=$!; trap 'kill "$VIEWER" 2>/dev/null' EXIT
( sleep 1.5; (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/openworlds/") \
            || (command -v xdg-open >/dev/null 2>&1 && xdg-open "http://127.0.0.1:$PORT/openworlds/") || true ) &
echo "[play] $RUN — open http://127.0.0.1:$PORT/openworlds/, act via the palette/input. Ctrl-C to stop."

# DM opens the scene + invites the player to make/confirm a character.
DMSG="$(dm_turn 1 "$DM_BRIEF

Begin a SOLO session for a human player in this world: start_world(\"$WORLD\"), start_session, create a level-3 PC (apply_srd_defaults, sensible skills/spells) — you may pick a fitting concept and tell the player who they are — and recruit a roster companion. Then open a human-scale, personal scene with real dialogue and hand the player an open moment + a clear choice. Their action will arrive next as tagged moves.")"
chatlog dm "$DMSG"; DM_TURNS=1

# M8: stop the (otherwise infinite) loop once the session hits its cost or turn ceiling.
# total_cost_usd is reported on each turn's result event (accumulated in $COMBINED).
over_budget() {
  local spent; spent="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$COMBINED" 2>/dev/null)"
  [ "$DM_TURNS" -ge "$MAX_TURNS" ] && { echo "[play] turn cap ($MAX_TURNS) reached — stopping (raise CLAWDND_PLAY_MAX_TURNS)."; return 0; }
  awk -v s="${spent:-0}" -v b="$SESSION_BUDGET" 'BEGIN{exit !(s+0>=b+0)}' \
    && { echo "[play] session budget reached (~\$$spent/\$$SESSION_BUDGET) — stopping (raise CLAWDND_PLAY_SESSION_BUDGET)."; return 0; }
  return 1
}

# Human-paced loop: when a new move lands in $MOVES, resolve it with a DM turn.
MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
while true; do
  over_budget && break
  total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
  if [ "$total" -gt "$MCURSOR" ]; then
    new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"; MCURSOR="$total"
    # Compose the human's move(s); dashboard palette sends {kind,name}, say/do send {kind,text}.
    PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
    [ -z "$PMSG" ] && continue
    echo "[play] you: ${PMSG:0:100}"
    chatlog player "$PMSG"
    DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice NPCs) and narrate the next beat as a played scene. Hand the moment back to the player.")"
    chatlog dm "$DMSG"; DM_TURNS=$((DM_TURNS + 1))
  else
    sleep 2
  fi
done
