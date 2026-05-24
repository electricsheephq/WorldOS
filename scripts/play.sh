#!/usr/bin/env bash
# Play ClawDnD in the dashboard — the local viewer IS your play surface.
#
# This launches the play dashboard (the 127.0.0.1 viewer) pointed at a fresh game and
# runs a live Dungeon Master beside it: YOU act through the dashboard's action palette
# (Say / Do / Continue, the dice/skill/save/combat buttons, click-to-travel) and the DM
# — Claude running this full plugin and its own `dungeon-master` skill — narrates the
# world, voices the NPCs and your companion, resolves your moves through the engine, and
# logs each beat to the chat the dashboard renders live. Turn by turn, in one window.
#
# It is the dashboard counterpart to `/world-play`: same living-world generative mode,
# but you play it in the browser instead of by typing in Claude Code. No gateway needed
# (one `claude -p` DM session + the local viewer). Open the dashboard it opens for you,
# confirm the character the DM hands you, and play. Ctrl-C to stop.
#
# Usage: scripts/play.sh [world-id] [run-id] [port]
#   world-id   a living world to drop into (default: baldurs-gate). See `/world-list`.
#   run-id     names this game's save dir under play-state/ (default: a timestamp).
#   port       the dashboard port (default: 8765 or $CLAWDND_PLAY_PORT).
#
# Safety caps (a runaway DM loop self-stops):
#   CLAWDND_PLAY_BUDGET           per-turn USD budget for one DM turn   (default 1.50)
#   CLAWDND_PLAY_SESSION_BUDGET   aggregate USD ceiling for the session (default 15.00)
#   CLAWDND_PLAY_MAX_TURNS        hard cap on DM turns                  (default 40)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
WORLD="${1:-baldurs-gate}"
RUN="${2:-play-$(date +%Y%m%d-%H%M%S)}"
PORT="${3:-${CLAWDND_PLAY_PORT:-8765}}"
BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"                   # per DM turn
SESSION_BUDGET="${CLAWDND_PLAY_SESSION_BUDGET:-15.00}"  # aggregate ceiling for the whole session
MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-40}"              # hard turn cap (worst case = MAX_TURNS×BUDGET)
DM_TURNS=0

# Product play state lives under the repo's play-state/ (git-ignored), one dir per game,
# so saves, the chat log, and the move sink stay together and out of the QA sandbox.
STATE_DIR="$ROOT/play-state/$RUN"
mkdir -p "$STATE_DIR"
DM_CFG="$STATE_DIR/dm.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"
CHAT="$STATE_DIR/chat.jsonl"; : > "$CHAT"
DM_LOG="$STATE_DIR/dm"          # per-turn stream-json files: $DM_LOG.<ts>.jsonl
COMBINED="$STATE_DIR/dm.combined.jsonl"; : > "$COMBINED"
VIEWER_LOG="$STATE_DIR/viewer.log"

# Wire the three plugin MCP servers (engine/rules/voice) from THIS repo, with the
# engine pointed at this game's state dir and a silent voice backend (the dashboard
# still renders narration; spoken audio is opt-in). Generated fresh from $ROOT so the
# script works from any checkout — it does not depend on the QA harness config.
python3 - "$ROOT" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
root, state_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"mcpServers": {
    "clawdnd-engine": {"type": "stdio", "command": "uv",
        "args": ["run", "--directory", f"{root}/servers/engine", "server.py"],
        "env": {"CLAWDND_STATE_DIR": state_dir}},
    "clawdnd-rules": {"type": "stdio", "command": "uv",
        "args": ["run", "--directory", f"{root}/servers/rules", "server.py"],
        "env": {"CLAWDND_RULES_OFFLINE": "1"}},
    "clawdnd-voice": {"type": "stdio", "command": "uv",
        "args": ["run", "--directory", f"{root}/servers/voice", "server.py"],
        "env": {"CLAWDND_TTS_BACKEND": "null"}},
}}
json.dump(cfg, open(out, "w"))
PY

DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }

# One DM turn (claude -p, full plugin, resumed across the session). Echoes the reply text.
dm_turn() {
  local first="$1" msg="$2" out resume=()
  [ "$first" = "0" ] && resume=(--resume "$DSID") || resume=(--session-id "$DSID")
  out="$DM_LOG.$(date +%s%N).jsonl"
  claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model sonnet --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
    --output-format stream-json --verbose > "$out" 2>> "$DM_LOG.err"
  cat "$out" >> "$COMBINED"
  jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
}

# Launch the dashboard pointed at THIS game; setting CLAWDND_PLAYER_MOVES flips the
# viewer into interactive (live) mode so the action palette accepts moves — /move
# appends each to $MOVES; CLAWDND_VIEWER_CHAT is the beat-by-beat chat it renders.
#
# Note: the viewer now binds immediately even before any campaign exists (it serves a
# graceful empty state and lazily attaches once the DM's first turn mints the world), so
# it no longer crashes the way it once did. We still run it under a tiny supervisor as a
# SAFETY NET — if the viewer process ever dies (a crash, an OOM), this restarts it rather
# than leaving the dashboard dead mid-game. The supervisor runs in its own subshell: it
# (re)starts the viewer and BLOCKS on it with `wait` — which also reaps it, so an exited
# viewer is truly gone (no zombie that would fool a liveness check). On exit, loop and
# restart after a short pause; in the normal case it binds on the first try and `wait`
# blocks for the rest of the game. Writes the live viewer pid to $VPID_FILE so the
# parent's EXIT trap can kill the actual server, not just the supervisor.
VPID_FILE="$STATE_DIR/.viewer.pid"
viewer_supervisor() {
  while :; do
    CLAWDND_STATE_DIR="$STATE_DIR" CLAWDND_VIEWER_CHAT="$CHAT" CLAWDND_PLAYER_MOVES="$MOVES" \
      python3 viewer/server.py "" "$PORT" >> "$VIEWER_LOG" 2>&1 &
    local vp=$!; echo "$vp" > "$VPID_FILE"
    wait "$vp" 2>/dev/null   # blocks until the viewer exits (and reaps it)
    sleep 1                  # campaign not ready yet → brief pause, then relaunch
  done
}
viewer_supervisor &  SUP=$!
# On any exit: kill the supervisor (so it can't respawn) and the live viewer it tracks.
trap 'kill "$SUP" 2>/dev/null; [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null' EXIT INT TERM

# Open the browser once the dashboard is actually serving (after the campaign exists).
( for _ in $(seq 1 60); do
    curl -s --max-time 2 "http://127.0.0.1:$PORT/state" >/dev/null 2>&1 && break
    sleep 1
  done
  (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/dashboard") \
    || (command -v xdg-open >/dev/null 2>&1 && xdg-open "http://127.0.0.1:$PORT/dashboard") || true ) &

echo "ClawDnD — playing in the dashboard → http://127.0.0.1:$PORT/dashboard"
echo "  Opening the world… the dashboard fills in as the DM narrates the first scene."
echo "  Act via the palette (Say / Do / Continue, dice & combat, click-to-travel). Ctrl-C to stop."
echo "  Save dir: $STATE_DIR"

# The DM opens the world live and hands you a character + an open moment. This relies on
# the SHIPPED dungeon-master skill (its "Generating a world live" mode) — not a QA brief.
DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo ClawDnD adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

Begin a SOLO session in a living world for a single human player who will act through the dashboard:
- start_world(\"$WORLD\") and read the returned bible (premise, era/chronology, tone, standing threads, seeded regions/factions/roster). If it returns existing_campaigns, start fresh.
- start_session (for continuity and the recap).
- Create a level-3 player character (generate_ability_scores + create_character, apply_srd_defaults, sensible skills/spells). You may pick a fitting concept for them and tell the player who they are.
- Bring in a companion — recruit a roster legend (recruit_companion / load_canon_character) or create an original — with a real wound and a distinct voice.
- Open a human-scale, personal scene grounded in the world's canon, with real quoted dialogue, and hand the player an open moment + a clear, real choice.

Their actions will arrive as tagged moves — [say] (their dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — one per turn from the dashboard.")"
chatlog dm "$DMSG"; DM_TURNS=1

# Stop the (otherwise human-paced, unbounded) loop once the session hits its cost or turn
# ceiling. total_cost_usd is reported on each turn's result event (accumulated in $COMBINED).
over_budget() {
  local spent; spent="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$COMBINED" 2>/dev/null)"
  [ "$DM_TURNS" -ge "$MAX_TURNS" ] && { echo "[play] turn cap ($MAX_TURNS) reached — stopping (raise CLAWDND_PLAY_MAX_TURNS)."; return 0; }
  awk -v s="${spent:-0}" -v b="$SESSION_BUDGET" 'BEGIN{exit !(s+0>=b+0)}' \
    && { echo "[play] session budget reached (~\$$spent/\$$SESSION_BUDGET) — stopping (raise CLAWDND_PLAY_SESSION_BUDGET)."; return 0; }
  return 1
}

# Human-paced loop: when a new move lands in $MOVES (you acted in the dashboard), resolve
# it with a DM turn and render the next beat. Otherwise idle, waiting on your next move.
MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
while true; do
  over_budget && break
  total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
  if [ "$total" -gt "$MCURSOR" ]; then
    new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"; MCURSOR="$total"
    # The dashboard palette sends {kind,name}; Say/Do send {kind,text}.
    PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
    [ -z "$PMSG" ] && continue
    echo "[play] you: ${PMSG:0:100}"
    chatlog player "$PMSG"
    DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice the NPCs and companion) and narrate the next beat as a played scene. Hand the moment back to the player.")"
    chatlog dm "$DMSG"; DM_TURNS=$((DM_TURNS + 1))
  else
    sleep 2
  fi
done
