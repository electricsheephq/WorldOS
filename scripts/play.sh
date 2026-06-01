#!/usr/bin/env bash
# Play WorldOS in OpenWorlds — the local viewer IS your play surface.
#
# This launches OpenWorlds (the 127.0.0.1 viewer) pointed at a fresh game and
# runs a live Dungeon Master beside it: YOU act through OpenWorlds' action palette
# (Say / Do / Continue, the dice/skill/save/combat buttons, click-to-travel) and the DM
# — Claude running this full plugin and its own `dungeon-master` skill — narrates the
# world, voices the NPCs and your companion, resolves your moves through the engine, and
# logs each beat to the chat OpenWorlds renders live. Turn by turn, in one window.
#
# It is the browser counterpart to `/world-play`: same living-world generative mode,
# but you play it in the browser instead of by typing in Claude Code. No gateway needed
# (one `claude -p` DM session + the local viewer). Open the page it opens for you,
# confirm the character the DM hands you, and play. Ctrl-C to stop.
#
# Usage: scripts/play.sh [world-id] [run-id] [port]
#   world-id   a living world to drop into (default: baldurs-gate). See `/world-list`.
#   run-id     names this game's save dir under play-state/ (default: a timestamp).
#   port       the OpenWorlds port (default: 8765 or $CLAWDND_PLAY_PORT).
#
# Safety caps (a runaway DM loop self-stops):
#   CLAWDND_PLAY_BUDGET           per-turn USD budget for one DM turn   (default 1.50)
#   CLAWDND_PLAY_SESSION_BUDGET   aggregate USD ceiling for the session (default 15.00)
#   CLAWDND_PLAY_MAX_TURNS        hard cap on DM turns                  (default 40)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
COMMON="$ROOT/scripts/launch_common.sh"
if [ -f "$COMMON" ]; then
  # shellcheck source=launch_common.sh
  . "$COMMON"
fi
if declare -F clawdnd_missing_commands >/dev/null 2>&1; then
  clawdnd_missing_commands python3 claude uv jq curl || exit 127
fi
# Shared beat-driver helpers: the C soft clock-tick backstop + the A beat-aware runbooks —
# the SAME implementation the QA duo loop sources, so the human-paced and QA loops can't drift.
# shellcheck source=../qa/lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"
WORLD="${1:-baldurs-gate}"
RUN="${2:-play-$(date +%Y%m%d-%H%M%S)}"
PORT="${3:-${CLAWDND_PLAY_PORT:-8765}}"
PORT_EXPLICIT=0
[ -n "${3:-}" ] || [ -n "${CLAWDND_PLAY_PORT:-}" ] && PORT_EXPLICIT=1
if declare -F clawdnd_choose_port >/dev/null 2>&1; then
  PORT="$(clawdnd_choose_port "$PORT" "$PORT_EXPLICIT")" || exit 1
fi
BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"                   # per DM turn
SESSION_BUDGET="${CLAWDND_PLAY_SESSION_BUDGET:-15.00}"  # aggregate ceiling for the whole session
MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-40}"              # hard turn cap (worst case = MAX_TURNS×BUDGET)
# The DM model is an env var (default sonnet) so Opus-vs-sonnet structural-adherence testing
# is a one-flag flip — mirrors qa/run_duo.sh (decision-dm-driver.md §3).
CLAWDND_DM_MODEL="$(worldos_env DM_MODEL sonnet)"
DM_TURNS=0

# --- Lean-per-beat context (PERF, default OFF → byte-identical to today). --------------
# The DM turn normally `--resume`s the DM's growing claude -p session every beat, which
# REPLAYS the whole accumulated transcript (each beat's prompt + the DM's tool calls +
# narration), so prefill grows ~6–10K tokens/beat and a late-session beat can take minutes
# (the #1 felt-latency complaint — a narrative persona rated the STORY 9/10 but GAVE UP on
# the 3–5 min/turn wait). With CLAWDND_LEAN_BEATS=1, beats 2+ instead start a FRESH session
# (a new --session-id, NO transcript carried) seeded with a re-ground directive: the DM
# re-grounds from the engine's persisted truth via scene_context (which already bundles
# state/director/events/companion_arcs + the recent player-facing narration TAIL) rather
# than from the fat transcript. Beat 1 (the cold open) is ALWAYS full — it establishes and
# persists the world/scene/PC. DEFAULT 0 ⇒ the resume path below is untouched, so this is
# fully reversible and a regression can't ship by accident. A/B harness:
# qa/lib/lean_beats_check.sh.
CLAWDND_LEAN_BEATS="${CLAWDND_LEAN_BEATS:-0}"
# Per-beat backend timeout (seconds) + ONE retry, so a wedged DM turn recovers instead of
# hanging the session. Applies in BOTH modes (it only wraps the existing claude -p call).
CLAWDND_BEAT_TIMEOUT="${CLAWDND_BEAT_TIMEOUT:-200}"
# Recent player-facing narration tail the lean re-ground asks scene_context for (generous by
# default so continuity survives the lean boundary — named NPCs, prior choices, the scene).
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"

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

# --- OPTIONAL: pre-seed a PLAYER-authored hero before the DM's first turn. ---------------
# When the Creation wizard's "Bind" runs, it passes the authored hero through the native
# bridge as CLAWDND_PLAY_HERO (a compact JSON spec). We then mint that EXACT PC via the
# engine's own tools — the same precedent play_party.sh uses to pre-seed companions — so the
# engine stays the sole writer and the hero the player authored is the hero they play. With
# CLAWDND_PLAY_HERO UNSET this whole block is skipped and the DM invents the PC live, exactly
# as before (this path is byte-unchanged). Spec fields: {name, race, class, level, abilities
# {str..cha}, background, alignment, skills[]}; race/class ids map 1:1 to engine SRD keys, and
# AbilityScores accepts the str/dex/… shorthand directly. Prints {campaign_id, pc:{id,name,
# race,class}} which we parse to re-ground the DM opener onto the existing PC.
HERO_CAMP=""; HERO_PC_ID=""; HERO_PC_NAME=""; HERO_PC_RACE=""; HERO_PC_CLASS=""
if [ -n "${CLAWDND_PLAY_HERO:-}" ]; then
  HERO_SEED_JSON="$(CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$WORLD" "$CLAWDND_PLAY_HERO" <<'PY'
import json, sys
world, spec_raw = sys.argv[1], sys.argv[2]
import server  # engine tools as plain functions (state dir from CLAWDND_STATE_DIR; cwd is the engine dir)
import imagegen  # for the 265 portrait re-key; derived cache only, never touches snapshot.json

spec = json.loads(spec_raw)
# A new campaign in this world with an active session — the player's PC is then seated into it.
camp = server.start_world(world)["campaign_id"]
server.start_session(camp, title="Authored hero")
# TWO bind variants share this pre-seed plumbing:
#   • canon PICKUP (the roster picker / "reverse character creator", the default new-game path):
#     spec={canon:true, name:"<canon NPC>"} — seat that EXACT ingested canon figure (real
#     race/class/backstory + ingested portrait) AS the player via load_canon_character. NEVER
#     invents and NEVER an origin hero (the picker only offers playable_only=true rows).
#   • custom AUTHORED hero (the Creation wizard, deferred today): the full create_character path.
if spec.get("canon"):
    canon_name = (spec.get("name") or "").strip()
    rec = server.load_canon_character(camp, canon_name, kind="player", add_to_party=True)
    if not isinstance(rec, dict) or rec.get("error"):
        # Unknown canon name — fail loudly so play.sh falls back to DM-invents-PC rather than
        # silently seating nobody (the empty stdout triggers the "pre-seed FAILED" branch below).
        sys.stderr.write("canon pickup failed: " + str((rec or {}).get("error") if isinstance(rec, dict) else rec) + "\n")
        sys.exit(1)
    # load_canon_character returns the seated character directly — {id, name, race, class, kind,
    # in_party} — with the canon figure's real identity, so the opener re-grounding text below
    # speaks the true race/class. (Equip a full combat sheet on first DM turn via
    # apply_srd_defaults, exactly as the DM-invents path does for a pickup.)
    print(json.dumps({
        "campaign_id": camp,
        "pc": {"id": rec.get("id"), "name": rec.get("name") or canon_name,
               "race": str(rec.get("race", "") or ""), "class": str(rec.get("class", "") or "")},
    }))
    sys.exit(0)
try:
    level = int(spec.get("level", 1) or 1)
except (TypeError, ValueError):
    level = 1
pc = server.create_character(
    camp,
    spec.get("name") or "Unnamed Hero",
    kind="player",
    race=spec.get("race", "") or "",
    class_name=spec.get("class", "") or "",
    level=level,
    abilities=spec.get("abilities") or None,  # {str..cha}; AbilityScores accepts the shorthand
    background=spec.get("background", "") or "",
    skills=spec.get("skills") or None,
    apply_srd_defaults=True,
)
# 265 portrait re-key: the wizard generated a unique face to a PROVISIONAL content-scope
# portrait-pc-<hash> because the PC had no engine id yet. Now that create_character minted
# the real opaque id, copy that generated face onto portrait-<char_id> so it resolves on every
# render surface camp/character/inventory/combat/table all key the face by the engine id. A
# gallery selection, or a generation that fell back to a placeholder, leaves no provisional
# descriptor, so copy_scope is a benign no-op and the canon gallery slug resolves via the
# viewer _portrait_by_name bridge. Derived-cache write only, engine stays the sole writer.
portrait = spec.get("portrait") if isinstance(spec.get("portrait"), dict) else {}
if portrait.get("mode") == "gen" and portrait.get("scope"):
    try:
        imagegen.copy_scope(str(portrait["scope"]), "portrait-" + pc["id"])
    except Exception:
        pass  # a failed re-key just falls back to the silhouette; never block the PC mint
print(json.dumps({
    "campaign_id": camp,
    "pc": {"id": pc["id"], "name": pc["name"],
           "race": spec.get("race", "") or "", "class": spec.get("class", "") or ""},
}))
PY
)"
  if [ -z "$HERO_SEED_JSON" ]; then echo "[play] hero pre-seed FAILED — see above; falling back to DM-invents-PC" >&2; else
    HERO_CAMP="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.campaign_id // ""')"
    HERO_PC_ID="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.id // ""')"
    HERO_PC_NAME="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.name // ""')"
    HERO_PC_RACE="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.race // ""')"
    HERO_PC_CLASS="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.class // ""')"
    echo "[play] seeded authored hero: $HERO_PC_NAME ($HERO_PC_RACE $HERO_PC_CLASS) in campaign $HERO_CAMP"
  fi
fi

# One DM turn (claude -p, full plugin). $1=first?(1/0) $2=message $3=campaign_id(optional).
# Session-threading modes:
#   * DEFAULT (CLAWDND_LEAN_BEATS=0): --session-id on beat 1, --resume after — the shipped
#     behavior, byte-for-byte. The DM carries its prior transcript (which is what grows
#     prefill ~6–10K tok/beat → the late-session slowdown).
#   * LEAN (CLAWDND_LEAN_BEATS=1): beat 1 is still the full --session-id cold open; beats 2+
#     start a FRESH --session-id (new uuid, NO transcript) plus an --append-system-prompt
#     re-ground directive telling the DM it has no prior transcript this turn and MUST
#     re-ground from the engine (scene_context bundles state/threads/arcs + the recent
#     narration tail) and honor every established fact/name/voice as canon it already
#     authored. $3 (campaign_id) lets the directive name the exact id to pass to
#     scene_context; on beat 1, or when $3 is empty, lean is a no-op (we mint/resume $DSID).
# A per-beat timeout (CLAWDND_BEAT_TIMEOUT) wraps the claude -p in BOTH modes; ONE retry on
# timeout/failure, then the caller's #357 fallback recovers any prose the DM streamed live.
# Echoes the DM's final text.
dm_turn() {
  local first="$1" msg="$2" campaign_id="${3:-}" out resume=() extra=() rc
  if [ "$first" != "0" ] && [ "$CLAWDND_LEAN_BEATS" = "1" ] && [ -n "$campaign_id" ]; then
    # LEAN beat: fresh session, no transcript replay. Re-ground from persisted truth.
    resume=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
    extra=(--append-system-prompt "LEAN RE-GROUND (this turn has NO prior conversation transcript — by design, to keep your turn fast). You are mid-campaign, NOT starting over. Your FIRST action this turn MUST be clawdnd-engine scene_context(campaign_id=\"$campaign_id\", recent_narration=$CLAWDND_LEAN_TAIL). That one call returns the campaign's CANON, and it is your whole memory for this beat — HONOR all of it as canon YOU already authored:
  • durable — the standing threads that persist across the campaign: open_quests (each with its still-open objectives = what the party still OWES), npc_relationships (every NPC the party has MET, with their attitude_value + attitude + relationship tags), companions (each companion's standing bond — attitude_value, has_arc, has_betrayal_agenda), factions (reputation + standing gauges), and the set flags.
  • director — the top structural debts the campaign owes right now (advisory; pay the top one as fiction, never recite it).
  • events / companion_arcs — any decisional that fired this beat, and any bond that just turned or betrayal_warning to foreshadow.
  • recent_narration — the last $CLAWDND_LEAN_TAIL player-facing beats' prose (the immediate story-so-far).
  • state — the volatile current scene, party vitals, day/time, active quests, combat, pacing_mode, seed_params.
Do NOT contradict any of it, re-introduce an already-met NPC, reset the clock, or forget a prior choice. CRUCIAL — LOSSLESS RULE: this compact bundle is the always-pinned SPINE, not the whole world. For ANYTHING the moment reaches back to that is NOT in this bundle (a fact, NPC, place, event, or lore detail from earlier), you MUST retrieve it BEFORE you narrate — the entire world/lore/history is searchable on disk: call recall(campaign_id=\"$campaign_id\", query=\"…\") for past events/decisions/facts, lookup_lore(campaign_id=\"$campaign_id\", query=\"…\") for world/setting lore, or recall_npc(campaign_id=\"$campaign_id\", npc_id=\"…\") before voicing a returning NPC. NEVER guess and NEVER invent a detail that contradicts established canon — retrieve first. (You may also pass recall_query=\"…\" to scene_context to fold a recall into the same first call.) Then resolve the move and narrate, seamlessly continuing the established story.")
  elif [ "$first" = "0" ]; then
    resume=(--resume "$DSID")
  else
    resume=(--session-id "$DSID")
  fi
  out="$DM_LOG.$(date +%s%N).jsonl"
  _dm_invoke() {
    timeout "$CLAWDND_BEAT_TIMEOUT" \
      claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
        --model "$CLAWDND_DM_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
        --output-format stream-json --verbose > "$out" 2>> "$DM_LOG.err"
  }
  _dm_invoke; rc=$?
  if [ "$rc" -ne 0 ]; then
    # timeout(1) exits 124 on the deadline; any nonzero gets ONE retry. A retried lean beat
    # mints a NEW fresh session id (never replay a half-written transcript).
    echo "[play] DM turn rc=$rc (timeout=${CLAWDND_BEAT_TIMEOUT}s) — retrying once" >&2
    if [ "$first" != "0" ] && [ "$CLAWDND_LEAN_BEATS" = "1" ] && [ -n "$campaign_id" ]; then
      resume=(--session-id "$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
    fi
    out="$DM_LOG.$(date +%s%N).jsonl"
    _dm_invoke; rc=$?
    [ "$rc" -ne 0 ] && echo "[play] DM turn retry also rc=$rc — relying on engine-logged narration" >&2
  fi
  cat "$out" >> "$COMBINED" 2>/dev/null
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
    WORLDOS_STATE_DIR="$STATE_DIR" CLAWDND_STATE_DIR="$STATE_DIR" \
    WORLDOS_VIEWER_CHAT="$CHAT" CLAWDND_VIEWER_CHAT="$CHAT" \
    WORLDOS_PLAYER_MOVES="$MOVES" CLAWDND_PLAYER_MOVES="$MOVES" \
      python3 viewer/server.py "" "$PORT" >> "$VIEWER_LOG" 2>&1 &
    local vp=$!; echo "$vp" > "$VPID_FILE"
    wait "$vp" 2>/dev/null   # blocks until the viewer exits (and reaps it)
    sleep 1                  # campaign not ready yet → brief pause, then relaunch
  done
}
viewer_supervisor &  SUP=$!
# On any exit: kill the supervisor (so it can't respawn) and the live viewer it tracks. CRITICAL:
# INT/TERM must CLEAN UP **and EXIT** — a bare `trap … TERM` runs the handler and then RESUMES the
# main loop, so closing the app / `kill` couldn't stop a wedged run (it took kill -9, and the human-
# paced loop below has no idle ceiling, so it would spin `sleep 2` forever). Separate the EXIT trap
# (cleanup) from the signal traps (cleanup + exit) so a normal `kill` actually stops it. (Mirrors
# play_party.sh.)
_play_cleanup() { kill "$SUP" 2>/dev/null; [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null; }
trap _play_cleanup EXIT
trap '_play_cleanup; exit 130' INT TERM

# Open the browser once OpenWorlds is actually serving (after the campaign exists).
( for _ in $(seq 1 60); do
    curl -s --max-time 2 "http://127.0.0.1:$PORT/state" >/dev/null 2>&1 && break
    sleep 1
  done
  (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/openworlds/") \
    || (command -v xdg-open >/dev/null 2>&1 && xdg-open "http://127.0.0.1:$PORT/openworlds/") || true ) &

echo "ClawDnD — playing in OpenWorlds → http://127.0.0.1:$PORT/openworlds/"
echo "  Opening the world… OpenWorlds fills in as the DM narrates the first scene."
echo "  Act via the palette (Say / Do / Continue, dice & combat, click-to-travel). Ctrl-C to stop."
echo "  Save dir: $STATE_DIR"

# The DM opens the world live and hands you a character + an open moment. This relies on
# the SHIPPED dungeon-master skill (its "Generating a world live" mode) — not a QA brief.
# TWO openers: (1) the DEFAULT, where the DM invents the PC live (unchanged); (2) when the
# player AUTHORED a hero in the Creation wizard, the campaign + PC ALREADY EXIST (pre-seeded
# above), so the DM must NOT start_world and must NOT create a character — it re-grounds via
# get_state and opens a scene around the EXISTING authored PC.
if [ -n "$HERO_CAMP" ]; then
  DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo ClawDnD adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

Begin a SOLO session in a living world for a single human player who will act through the dashboard. The player AUTHORED their own character in the Creation wizard, so the world AND the player's character ALREADY EXIST — they were pre-seeded for you:
- This session's campaign ALREADY EXISTS: use campaign_id=$HERO_CAMP for EVERY engine call. DO NOT call start_world — it would mint a NEW campaign id and ORPHAN the pre-seeded PC.
- The player's character ALREADY EXISTS: \"$HERO_PC_NAME\" (a $HERO_PC_RACE $HERO_PC_CLASS), id=$HERO_PC_ID, already in the party with a full sheet. DO NOT create a character and DO NOT reroll their stats — this is the hero the player built.
- call get_state(\"$HERO_CAMP\") FIRST to read the world bible (premise, era/chronology, tone, standing threads, seeded regions/factions/roster) AND the existing party so you know who $HERO_PC_NAME is.
- start_session only if get_state shows no active session.
- Open a human-scale, personal scene grounded in the world's canon, built around the EXISTING player character $HERO_PC_NAME, with real quoted dialogue, and hand the player an open moment + a clear, real choice.
- This is a SOLO session: the player begins ALONE. Do NOT recruit a companion at cold-open and do NOT seat anyone but the player in the party. A roster legend may APPEAR in the opening scene as a face in the world (voiced, with a real wound), but they are MET, not recruited — companions join the party LATER, in play, only when the player chooses to bring them along and the meeting has earned it. The party at the end of this opening turn is the player alone.

CRITICAL — your FINAL output THIS turn MUST BE the opening SCENE itself, written as 2nd-person player-facing prose (addressed to \"you\"): where $HERO_PC_NAME IS, what they see/hear/smell, who is present and a real quoted line from them, ending on a clear open moment + choice. The player reads ONLY your final reply text as the scene — so the opening prose MUST be IN it. Re-ground via the tools FIRST, then CLOSE the turn by writing the scene. NEVER end this turn on a tool call, and NEVER let your reply be a 3rd-person setup brief or game-system notation (e.g. \"COLD OPEN — ARRIVAL: $HERO_PC_NAME (tiefling wizard, PC) walks toward…\") — that is your private scratchpad, not the player's scene. If you logged a setup note via log_event, you must STILL write the 2nd-person scene as your reply text.

Their actions will arrive as tagged moves — [say] (their dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — one per turn from the dashboard.")"
else
  DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo ClawDnD adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

Begin a SOLO session in a living world for a single human player who will act through the dashboard:
- start_world(\"$WORLD\") and read the returned bible (premise, era/chronology, tone, standing threads, seeded regions/factions/roster). If it returns existing_campaigns, start fresh.
- start_session (for continuity and the recap).
- Choose the player's hero by SELECTING a real canon NPC — NEVER invent a custom character. Use list_canon_characters(playable_only=true) (the 7 BG3 origin heroes are excluded), pick a fitting MID-TIER canon figure who has an ingested portrait + real backstory (a Harper agent, a Flaming Fist officer, a Guild operative, a hedge-wizard), then load_canon_character(that name, kind=\"player\", add_to_party=true) to seat them as the PC and tell the player who they are. (Custom character creation is a separate wizard flow — never invent a portrait-less PC here.)
- Open a human-scale, personal scene grounded in the world's canon, with real quoted dialogue, and hand the player an open moment + a clear, real choice.
- This is a SOLO session: the player begins ALONE. Do NOT recruit a companion at cold-open and do NOT seat anyone but the player in the party. A roster legend may APPEAR in the opening scene as a face in the world (voiced, with a real wound), but they are MET, not recruited — companions join the party LATER, in play, only when the player chooses to bring them along and the meeting has earned it. The party at the end of this opening turn is the player alone.

CRITICAL — your FINAL output THIS turn MUST BE the opening SCENE itself, written as 2nd-person player-facing prose (addressed to \"you\"): where the player IS, what they see/hear/smell, who is present and a real quoted line from them, ending on a clear open moment + choice. The player reads ONLY your final reply text as the scene — so the opening prose MUST be IN it. Do your world/character/log setup with the tools FIRST, then CLOSE the turn by writing the scene. NEVER end this turn on a tool call, and NEVER let your reply be a 3rd-person setup brief or game-system notation (e.g. \"COLD OPEN — ARRIVAL: <Name> (tiefling wizard, PC) walks toward…\") — that is your private scratchpad, not the player's scene. If you logged a setup note via log_event, you must STILL write the 2nd-person scene as your reply text.

Their actions will arrive as tagged moves — [say] (their dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — one per turn from the dashboard.")"
fi
# #357: same empty-reply fallback as the beat loop — recover the engine's logged opening
# narration if the DM's first turn ended on a tool call rather than prose.
DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
chatlog dm "$DMSG"; DM_TURNS=1

# Resolve the campaign id the DM just minted (for the lean re-ground; harmless when
# CLAWDND_LEAN_BEATS=0). The opening beat called start_world/get_state, which writes the
# snapshot to $STATE_DIR/campaigns/<id>/snapshot.json — read the id back from that dir.
# A solo launch uses a brand-new state dir, so there is exactly one campaign here. When a
# hero was pre-seeded we already know it ($HERO_CAMP). Empty ⇒ lean falls back to the
# normal --resume path (dm_turn no-ops lean when the id is unknown).
CAMPAIGN_ID="$HERO_CAMP"
if [ -z "$CAMPAIGN_ID" ] && [ -d "$STATE_DIR/campaigns" ]; then
  CAMPAIGN_ID="$(find "$STATE_DIR/campaigns" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | head -n1)"
fi
if [ "$CLAWDND_LEAN_BEATS" = "1" ]; then
  if [ -n "$CAMPAIGN_ID" ]; then
    echo "[play] lean-beats ON — beats 2+ re-ground via scene_context (campaign=$CAMPAIGN_ID), no transcript replay"
  else
    echo "[play] lean-beats ON but campaign id not found under $STATE_DIR/campaigns — beats use the normal resume path" >&2
  fi
fi

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
    # The dashboard palette sends {kind,name}; Say/Do send {kind,text}. The Seed screen sends
    # {kind:"set_seed_param",param,value[,force]} (#266) — render it as a config directive
    # the DM applies via the engine's set_seed_param tool (it has no text/name body).
    PMSG="$(printf '%s' "$new" | jq -rs 'map(if .kind == "set_seed_param" then "[set_seed_param] \(.param)=\(.value)\(if .force then " (force)" else "" end)" else "[\(.kind)] \(.text // .name // "")" end) | join("  ")' 2>/dev/null)"
    [ -z "$PMSG" ] && continue
    echo "[play] you: ${PMSG:0:100}"
    chatlog player "$PMSG"
    # Beat-aware (decision §A): the "beat" is each resolved player move; the session's beat
    # budget is MAX_TURNS, so the midpoint/climax windows scale to the play cap. Read the clock
    # + location BEFORE the DM turn (for the runbook + the soft tick), pick the ONE runbook for
    # this beat, then run the soft clock-tick backstop after.
    PROG_PRE="$(clawdnd_read_progress "$STATE_DIR")"
    PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
    PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
    PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"
    BEAT_NO=$((DM_TURNS + 1))
    RUNBOOK="$(clawdnd_runbook_for_beat "$BEAT_NO" "$MAX_TURNS" "$PREV_LOC" "$STATE_DIR")"
    echo "[play] beat $BEAT_NO runbook: ${RUNBOOK%% (*}…"
    DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice the NPCs and companion) and narrate the next beat as a played scene. Hand the moment back to the player. ALWAYS end your turn on 2nd-person player-facing narration (addressed to \"you\"), never on a tool call or a 3rd-person status line — the player reads your final reply text as the scene, so the beat's prose MUST be in it. If a move is tagged [set_seed_param] param=value, that is a World-Seed dial the player changed from the Seed screen — apply it with the engine's set_seed_param(campaign_id, param, value[, force=True]) tool (it returns applied/warning), then briefly acknowledge it in-world rather than treating it as an in-scene action.

$RUNBOOK" "$CAMPAIGN_ID")"
    # #357: if the DM turn ended on a tool call / 3rd-person status line, its final reply text is
    # empty — fall back to the player-facing narration the engine logged this beat so the chat is
    # never blank on a resolved move (engine stays the sole writer; this only READS its log).
    DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
    chatlog dm "$DMSG"; DM_TURNS=$((DM_TURNS + 1))
    # C — soft clock-tick backstop: advance one phase via the engine only if the DM left the
    # clock frozen this beat (engine stays the sole writer; defers to the DM's in-fiction pacing).
    clawdnd_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
  else
    sleep 2
  fi
done
