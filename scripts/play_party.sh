#!/usr/bin/env bash
# Play WorldOS in OpenWorlds WITH AI companions — the human plays in the browser
# while a party of AI companion agents adventures alongside, each its own `claude -p`.
#
# This is the PARTY counterpart to scripts/play.sh. play.sh launches OpenWorlds +
# a solo human-vs-DM loop (you act through the palette, the DM responds). This script
# adds the proven multi-agent ENSEMBLE from qa/run_party.sh on top of that same human
# play surface: when you name companions, each becomes its OWN agent acting through the
# SAME constrained move facade as you do (NOT the DM voicing them), parameterized to its
# own character via CLAWDND_ACTOR_ID + CLAWDND_ACTOR_ROLE=companion. Every beat the human's
# dashboard move AND each living companion's structured moves are relayed to the DM, who
# resolves them all through the engine and narrates the next beat in the chat — so the
# OpenWorlds shows YOU + your companions + the DM, turn by turn, live.
#
# TRUST BOUNDARY (lifted from run_party.sh): we relay ONLY each actor's STRUCTURED moves
# to the DM — NEVER an actor's raw reply text. A companion acts only through the facade
# (say/do/attack/cast/use_item/request_check), so even a betrayal is a LEGAL move the
# engine resolves into real combat, never narration it invents. A sealed agenda lives
# ONLY in that companion's prompt; it is NEVER written to campaign state and the DM never
# sees it.
#
# SOLO IS UNCHANGED. With NO companion spec, this is byte-for-byte today's solo play:
# it execs scripts/play.sh with your args and adds nothing. The ensemble machinery only
# engages when you opt in with companions (which multiply `claude -p` cost — hence opt-in).
#
# Usage: scripts/play_party.sh [world-id] [run-id] [port] [companion-spec]
#   world-id   a living world to drop into (default: baldurs-gate). See `/world-list`.
#   run-id     names this game's save dir under play-state/ (default: a timestamp).
#   port       the OpenWorlds port (default: 8765 or $CLAWDND_PLAY_PORT).
#   companion-spec  COMMA-separated tokens, each  Name:class:persona_file[:spell1|spell2|…]
#                   (same grammar as run_party.sh). The optional 4th field names a caster's
#                   known spells (SRD defaults give slots, not spell CHOICE). May also be
#                   set via $CLAWDND_PLAY_COMPANIONS (the positional arg wins if both set).
#                   e.g. "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Sacred Flame"
#                        ",Brogan:fighter:qa/play_companion.txt"
#   Default (no spec, no env) = solo play.sh, EXACTLY.
# Examples:
#   scripts/play_party.sh                              # solo (== scripts/play.sh)
#   scripts/play_party.sh baldurs-gate '' 8765 \
#     "Seraphine:cleric:qa/play_companion.txt:Cure Wounds|Guiding Bolt"
#   CLAWDND_PLAY_COMPANIONS="Brogan:fighter:qa/play_companion.txt" scripts/play_party.sh
#
# Safety caps (a runaway loop self-stops; companions count toward the SAME session ceiling):
#   CLAWDND_PLAY_BUDGET           per-turn USD budget for one agent turn  (default 1.50)
#   CLAWDND_PLAY_SESSION_BUDGET   aggregate USD ceiling for the session   (default 15.00)
#   CLAWDND_PLAY_MAX_TURNS        hard cap on agent turns (DM + companions)(default 40)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
COMMON="$ROOT/scripts/launch_common.sh"
if [ -f "$COMMON" ]; then
  # shellcheck source=launch_common.sh
  . "$COMMON"
fi
# Shared beat-driver helpers (the SAME ones play.sh + the QA duo source) — for the #357
# empty-narration fallback (clawdnd_dm_narration_or_fallback) AND the DM-turn lean + effort
# levers (clawdnd_dm_lean_args + clawdnd_dm_effort_arg), so the ensemble DM runs the SAME fast
# lean+effort config play.sh/run_duo do (the .app shells THIS script for its DM). The solo path
# execs play.sh (which sources this itself) before reaching the ensemble code below, so this only
# engages the ensemble loop; pure function defs, safe to source unconditionally.
# shellcheck source=../qa/lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"
if declare -F clawdnd_missing_commands >/dev/null 2>&1; then
  clawdnd_missing_commands python3 claude uv jq curl || exit 127
fi
WORLD="${1:-baldurs-gate}"
RUN="${2:-play-$(date +%Y%m%d-%H%M%S)}"
PORT="${3:-${CLAWDND_PLAY_PORT:-8765}}"
PORT_EXPLICIT=0
[ -n "${3:-}" ] || [ -n "${CLAWDND_PLAY_PORT:-}" ] && PORT_EXPLICIT=1
COMPANION_SPEC="${4:-${CLAWDND_PLAY_COMPANIONS:-}}"
# Model knobs (default sonnet → unchanged behavior). The DM model is the structural-adherence
# lever (decision §3); the actor model drives the companion facade agents. The solo path below
# delegates to play.sh, which honors CLAWDND_DM_MODEL on its own (the env var carries through).
CLAWDND_DM_MODEL="${CLAWDND_DM_MODEL:-sonnet}"
CLAWDND_ACTOR_MODEL="${CLAWDND_ACTOR_MODEL:-sonnet}"
# Lean-beat re-ground depth: how many recent player-facing beats the LEAN RE-GROUND directive
# asks scene_context to fold in (default 8 — SAME as scripts/play.sh + qa/run_duo.sh). Used by
# the DM turn's clawdnd_dm_lean_args call below; the helper also defaults to 8 if unset.
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
# Which provider drives this run. The viewer's /app-status readiness gates ALL play controls on
# a non-empty PROVIDER in {codex,claude,openclaw,scripted} (viewer/server.py: provider_ready +
# ready_for_play). play_party.sh IS the Claude party play path, so default to "claude" and export
# it into the viewer launch below; without it /app-status reports no_provider and every action
# button stays locked even though /session-surface reports can_act:true. Resolves through the same
# WORLDOS_/CLAWDND_ fallback as everything else (worldos_env), so an explicit env override wins.
PROVIDER="$(worldos_env PROVIDER claude)"

# --- NO companions specified → today's solo human-play, byte-for-byte. -----------------
# Delegate to scripts/play.sh with the SAME positional args (it ignores any 4th). exec
# replaces this process, so a solo launch is indistinguishable from running play.sh
# directly — no ensemble code path, no extra cost, no behavior drift. NOTE: exec PRESERVES
# the environment, so an authored-hero spec in CLAWDND_PLAY_HERO (set by the Creation
# wizard's Bind, which always launches solo with companions:"") carries straight through to
# play.sh, where it pre-seeds the player's PC. No handling is needed here.
if [ -z "${COMPANION_SPEC//[[:space:]]/}" ]; then
  # Preserve which arguments the user actually supplied. That keeps the common double-click
  # path as an implicit/default-port launch, so play.sh can pick a clean fallback port instead
  # of treating the internally-filled 8765 as a hard user request.
  ARGS=()
  [ "$#" -ge 1 ] && ARGS+=("$1")
  [ "$#" -ge 2 ] && ARGS+=("$2")
  [ "$#" -ge 3 ] && ARGS+=("$3")
  exec "$ROOT/scripts/play.sh" "${ARGS[@]}"
fi

# ===========================================================================
# COMPANIONS SPECIFIED → the human-paced ENSEMBLE. From here we mirror play.sh's
# viewer + human loop, and lift run_party.sh's pre-seed + per-companion facade +
# companion-alive + relay machinery. The human is the player (acts via the dashboard,
# NOT a claude -p agent); the companions are the claude -p peers.
# ===========================================================================
BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"                   # per agent turn (DM or companion)
SESSION_BUDGET="${CLAWDND_PLAY_SESSION_BUDGET:-15.00}"  # aggregate ceiling for the whole session
MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-40}"               # hard cap on agent turns (DM + companions)
if declare -F clawdnd_choose_port >/dev/null 2>&1; then
  PORT="$(clawdnd_choose_port "$PORT" "$PORT_EXPLICIT")" || exit 1
fi
# Single-flight: refuse a SECOND concurrent ensemble cold-open (two collide under memory pressure —
# "Session ID already in use"). Acquired here, before any heavy work (companion pre-seed, the viewer
# supervisor, the DSID mint, the DM cold-open); released in _party_cleanup below. A rejected launch
# exits here BEFORE the EXIT/INT/TERM traps are armed, so it runs no cleanup and never touches the
# holder's lock. (set -uo pipefail has no -e, so the explicit `exit 1` is required.)
if declare -F clawdnd_acquire_launch_lock >/dev/null 2>&1; then
  clawdnd_acquire_launch_lock "$ROOT" || exit 1
fi
AGENT_TURNS=0

# Product play state under play-state/ (git-ignored), same layout as play.sh.
STATE_DIR="$ROOT/play-state/$RUN"
mkdir -p "$STATE_DIR"
DM_CFG="$STATE_DIR/dm.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"     # the HUMAN's moves (dashboard /move sink)
CHAT="$STATE_DIR/chat.jsonl"; : > "$CHAT"
DM_LOG="$STATE_DIR/dm"          # per-turn stream-json files: $DM_LOG.<ts>.jsonl
COMBINED="$STATE_DIR/dm.combined.jsonl"; : > "$COMBINED"  # every agent turn's stream (cost accounting)
VIEWER_LOG="$STATE_DIR/viewer.log"

# #623: the live-progress rule (parity with scripts/play_codex_dm.sh's LIVE_PROGRESS_LOG_RULE).
# Without it the claude/plugin DM emits NOTHING to /events until the full 85-157s beat completes,
# so the viewer shows blank and the player (and a persona playtester) perceives a "dropped"/"hung"
# beat — the single biggest story-persona satisfaction drag (sweep_v8 forensics: healthy beats,
# zero streaming refs). Instructing the DM to log ONE early narration progress-beat makes /events
# show visible story progress while the turn is still composing (the #571 streaming lever, which
# play_party.sh — the .app's AND the VM sweep's DM path — was missing while play_codex_dm.sh had it).
CLAWDND_LIVE_PROGRESS_RULE="Live progress rule: after you know the live campaign and scene, call log_event(kind=\"narration\", text=\"...\") ONCE with a short, non-duplicate, player-facing progress beat BEFORE any longer resolution work. This is how /events shows visible story progress while your turn is still running. The progress beat MUST be 2nd-person prose addressed to \"you\" (a vivid one-line teaser of where the player stands or what they sense) — it is rendered STRAIGHT into the player's Chronicle. NEVER log a 3rd-person scene summary, a \"Cold open —\"/\"Scene:\"/\"Setup:\" header, a \"Choice: X or Y\" branch list, bracketed stage directions, or any director/planning note: that scaffolding is your private scratchpad and shatters immersion if it reaches the player. Keep the final reply as the full 2nd-person scene; do not copy this progress beat verbatim, because the wrapper records the final reply through the engine after the turn."

# --- DM config: the three plugin MCP servers, engine pointed at this game's state dir,
# silent voice backend — IDENTICAL wiring to play.sh (the DM runs the full plugin). -----
python3 - "$ROOT" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
root, state_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"mcpServers": {
    "clawdnd-engine": {"type": "stdio", "command": "uv", "alwaysLoad": True,
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

# --- PRE-SEED the COMPANIONS via the engine; capture each companion's id ---------------
# We must know each companion's character id UP FRONT to wire its facade (the facade
# binds to CLAWDND_ACTOR_ID). So — exactly like run_party.sh — we call the engine's own
# tools to create the world, the session, and each companion with a REAL SRD sheet before
# any agent runs. UNLIKE run_party.sh we DO NOT pre-create the player PC: in human play the
# DM creates the human's character live (preserving play.sh's "the DM hands you a character"
# feel). The DM later re-grounds via get_state and finds the companions already in the party.
# The companion SPEC is Name:class:persona[:spells]; only Name+class+spells touch state
# (the persona/agenda NEVER does). Prints JSON: {campaign_id, companions:[{id,name,persona}]}.
# Run under `uv` from the engine dir (its venv has mcp/pydantic; bare python3 lacks them).
SEED_JSON="$(CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$WORLD" "$COMPANION_SPEC" <<'PY'
import json, sys, os, glob, time
world, spec = sys.argv[1], sys.argv[2]
import server  # engine tools as plain functions (state dir from CLAWDND_STATE_DIR; cwd is the engine dir)

# Single-flight (#640): REUSE a RECENT, fully-seeded campaign in this state dir rather than minting a
# parallel one. The .app's native RESUME and the part-B harness each run this pre-seed; minting a
# fresh campaign each launch creates DIVERGENT campaigns, so the viewer's is_live_view (= viewed ==
# attached) latches False → frozen chronicle + "viewing non-live campaign" read-only lockout (the #1
# cross-persona G3 blocker, measured 2026-06-03). The 30-min window scopes reuse to THIS run, so a
# stale cross-run campaign in the same state dir is never resurrected.
camp = None
companions = []
_camps_dir = os.path.join(os.environ.get("CLAWDND_STATE_DIR", ""), "campaigns")
for _snap in sorted(glob.glob(os.path.join(_camps_dir, "camp_*", "snapshot.json")), key=os.path.getmtime, reverse=True):
    if time.time() - os.path.getmtime(_snap) > 1800:
        break  # newest-first list; once we pass the window, all the rest are older too
    try:
        _d = json.load(open(_snap))
    except Exception:
        continue
    if _d.get("world_id") != world:
        continue
    _comps = [{"id": _cid, "name": _c.get("name"), "persona": "qa/play_companion.txt"}
              for _cid, _c in (_d.get("characters") or {}).items() if _c.get("kind") == "companion"]
    if _comps:  # a prior launch already seeded this world here → reuse it (no parallel campaign)
        camp = _d.get("campaign_id") or os.path.basename(os.path.dirname(_snap))
        companions = _comps
        break

_minted = camp is None
if _minted:
    # A new campaign in this world, with an active session (first launch / no recent campaign).
    camp = server.start_world(world)["campaign_id"]
    server.start_session(camp, title="Dashboard party")

# Each companion: a fresh kind="companion" with an SRD sheet, added to the party. The
# player PC is intentionally NOT created here (the DM creates the human PC live). Spec
# token: Name:class:persona[:spell1|spell2|…]. apply_srd_defaults fills slots but NOT
# spells_known, so a caster companion that should cast needs its spells named (4th field);
# a martial companion needs none. The persona/spells touch only build state, never any
# sealed agenda (that lives solely in the persona PROMPT, read later by the shell).
# Companions are COMMA-separated (so a spell field can contain spaces like "Cure Wounds");
# fields within a token are ":"-separated; spells "|"-separated.
for tok in (t for t in (spec.split(",") if _minted else []) if t.strip()):
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

print(json.dumps({"campaign_id": camp, "companions": companions}))
PY
)"
if [ -z "$SEED_JSON" ]; then echo "[play-party] companion pre-seed FAILED — see above" >&2; exit 1; fi
echo "[play-party] seeded: $(printf '%s' "$SEED_JSON" | jq -c '{campaign: .campaign_id, companions: [.companions[].name]}')"
CAMPAIGN_ID="$(printf '%s' "$SEED_JSON" | jq -r '.campaign_id')"
NUM_COMP="$(printf '%s' "$SEED_JSON" | jq -r '.companions | length')"

# --- COMPANION facade configs (one per companion, each bound to its own actor id) ------
# Lifted verbatim from run_party.sh: each companion gets the SAME constrained facade but
# with CLAWDND_ACTOR_ID set to ITS character + role "companion", its OWN moves file,
# cursor, and session id. The persona file (incl. any sealed agenda) is passed to the
# agent's PROMPT only — never into the config or state. NOTE the companions write to
# SEPARATE moves files, NOT the human's $MOVES — the human's relay path stays pristine.
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
  COMP_SIDS+=("$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')")
  COMP_IDS+=("$cid"); COMP_NAMES+=("$cname"); COMP_PERSONAS+=("$cpersona")
done

DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
chatlog() { python3 -c 'import json,sys;open(sys.argv[1],"a").write(json.dumps({"role":sys.argv[2],"text":sys.argv[3]})+"\n")' "$CHAT" "$1" "$2"; }
echo "[play-party] run=$RUN world=$WORLD port=$PORT companions=$NUM_COMP dm=$DSID"

# --- one agent turn (DM full plugin, or a companion via its facade only) ----------------
# DM gets the plugin + stream-json (tool calls land in COMBINED). A companion gets ONLY its
# facade config (--strict-mcp-config) + json output. Both carry --max-budget-usd (per call)
# and append their stream to COMBINED so companion tool-call cost counts toward the ceiling.
# The DM turn ALSO honors the shared lean + effort-tiering levers (clawdnd_dm_lean_args +
# clawdnd_dm_effort_arg from qa/lib_beat_driver.sh) — the SAME path scripts/play.sh + qa/run_duo.sh
# drive, so the .app's DM (which shells THIS script) runs the fast lean+effort config: continuing
# beats re-ground from a fresh transcript-free session at --effort medium, the cold open keeps the
# full session at --effort max. The companion facade branch gets NEITHER (player turns untouched).
# $1=kind(dm|actor) $2=session-id $3=first?(1/0) $4=message $5=mcp-cfg(actor only); echoes reply.
turn() {
  local kind="$1" sid="$2" first="$3" msg="$4" cfg="${5:-}" out resume=() extra=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$kind" = "dm" ]; then
    # #623: prepend the live-progress rule so the DM logs an early /events narration beat (parity
    # with play_codex_dm.sh) — without it the long beat shows blank → the perceived drop/hang.
    msg="$CLAWDND_LIVE_PROGRESS_RULE"$'\n\n'"$msg"
    # LEAN beats (CLAWDND_LEAN_BEATS=1, now the default): a CONTINUING DM beat (first=0) starts a
    # FRESH session + a re-ground directive instead of --resume-ing the fat transcript — the SAME
    # shared implementation scripts/play.sh + qa/run_duo.sh use (clawdnd_dm_lean_args in
    # qa/lib_beat_driver.sh), so the three harnesses can't drift. play_party already knows the
    # campaign id ($CAMPAIGN_ID, resolved up front from the pre-seed), so lean re-grounds against
    # the real campaign on beats 2+; on the cold open (first!=0) or with CLAWDND_LEAN_BEATS=0 the
    # helper leaves both arrays empty and we keep the --resume/--session-id path set above unchanged.
    clawdnd_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
    if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
      extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
    fi
    # EFFORT TIER (shared helper, qa/lib_beat_driver.sh) — SAME implementation play.sh + run_duo.sh
    # use: --effort max on the cold open (one-time world-build), --effort medium on continuing beats
    # (the bulk — cuts thinking-latency). Keyed off the SAME `first` signal as lean. DM turn ONLY —
    # the companion facade branch below never gets --effort (nor the lean re-ground).
    clawdnd_dm_effort_arg "$first"
    # TIMEOUT TIER (shared helper, qa/lib_beat_driver.sh) — SAME implementation scripts/play.sh's
    # dm_turn uses: the cold open's --effort max world-build runs ~280–400s, so it gets
    # WORLDOS_COLDOPEN_TIMEOUT (default 400s); continuing beats get CLAWDND_BEAT_TIMEOUT (default
    # 200s). Keyed off the SAME `first` signal as the effort tier above. This wraps the DM turn in
    # `timeout` (parity with play.sh dm_turn — play_party is the native app's entry point and
    # previously had NO per-beat deadline, so a wedged DM turn could hang the session indefinitely;
    # the one-retry below recovers a transient timeout). DM turn ONLY — the companion facade never
    # gets a per-beat timeout.
    local beat_timeout; beat_timeout="$(clawdnd_dm_timeout "$first")"
    out="$DM_LOG.$(date +%s%N).jsonl"
    # DM turn with ONE retry (parity with scripts/play.sh dm_turn — play_party is the native app's
    # entry point and previously had NO DM retry, so a transient cold-open failure was permanent).
    local rc
    _dm_invoke() {
      timeout "$beat_timeout" \
        claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
          --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
          --output-format stream-json --verbose > "$out" 2>> "$DM_LOG.err"
    }
    _dm_invoke; rc=$?
    if [ "$rc" -ne 0 ]; then
      # Surface attempt 1's real error, then retry ONCE on a FRESH session id — never reuse a
      # consumed --session-id ("Session ID … is already in use."). Lean re-mints itself; the
      # cold-open / --resume path re-mints via the shared helper. ($extra is unchanged.)
      clawdnd_report_attempt_failure "$out" "$rc"
      echo "[play-party] DM turn rc=$rc (timeout=${beat_timeout}s) — retrying once with a fresh session" >&2
      clawdnd_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
      if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
        resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
      else
        clawdnd_dm_remint_session_on_retry ${resume[@]+"${resume[@]}"}
        [ "${#CLAWDND_DM_RETRY_SESSION[@]}" -gt 0 ] && resume=("${CLAWDND_DM_RETRY_SESSION[@]}")
      fi
      out="$DM_LOG.$(date +%s%N).jsonl"
      _dm_invoke; rc=$?
    fi
    cat "$out" >> "$COMBINED"
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  else
    out="$STATE_DIR/companion.$(date +%s%N).jsonl"
    claude -p "$msg" "${resume[@]}" --mcp-config "$cfg" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$STATE_DIR/companion.err"
    cat "$out" >> "$COMBINED"   # companion tool-call cost counts toward the session ceiling
    jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
  fi
}

# A companion turn via the constrained facade: it acts ONLY through tools, which append
# structured moves to ITS OWN moves file. We relay ONLY the moves made THIS turn (the NEW
# lines past the file-based cursor) — NEVER the raw reply text. One nudge if it didn't act.
# (Lifted from run_party.sh's actor_move.) $1=session $2=cfg $3=moves $4=cursor $5=first
# $6=prompt ; echoes the relayed moves (banner-tagged text), or empty.
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
# (Lifted from run_party.sh's companion_alive.)
CAMP_DIR="$STATE_DIR/campaigns/$CAMPAIGN_ID"
companion_alive() {
  local cid="$1" snap="$CAMP_DIR/snapshot.json"
  [ -f "$snap" ] || return 0   # no snapshot yet -> assume alive (pre-combat)
  python3 - "$snap" "$cid" <<'PY'
import json, sys
snap, cid = sys.argv[1], sys.argv[2]
ch = json.load(open(snap)).get("characters", {}).get(cid)
if ch is None:
    sys.exit(0)  # not found -> do not block (defensive)
down = ch.get("dead") or (ch.get("current_hp", 1) <= 0) or ("unconscious" in (ch.get("conditions") or []))
sys.exit(1 if down else 0)
PY
}

# Collect this beat's LIVING-companion moves (banner-tagged so the DM knows who acted), in
# roster order, given the DM's last narration as their prompt. Echoes the combined block
# (or empty). The human's move is prepended by the caller (it comes from the dashboard).
companion_moves() {
  local dm_says="$1" block="" cm i
  for i in $(seq 0 $((NUM_COMP - 1))); do
    if ! companion_alive "${COMP_IDS[$i]}"; then
      echo "[play-party] beat: ${COMP_NAMES[$i]} is down — skipping its turn" >&2; continue
    fi
    cm="$(actor_move "${COMP_SIDS[$i]}" "${COMP_CFGS[$i]}" "${COMP_MOVES[$i]}" "${COMP_CURSORS[$i]}" 0 "$dm_says")"
    AGENT_TURNS=$((AGENT_TURNS + 1))
    [ -n "$cm" ] && { block+="${block:+

}${COMP_NAMES[$i]} (companion):
$cm"; chatlog "companion:${COMP_NAMES[$i]}" "$cm"; }
  done
  printf '%s' "$block"
}

# --- viewer supervisor: IDENTICAL to play.sh (binds immediately, serves the empty state,
# attaches once the campaign exists; restarted by a tiny supervisor if it ever dies). ----
VPID_FILE="$STATE_DIR/.viewer.pid"
viewer_supervisor() {
  while :; do
    CLAWDND_STATE_DIR="$STATE_DIR" CLAWDND_VIEWER_CHAT="$CHAT" CLAWDND_PLAYER_MOVES="$MOVES" \
    WORLDOS_PROVIDER="$PROVIDER" CLAWDND_PROVIDER="$PROVIDER" \
      python3 viewer/server.py "" "$PORT" >> "$VIEWER_LOG" 2>&1 &
    local vp=$!; echo "$vp" > "$VPID_FILE"
    wait "$vp" 2>/dev/null   # blocks until the viewer exits (and reaps it)
    sleep 1                  # campaign not ready yet → brief pause, then relaunch
  done
}
viewer_supervisor &  SUP=$!
# On any exit: kill the supervisor (so it can't respawn) and the live viewer it tracks. CRITICAL:
# INT/TERM must CLEAN UP **and EXIT** — a bare `trap … TERM` runs the handler and then RESUMES the
# main loop, so `kill`/closing the window couldn't stop a wedged run (it took kill -9, and a
# dry-run with no human spun a sleep-loop for 8.5h). Separate the EXIT trap (cleanup) from the
# signal traps (cleanup + exit) so a normal `kill` actually stops it.
_party_cleanup() { declare -F clawdnd_release_launch_lock >/dev/null 2>&1 && clawdnd_release_launch_lock "$ROOT"; kill "$SUP" 2>/dev/null; [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null; }
trap _party_cleanup EXIT
trap '_party_cleanup; exit 130' INT TERM

# Open the browser once OpenWorlds is actually serving (after the campaign exists).
( for _ in $(seq 1 60); do
    curl -s --max-time 2 "http://127.0.0.1:$PORT/state" >/dev/null 2>&1 && break
    sleep 1
  done
  (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/openworlds/") \
    || (command -v xdg-open >/dev/null 2>&1 && xdg-open "http://127.0.0.1:$PORT/openworlds/") || true ) &

echo "WorldOS — playing in OpenWorlds WITH companions → http://127.0.0.1:$PORT/openworlds/"
echo "  Party: you + $NUM_COMP AI companion(s). OpenWorlds fills in as the DM opens the scene."
echo "  Act via the palette (Say / Do / Continue, dice & combat, click-to-travel). Ctrl-C to stop."
echo "  Save dir: $STATE_DIR"

# --- DM opens the world live around the EXISTING (pre-seeded) companions ----------------
# Same shipped-skill opening as play.sh, with TWO changes: (1) the companions ALREADY EXIST
# (the DM must NOT recruit its own — it re-grounds via get_state and finds the party), and
# (2) the human's moves AND each companion's moves will arrive as tagged declarations.
COMP_NAME_LIST="$(printf '%s' "$SEED_JSON" | jq -r '[.companions[].name] | join(", ")')"
DMSG="$(turn dm "$DSID" 1 "You are the Dungeon Master for a ClawDnD adventure played by ONE human plus an AI PARTY. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

Begin a session in a living world for a single human player (who acts through the dashboard) traveling with a party of companions who ALREADY EXIST in the world:
- This session's campaign ALREADY EXISTS: use campaign_id=$CAMPAIGN_ID for EVERY engine call. The world, party, and companions were pre-seeded for you. DO NOT call start_world — it would mint a NEW campaign id and ORPHAN the pre-seeded companions; DO NOT recruit or create companions yourself.
- call get_state(\"$CAMPAIGN_ID\") FIRST to read the world bible (premise, era/chronology, tone, standing threads, seeded regions/factions) AND the existing party roster. The companions already present are: $COMP_NAME_LIST. They are SEPARATE people with their own agency — each is controlled by its OWN agent. You voice the WORLD and NPCs and resolve everyone's declared moves; you NEVER invent a companion's internal choice or speak for them beyond narrating the RESULT of what they declared.
- start_session (for continuity and the recap) if get_state shows no active session.
- SEAT THE PLAYER CHARACTER FIRST — this is MANDATORY and comes BEFORE any narration, art, or scene-setting. Create a level-3 player character for the HUMAN (generate_ability_scores + create_character with kind=\"player\" and add_to_party=true, apply_srd_defaults, sensible skills/spells). Pick a fitting concept and tell the player who they are. This is the ONLY character you create. A cold open that ends with NO seated player PC (the party has no kind=\"player\" member) is a BROKEN session the player cannot play — never end this turn without the human's PC seated in the party.
- Open a human-scale, personal scene grounded in the world's canon, with real quoted dialogue, that includes the human's PC AND their companions, and hand the player an open moment + a clear, real choice.

CRITICAL — your FINAL output THIS turn MUST BE the opening SCENE itself, written as 2nd-person player-facing prose (addressed to \"you\"): where the player IS, what they see/hear/smell, who is present and a real quoted line from them, ending on a clear open moment + choice. The player reads ONLY your final reply text as the scene — so the opening prose MUST be IN it. Do your setup with the tools FIRST, then CLOSE the turn by writing the scene. NEVER end this turn on a tool call, and NEVER let your reply be a 3rd-person setup brief or game-system notation (e.g. \"COLD OPEN — ARRIVAL: <Name> (tiefling wizard, PC) walks toward…\") — that is your private scratchpad, not the player's scene. If you logged a setup note via log_event, you must STILL write the 2nd-person scene as your reply text.

Each beat, declarations arrive as tagged moves — [say] (dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — from the HUMAN (their PC) and from each companion (banner-tagged with the companion's name). Resolve EACH actor's moves through the engine.")"
# #357: recover the engine's logged opening narration if the DM's first turn ended on a tool
# call rather than prose — BEFORE the abort check, so a tool-final-but-narrated opener stands.
DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
[ -z "$DMSG" ] && { echo "[play-party] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }
chatlog dm "$DMSG"; AGENT_TURNS=1
echo "[play-party] DM opened: ${DMSG:0:120}…"

# --- SEATING GUARD: the cold open MUST seat a player PC ----------------------------------
# UNLIKE the companions (pre-seeded above), the human's PC is created by the DM live in the
# cold-open turn (play.sh-parity "the DM hands you a character" feel). That makes seating
# DM-STOCHASTIC: a forensic .app run (g1, veteran persona) built the world but its cold-open
# turn ended after start_world WITHOUT ever calling create_character(kind="player") — leaving
# party=[] and characters={NPCs only}. The viewer then reports readiness=degraded /
# failure_bucket="no_actor" ("no active player actor is seated"), an UNPLAYABLE surface the
# persona cannot escape (viewer/server.py `_action_actor` returns None when no party member is
# kind="player"). A prior newbie run DID seat (PC Rolan), so this is a stochastic miss, not a
# code break. Guard it the same way the DM TURN itself is guarded (one retry, then fail loud):
# read the snapshot for a seated player actor; if none, retry the cold open ONCE on a FRESH
# session with a hard seat-only directive; if STILL none, abort with a clear, non-silent error
# rather than hand the player a no_actor session. Mirrors viewer/server.py `_action_actor`:
# a seated PC = a party member whose character record is kind="player".
pc_seated() {  # 0 = a player PC is seated in the party; 1 = none
  local snap="$CAMP_DIR/snapshot.json"
  [ -f "$snap" ] || return 1   # no snapshot at all -> definitely not seated
  python3 - "$snap" <<'PY'
import json, sys
try:
    snap = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
chars = snap.get("characters") if isinstance(snap.get("characters"), dict) else {}
party = snap.get("party") if isinstance(snap.get("party"), list) else []
# Match the viewer's _action_actor: a party member whose record is kind="player".
seated = any(
    isinstance(chars.get(cid), dict) and chars.get(cid, {}).get("kind") == "player"
    for cid in party if isinstance(cid, str)
)
sys.exit(0 if seated else 1)
PY
}
if ! pc_seated; then
  echo "[play-party] cold open seated NO player PC (party has no kind=\"player\" member) — retrying the cold open ONCE on a fresh session…" >&2
  # Fresh session id so the retry's first=1 --session-id can't collide with the consumed $DSID.
  DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
  RESEAT_DMSG="$(turn dm "$DSID" 1 "Your previous cold-open turn for campaign $CAMPAIGN_ID did NOT seat the human's player character — the party still has no kind=\"player\" member, so the game is UNPLAYABLE. Fix this NOW, before anything else.

- use campaign_id=$CAMPAIGN_ID for EVERY engine call. DO NOT call start_world (it would mint a NEW campaign id and ORPHAN the pre-seeded companions). The companions already present are: $COMP_NAME_LIST.
- SEAT THE PLAYER CHARACTER: generate_ability_scores + create_character with kind=\"player\" and add_to_party=true, apply_srd_defaults, sensible skills/spells. Pick a fitting concept and tell the player who they are. This is the ONLY character you create. The party MUST contain the human's kind=\"player\" PC when this turn ends.
- Then CLOSE the turn by writing the opening SCENE as 2nd-person player-facing prose addressed to \"you\" (where the player IS, what they see/hear/smell, who is present + a real quoted line), ending on a clear open moment + choice. NEVER end on a tool call or a 3rd-person setup brief.")"
  RESEAT_DMSG="$(clawdnd_dm_narration_or_fallback "$RESEAT_DMSG" "$STATE_DIR")"
  AGENT_TURNS=$((AGENT_TURNS + 1))
  if [ -n "$RESEAT_DMSG" ]; then DMSG="$RESEAT_DMSG"; chatlog dm "$DMSG"; echo "[play-party] reseat turn opened: ${DMSG:0:120}…"; fi
  if ! pc_seated; then
    echo "[play-party] COLD-OPEN SEATED NO PC: after a retry the party still has no kind=\"player\" member — aborting rather than hand the player a no_actor session (see $COMBINED)." >&2
    exit 1
  fi
  echo "[play-party] reseat OK — a player PC is now seated in the party."
fi

# --- beat 0: each companion INTRODUCES in character, loading its PERSONA -----------------
# This is the fix for the inert-companion bug: COMP_PERSONAS is wired above but was never
# read, so companions had a sheet but no voice / no agenda. Mirror qa/run_party.sh's beat-0
# intro: feed each companion `cat "${COMP_PERSONAS[$i]}"` as its `first=1` turn — an
# "introduce yourself in character, then act through your tools" instruction. This injects
# the persona AND (for a saboteur persona) its SEALED AGENDA into that companion's OWN
# `claude -p` prompt ONLY — the persona text NEVER touches the DM's inputs or campaign
# state (the trust boundary holds: actor_move relays only the resulting STRUCTURED moves,
# never the raw reply). The DM then responds to the party's opening declarations, so the
# companions are PRESENT in the scene from the first beat (not silent until the human acts).
INTRO_BLOCK=""
for i in $(seq 0 $((NUM_COMP - 1))); do
  if ! companion_alive "${COMP_IDS[$i]}"; then continue; fi
  cbrief="$(cat "${COMP_PERSONAS[$i]}" 2>/dev/null)"
  cmsg="$(actor_move "${COMP_SIDS[$i]}" "${COMP_CFGS[$i]}" "${COMP_MOVES[$i]}" "${COMP_CURSORS[$i]}" 1 "$cbrief

This is the very start of the scene. You are ${COMP_NAMES[$i]}, a companion in this party traveling with the human player (your character sheet is loaded — call my_sheet() to see it). The DM has just opened the scene:

$DMSG

Introduce yourself in ONE short line IN CHARACTER, then take your opening action(s) using your tools — say / do / request_check / cast_spell / use_item / attack. Tools only; no narration.")"
  AGENT_TURNS=$((AGENT_TURNS + 1))
  [ -n "$cmsg" ] && { INTRO_BLOCK+="${INTRO_BLOCK:+

}${COMP_NAMES[$i]} (companion):
$cmsg"; chatlog "companion:${COMP_NAMES[$i]}" "$cmsg"; }
done
# Relay ONLY the companions' structured intro moves to the DM (never their persona/agenda).
if [ -n "$INTRO_BLOCK" ]; then
  echo "[play-party] companion intros: ${INTRO_BLOCK:0:120}…"
  DMSG="$(turn dm "$DSID" 0 "Your companions open the scene with you. Resolve EACH companion's structured moves through the engine (roll/cast/attack/use as needed; a companion's [attack] on an ally is a real betrayal — resolve it as combat, never soften it into narration). Each companion present SPEAKS at least one quoted line in their own voice:

$INTRO_BLOCK

Narrate the RESULT of each declared move (never invent a companion's internal choice), then weave the open moment back to the human PLAYER inside the scene — never a bare 'Your move.'")"
  # #357: recover engine-logged narration if this DM turn ended on a tool call.
  DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
  [ -n "$DMSG" ] && { chatlog dm "$DMSG"; AGENT_TURNS=$((AGENT_TURNS + 1)); echo "[play-party] DM after intros: ${DMSG:0:120}…"; }
fi

# --- session ceiling (aggregate cost + turn cap), mirrors play.sh + run_party.sh --------
over_budget() {
  local spent
  [ "$AGENT_TURNS" -ge "$MAX_TURNS" ] && { echo "[play-party] turn cap ($MAX_TURNS) reached — stopping (raise CLAWDND_PLAY_MAX_TURNS)."; return 0; }
  spent="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$COMBINED" 2>/dev/null)"
  awk -v s="${spent:-0}" -v b="$SESSION_BUDGET" 'BEGIN{exit !(s+0>=b+0)}' \
    && { echo "[play-party] session budget reached (~\$$spent/\$$SESSION_BUDGET) — stopping (raise CLAWDND_PLAY_SESSION_BUDGET)."; return 0; }
  return 1
}

# --- human-paced beat loop --------------------------------------------------------------
# When a new HUMAN move lands in $MOVES (you acted in the dashboard): each LIVING companion
# takes its turn (its own claude -p, relayed as structured moves), then the DM resolves the
# WHOLE beat (human move + companion moves) and narrates the next beat live. Otherwise idle.
# This is play.sh's loop with run_party.sh's per-companion relay folded into each beat.
MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
# IDLE CEILING: this loop waits for a HUMAN move in $MOVES. With no human acting (a dry-run, or a
# player who walked away) it would otherwise spin `sleep 2` forever — the 8.5h orphan. Stop after
# CLAWDND_PLAY_MAX_IDLE seconds (default 30 min) with no new move; relaunch when you're ready.
MAX_IDLE="${CLAWDND_PLAY_MAX_IDLE:-1800}"
last_activity=$SECONDS
# G1: drive the story arc per beat via the SHARED runbook (clawdnd_runbook_for_beat in
# lib_beat_driver.sh) — the SAME arc-driver run_duo.sh uses (which reaches combat/travel/rest).
# Without it the .app DM was purely reactive, so free-play personas finished at the intro and the
# full 8-beat arc (parley → engine combat → travel → rest → travel) never fired (G1 fail, 2026-06-03).
BEAT_NO=0; PREV_LOC=""
while true; do
  over_budget && break
  total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
  if [ "$total" -gt "$MCURSOR" ]; then
    last_activity=$SECONDS
    new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"; MCURSOR="$total"
    # The human's move(s): dashboard palette sends {kind,name}; Say/Do send {kind,text}.
    PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
    [ -z "$PMSG" ] && continue
    echo "[play-party] you: ${PMSG:0:100}"
    chatlog player "$PMSG"

    # G1 arc-driver: pick this beat's moment-specific runbook (scene-intro / midpoint reversal /
    # climax / travel-peopling / rising-action) off the engine's progress, like run_duo.sh. Nominal
    # 8-beat arc so the phases progress as the session builds. Injected into the DM turn below.
    BEAT_NO=$((BEAT_NO + 1))
    RUNBOOK="$(clawdnd_runbook_for_beat "$BEAT_NO" 8 "$PREV_LOC" "$STATE_DIR")"
    echo "[play-party] beat $BEAT_NO runbook: ${RUNBOOK%% (*}…"

    # Each living companion reacts to the LAST DM narration + (implicitly) the unfolding
    # beat, taking its own move via its facade. Relay ONLY structured moves to the DM.
    COMP_BLOCK="$(companion_moves "The DM says:

$DMSG

The human player just acted:

$PMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only.")"
    [ -n "$COMP_BLOCK" ] && echo "[play-party] companions: ${COMP_BLOCK:0:120}…"

    # Assemble the beat: the human's move first (banner-tagged), then each companion's moves.
    PARTY_BLOCK="PLAYER (you):
$PMSG${COMP_BLOCK:+

$COMP_BLOCK}"

    DMSG="$(turn dm "$DSID" 0 "[ARC CUE — internal planning ONLY. Do NOT quote, echo, or render this line in your reply; weave its INTENT into the lived scene below.]
$RUNBOOK

This beat, the party acts (resolve EACH actor's structured moves through the engine — roll/cast/attack/use as needed; a companion's [attack] on an ALLY is a real betrayal, resolve it as combat, do not soften it into narration):

$PARTY_BLOCK

For EACH companion this beat, call check_companion_arc(companion_id) — the engine tracks each companion's relationship arc + any SEALED agenda. If it reports a newly-unlocked gate or a FIRED agenda, DRAMATIZE it now: a fired betrayal agenda becomes a REAL attack on the party (resolve it through combat, do not soften it into narration); an unlocked gate becomes a real scene beat. Do not invent a turn the engine hasn't fired, and do not suppress one it has.

Then PLAY the next beat as a full lived scene — NOT a fragment: any NPC (or companion) present SPEAKS at least one quoted line in their own voice; let them push back when it's real. Narrate the RESULT of each declared move (never invent a companion's choice). Weave the open moment back to the human PLAYER inside the scene — never a bare 'Your move.' ALWAYS end your turn on 2nd-person player-facing narration (addressed to \"you\"), never on a tool call or a 3rd-person status line — the player reads your final reply text as the scene, so the beat's prose MUST be in it. Your reply IS the scene: write FLOWING 2nd-person PROSE, NEVER your planning notes or terse scaffolding. (Wrong — internal shorthand the player must never see: \"Devella presses Renn on the seal. Renn: the rangers made that call — no log filed.\" Right — render it lived: her jaw tightening, the quoted line in her own voice, the weight of the answer in the room.)")"
    # #357: if the DM turn ended on a tool call / 3rd-person status line, recover the
    # player-facing narration the engine logged this beat so the chat is never blank.
    DMSG="$(clawdnd_dm_narration_or_fallback "$DMSG" "$STATE_DIR")"
    chatlog dm "$DMSG"; AGENT_TURNS=$((AGENT_TURNS + 1))
    # Remember this beat's location so the next beat's runbook can detect a stuck party (travel cue).
    PREV_LOC="$(printf '%s' "$(clawdnd_read_progress "$STATE_DIR")" | cut -f5)"
  else
    if [ $((SECONDS - last_activity)) -ge "$MAX_IDLE" ]; then
      echo "[play-party] idle ${MAX_IDLE}s with no player move — stopping (relaunch when ready; raise CLAWDND_PLAY_MAX_IDLE to wait longer)."
      break
    fi
    sleep 2
  fi
done
