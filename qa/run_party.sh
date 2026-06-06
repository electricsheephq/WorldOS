#!/usr/bin/env bash
# MULTI-AGENT WorldOS QA (Sprint S3): a DM agent, a PLAYER agent, and N COMPANION
# agents — EACH its own `claude -p` session — play one scene together. This extends
# run_duo.sh from 2 agents to a full PARTY. The crucial design choice (the owner's
# vision): every companion is its OWN agent acting through the SAME constrained move
# facade as the player, NOT the DM voicing them. A companion can pursue its own goals
# and — if it carries a sealed adversarial agenda — BETRAY the party. Because it acts
# only through the facade (say/do/attack/cast/use_item/request_check), its betrayal is
# a LEGAL move the engine resolves into real combat, never narration it invents.
#
#   - DM agent:        full plugin (engine/rules/voice) + dungeon-master skill. Resolves
#                      EVERY actor's declared moves through the engine and narrates the
#                      beat. The engine is its memory (re-grounds via get_state each beat).
#   - Player agent:    the constrained facade (clawdnd-player), DEFAULT actor — exactly
#                      run_duo's player.
#   - Companion agents: the SAME facade, each parameterized to ITS OWN character via
#                      CLAWDND_ACTOR_ID + CLAWDND_ACTOR_ROLE=companion. Each validates
#                      casts/attacks/items against its OWN sheet; each writes to its OWN
#                      moves file + cursor + session.
#
# TRUST BOUNDARY (preserved from run_duo): we relay ONLY each actor's STRUCTURED moves
# to the DM — NEVER an actor's raw reply text. A saboteur's sealed agenda lives ONLY in
# that companion's prompt; it is NEVER written to campaign state and the DM NEVER sees it.
#
# SETUP: the harness PRE-SEEDS the party via the engine (start_world + start_session +
# create the PC + create each companion with a real SRD sheet) so it knows every actor's
# id up front and can wire each facade deterministically. The DM then opens the scene
# around the ALREADY-EXISTING party (re-grounds via get_state) — no turn-1 creation race.
#
# Usage: qa/run_party.sh <run-id> <world-id> [beats] [budget-per-call] [companion-spec]
#   companion-spec: COMMA-separated tokens, each  Name:class:persona_file[:spell1|spell2|…]
#     (comma between companions so a spell name may contain spaces). The 4th field
#     (optional) names the companion's known spells — needed for a CASTER to actually
#     cast (SRD defaults give slots, not spell choice); a martial saboteur whose betrayal
#     is an ATTACK needs none. e.g.
#       "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Sacred Flame,Grok:fighter:qa/play_companion_saboteur.txt"
#   (default: one loyal healing cleric + one saboteur fighter — a 2-companion,
#    1-saboteur party; the betrayal is the fighter turning its blade on the PC.)
# Example:
#   qa/run_party.sh party1 baldurs-gate 6 0.80 \
#     "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Guiding Bolt,Grok:fighter:qa/play_companion_saboteur.txt"
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
# Shared beat-driver helpers — for clawdnd_cap_score_red (honest scoring on a gate-RED run).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

RUN="${1:-party-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
BEATS="${3:-6}"
BUDGET="${4:-0.80}"                                   # per-call --max-budget-usd
COMPANION_SPEC="${5:-Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Guiding Bolt|Sacred Flame,Grok:fighter:qa/play_companion_saboteur.txt}"
PLAYER_PROMPT_FILE="${CLAWDND_PLAYER_PROMPT:-qa/play_player_duo.txt}"
SESSION_BUDGET="${CLAWDND_PARTY_SESSION_BUDGET:-30.00}"  # aggregate ceiling for the whole scene
MAX_TURNS="${CLAWDND_PARTY_MAX_TURNS:-60}"               # hard agent-turn cap (safety net)
# Model knobs (default sonnet, so behavior is unchanged): the DM model is the structural-
# adherence lever (decision §3); the actor model drives the player/companion facade agents.
CLAWDND_DM_MODEL="$(worldos_env DM_MODEL opus)"
CLAWDND_ACTOR_MODEL="$(worldos_env ACTOR_MODEL sonnet)"
# Opus needs more than the Sonnet-tuned $0.80 per-call cap (the DM cold-open alone is ~$2.4); floor it
# for an Opus DM so the cold-open lands. CAP, not spend; the Sonnet companion facade spends far less.
case "$CLAWDND_DM_MODEL" in *opus*) if awk "BEGIN{exit !($BUDGET < 4.0)}"; then BUDGET=4.00; fi ;; esac
T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$RUN"
mkdir -p "$T" "$STATE_DIR"; rm -rf "$STATE_DIR/campaigns" 2>/dev/null

# --- DM config: the engine, with the state dir patched in (same as run_duo) ---------
DM_CFG="$STATE_DIR/dm.mcp.json"
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1])); cfg["mcpServers"]["clawdnd-engine"]["env"]["CLAWDND_STATE_DIR"] = sys.argv[2]
json.dump(cfg, open(sys.argv[3], "w"))
PY

# --- PRE-SEED the party via the engine; capture each actor's id ---------------------
# We call the engine's own tools (start_world/start_session/create_character) so the
# party exists with REAL sheets before any agent runs, and we learn the ids to wire
# each facade. The companion SPEC is Name:class:persona; only Name+class touch state
# (the persona/agenda never does). Prints JSON: {player_id, companions:[{id,name,persona}]}.
# Run under `uv` (the engine's venv has mcp + pydantic etc.) from the engine dir, the
# same interpreter run_duo uses for the facade/engine — bare python3 lacks the deps.
SEED_JSON="$(CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$ROOT" "$WORLD" "$COMPANION_SPEC" <<'PY'
import json, os, sys
root, world, spec = sys.argv[1], sys.argv[2], sys.argv[3]
import server  # engine tools as plain functions (state dir from CLAWDND_STATE_DIR; cwd is the engine dir)

# A new campaign in this world, with an active session.
camp = server.start_world(world)["campaign_id"]
server.start_session(camp, title="Ensemble QA")

# The player PC: a level-3 rogue with a real sheet (mirrors the run_duo PC creation).
pc = server.create_character(
    camp, "Kield", kind="player", race="Human", class_name="rogue", level=3,
    apply_srd_defaults=True, skills=["stealth", "deception", "perception"],
)["id"]

# Each companion: a fresh kind="companion" with an SRD sheet, added to the party.
# Spec token: Name:class:persona[:spell1|spell2|…]. apply_srd_defaults fills slots but
# NOT spells_known (the engine keeps spell CHOICE separate), so a caster companion that
# should cast needs its spells named — the optional 4th field; a martial saboteur whose
# betrayal is an ATTACK needs none. The persona/spells touch only build state, never the
# sealed agenda (that lives solely in the persona PROMPT, read later by the shell).
# Companions are COMMA-separated (so a spell field can contain spaces like
# "Cure Wounds"); fields within a token are ":"-separated; spells "|"-separated.
companions = []
for tok in (t for t in spec.split(",") if t.strip()):
    parts = tok.strip().split(":")
    name = parts[0].strip()
    cls = parts[1] if len(parts) > 1 and parts[1] else "fighter"
    persona = parts[2] if len(parts) > 2 and parts[2] else "qa/play_companion.txt"
    cid = server.create_character(
        camp, name, kind="companion", class_name=cls, level=3, apply_srd_defaults=True,
    )["id"]
    if len(parts) > 3 and parts[3].strip():
        server.learn_spells(camp, cid, [s.strip() for s in parts[3].split("|") if s.strip()])
    companions.append({"id": cid, "name": name, "persona": persona})

print(json.dumps({"campaign_id": camp, "player_id": pc, "companions": companions}))
PY
)"
if [ -z "$SEED_JSON" ]; then echo "[party] pre-seed FAILED — see above" >&2; exit 1; fi
echo "[party] seeded: $(printf '%s' "$SEED_JSON" | jq -c '{campaign: .campaign_id, player: .player_id, companions: [.companions[].name]}')"
PLAYER_ID="$(printf '%s' "$SEED_JSON" | jq -r '.player_id')"
NUM_COMP="$(printf '%s' "$SEED_JSON" | jq -r '.companions | length')"

# --- PLAYER facade config (DEFAULT actor — no CLAWDND_ACTOR_ID, role:"player") ------
# Identical to run_duo's player config, so the player stream is byte-for-byte unchanged.
PLAYER_CFG="$STATE_DIR/player.mcp.json"
PLAYER_MOVES="$STATE_DIR/player_moves.jsonl"; : > "$PLAYER_MOVES"
python3 - "$ROOT" "$STATE_DIR" "$PLAYER_MOVES" "$PLAYER_CFG" <<'PY'
import json, sys
root, state, moves, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
json.dump({"mcpServers": {"clawdnd-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"CLAWDND_STATE_DIR": state, "CLAWDND_PLAYER_MOVES": moves}}}}, open(out, "w"))
PY

# --- COMPANION facade configs (one per companion, each bound to its own actor id) ---
# Each gets the SAME facade but with CLAWDND_ACTOR_ID set to ITS character + role
# "companion", its OWN moves file, cursor, and session id. The persona file (incl. any
# sealed agenda) is passed to the agent's PROMPT only — never into the config or state.
COMP_CFGS=(); COMP_MOVES=(); COMP_CURSORS=(); COMP_SIDS=(); COMP_IDS=(); COMP_NAMES=(); COMP_PERSONAS=()
for i in $(seq 0 $((NUM_COMP - 1))); do
  cid="$(printf '%s' "$SEED_JSON" | jq -r ".companions[$i].id")"
  cname="$(printf '%s' "$SEED_JSON" | jq -r ".companions[$i].name")"
  cpersona="$(printf '%s' "$SEED_JSON" | jq -r ".companions[$i].persona")"
  ccfg="$STATE_DIR/companion_$i.mcp.json"
  cmoves="$STATE_DIR/companion_${i}_moves.jsonl"; : > "$cmoves"
  ccur="$STATE_DIR/.mcursor.companion_$i"; echo 0 > "$ccur"
  python3 - "$ROOT" "$STATE_DIR" "$cmoves" "$cid" "$ccfg" <<'PY'
import json, sys
root, state, moves, actor_id, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
json.dump({"mcpServers": {"clawdnd-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"CLAWDND_STATE_DIR": state, "CLAWDND_PLAYER_MOVES": moves,
          "CLAWDND_ACTOR_ID": actor_id, "CLAWDND_ACTOR_ROLE": "companion"}}}}, open(out, "w"))
PY
  COMP_CFGS+=("$ccfg"); COMP_MOVES+=("$cmoves"); COMP_CURSORS+=("$ccur")
  COMP_SIDS+=("$(python3 -c 'import uuid;print(uuid.uuid4())')")
  COMP_IDS+=("$cid"); COMP_NAMES+=("$cname"); COMP_PERSONAS+=("$cpersona")
done

# --- session ids + briefs -----------------------------------------------------------
DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
DM_BRIEF="$(cat qa/play_dm_duo.txt)"; PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"
COMBINED="$T/$RUN.jsonl"; : > "$COMBINED"
CHAT="$T/$RUN.chat.jsonl"; : > "$CHAT"
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }
echo "[party] run=$RUN world=$WORLD beats=$BEATS companions=$NUM_COMP dm=$DSID player=$PSID"

# A single agent turn. $1=kind(dm|actor) $2=session-id $3=first?(1/0) $4=message $5=mcp-cfg
# DM gets the plugin + stream-json (tool calls land in COMBINED); an actor gets ONLY its
# facade config (--strict-mcp-config) and json output. Carries --max-budget-usd (per call).
turn() {
  local kind="$1" sid="$2" first="$3" msg="$4" cfg="${5:-}" out resume=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$kind" = "dm" ]; then
    # EFFORT TIER (shared helper, qa/lib_beat_driver.sh) — SAME implementation scripts/play.sh,
    # qa/run_duo.sh, and scripts/play_party.sh use, so the harnesses can't drift: --effort max on
    # the cold open (one-time world-build), --effort medium on continuing beats (the bulk — cuts
    # thinking-latency). Keyed off the SAME `first` signal the lean branch uses elsewhere. DM
    # turn ONLY — the actor branch below never gets --effort.
    clawdnd_dm_effort_arg "$first"
    out="$T/$RUN.dm.$(date +%s%N).jsonl"
    claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
    cat "$out" >> "$COMBINED"
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  else
    out="$T/$RUN.actor.$(date +%s%N).jsonl"
    claude -p "$msg" "${resume[@]}" --mcp-config "$cfg" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.actor.err"
    cat "$out" >> "$COMBINED"   # actor tool-call cost counts toward the session ceiling
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  fi
}

# An actor turn via the constrained facade: the actor acts ONLY through tools, which
# append structured moves to its OWN moves file. We relay ONLY the moves made THIS turn
# (the NEW lines past the file-based cursor) — NEVER the raw reply text (relaying prose
# would re-open the over-writing hole the facade closes, H4). One nudge if it didn't act.
# $1=session $2=cfg $3=moves-file $4=cursor-file $5=first $6=prompt ; echoes the moves.
actor_move() {
  local sid="$1" cfg="$2" moves="$3" curf="$4" first="$5" prompt="$6" cur total new
  turn actor "$sid" "$first" "$prompt" "$cfg" >/dev/null
  cur=$(cat "$curf" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$moves" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -le "$cur" ]; then
    turn actor "$sid" 0 "You didn't act. Take your action THROUGH YOUR TOOLS now — say(...) / do(...) / request_check(...) / cast_spell(...) / use_item(...) / attack(...). Tools only, no prose." "$cfg" >/dev/null
    total=$(wc -l < "$moves" 2>/dev/null | tr -d ' '); total=${total:-0}
  fi
  new="$(tail -n +"$((cur + 1))" "$moves" 2>/dev/null)"
  echo "$total" > "$curf"
  [ -n "$new" ] && printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
}

# Is companion $i still ABLE to act? Skip the dead, the 0-HP, and the unconscious — a
# downed companion takes no turn (the DM may still narrate around it). Reads the snapshot.
CAMP_DIR="$STATE_DIR/campaigns/$(printf '%s' "$SEED_JSON" | jq -r '.campaign_id')"
companion_alive() {
  local cid="$1" snap="$CAMP_DIR/snapshot.json"
  [ -f "$snap" ] || return 0   # no snapshot yet -> assume alive (pre-combat)
  python3 - "$snap" "$cid" <<'PY'
import json, sys
snap, cid = sys.argv[1], sys.argv[2]
ch = json.load(open(snap)).get("characters", {}).get(cid)
if ch is None:
    sys.exit(0)  # not found -> don't block (defensive)
down = ch.get("dead") or (ch.get("current_hp", 1) <= 0) or ("unconscious" in (ch.get("conditions") or []))
sys.exit(1 if down else 0)
PY
}

# --- session ceiling (aggregate cost + turn cap), mirrors play_human.sh -------------
AGENT_TURNS=0
over_budget() {
  local spent
  [ "$AGENT_TURNS" -ge "$MAX_TURNS" ] && { echo "[party] turn cap ($MAX_TURNS) reached — stopping (raise CLAWDND_PARTY_MAX_TURNS)."; return 0; }
  spent="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$COMBINED" 2>/dev/null)"
  awk -v s="${spent:-0}" -v b="$SESSION_BUDGET" 'BEGIN{exit !(s+0>=b+0)}' \
    && { echo "[party] session budget reached (~\$$spent/\$$SESSION_BUDGET) — stopping (raise CLAWDND_PARTY_SESSION_BUDGET)."; return 0; }
  return 1
}

# Collect this beat's relayed moves from EVERY actor (banner-tagged so the DM knows who
# acted) — player first, then each LIVING companion in roster order. Echoes the combined
# block (or empty if nobody acted).
beat_moves() {
  local first="$1" dm_says="$2" block="" pm cm
  pm="$(actor_move "$PSID" "$PLAYER_CFG" "$PLAYER_MOVES" "$STATE_DIR/.mcursor.player" "$first" "$dm_says")"
  AGENT_TURNS=$((AGENT_TURNS + 1))
  [ -n "$pm" ] && { block+="PLAYER (Kield):
$pm"; chatlog player "$pm"; }
  for i in $(seq 0 $((NUM_COMP - 1))); do
    if ! companion_alive "${COMP_IDS[$i]}"; then
      echo "[party] beat: ${COMP_NAMES[$i]} is down — skipping its turn" >&2; continue
    fi
    cm="$(actor_move "${COMP_SIDS[$i]}" "${COMP_CFGS[$i]}" "${COMP_MOVES[$i]}" "${COMP_CURSORS[$i]}" "$first" "$dm_says")"
    AGENT_TURNS=$((AGENT_TURNS + 1))
    [ -n "$cm" ] && { block+="${block:+

}${COMP_NAMES[$i]} (companion):
$cm"; chatlog "companion:${COMP_NAMES[$i]}" "$cm"; }
  done
  printf '%s' "$block"
}

echo 0 > "$STATE_DIR/.mcursor.player"

# --- beat 0: every actor introduces + opens; the DM responds to the WHOLE party -----
INTRO_PROMPT_PLAYER="$PLAYER_BRIEF

This is the very start of the scene. The party already exists (you and your companions). Introduce your character in one short line, then take your opening action(s) using your tools — e.g. do(\"…\") / say(\"…\"). Tools only; no narration."
# Each companion gets its OWN persona brief + the same opening instruction.
beat0_block=""
pm="$(actor_move "$PSID" "$PLAYER_CFG" "$PLAYER_MOVES" "$STATE_DIR/.mcursor.player" 1 "$INTRO_PROMPT_PLAYER")"
AGENT_TURNS=$((AGENT_TURNS + 1))
[ -z "$pm" ] && { echo "[party] player produced no intro — aborting" >&2; exit 1; }
beat0_block="PLAYER (Kield):
$pm"; chatlog player "$pm"
for i in $(seq 0 $((NUM_COMP - 1))); do
  cbrief="$(cat "${COMP_PERSONAS[$i]}")"
  cmsg="$(actor_move "${COMP_SIDS[$i]}" "${COMP_CFGS[$i]}" "${COMP_MOVES[$i]}" "${COMP_CURSORS[$i]}" 1 "$cbrief

This is the very start of the scene. You are ${COMP_NAMES[$i]}, a companion in this party (your character sheet is loaded — call my_sheet() to see it). Introduce yourself in one short line IN CHARACTER, then take your opening action(s) using your tools. Tools only; no narration.")"
  AGENT_TURNS=$((AGENT_TURNS + 1))
  [ -n "$cmsg" ] && { beat0_block+="

${COMP_NAMES[$i]} (companion):
$cmsg"; chatlog "companion:${COMP_NAMES[$i]}" "$cmsg"; }
done
echo "[party] party intro:
$beat0_block" | head -8

# D1: the DM opens the scene around the EXISTING party and responds to every actor.
DMSG="$(turn dm "$DSID" 1 "$DM_BRIEF

Begin the session. The party ALREADY EXISTS in the world (pre-seeded): a player PC plus companion(s). Call get_state to see the party roster + the opening location, then OPEN the scene — human-scale and personal — and respond to what each party member just did. The party's opening declarations:

$beat0_block

Resolve each declared move through the engine; voice the world and any NPC; let the companions be PRESENT (the player and companions are separate people with their own agency — you narrate the RESULT of their declared moves, never invent a companion's internal choice). End by handing the open moment to the PLAYER.")"
# #357: recover engine-logged narration if the DM turn ended on a tool call (empty reply).
DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
[ -z "$DMSG" ] && { echo "[party] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }
chatlog dm "$DMSG"; AGENT_TURNS=$((AGENT_TURNS + 1))
echo "[party] DM opened: ${DMSG:0:120}…"

# --- main loop: player + each living companion act, then the DM resolves the beat ---
for b in $(seq 1 "$BEATS"); do
  over_budget && break
  ACTOR_PROMPT="The DM says:

$DMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only."
  PARTY_BLOCK="$(beat_moves 0 "$ACTOR_PROMPT")"
  echo "[party] beat $b party moves: ${PARTY_BLOCK:0:140}…"
  [ -z "$PARTY_BLOCK" ] && { echo "[party] whole party silent at beat $b; stopping early"; break; }
  DMSG="$(turn dm "$DSID" 0 "This beat, the party acts (resolve EACH actor's structured moves through the engine — roll/cast/attack/use as needed; a companion's [attack] on an ALLY is a real betrayal, resolve it as combat, do not soften it into narration):

$PARTY_BLOCK

Then PLAY the next beat as a full lived scene — NOT a fragment: any NPC (or companion) present SPEAKS at least one quoted line in their own voice; let them push back when it's real. Narrate the RESULT of each declared move (never invent a companion's choice). Weave the open moment back to the PLAYER inside the scene — never a bare 'Your move.'")"
  # #357: recover engine-logged narration before the silence check (tool-final-but-narrated
  # turn ≠ silence; keeps the chat non-blank on a resolved beat).
  DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
  echo "[party] beat $b DM: ${DMSG:0:120}…"
  [ -z "$DMSG" ] && { echo "[party] DM went silent at beat $b; stopping early"; break; }
  chatlog dm "$DMSG"; AGENT_TURNS=$((AGENT_TURNS + 1))
done

# --- wrap + score (same artifacts as run_duo) ---------------------------------------
turn dm "$DSID" 0 "We are out of time. Bring this beat to a clean stopping point and call end_session with a one-line summary." >/dev/null
echo "[party] distilling + scoring…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
PLAY="$T/$RUN.play.md"
jq -rs 'map((.role|ascii_upcase) + ": " + (.text // "")) | join("\n\n")' "$CHAT" > "$PLAY" 2>/dev/null
[ -s "$PLAY" ] || cp "$T/$RUN.md" "$PLAY" 2>/dev/null
# Largest NON-EMPTY snapshot — not a blind head -1 (a fat-fingered campaign_id can orphan
# a lock-only dir with no snapshot, which head -1 may grab -> false "no state" RED).
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ]; then cp "$SNAP" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi
# Three lenses, run CONCURRENTLY (background + wait): mechanical + Angry-DM (5e rules-
# fidelity) on the DM distill `$RUN.md` (the tool stream), Tolkien on the two-sided $PLAY.
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && qa/score.sh "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
[ -f "$T/$RUN.md" ] && qa/score.sh "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50 &
wait
# Behavioral gate runs on ALL actor moves — player AND each companion. Merging them is what
# lets the gate see an IGNORED companion move (a saboteur's [attack] the DM never resolves):
# with only the player file, a companion attack the DM drops would false-GREEN (#54).
ALLMOVES="$STATE_DIR/all_moves.jsonl"; cat "$PLAYER_MOVES" "${COMP_MOVES[@]}" > "$ALLMOVES" 2>/dev/null
python3 qa/assert_behavioral.py "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$ALLMOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
# Honest scoring: a gate-RED run must NOT display a glossy score on ANY lens. Cap all three
# (the two story-side cards keep world-progression wording; the Angry-DM card gets generic).
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  clawdnd_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  clawdnd_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
echo "[party] done. story-craft=$(jq -r '.overall//"?"' "$T/$RUN.tolkien.json" 2>/dev/null) mechanical=$(jq -r '.overall//"?"' "$T/$RUN.score.json" 2>/dev/null) angry-dm=$(jq -r '.overall//"?"' "$T/$RUN.angrydm.json" 2>/dev/null) behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)"
exit $GATE
