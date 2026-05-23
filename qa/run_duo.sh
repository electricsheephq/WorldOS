#!/usr/bin/env bash
# TWO-AGENT ClawDnD QA: a DM agent and a SEPARATE player agent play against each
# other, mediated only by the shared engine state + the narration they exchange.
# This replaces the single-agent "play both roles" harness — the player is now an
# independent agent with its own context and agenda, so it can't "play along" with
# the DM's reasoning. Gateway-free: two `claude -p` sessions (no OpenClaw), so it's
# portable and never touches the Eva gateway.
#
#   - DM agent:     full plugin (engine/rules/voice) + dungeon-master skill. Resolves
#                   the player's declared actions through the engine; the engine is
#                   its memory across turns (re-grounds via get_state each beat).
#   - Player agent: NO tools (empty strict MCP). Sees only the DM's narration; declares
#                   its character's actions in text, per a persona brief.
#
# Usage: qa/run_duo.sh <run-id> <world-id> <player-persona> [beats] [budget-per-call]
# Example: qa/run_duo.sh duo1 baldurs-gate qa/play_player_duo.txt 6 0.80
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

RUN="${1:-duo-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
PLAYER_PROMPT_FILE="${3:-qa/play_player_duo.txt}"
BEATS="${4:-6}"
BUDGET="${5:-0.80}"
T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$RUN"
mkdir -p "$T" "$STATE_DIR"; rm -rf "$STATE_DIR/campaigns" 2>/dev/null

# DM gets the engine (state dir patched in); the player gets an EMPTY strict config.
DM_CFG="$STATE_DIR/dm.mcp.json"; PLAYER_CFG="$STATE_DIR/player.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"  # the player's structured moves (It.1)
python3 - "$ROOT/qa/qa.mcp.json" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); cfg["mcpServers"]["clawdnd-engine"]["env"]["CLAWDND_STATE_DIR"] = sys.argv[2]
json.dump(cfg, open(sys.argv[3], "w"))
PY
# The player gets ONLY the constrained move facade (clawdnd-player): it acts through
# tools, never free-text narration; moves land in $MOVES for the orchestrator to relay.
python3 - "$ROOT" "$STATE_DIR" "$MOVES" "$PLAYER_CFG" <<'PY'
import json, sys
root, state, moves, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
json.dump({"mcpServers": {"clawdnd-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"CLAWDND_STATE_DIR": state, "CLAWDND_PLAYER_MOVES": moves}}}}, open(out, "w"))
PY

DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
DM_BRIEF="$(cat qa/play_dm_duo.txt)"; PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"
COMBINED="$T/$RUN.jsonl"; : > "$COMBINED"
# A clean two-sided conversation log (the player agent's turns AND the DM's), so the
# dashboard can show the PROTAGONIST acting — not just the DM narrating. The DM's own
# stream (COMBINED) doesn't echo the player's turns, so we capture both sides here.
CHAT="$T/$RUN.chat.jsonl"; : > "$CHAT"
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }
echo "[duo] run=$RUN world=$WORLD beats=$BEATS dm=$DSID player=$PSID"

# $1=role(player|dm) $2=session-id $3=first?(1/0) $4=message ; echoes the agent's reply text
turn() {
  local role="$1" sid="$2" first="$3" msg="$4" out resume=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$role" = "dm" ]; then
    out="$T/$RUN.dm.$(date +%s%N).jsonl"
    claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model sonnet --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
    cat "$out" >> "$COMBINED"
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  else
    claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
      --model sonnet --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format json 2>> "$T/$RUN.player.err" \
      | jq -r '.result // ""' 2>/dev/null
  fi
}

# A turn, with ONE retry on empty output (a transient CLI/auth/rate blip shouldn't
# silently truncate a run). Echoes the reply text (possibly empty after the retry).
turn_retry() {
  local r; r="$(turn "$@")"
  [ -z "$r" ] && { echo "[duo] empty turn ($1) — retrying once…" >&2; r="$(turn "$@")"; }
  printf '%s' "$r"
}

MCURSOR=0
# A player turn via the constrained facade: the player acts ONLY through tools, which
# append structured moves to $MOVES. Read the moves it made THIS turn and compose a
# readable action for the DM (fall back to its reply text if it made no moves).
player_move() {
  local first="$1" prompt="$2" txt total new
  txt="$(turn player "$PSID" "$first" "$prompt")"
  total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"
  MCURSOR="$total"
  if [ -n "$new" ]; then
    printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
  else
    printf '%s' "$txt"
  fi
}

# P0: the player introduces their character + opening intent.
PMSG="$(player_move 1 "$PLAYER_BRIEF

This is the very start (the world isn't built yet). Introduce your character in one short line, then take your opening action(s) using your tools — e.g. do(\"…\") / say(\"…\"). Tools only; no narration.")"
echo "[duo] player intro: ${PMSG:0:120}…"
[ -z "$PMSG" ] && { echo "[duo] player produced no intro — aborting" >&2; exit 1; }
chatlog player "$PMSG"

# D1: DM spins up the world and opens the scene around the player's concept.
DMSG="$(turn_retry dm "$DSID" 1 "$DM_BRIEF

Begin the session. The player agent introduces their character and opening intent:

$PMSG

Do the setup now: start_world(\"$WORLD\"), start_session, create their PC to match that concept (level 3, apply_srd_defaults, choose skills), and recruit a fitting roster companion with recruit_companion. Then open the scene — human-scale and personal — and respond to their stated intent. End by handing the moment to the player.")"
echo "[duo] DM opened: ${DMSG:0:120}…"
[ -z "$DMSG" ] && { echo "[duo] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }
chatlog dm "$DMSG"

# Alternate player <-> DM for BEATS rounds.
for b in $(seq 1 "$BEATS"); do
  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only.")"
  echo "[duo] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[duo] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"
  DMSG="$(turn_retry dm "$DSID" 0 "The player does:

$PMSG

Resolve it through the engine and narrate the next beat. Hand the moment back to the player.")"
  echo "[duo] beat $b DM: ${DMSG:0:100}…"
  [ -z "$DMSG" ] && { echo "[duo] DM went silent at beat $b; stopping early"; break; }
  chatlog dm "$DMSG"
done

# Wrap + score the DM transcript (it carries the narration + all tool calls).
turn dm "$DSID" 0 "We are out of time. Bring this beat to a clean stopping point and call end_session with a one-line summary." >/dev/null
echo "[duo] distilling + scoring…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
CAMP="$(find "$STATE_DIR/campaigns" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
if [ -n "$CAMP" ] && [ -f "$CAMP/snapshot.json" ]; then cp "$CAMP/snapshot.json" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50
# Behavioral gate — flip RED on a structurally broken run (treat it like software).
python3 qa/assert_behavioral.py "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES"; GATE=$?
echo "[duo] done. story-craft=$(jq -r '.overall//"?"' "$T/$RUN.tolkien.json" 2>/dev/null) mechanical=$(jq -r '.overall//"?"' "$T/$RUN.score.json" 2>/dev/null) behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)"
exit $GATE
