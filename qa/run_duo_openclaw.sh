#!/usr/bin/env bash
# OpenClaw / GPT-5.4 variant of the TWO-AGENT ClawDnD QA duo (qa/run_duo.sh).
# Same beat loop, runbook driver, soft clock-tick, move relay, distill, and behavioral
# gate as run_duo.sh — ONLY the agent-invocation layer differs: instead of two
# gateway-free `claude -p` sessions, this drives the two ISOLATED OpenClaw agents
# `clawdnd-qa-dm` / `clawdnd-qa-player` (both openai/gpt-5.4) via `openclaw agent`.
#
# Why a separate script (not a flag in run_duo.sh): the two harnesses parse two
# different envelopes. run_duo.sh's $COMBINED is Anthropic stream-json (tool_use /
# tool_result blocks) that distill.py + assert_behavioral.py read natively. OpenClaw's
# `--json` reply is a different shape AND its toolSummary.tools[] is DEDUPED (loses
# per-call counts). So this script TRANSCODES each DM turn's session-transcript JSONL
# (~/.openclaw/agents/clawdnd-qa-dm/sessions/<sid>.jsonl — which records EVERY tool
# call with repeats) into the exact Anthropic stream-json shape the gate/distill expect,
# appending to $COMBINED. The gate + distill then run UNCHANGED.
#
# SKILL INJECTION: the OpenClaw agents have no plugin/skill and their workspace isn't the
# repo, so the DM can't read skills/dungeon-master/AGENT.md off its cwd. On the FIRST DM
# turn we INJECT the DM brief + AGENT.md + SKILL.md + the key reference/*.md into the
# message; --session-id carries that identity across later beats (which send just the beat
# prompt + runbook). Likewise the wizard persona is injected into the player's first turn.
#
# Usage: qa/run_duo_openclaw.sh <run-id> <world-id> <player-persona> [beats] [budget-ignored]
# Example: CLAWDND_DM_MODEL=openai/gpt-5.4 qa/run_duo_openclaw.sh ocsmoke baldurs-gate qa/play_player_wizard.txt 4
#
# GUARDRAILS: only ever touches agents clawdnd-qa-dm / clawdnd-qa-player. Never sets MCP,
# never touches main/operations/the gateway config. Run ONE duo at a time (the engine MCP
# state dir + the player moves file are FIXED in the agents' MCP env — shared, not per-run).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="${1:-ocduo-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
PLAYER_PROMPT_FILE="${3:-qa/play_player_wizard.txt}"
BEATS="${4:-6}"
# Models are env knobs (default the proven gpt-5.4). budget arg ($5) is accepted for CLI
# parity with run_duo.sh but unused here — OpenClaw turns don't take a --max-budget flag.
CLAWDND_DM_MODEL="${CLAWDND_DM_MODEL:-openai/gpt-5.4}"
CLAWDND_ACTOR_MODEL="${CLAWDND_ACTOR_MODEL:-openai/gpt-5.4}"
TIMEOUT="${CLAWDND_OC_TIMEOUT:-600}"
# THINKING LEVEL — the single most important knob. The clawdnd-qa agents default to
# thinking=high, and at high gpt-5.4 over-deliberates on the big injected DM brief and
# can burn the whole 600s timeout WITHOUT EVER CALLING A TOOL (observed: first DM turn
# aborted at high; identical turn at low called start_world+start_session in 42s). So we
# force a modest thinking level per turn. `low` is the proven default; override if needed.
THINK="${CLAWDND_OC_THINKING:-low}"

# The OpenClaw agents' MCP env is FIXED (set globally, do not re-set): the engine writes
# campaign state under STATE_OC and the player facade writes moves to MOVES. Both are
# SHARED across runs (one duo at a time). We clear campaigns + truncate moves at the start
# so a prior run's larger snapshot can't be picked by the gate's "largest snapshot" rule,
# mirroring run_duo.sh's `rm -rf "$STATE_DIR/campaigns"`.
STATE_OC="$ROOT/qa/state-oc"
MOVES="$STATE_OC/player_moves.jsonl"
mkdir -p "$STATE_OC"
rm -rf "$STATE_OC/campaigns" 2>/dev/null; : > "$MOVES"

# Where each agent's session transcript lands. `openclaw agent --session-id X --agent A`
# writes the full per-call tool trace to <OC_HOME>/agents/A/sessions/X.jsonl (verified).
OC_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
DM_SID="${RUN}-dm"; PLAYER_SID="${RUN}-player"
DM_SESS_FILE="$OC_HOME/agents/clawdnd-qa-dm/sessions/$DM_SID.jsonl"
# Start each run from a clean DM session transcript so the per-turn transcode delta (below)
# isn't polluted by a same-id session left over from a previous run.
rm -f "$DM_SESS_FILE" "$OC_HOME/agents/clawdnd-qa-player/sessions/$PLAYER_SID.jsonl" 2>/dev/null

T="qa/transcripts"; mkdir -p "$T"
COMBINED="$T/$RUN.jsonl"; : > "$COMBINED"   # DM-side Anthropic-shaped stream-json (transcoded)
CHAT="$T/$RUN.chat.jsonl"; : > "$CHAT"       # two-sided conversation log (player + DM)
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }
echo "[ocduo] run=$RUN world=$WORLD beats=$BEATS dm-agent=clawdnd-qa-dm($CLAWDND_DM_MODEL) player-agent=clawdnd-qa-player($CLAWDND_ACTOR_MODEL)"

# --- skill injection payload (DM) -------------------------------------------------------
# The DM agent has NO plugin/skill loaded and its cwd is NOT the repo, so play_dm_duo.txt's
# "read AGENT.md at your cwd" cannot work. We strip that stale instruction and instead inline
# the agent definition + skill + the load-bearing references, so the gpt-5.4 DM holds its
# identity, the 3-act process, the tool discipline, and the living-world obligations.
DM_PERSONA="$(sed -E 's#FIRST read your agent definition at `skills/dungeon-master/AGENT\.md` \(path relative to your cwd, the repo root\) — it#Your agent definition and skill are INLINED below \(your workspace is not the repo, so read them here, not from disk\). The agent definition#' qa/play_dm_duo.txt)"
DM_REFS=""
for f in combat storycraft living-world; do
  [ -f "skills/dungeon-master/reference/$f.md" ] && DM_REFS="$DM_REFS

===== reference/$f.md =====
$(cat "skills/dungeon-master/reference/$f.md")"
done
DM_INJECT="$DM_PERSONA

================ INLINED: skills/dungeon-master/AGENT.md ================
$(cat skills/dungeon-master/AGENT.md)

================ INLINED: skills/dungeon-master/SKILL.md ================
$(cat skills/dungeon-master/SKILL.md)

================ INLINED: dungeon-master reference docs ================$DM_REFS

================ END INLINED SKILL — you now have your full DM contract. ================"
PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"

# --- the OpenClaw agent-invocation layer ------------------------------------------------
# A single agent turn. $1=role(dm|player) $2=session-id $3=message ; echoes the reply text.
# The reply text is at .result.payloads[0].text in the --json envelope (proven).
oc_turn() {
  local role="$1" sid="$2" msg="$3" agent model out
  if [ "$role" = "dm" ]; then agent="clawdnd-qa-dm"; model="$CLAWDND_DM_MODEL";
  else agent="clawdnd-qa-player"; model="$CLAWDND_ACTOR_MODEL"; fi
  out="$T/$RUN.$role.$(date +%s%N).json"
  openclaw agent --agent "$agent" --model "$model" --session-id "$sid" --json \
    --thinking "$THINK" --timeout "$TIMEOUT" --message "$msg" > "$out" 2>> "$T/$RUN.$role.err"
  jq -r '.result.payloads[0].text // empty' "$out" 2>/dev/null
}

# A turn, with ONE retry on empty output (a transient blip shouldn't truncate the run).
oc_turn_retry() {
  local r; r="$(oc_turn "$@")"
  [ -z "$r" ] && { echo "[ocduo] empty turn ($1) — retrying once…" >&2; r="$(oc_turn "$@")"; }
  printf '%s' "$r"
}

# Transcode the NEW lines of the DM's session transcript (the cumulative <sid>.jsonl) into
# the Anthropic stream-json shape distill.py + assert_behavioral.py consume, appending to
# $COMBINED. Cursor = a line count in a file (the call runs in $(...) elsewhere, so a shell
# var would be lost — same reasoning as the move cursor). Tool names are rewritten from
# OpenClaw's "server.tool" to Anthropic's "server__tool" so the gate's `name.split("__")[-1]`
# yields the bare tool name (get_state, attack, cast_spell, …) — the whole gate hinges on this.
DM_TX_CURSOR="$STATE_OC/.dm_tx_cursor"; echo 0 > "$DM_TX_CURSOR"
transcode_dm_session() {
  local cur total
  [ -f "$DM_SESS_FILE" ] || return 0
  cur=$(cat "$DM_TX_CURSOR" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$DM_SESS_FILE" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -gt "$cur" ]; then
    tail -n +"$((cur + 1))" "$DM_SESS_FILE" 2>/dev/null \
      | python3 "$ROOT/qa/oc_transcode.py" >> "$COMBINED" 2>/dev/null
  fi
  echo "$total" > "$DM_TX_CURSOR"
}

# --- player move relay (UNCHANGED logic from run_duo.sh) --------------------------------
# The player acts ONLY through the facade (its MCP appends structured moves to $MOVES).
# Relay ONLY the NEW structured moves it made THIS turn — never its raw reply text. The
# cursor lives in a file (subshell-safe). If it called no move-tool, nudge once, then give up.
MCURSOR_FILE="$STATE_OC/.mcursor"; echo 0 > "$MCURSOR_FILE"
player_move() {
  local first="$1" prompt="$2" cur total new
  oc_turn player "$PLAYER_SID" "$prompt" >/dev/null
  cur=$(cat "$MCURSOR_FILE" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -le "$cur" ]; then
    oc_turn player "$PLAYER_SID" "You didn't act. Take your action THROUGH YOUR TOOLS now — say(...) / do(...) / request_check(...) / cast_spell(...) / use_item(...) / attack(...). Tools only, no prose." >/dev/null
    total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  fi
  new="$(tail -n +"$((cur + 1))" "$MOVES" 2>/dev/null)"
  echo "$total" > "$MCURSOR_FILE"
  [ -n "$new" ] && printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
}

# P0: the player introduces their character with a SINGLE say() — who they are + what they're
# after. They do NOT act yet (the world isn't built); the DM opens the scene next.
PMSG="$(player_move 1 "$PLAYER_BRIEF

This is the very start — the world isn't built and the scene isn't set yet. Introduce your character with a SINGLE say(\"…\"): who they are and what they want. Do NOT do()/attack/cast yet — wait for the DM to open the scene. One say(), nothing else.")"
echo "[ocduo] player intro: ${PMSG:0:120}…"
[ -z "$PMSG" ] && { echo "[ocduo] player produced no intro — aborting" >&2; exit 1; }
chatlog player "$PMSG"

# D1: DM spins up the world and opens the scene around the player's concept. The FULL skill
# injection rides on this first DM turn; --session-id carries it forward.
DMSG="$(oc_turn_retry dm "$DM_SID" "$DM_INJECT

================ BEGIN THE SESSION ================
The player agent introduces their character and opening intent:

$PMSG

Do the setup now: start_world(\"$WORLD\"), start_session, create their PC to match that concept (level 3, apply_srd_defaults, choose skills). Then OPEN the scene — human-scale and personal — grounded in the world's canon, responding to their stated intent. A companion should ENTER as part of that opening scene: someone the player MEETS on-screen (voiced, with a real wound and a reason they fall in together) — recruit_companion / load_canon_character as that meeting lands, NOT a silent name dropped into the party before the player has met anyone. End by handing the moment to the player.")"
transcode_dm_session
echo "[ocduo] DM opened: ${DMSG:0:120}…"
[ -z "$DMSG" ] && { echo "[ocduo] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }
chatlog dm "$DMSG"

# Alternate player <-> DM for BEATS rounds. Each beat is BEAT-AWARE: read clock+location at
# the START, pick the ONE moment-specific runbook, then after the DM beat run the soft
# clock-tick backstop. Identical control flow to run_duo.sh.
for b in $(seq 1 "$BEATS"); do
  PROG_PRE="$(clawdnd_read_progress "$STATE_OC")"
  PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
  PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
  PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"

  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only.")"
  echo "[ocduo] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[ocduo] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"

  RUNBOOK="$(clawdnd_runbook_for_beat "$b" "$BEATS" "$PREV_LOC" "$STATE_OC")"
  echo "[ocduo] beat $b runbook: ${RUNBOOK%% (*}…"
  DMSG="$(oc_turn_retry dm "$DM_SID" "The player does:

$PMSG

Resolve it through the engine (roll/cast/attack as needed), then PLAY the next beat as a full lived scene — NOT a fragment: any NPC (or the companion) in the scene SPEAKS at least one quoted line in their own voice; let them push back, hesitate, lie, or counter when it's real (don't just grant every ask); and weave the open moment back to the player INTO the scene — never a bare 'Your move.' / 'What do you do?' on its own line.

$RUNBOOK")"
  transcode_dm_session
  echo "[ocduo] beat $b DM: ${DMSG:0:100}…"
  [ -z "$DMSG" ] && { echo "[ocduo] DM went silent at beat $b; stopping early"; break; }
  chatlog dm "$DMSG"

  # C — soft clock-tick backstop (skips during combat; defers to DM if it advanced time).
  clawdnd_soft_tick "$ROOT" "$STATE_OC" "$PREV_DAY" "$PREV_TOD"
done

# Wrap: bring the scene to a clean stop + end_session.
oc_turn dm "$DM_SID" "We are out of time. Bring this beat to a clean stopping point and call end_session with a one-line summary." >/dev/null
transcode_dm_session
echo "[ocduo] distilling + gating…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
# The PLAYED exchange (both sides), kept for parity / for the lead's separate scoring pass.
PLAY="$T/$RUN.play.md"
jq -rs 'map((.role|ascii_upcase) + ": " + (.text // "")) | join("\n\n")' "$CHAT" > "$PLAY" 2>/dev/null
[ -s "$PLAY" ] || cp "$T/$RUN.md" "$PLAY" 2>/dev/null
# Largest NON-EMPTY snapshot under the FIXED state-oc/campaigns (the engine wrote it there).
SNAP="$(find "$STATE_OC/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ]; then cp "$SNAP" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi

# NOTE: NO claude -p scorers run here (scoring is done separately by the lead). We keep only
# the deterministic behavioral gate + the honest RED-cap of any scorecards (no-op if absent).
python3 qa/assert_behavioral.py "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  clawdnd_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
echo "[ocduo] done. behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)  snapshot=$T/$RUN.state.json  combined=$COMBINED"
exit $GATE
