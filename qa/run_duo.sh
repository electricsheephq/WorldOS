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
# Shared beat-driver helpers: the C soft clock-tick backstop + the A beat-aware runbooks.
# Sourced (not forked) so the duo + play loops share ONE implementation and can't drift.
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="${1:-duo-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
PLAYER_PROMPT_FILE="${3:-qa/play_player_duo.txt}"
BEATS="${4:-6}"
BUDGET="${5:-0.80}"
# The DM model is an env var so A/B-testing Opus vs sonnet for structural adherence is a
# one-flag flip (decision-dm-driver.md §3 "model choice as an orthogonal lever"). Default sonnet.
CLAWDND_DM_MODEL="${CLAWDND_DM_MODEL:-sonnet}"
# The player facade is a near-free no-tool agent; its model is a separate knob (default sonnet,
# so behavior is unchanged) kept consistent with the party harness's CLAWDND_ACTOR_MODEL.
CLAWDND_ACTOR_MODEL="${CLAWDND_ACTOR_MODEL:-sonnet}"
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
      --model "$CLAWDND_DM_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
    cat "$out" >> "$COMBINED"
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  else
    claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
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

# The move cursor lives in a FILE, not a shell var: player_move runs inside $(...) (a
# subshell), so a `MCURSOR=…` assignment is LOST on return — the cursor would stay 0 and
# every beat would re-relay the ENTIRE move history to the DM (stale, ballooning input).
# A file persists across the subshell, so each beat relays only the NEW moves.
MCURSOR_FILE="$STATE_DIR/.mcursor"; echo 0 > "$MCURSOR_FILE"
# A player turn via the constrained facade: the player acts ONLY through tools, which
# append structured moves to $MOVES. Relay ONLY the structured moves it made THIS turn —
# NEVER its raw reply text (relaying free-text would re-open the over-writing hole the
# facade exists to close, H4). If it called no move-tool, nudge once, then give up (empty).
player_move() {
  local first="$1" prompt="$2" cur total new
  turn player "$PSID" "$first" "$prompt" >/dev/null
  cur=$(cat "$MCURSOR_FILE" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -le "$cur" ]; then
    turn player "$PSID" 0 "You didn't act. Take your action THROUGH YOUR TOOLS now — say(...) / do(...) / request_check(...) / cast_spell(...) / use_item(...) / attack(...). Tools only, no prose." >/dev/null
    total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  fi
  new="$(tail -n +"$((cur + 1))" "$MOVES" 2>/dev/null)"
  echo "$total" > "$MCURSOR_FILE"
  [ -n "$new" ] && printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
}

# P0: the player introduces their character with a SINGLE say() — who they are + what
# they're after. They do NOT act yet: the world isn't built and the scene isn't set, so
# "firing off" actions into a void reads as the PLAYER authoring the story (owner live-QA:
# "the player just starts making up story; there's no intro"). The DM opens the scene next
# (D1); the player's first real action comes at beat 1.
PMSG="$(player_move 1 "$PLAYER_BRIEF

This is the very start — the world isn't built and the scene isn't set yet. Introduce your character with a SINGLE say(\"…\"): who they are and what they want. Do NOT do()/attack/cast yet — wait for the DM to open the scene. One say(), nothing else.")"
echo "[duo] player intro: ${PMSG:0:120}…"
[ -z "$PMSG" ] && { echo "[duo] player produced no intro — aborting" >&2; exit 1; }
chatlog player "$PMSG"

# D1: DM spins up the world and opens the scene around the player's concept.
DMSG="$(turn_retry dm "$DSID" 1 "$DM_BRIEF

Begin the session. The player agent introduces their character and opening intent:

$PMSG

Do the setup now: start_world(\"$WORLD\"), start_session, create their PC to match that concept (level 3, apply_srd_defaults, choose skills). Then OPEN the scene — human-scale and personal — grounded in the world's canon, responding to their stated intent. A companion should ENTER as part of that opening scene: someone the player MEETS on-screen (voiced, with a real wound and a reason they fall in together) — recruit_companion / load_canon_character as that meeting lands, NOT a silent name dropped into the party before the player has met anyone. End by handing the moment to the player.")"
echo "[duo] DM opened: ${DMSG:0:120}…"
[ -z "$DMSG" ] && { echo "[duo] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }
chatlog dm "$DMSG"

# Alternate player <-> DM for BEATS rounds. Each beat is now BEAT-AWARE (decision §A):
# read the clock + location at the START of the beat, pick the ONE moment-specific runbook
# for this beat (scene-intro / reversal / climax / travel-peopling / rising-action) instead
# of the old constant "keep the world moving" paragraph, then after the DM beat run the soft
# clock-tick backstop (decision §C) so a frozen clock advances ONE phase via the engine.
for b in $(seq 1 "$BEATS"); do
  # Progression snapshot at the START of this beat (drives both the runbook + the tick).
  PROG_PRE="$(clawdnd_read_progress "$STATE_DIR")"
  PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
  PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
  PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"

  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only.")"
  echo "[duo] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[duo] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"

  RUNBOOK="$(clawdnd_runbook_for_beat "$b" "$BEATS" "$PREV_LOC" "$STATE_DIR")"
  echo "[duo] beat $b runbook: ${RUNBOOK%% (*}…"
  DMSG="$(turn_retry dm "$DSID" 0 "The player does:

$PMSG

Resolve it through the engine (roll/cast/attack as needed), then PLAY the next beat as a full lived scene — NOT a fragment: any NPC (or the companion) in the scene SPEAKS at least one quoted line in their own voice; let them push back, hesitate, lie, or counter when it's real (don't just grant every ask); and weave the open moment back to the player INTO the scene — never a bare 'Your move.' / 'What do you do?' on its own line.

$RUNBOOK")"
  echo "[duo] beat $b DM: ${DMSG:0:100}…"
  [ -z "$DMSG" ] && { echo "[duo] DM went silent at beat $b; stopping early"; break; }
  chatlog dm "$DMSG"

  # C — soft clock-tick backstop: if the DM didn't move the clock this beat, advance one
  # phase via the engine (sole writer). Defers to the DM when it advanced time in-fiction.
  clawdnd_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
done

# Wrap + score the DM transcript (it carries the narration + all tool calls).
turn dm "$DSID" 0 "We are out of time. Bring this beat to a clean stopping point and call end_session with a one-line summary." >/dev/null
echo "[duo] distilling + scoring…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
# The PLAYED exchange (both sides) for the STORY scorer: scene_craft/playability must be
# judged on the actual back-and-forth (player moves + DM responses), not the DM's narration
# in isolation. Mechanical scoring stays on the DM distill (it grades tool usage, which lives there).
PLAY="$T/$RUN.play.md"
jq -rs 'map((.role|ascii_upcase) + ": " + (.text // "")) | join("\n\n")' "$CHAT" > "$PLAY" 2>/dev/null
[ -s "$PLAY" ] || cp "$T/$RUN.md" "$PLAY" 2>/dev/null
# Largest NON-EMPTY snapshot — not a blind head -1 (a fat-fingered campaign_id can orphan
# a lock-only dir with no snapshot, which head -1 may grab -> false "no state" RED).
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ]; then cp "$SNAP" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi
# Three lenses, run CONCURRENTLY (background + wait) so the third pass adds no wall-clock.
# Mechanical + Angry-DM (5e rules-fidelity) score the DM distill `$RUN.md` — the tool
# stream (→ tool / ← result) where the MECHANICS live; Tolkien scores the two-sided $PLAY
# (scene-craft must be judged on the actual back-and-forth).
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && qa/score.sh "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50 &
wait
# Behavioral gate — flip RED on a structurally broken run (treat it like software).
python3 qa/assert_behavioral.py "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
# Honest scoring: a gate-RED (non-progressing/structurally broken) run must NOT display as 4.1.
# CAP both recorded scorecards to ≤2.5 / INVALID and annotate WHY (the failed checks), so a dead
# scene can't masquerade as prestige play. Engine/scoring untouched on a GREEN run.
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  clawdnd_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
echo "[duo] done. story-craft=$(jq -r '.overall//"?"' "$T/$RUN.tolkien.json" 2>/dev/null) mechanical=$(jq -r '.overall//"?"' "$T/$RUN.score.json" 2>/dev/null) angry-dm=$(jq -r '.overall//"?"' "$T/$RUN.angrydm.json" 2>/dev/null) behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)"
exit $GATE
