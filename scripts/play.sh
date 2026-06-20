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
#   port       the OpenWorlds port (default: 8765 or $WORLDOS_PLAY_PORT).
#
# Safety caps (a runaway DM loop self-stops):
#   WORLDOS_PLAY_BUDGET           per-turn USD budget for one DM turn   (default 1.50)
#   WORLDOS_PLAY_SESSION_BUDGET   aggregate USD ceiling for the session (default 15.00)
#   WORLDOS_PLAY_MAX_TURNS        hard cap on DM turns                  (default 40)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
# PRODUCT PLAY IS CLAUDE-QUALITY — force-neutralize any ambient GLM env before any `claude -p`.
# The .app DM must run Claude (Opus) quality; it must NEVER inherit the GLM (z.ai) endpoint just
# because a user happened to export GLM vars in their interactive shell (e.g. after a QA run).
# QA uses GLM ONLY via qa/glm_profile.sh; the product play path never does and never opts in.
# We neutralize CONDITIONALLY so we strip GLM but never a legitimate Claude value: a GLM env is
# recognized by the z.ai base URL — when present we drop the WHOLE GLM set (endpoint + auth token
# + API key + auth token + timeout) since glm.env always exports them together; the GLM
# CLAUDE_CONFIG_DIR (/tmp/glm-claude-config) is dropped by its own path match. A non-z.ai
# ANTHROPIC_BASE_URL (api.anthropic.com, a corporate proxy) and an unrelated CLAUDE_CONFIG_DIR are
# left UNTOUCHED — so a normal clean launch (and a legit Anthropic-env launch) is byte-identical to
# today. We MUST drop ANTHROPIC_API_KEY too: leaving the GLM key behind while clearing the z.ai URL
# would make the product DM hit api.anthropic.com with a z.ai key → auth-failed dead cold-open.
case "${ANTHROPIC_BASE_URL:-}" in
  *api.z.ai*) unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN API_TIMEOUT_MS ;;
esac
case "${CLAUDE_CONFIG_DIR:-}" in
  */glm-claude-config|*/glm-claude-config/) unset CLAUDE_CONFIG_DIR ;;
esac
COMMON="$ROOT/scripts/launch_common.sh"
if [ -f "$COMMON" ]; then
  # shellcheck source=launch_common.sh
  . "$COMMON"
fi
if declare -F worldos_missing_commands >/dev/null 2>&1; then
  worldos_missing_commands python3 claude uv jq curl || exit 127
fi
# F12-8 (#787): timeout(1) is a coreutils binary stock macOS lacks. The DM beats are bounded via
# the worldos_timeout shim (python3 fallback), so absence is non-fatal — but warn with the brew
# hint so the native tool gets installed.
if declare -F worldos_warn_if_no_timeout >/dev/null 2>&1; then
  worldos_warn_if_no_timeout
fi
# Shared beat-driver helpers: the C soft clock-tick backstop + the A beat-aware runbooks —
# the SAME implementation the QA duo loop sources, so the human-paced and QA loops can't drift.
# shellcheck source=../qa/lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"
WORLD="${1:-baldurs-gate}"
RUN="${2:-play-$(date +%Y%m%d-%H%M%S)}"
PORT="${3:-${WORLDOS_PLAY_PORT:-8765}}"
PORT_EXPLICIT=0
[ -n "${3:-}" ] || [ -n "${WORLDOS_PLAY_PORT:-}" ] && PORT_EXPLICIT=1
if declare -F worldos_choose_port >/dev/null 2>&1; then
  PORT="$(worldos_choose_port "$PORT" "$PORT_EXPLICIT")" || exit 1
fi
# RESUME re-attach (#356 GA blocker): when the launcher's Resume runs, the .app reuses the SAVED
# run id (positional $2 → $RUN, so STATE_DIR already points at the saved game's dir) AND passes the
# saved campaign id as WORLDOS_RESUME_CAMPAIGN. In that mode this launch must NOT mint a new empty
# world: it re-attaches the EXISTING campaign and restarts the provider serving it. UNSET ⇒ the
# normal fresh cold-open path, byte-identical. The detection is confirmed against the on-disk save
# below (after STATE_DIR is known) — a stale/missing id falls back to a fresh cold open, never a
# dead table.
RESUME_CAMPAIGN_REQ="$(worldos_env RESUME_CAMPAIGN)"
# F12-13 (audit 2026-06-11): single-flight launch lock — refuse a SECOND concurrent solo cold-open
# from this checkout so two play.sh runs can't collide on session ids / the viewer port (the
# "Session ID already in use" failure observed under memory pressure on the 16GB host). play_party.sh
# has carried this lock since its ensemble landed, BUT it acquires AFTER `exec play.sh` on the solo
# path — so a solo launch (the .app's default) previously had NO lock and two solo launches stacked
# two viewers + two DM sessions. Acquired here, before any heavy work (the hero pre-seed mints a
# campaign, the viewer supervisor, the DSID mint, the DM cold-open); released in _play_cleanup below.
# A rejected launch exits HERE, before the EXIT/INT/TERM traps are armed, so it runs no cleanup and
# never touches the holder's lock. (set -uo pipefail has no -e, so the explicit `exit 1` is required.)
if declare -F worldos_acquire_launch_lock >/dev/null 2>&1; then
  worldos_acquire_launch_lock "$ROOT" || exit 1
fi
# The DM model is an env var (default opus, owner 2026-06-06) — one-flag flip; mirrors qa/run_duo.sh.
WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
# Budgets scale to the DM model: an Opus turn — especially the max-effort cold-open world-build —
# costs ~5x a Sonnet turn, so the Sonnet-tuned $1.50/$15 caps trip error_max_budget_usd on the Opus
# cold-open and the backend never seats a PC. These are CAPS, not spends — routine beats spend far
# less than the cap; the session ceiling bounds any runaway turn.
case "$WORLDOS_DM_MODEL" in
  *opus*) _PT_DEF=12.00; _SESS_DEF=30.00 ;;
  *)      _PT_DEF=1.50;  _SESS_DEF=15.00 ;;
esac
BUDGET="${WORLDOS_PLAY_BUDGET:-$_PT_DEF}"                    # per DM turn (model-aware default)
SESSION_BUDGET="${WORLDOS_PLAY_SESSION_BUDGET:-$_SESS_DEF}"  # aggregate session ceiling (model-aware)
MAX_TURNS="${WORLDOS_PLAY_MAX_TURNS:-40}"              # hard turn cap (worst case = MAX_TURNS×BUDGET)
DM_TURNS=0

# --- Lean-per-beat context (PERF, default OFF → byte-identical to today). --------------
# The DM turn normally `--resume`s the DM's growing claude -p session every beat, which
# REPLAYS the whole accumulated transcript (each beat's prompt + the DM's tool calls +
# narration), so prefill grows ~6–10K tokens/beat and a late-session beat can take minutes
# (the #1 felt-latency complaint — a narrative persona rated the STORY 9/10 but GAVE UP on
# the 3–5 min/turn wait). With WORLDOS_LEAN_BEATS=1, beats 2+ instead start a FRESH session
# (a new --session-id, NO transcript carried) seeded with a re-ground directive: the DM
# re-grounds from the engine's persisted truth via scene_context (which already bundles
# state/director/events/companion_arcs + the recent player-facing narration TAIL) rather
# than from the fat transcript. Beat 1 (the cold open) is ALWAYS full — it establishes and
# persists the world/scene/PC. DEFAULT 1 (lean is now STANDARD — validated: ~10–27× context
# drop, story quality held at 4.4). Set WORLDOS_LEAN_BEATS=0 to force the legacy full-resume
# path (byte-identical to the pre-lean behavior) — still fully reversible per-run. A/B harness:
# qa/lib/lean_beats_check.sh.
WORLDOS_LEAN_BEATS="${WORLDOS_LEAN_BEATS:-1}"
# Per-beat backend timeout (seconds) + ONE retry, so a wedged DM turn recovers instead of hanging
# the session. The deadline is TIERED off the cold-open `first` signal (worldos_dm_timeout in
# qa/lib_beat_driver.sh, the sibling of the effort tier): the cold open's --effort max world-build
# runs ~280–400s so it gets WORLDOS_COLDOPEN_TIMEOUT (default 400s); continuing/routine beats keep
# WORLDOS_BEAT_TIMEOUT (default 360s — F12-1: the old flat 200s killed ~18% of HEALTHY routine
# beats, measured routine max=360s). The ONE retry ESCALATES its deadline to the cold-open tier
# via worldos_dm_retry_timeout (attempt 2 never reuses attempt 1's deadline verbatim). Both knobs
# are read inside dm_turn via the helper; this line documents (and seeds) the routine default.
# Applies in BOTH lean/legacy modes (it only wraps the existing claude -p call) and ONLY to the
# DM turn.
WORLDOS_BEAT_TIMEOUT="${WORLDOS_BEAT_TIMEOUT:-360}"
# Recent player-facing narration tail the lean re-ground asks scene_context for (generous by
# default so continuity survives the lean boundary — named NPCs, prior choices, the scene).
WORLDOS_LEAN_TAIL="${WORLDOS_LEAN_TAIL:-8}"
# Which provider drives this run. The viewer's /app-status readiness gates ALL play controls
# on a non-empty PROVIDER in {codex,claude,openclaw,scripted} (viewer/server.py: provider_ready
# + ready_for_play). play.sh IS the Claude play path, so default to "claude" and export it into
# the viewer launch below; without it /app-status reports no_provider and every action button
# stays locked even though /session-surface reports can_act:true. Resolves through the same
# WORLDOS_/WORLDOS_ fallback as everything else, so an explicit env override still wins.
PROVIDER="$(worldos_env PROVIDER claude)"

# Product play state lives under the play-state ROOT (git-ignored), one dir per game, so
# saves, the chat log, and the move sink stay together and out of the QA sandbox. The ROOT is
# normally the repo's own play-state/ — but a SHIPPED .app must NOT read/write the dev repo, so
# it injects WORLDOS_STATE_DIR pointing at a per-USER play-state root
# (~/.worldos/state — the engine's own home). When neither is set (dev runs, the QA harness, a
# bare double-click in a checkout) this is BYTE-IDENTICAL to the old "$ROOT/play-state/$RUN", so
# no existing path changes. The per-$RUN subdir layout is preserved in every case.
STATE_ROOT="${WORLDOS_STATE_DIR:-$ROOT/play-state}"
STATE_DIR="$STATE_ROOT/$RUN"
mkdir -p "$STATE_DIR"
# Re-pin BOTH env names to THIS run's per-$RUN dir for the whole script. A SHIPPED .app exports a
# BARE WORLDOS_STATE_DIR=<user-root> (the play-state ROOT) to select the user dir above; if it
# leaked unchanged into the engine subprocesses (the hero pre-seed, worldos_live_campaign_id, the
# soft-tick) those would resolve <user-root>/campaigns instead of this game's <user-root>/$RUN,
# because the engine reads WORLDOS_STATE_DIR before WORLDOS_STATE_DIR. Overwriting both with the
# per-$RUN dir here makes EVERY engine call in this script land in the right game. Byte-identical
# when no override was set (the value equals $ROOT/play-state/$RUN, the engine config's old value).
export WORLDOS_STATE_DIR="$STATE_DIR"
# #892 follow-up: keep the .app-spawned cold-open `claude -p` (the DM) off the macOS keychain +
# off any /Volumes TCC prompt so it runs headless. GATED no-op without an env/file credential (so
# the Terminal/keychain path is byte-unchanged); when a credential is resolvable it isolates the
# config dir + puts the token in the env. Called ONCE here, after STATE_DIR, before the pre-seed +
# DM cold-open below. Defined in qa/lib_beat_driver.sh (sourced above). Never fails a launch.
worldos_isolate_claude_auth
# RESUME re-attach confirmation: a resume was requested AND the saved campaign's snapshot is
# actually on disk under THIS run's STATE_DIR. Only then do we take the resume path; a stale id
# (the save was deleted, or the run dir is empty) falls through to a fresh cold open so the player
# is never handed a dead table. RESUME=1 gates: (1) NOT truncating the live move sink, (2) skipping
# the start_world cold-open in favor of a re-ground-onto-the-existing-campaign opener below.
RESUME=0; RESUME_CAMPAIGN=""
if [ -n "${RESUME_CAMPAIGN_REQ//[[:space:]]/}" ] \
   && [ -f "$STATE_DIR/campaigns/$RESUME_CAMPAIGN_REQ/snapshot.json" ]; then
  RESUME=1; RESUME_CAMPAIGN="$RESUME_CAMPAIGN_REQ"
  echo "[play] RESUME — re-attaching saved campaign $RESUME_CAMPAIGN under $STATE_DIR (move sink preserved, no fresh cold open)."
elif [ -n "${RESUME_CAMPAIGN_REQ//[[:space:]]/}" ]; then
  echo "[play] resume requested for campaign $RESUME_CAMPAIGN_REQ but no snapshot under $STATE_DIR/campaigns — falling back to a fresh cold open." >&2
fi
DM_CFG="$STATE_DIR/dm.mcp.json"
# In RESUME mode the move sink + chat + combined stream are APPEND targets for the saved game — do
# NOT truncate them (truncating resets the cursor and, if a fresh cold-open then failed, left the
# dead "no live move sink"). Ensure they EXIST (a first-ever resume of a save minted by an older
# build may lack chat/combined) without clobbering history. A fresh launch truncates as before.
MOVES="$STATE_DIR/player_moves.jsonl"
CHAT="$STATE_DIR/chat.jsonl"
DM_LOG="$STATE_DIR/dm"          # per-turn stream-json files: $DM_LOG.<ts>.jsonl
COMBINED="$STATE_DIR/dm.combined.jsonl"
if [ "$RESUME" = "1" ]; then
  : >> "$MOVES"; : >> "$CHAT"; : >> "$COMBINED"
else
  : > "$MOVES"; : > "$CHAT"; : > "$COMBINED"
fi
VIEWER_LOG="$STATE_DIR/viewer.log"
# F12-10: the provider-status sidecar path is defined HERE (before the traps are armed) so an EARLY
# abort — a dead cold open that exits before the loop — still leaves a "failed" sidecar for the viewer
# to bucket as no_provider, instead of the live-looking "unknown" fallback. The actual writes happen
# below: "running" once seated, "stopped" on a clean cap/budget stop, "failed" in the EXIT trap.
PROVIDER_STATUS="$STATE_DIR/provider_status.json"
PROVIDER_STOPPED_CLEANLY=0

# Wire the three plugin MCP servers (engine/rules/voice) from THIS repo, with the
# engine pointed at this game's state dir and a silent voice backend (the dashboard
# still renders narration; spoken audio is opt-in). Generated fresh from $ROOT so the
# script works from any checkout — it does not depend on the QA harness config.
python3 - "$ROOT" "$STATE_DIR" "$DM_CFG" <<'PY'
import json, sys
root, state_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"mcpServers": {
    "worldos-engine": {"type": "stdio", "command": "uv", "alwaysLoad": True,
        "args": ["run", "--directory", f"{root}/servers/engine", "server.py"],
        # Pin BOTH names to THIS run's per-$RUN dir. The engine resolves WORLDOS_STATE_DIR
        # FIRST (WORLDOS_STATE_DIR is the v1.x fallback); when a SHIPPED .app exported a bare
        # WORLDOS_STATE_DIR=<user-root> into play.sh's environment, the MCP env inherits it,
        # and pinning ONLY WORLDOS_STATE_DIR here would let that inherited bare root win and
        # point the engine at <user-root>/campaigns instead of this game's <user-root>/$RUN.
        # Setting both to state_dir keeps every game isolated to its own per-$RUN dir. When
        # neither was inherited (dev/QA), this is byte-identical (both name the same dir).
        "env": {"WORLDOS_STATE_DIR": state_dir}},
    "worldos-rules": {"type": "stdio", "command": "uv",
        "args": ["run", "--directory", f"{root}/servers/rules", "server.py"],
        "env": {"WORLDOS_RULES_OFFLINE": "1"}},
    "worldos-voice": {"type": "stdio", "command": "uv",
        "args": ["run", "--directory", f"{root}/servers/voice", "server.py"],
        "env": {"WORLDOS_TTS_BACKEND": "null"}},
}}
json.dump(cfg, open(out, "w"))
PY

DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
# chatlog + log_engine_narration + record_dm_reply are shared helpers in qa/lib_beat_driver.sh
# (sourced above); they read $CHAT/$STATE_DIR/$ROOT from this scope. record_dm_reply (#720)
# stamps engine_logged on DM-reply rows so the OpenWorlds client de-dups the cold-open opening.

# --- OPTIONAL: pre-seed a PLAYER-authored hero before the DM's first turn. ---------------
# When the Creation wizard's "Bind" runs, it passes the authored hero through the native
# bridge as WORLDOS_PLAY_HERO (a compact JSON spec). We then mint that EXACT PC via the
# engine's own tools — the same precedent play_party.sh uses to pre-seed companions — so the
# engine stays the sole writer and the hero the player authored is the hero they play. With
# WORLDOS_PLAY_HERO UNSET this whole block is skipped and the DM invents the PC live, exactly
# as before (this path is byte-unchanged). Spec fields: {name, race, class, level, abilities
# {str..cha}, background, alignment, skills[]}; race/class ids map 1:1 to engine SRD keys, and
# AbilityScores accepts the str/dex/… shorthand directly. Prints {campaign_id, pc:{id,name,
# race,class}} which we parse to re-ground the DM opener onto the existing PC.
HERO_CAMP=""; HERO_PC_ID=""; HERO_PC_NAME=""; HERO_PC_RACE=""; HERO_PC_CLASS=""
if [ -n "${WORLDOS_PLAY_HERO:-}" ]; then
  HERO_SEED_JSON="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$WORLD" "$WORLDOS_PLAY_HERO" <<'PY'
import json, sys
world, spec_raw = sys.argv[1], sys.argv[2]
import server  # engine tools as plain functions (state dir from WORLDOS_STATE_DIR; cwd is the engine dir)
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
    # Loop-10 #383: player-authored identity prose from the wizard's Family/House
    # + Biography inputs. PR #369 threaded both into the bindHero spec; this is
    # where the engine seating path picks them up. Empty == today's behavior.
    house=str(spec.get("house", "") or ""),
    biography=str(spec.get("biography", "") or ""),
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
#   * DEFAULT (WORLDOS_LEAN_BEATS=0): --session-id on beat 1, --resume after — the shipped
#     behavior, byte-for-byte. The DM carries its prior transcript (which is what grows
#     prefill ~6–10K tok/beat → the late-session slowdown).
#   * LEAN (WORLDOS_LEAN_BEATS=1): beat 1 is still the full --session-id cold open; beats 2+
#     start a FRESH --session-id (new uuid, NO transcript) plus an --append-system-prompt
#     re-ground directive telling the DM it has no prior transcript this turn and MUST
#     re-ground from the engine (scene_context bundles state/threads/arcs + the recent
#     narration tail) and honor every established fact/name/voice as canon it already
#     authored. $3 (campaign_id) lets the directive name the exact id to pass to
#     scene_context; on beat 1, or when $3 is empty, lean is a no-op (we mint/resume $DSID).
# A per-beat timeout wraps the claude -p in BOTH modes; ONE retry on timeout/failure, then the
# caller's #357 fallback recovers any prose the DM streamed live. The deadline is TIERED off the
# SAME `first` cold-open signal as the effort tier (worldos_dm_timeout, qa/lib_beat_driver.sh): the
# cold open (first=1) is the --effort max world-build that runs ~280–400s, so it gets a generous
# WORLDOS_COLDOPEN_TIMEOUT (default 400s) instead of the routine deadline that was KILLING it;
# continuing beats keep WORLDOS_BEAT_TIMEOUT (default 360s — F12-1). The ONE retry escalates its
# deadline via worldos_dm_retry_timeout. Echoes the DM's final text.
dm_turn() {
  local first="$1" msg="$2" campaign_id="${3:-}" out resume=() extra=() rc beat_timeout
  # SYN-01: pre-beat log-tail mark — ONCE per beat, BEFORE attempt 1 (the in-function retry
  # below must not re-mark: attempt 1's logged prose still counts as this beat's), so the
  # caller's worldos_resolve_dm_reply can tell a GENUINE #357 recovery from RECYCLED prose.
  worldos_dm_prebeat_mark "$STATE_DIR" "$first"
  # #623: prepend the live-progress rule (the ONE shared WORLDOS_LIVE_PROGRESS_RULE in
  # qa/lib_beat_driver.sh — parity with scripts/play_party.sh + scripts/play_codex_dm.sh) so the DM
  # logs an EARLY /events narration beat. Its ABSENCE in this SOLO path was the #623 bug: the DM
  # emitted nothing to /events until the full 85-157s beat completed, so the viewer showed blank and
  # the persona perceived a 'dropped'/'hung' beat. This is the MODEL-COOPERATIVE half; the harness
  # also emits a model-INDEPENDENT heartbeat (worldos_emit_progress_heartbeat) before each beat below.
  msg="$WORLDOS_LIVE_PROGRESS_RULE"$'\n\n'"$msg"
  # The lean-beat path (fresh session + re-ground directive) is the ONE shared implementation
  # in qa/lib_beat_driver.sh — qa/run_duo.sh drives the SAME helper, so the two harnesses can't
  # drift. It populates WORLDOS_DM_LEAN_{SESSION,EXTRA} when this is a continuing beat AND
  # WORLDOS_LEAN_BEATS=1 AND campaign_id is known; otherwise both stay empty and we use the
  # normal resume path below (byte-identical to the flag-off behavior).
  worldos_dm_lean_args "$first" "$campaign_id" "$WORLDOS_LEAN_TAIL"
  if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then
    resume=("${WORLDOS_DM_LEAN_SESSION[@]}")
    extra=("${WORLDOS_DM_LEAN_EXTRA[@]}")
  elif [ "$first" = "0" ]; then
    resume=(--resume "$DSID")
  else
    resume=(--session-id "$DSID")
  fi
  # EFFORT TIER (shared helper, qa/lib_beat_driver.sh): --effort max on the cold open (rich,
  # one-time world-build), --effort medium on continuing beats (the bulk — cuts thinking-latency).
  # Keyed off the SAME `first` signal as lean. DM turn ONLY (the player facade never gets --effort).
  worldos_dm_effort_arg "$first"
  # TIMEOUT TIER (shared helper, qa/lib_beat_driver.sh): the cold open's max-effort world-build runs
  # ~280–400s, so it gets WORLDOS_COLDOPEN_TIMEOUT (default 400s); continuing beats keep
  # WORLDOS_BEAT_TIMEOUT (default 360s). Keyed off the SAME `first` signal as the effort tier above.
  beat_timeout="$(worldos_dm_timeout "$first")"
  out="$DM_LOG.$(date +%s%N).jsonl"
  # #835 Increment 1 — Live Composition flag (default OFF behind WORLDOS_STREAM_BEATS). The shared
  # helpers (qa/lib_beat_driver.sh) build WORLDOS_STREAM_FLAG = (--include-partial-messages) ONLY
  # when streaming is on; when off the array is empty and the splice below expands to nothing, so the
  # invocation is byte-identical to today. _dm_invoke launches the per-attempt stream tailer against
  # $out before the call and kills it after (no-op when off) — the tailer is a sidecar (a crash never
  # affects the beat). Both attempt 1 and the retry (which re-mint $out) get their own tailer.
  worldos_stream_flag_arg
  # F12-8: worldos_timeout (qa/lib_beat_driver.sh) — timeout(1) when present, else a python3
  # fallback with the same rc=124 semantics. A bare `timeout` died rc=127 on stock (non-coreutils)
  # Macs, killing every beat in <1s with the failure masked.
  _dm_invoke() {
    worldos_stream_tailer_start "$out" "$STATE_DIR"
    worldos_timeout "$beat_timeout" \
      claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
        --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
        ${WORLDOS_STREAM_FLAG[@]+"${WORLDOS_STREAM_FLAG[@]}"} \
        --output-format stream-json --verbose > "$out" 2>> "$DM_LOG.err"
    local _rc=$?
    worldos_stream_tailer_stop
    return $_rc
  }
  _dm_invoke; rc=$?
  if [ "$rc" -ne 0 ]; then
    # Surface attempt 1's REAL error (it's a stdout result event in $out; only stderr reaches
    # $DM_LOG.err, so without this the run log shows just a downstream "Session ID … in use").
    worldos_report_attempt_failure "$out" "$rc"
    # FIX 2(b) (#623): a DEADLINE-KILLED (rc=124) ROUTINE beat (first=0) must NOT retry — the retry
    # ESCALATES to the cold-open tier (500/550s), so a routine beat that already burned its full
    # 360s deadline would then burn a SECOND ~500s deadline → a ~14-15min single-beat wall blocking
    # the sequential queue, for a beat the model clearly can't resolve in time. Send it STRAIGHT to
    # the visible-failure path (worldos_resolve_dm_reply → worldos_chatlog_dm_failed in the caller).
    # We STILL retry: (a) any NON-124 failure (real transient API/session errors recover on a fresh
    # session id) and (b) a deadline-killed COLD OPEN (first=1) — the one-time max-effort world-build
    # legitimately needs the escalated budget. Only the rc==124 && first==0 case is dropped.
    if [ "$rc" -eq 124 ] && [ "$first" = "0" ]; then
      echo "[play] DM turn rc=124 (deadline) on a routine beat — NOT retrying (a 2nd escalated deadline would block the queue); routing to the visible-failure path." >&2
    else
    # timeout(1) exits 124 on the deadline; a retriable failure gets ONE retry — on a FRESH session
    # id. A failed attempt STILL registered its --session-id, so reusing it dies "Session ID … is
    # already in use." → 0-byte → empty narration. A lean beat re-mints via worldos_dm_lean_args;
    # the cold-open / legacy --resume path re-mints via worldos_dm_remint_session_on_retry. (The
    # re-ground directive $extra is unchanged — we only refresh the session id.)
    # F12-1: the retry must NOT reuse attempt 1's deadline verbatim — a healthy-but-long beat that
    # tripped the routine deadline would just be killed again at the same mark. Escalate attempt 2
    # to the model-aware cold-open tier (never de-escalating below attempt 1's).
    beat_timeout="$(worldos_dm_retry_timeout "$beat_timeout")"
    echo "[play] DM turn rc=$rc — retrying once with a fresh session (deadline escalated to ${beat_timeout}s)" >&2
    worldos_dm_lean_args "$first" "$campaign_id" "$WORLDOS_LEAN_TAIL"
    if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${WORLDOS_DM_LEAN_SESSION[@]}")
    else
      worldos_dm_remint_session_on_retry ${resume[@]+"${resume[@]}"}
      [ "${#WORLDOS_DM_RETRY_SESSION[@]}" -gt 0 ] && resume=("${WORLDOS_DM_RETRY_SESSION[@]}")
    fi
    # #719: a DEFAULT cold-open (first=1) retry must RESUME attempt-1's minted campaign, not re-seed
    # a SECOND party-less one (the cold-open prompt instructs start_world / "start fresh"). The helper
    # returns $msg UNCHANGED for continuing beats, an authored hero, or a fresh first attempt with no
    # live campaign — byte-identical except on the one bug. Read-only (asks the engine for the live
    # save) and only on the slow retry path.
    if [ "$first" = "1" ]; then
      local _live_cid; _live_cid="$(worldos_live_campaign_id "$ROOT" "$STATE_DIR" "$WORLD")"
      # In RESUME mode the campaign already exists ($RESUME_CAMPAIGN), exactly like the authored-hero
      # case — pass it as the "known campaign" arg so the helper keeps the re-attach $msg verbatim
      # (which already pins campaign_id=$RESUME_CAMPAIGN and forbids start_world) instead of swapping
      # in the fresh-cold-open re-mint prompt. Falls back to HERO_CAMP for the authored-hero path.
      msg="$(worldos_coldopen_retry_msg "$first" "${RESUME_CAMPAIGN:-${HERO_CAMP:-}}" "$_live_cid" "$WORLD" "$msg")"
    fi
    out="$DM_LOG.$(date +%s%N).jsonl"
    _dm_invoke; rc=$?
    [ "$rc" -ne 0 ] && echo "[play] DM turn retry also rc=$rc — relying on engine-logged narration" >&2
    fi
  fi
  cat "$out" >> "$COMBINED" 2>/dev/null
  # SYN-01: shared classification front door — notes the FINAL attempt's $out for the caller's
  # worldos_resolve_dm_reply and echoes NOTHING on an error-class result (a 401's "result" text
  # is the API's error string, never narration), surfacing the re-auth hint instead.
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# Launch the dashboard pointed at THIS game; setting WORLDOS_PLAYER_MOVES flips the
# viewer into interactive (live) mode so the action palette accepts moves — /move
# appends each to $MOVES; WORLDOS_VIEWER_CHAT is the beat-by-beat chat it renders.
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
    WORLDOS_STATE_DIR="$STATE_DIR" \
    WORLDOS_VIEWER_CHAT="$CHAT" \
    WORLDOS_PLAYER_MOVES="$MOVES" \
    WORLDOS_PROVIDER="$PROVIDER" \
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
# F12-10: on an ABNORMAL exit (a crash, a kill -TERM, an early cold-open abort) write the "failed"
# provider-status sidecar so the viewer buckets it as no_provider — BUT never overwrite a clean
# "stopped" (cap/budget) row, and only when the path is already known (set near STATE_DIR). Guarded
# for set -u (the early aborts run before some globals exist). Written BEFORE the viewer is killed so
# the row lands while a read could still serve it.
_play_cleanup() {
  if [ "${PROVIDER_STOPPED_CLEANLY:-0}" != "1" ] && [ -n "${PROVIDER_STATUS:-}" ] \
     && declare -F worldos_write_provider_status >/dev/null 2>&1; then
    worldos_write_provider_status "$PROVIDER_STATUS" failed crashed "WorldOS DM provider exited unexpectedly. Restart the session to continue." "${DM_TURNS:-0}" "scripts/play.sh"
  fi
  declare -F worldos_release_launch_lock >/dev/null 2>&1 && worldos_release_launch_lock "$ROOT"
  kill "$SUP" 2>/dev/null
  [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null
  # #835 Increment 2 FIX B: a SIGINT/SIGTERM mid-beat (Ctrl-C, or a deadline killing the runner)
  # bypasses the per-beat worldos_stream_tailer_stop, orphaning the live-composition tailer. The
  # tailer was launched inside dm_turn's $(...) subshell, so WORLDOS_STREAM_TAILER_PID is empty in
  # THIS parent shell — reap it from the pidfile the start helper persisted. No-op when streaming is
  # OFF (no pidfile) or the tailer already exited (its own self-bounding caps cover a missed signal).
  declare -F worldos_stream_tailer_kill_pidfile >/dev/null 2>&1 \
    && worldos_stream_tailer_kill_pidfile "$STATE_DIR"
}
trap _play_cleanup EXIT
trap '_play_cleanup; exit 130' INT TERM

# Open the browser once OpenWorlds is actually serving (after the campaign exists).
( for _ in $(seq 1 60); do
    curl -s --max-time 2 "http://127.0.0.1:$PORT/state" >/dev/null 2>&1 && break
    sleep 1
  done
  (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/openworlds/") \
    || (command -v xdg-open >/dev/null 2>&1 && xdg-open "http://127.0.0.1:$PORT/openworlds/") || true ) &

echo "WorldOS — playing in OpenWorlds → http://127.0.0.1:$PORT/openworlds/"
echo "  Opening the world… OpenWorlds fills in as the DM narrates the first scene."
echo "  Act via the palette (Say / Do / Continue, dice & combat, click-to-travel). Ctrl-C to stop."
echo "  Save dir: $STATE_DIR"

# The DM opens the world live and hands you a character + an open moment. This relies on
# the SHIPPED dungeon-master skill (its "Generating a world live" mode) — not a QA brief.
# TWO openers: (1) the DEFAULT, where the DM invents the PC live (unchanged); (2) when the
# player AUTHORED a hero in the Creation wizard, the campaign + PC ALREADY EXIST (pre-seeded
# above), so the DM must NOT start_world and must NOT create a character — it re-grounds via
# get_state and opens a scene around the EXISTING authored PC.
if [ "$RESUME" = "1" ]; then
  # RESUME re-attach: the saved campaign $RESUME_CAMPAIGN ALREADY EXISTS on disk (confirmed above).
  # Do NOT start_world (it would mint a NEW empty campaign and orphan the save) and do NOT create or
  # reroll a character — re-ground onto the saved world+party and open a "you're back" scene from
  # where the chronicle stood. The heartbeat targets the saved campaign so /events shows progress
  # within ~1s while the re-ground turn runs.
  worldos_emit_progress_heartbeat "$RESUME_CAMPAIGN" 1 0
  DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo WorldOS adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

RESUME an EXISTING chronicle that is being re-opened — the world, the player's character, and the party ALREADY EXIST. You are NOT starting a new game:
- This session's campaign ALREADY EXISTS: use campaign_id=$RESUME_CAMPAIGN for EVERY engine call. DO NOT call start_world — it would mint a NEW campaign id and ORPHAN this save.
- DO NOT create a character, DO NOT reroll stats, DO NOT re-seat the party. The player's PC and any companions are already in the party with their saved sheets and progress.
- call get_state(\"$RESUME_CAMPAIGN\") FIRST to re-ground: read the world bible, the party (who the player is + any companions), the current location, the world clock (day/time), active quests + standing threads, and the recap of where the chronicle last stood.
- start_session only if get_state shows no active session (so the recap + continuity are intact); otherwise continue the existing session.
- Open a short \"welcome back\" scene that PICKS UP exactly where the chronicle left off — same location, same party, the established threads still in motion — with real quoted dialogue from whoever is present, and hand the player an open moment + a clear, real choice. Do NOT reset the stakes or relocate the party; honor everything the save records as canon you already authored.

CRITICAL — your FINAL output THIS turn MUST BE the opening SCENE itself, written as 2nd-person player-facing prose (addressed to \"you\"): where the party IS right now, what they see/hear/smell, who is present and a real quoted line from them, ending on a clear open moment + choice that continues the saved story. The player reads ONLY your final reply text as the scene — so the prose MUST be IN it. Re-ground via the tools FIRST, then CLOSE the turn by writing the scene. NEVER end this turn on a tool call, and NEVER let your reply be a 3rd-person setup brief or game-system notation — that is your private scratchpad, not the player's scene.

Their actions will arrive as tagged moves — [say] (their dialogue), [do] (an attempt), [check] (roll that skill), [cast]/[use]/[attack] (resolve via the engine) — one per turn from the dashboard.")"
elif [ -n "$HERO_CAMP" ]; then
  # #623 (model-INDEPENDENT heartbeat): the authored-hero campaign ALREADY EXISTS ($HERO_CAMP, pre-
  # seeded above), so write a wrapper-authored opening progress beat to its engine log BEFORE the long
  # cold-open turn — /events renders it within ~1s and the viewer's opening spinner flips to "the scene
  # is arriving above" instead of staying blank for the ~280-500s world-build. (The DEFAULT cold open
  # below mints its campaign INSIDE the turn, so it has no pre-turn campaign to target — it relies on
  # the #718 cold-open spinner + the live-progress rule the DM turn carries.) first=1 → opening teaser.
  worldos_emit_progress_heartbeat "$HERO_CAMP" 1 0
  DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo WorldOS adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

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
  DMSG="$(dm_turn 1 "You are the Dungeon Master for a solo WorldOS adventure. Activate and follow your \`dungeon-master\` skill — run its \"Generating a world live\" mode and hold its craft bar (mechanics sourced from the engine, NPCs speak, the world pushes back, scenes played not logged).

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
worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
# F12-3 (#777): a cold open that produced NO opening (both attempts dead — the 401-class double
# failure proven 2026-06-02) must ABORT NON-ZERO here, BEFORE the move loop. Recording the blank
# unconditionally left an unflagged EMPTY chat row and an indefinitely-"running" session in which
# every beat resumed a nonexistent DM session and masked — under a live viewer serving the empty
# state. A non-zero exit surfaces through the native bridge instead. (play_party.sh has carried
# this same abort since its ensemble landed; this is the play.sh port.)
[ -z "$DMSG" ] && { echo "[play] DM produced no opening — aborting (see $COMBINED)" >&2; exit 1; }

# Resolve the campaign id the DM just minted, BEFORE writing the opening to the chronicle —
# record_dm_reply (#720) needs it to log the opening narration to the engine session log so the
# OpenWorlds client can de-dup the /chat opening blob against /events. The campaign already
# exists here (the cold-open dm_turn ran start_world/get_state, writing the snapshot to
# $STATE_DIR/campaigns/<id>/snapshot.json). A solo launch uses a brand-new state dir, so there
# is exactly one campaign. When a hero was pre-seeded we already know it ($HERO_CAMP). Empty ⇒
# record_dm_reply falls back to an unflagged row AND lean falls back to the normal --resume path
# (both are byte-identical to the pre-#720 behavior when the id is unknown).
# RESUME pins the campaign id to the SAVED campaign (we re-attached it; never let a stray
# start_world the DM might have run mis-point the lean re-ground / record_dm_reply at a parallel id).
CAMPAIGN_ID="${RESUME_CAMPAIGN:-$HERO_CAMP}"
if [ -z "$CAMPAIGN_ID" ] && [ -d "$STATE_DIR/campaigns" ]; then
  # No pre-seeded hero → ask the ENGINE which save is live (the most-recently-played
  # campaign in this world), NOT a blind first-dir pick — so a parallel campaign (a
  # cold-open start_world retry) can't mis-point the lean re-ground at a DIFFERENT save's
  # opening scene (#640 cross-chronicle contamination). Falls back to the first subdir only
  # if the engine can't answer (unreachable / no world match) — no regression vs today.
  CAMPAIGN_ID="$(worldos_live_campaign_id "$ROOT" "$STATE_DIR" "$WORLD")"
  [ -z "$CAMPAIGN_ID" ] && CAMPAIGN_ID="$(find "$STATE_DIR/campaigns" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | head -n1)"
fi
# #720: write the opening through record_dm_reply — stamps engine_logged:true on the chat row
# IFF the opening prose is also logged to the engine session log, so the client renders it ONCE.
record_dm_reply "$CAMPAIGN_ID" "$DMSG" opening; DM_TURNS=1
if [ "$WORLDOS_LEAN_BEATS" = "1" ]; then
  if [ -n "$CAMPAIGN_ID" ]; then
    echo "[play] lean-beats ON — beats 2+ re-ground via scene_context (campaign=$CAMPAIGN_ID), no transcript replay"
  else
    echo "[play] lean-beats ON but campaign id not found under $STATE_DIR/campaigns — beats use the normal resume path" >&2
  fi
fi

# --- F12-3 (#777): the cold open MUST leave a PLAYABLE session ----------------------------------
# (a) NO CAMPAIGN: a cold open that minted no campaign at all is unplayable — the heartbeat, lean
# re-ground, and record_dm_reply all no-op on an empty id, and every beat would fail and mask.
# Abort loudly instead of reporting "running" forever.
if [ -z "$CAMPAIGN_ID" ]; then
  echo "[play] COLD OPEN MINTED NO CAMPAIGN under $STATE_DIR/campaigns — aborting rather than leave a 'running' unplayable session (see $COMBINED)." >&2
  exit 1
fi
# (b) SEATING GUARD: the DM creates/selects the PC live in the cold-open turn, which is
# DM-STOCHASTIC — a forensic .app run built the world but ended the turn with party=[] (the
# viewer's "no_actor" unplayable surface). Guard it the same way the DM turn itself is guarded
# (the pattern play_party.sh proved; the check is the SHARED worldos_pc_seated in
# qa/lib_beat_driver.sh — snapshot-read-only, the viewer's _action_actor contract): one reseat
# retry on a FRESH session id (the consumed cold-open --session-id would collide), then a loud
# non-zero abort. Covers BOTH opener paths — the authored-hero and DM-invents openers converge
# here (the hero path pre-seeds the PC, so it normally passes on the first check).
if ! worldos_pc_seated "$STATE_DIR" "$CAMPAIGN_ID"; then
  echo "[play] cold open seated NO player PC (party has no kind=\"player\" member) — retrying the cold open ONCE on a fresh session…" >&2
  DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
  RESEAT_DMSG="$(dm_turn 1 "Your previous cold-open turn for campaign $CAMPAIGN_ID did NOT seat the player's character — the party still has no kind=\"player\" member, so the game is UNPLAYABLE. Fix this NOW, before anything else.

- use campaign_id=$CAMPAIGN_ID for EVERY engine call. DO NOT call start_world — it would mint a NEW campaign id and ORPHAN this save.
- SEAT THE PLAYER CHARACTER by SELECTING a real canon NPC — NEVER invent a custom character: list_canon_characters(playable_only=true), pick a fitting MID-TIER canon figure with an ingested portrait + real backstory, then load_canon_character(that name, kind=\"player\", add_to_party=true). The party MUST contain the player's kind=\"player\" PC when this turn ends.
- This is a SOLO session: seat ONLY the player — do NOT recruit companions.
- Then CLOSE the turn by writing the opening SCENE as 2nd-person player-facing prose addressed to \"you\" (where the player IS, what they see/hear/smell, who is present + a real quoted line), ending on a clear open moment + choice. NEVER end on a tool call or a 3rd-person setup brief." "$CAMPAIGN_ID")"
  worldos_resolve_dm_reply "$RESEAT_DMSG" "$STATE_DIR"; RESEAT_DMSG="$WORLDOS_DM_REPLY"
  DM_TURNS=$((DM_TURNS + 1))   # the reseat turn counts toward the cap (parity with play_party)
  # #720: the reseat turn re-writes the opening scene — route it through record_dm_reply too.
  if [ -n "$RESEAT_DMSG" ]; then DMSG="$RESEAT_DMSG"; record_dm_reply "$CAMPAIGN_ID" "$DMSG" reseat; echo "[play] reseat turn opened: ${DMSG:0:120}…"; fi
  if ! worldos_pc_seated "$STATE_DIR" "$CAMPAIGN_ID"; then
    echo "[play] COLD-OPEN SEATED NO PC: after a retry the party still has no kind=\"player\" member — aborting rather than hand the player a no_actor session (see $COMBINED)." >&2
    exit 1
  fi
  echo "[play] reseat OK — a player PC is now seated in the party."
fi

# F12-10 (audit 2026-06-11): the provider-status sidecar the viewer reads. The CLAUDE lane never wrote
# it (only the codex wrapper did), so on a turn-cap/budget stop or a crash the viewer fell back to
# "unknown" and showed a live-looking-but-dead dashboard instead of the "DM provider is no longer
# running" surface. Write it through the SHARED worldos_write_provider_status (qa/lib_beat_driver.sh):
# "running" now (the session is playable — the seating guard above passed), "stopped" on a clean cap/
# budget stop, "failed" on an abnormal exit (the EXIT trap below). The sidecar is derived/atomic state,
# not campaign state, so the engine stays the sole writer. A local thin wrapper passes $DM_TURNS
# (PROVIDER_STATUS + PROVIDER_STOPPED_CLEANLY were defined near STATE_DIR so an early-abort EXIT trap
# can write "failed").
provider_status_set() { worldos_write_provider_status "$PROVIDER_STATUS" "$1" "$2" "$3" "$DM_TURNS" "scripts/play.sh"; }
provider_status_set running active "WorldOS DM provider is running."

# Stop the (otherwise human-paced, unbounded) loop once the session hits its cost or turn
# ceiling. total_cost_usd is reported on each turn's result event (accumulated in $COMBINED).
# F12-10: a clean stop writes the "stopped" sidecar (with the reason) + a short grace window so the
# viewer can render the "provider stopped" surface BEFORE the EXIT trap kills it, then sets the
# clean-stop flag so the EXIT trap does NOT overwrite "stopped" with "failed".
over_budget() {
  local spent; spent="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$COMBINED" 2>/dev/null)"
  if [ "$DM_TURNS" -ge "$MAX_TURNS" ]; then
    echo "[play] turn cap ($MAX_TURNS) reached — stopping (raise WORLDOS_PLAY_MAX_TURNS)."
    provider_status_set stopped turn_cap "WorldOS DM stopped after reaching the configured max turns. Increase Max turns or start a new session to continue."
    PROVIDER_STOPPED_CLEANLY=1
    sleep "${WORLDOS_PROVIDER_STOP_GRACE_SECONDS:-20}"
    return 0
  fi
  if awk -v s="${spent:-0}" -v b="$SESSION_BUDGET" 'BEGIN{exit !(s+0>=b+0)}'; then
    echo "[play] session budget reached (~\$$spent/\$$SESSION_BUDGET) — stopping (raise WORLDOS_PLAY_SESSION_BUDGET)."
    provider_status_set stopped budget "WorldOS DM stopped after reaching the session budget (~\$$spent/\$$SESSION_BUDGET). Raise WORLDOS_PLAY_SESSION_BUDGET or start a new session."
    PROVIDER_STOPPED_CLEANLY=1
    sleep "${WORLDOS_PROVIDER_STOP_GRACE_SECONDS:-20}"
    return 0
  fi
  return 1
}

# Human-paced loop: when a new move lands in $MOVES (you acted in the dashboard), resolve
# it with a DM turn and render the next beat. Otherwise idle, waiting on your next move.
MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
# F12-13 (audit 2026-06-11): IDLE CEILING. This loop waits for a HUMAN move in $MOVES. With no human
# acting (a dry-run, or a player who walked away) it would otherwise spin `sleep 2` FOREVER — the same
# orphan class play_party.sh added WORLDOS_PLAY_MAX_IDLE for after an 8.5h orphan, but play.sh (the .app's
# DEFAULT solo entry point) never got it. Stop after WORLDOS_PLAY_MAX_IDLE seconds (default 30 min) with
# no new move; relaunch when ready. Same knob + default as play_party.sh (WORLDOS_/WORLDOS_ twin).
MAX_IDLE="${WORLDOS_PLAY_MAX_IDLE:-1800}"
last_activity=$SECONDS
while true; do
  over_budget && break
  total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
  if [ "$total" -gt "$MCURSOR" ]; then
    last_activity=$SECONDS
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
    PROG_PRE="$(worldos_read_progress "$STATE_DIR")"
    PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
    PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
    PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"
    BEAT_NO=$((DM_TURNS + 1))
    RUNBOOK="$(worldos_runbook_for_beat "$BEAT_NO" "$MAX_TURNS" "$PREV_LOC" "$STATE_DIR")"
    echo "[play] beat $BEAT_NO runbook: ${RUNBOOK%% (*}…"
    # #623 (model-INDEPENDENT heartbeat): write a wrapper-authored progress beat to the engine NOW,
    # before the DM's long think, so /events has a row to render within ~1s and the viewer flips its
    # spinner to "the scene is arriving above" — the player is never left staring at a blank chronicle
    # for the full 85-157s beat (the perceived 'drop'/'hang' #623 filed). Best-effort + non-fatal; the
    # engine stays the sole writer (it routes through log_engine_narration). DM_TURNS = the 0-based beat
    # index → the heartbeat text rotates so a long session never repeats the same teaser.
    worldos_emit_progress_heartbeat "$CAMPAIGN_ID" 0 "$DM_TURNS"
    DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice the NPCs and companion) and narrate the next beat as a played scene. Hand the moment back to the player. ALWAYS end your turn on 2nd-person player-facing narration (addressed to \"you\"), never on a tool call or a 3rd-person status line — the player reads your final reply text as the scene, so the beat's prose MUST be in it. If a move is tagged [set_seed_param] param=value, that is a World-Seed dial the player changed from the Seed screen — apply it with the engine's set_seed_param(campaign_id, param, value[, force=True]) tool (it returns applied/warning), then briefly acknowledge it in-world rather than treating it as an in-scene action.

$RUNBOOK" "$CAMPAIGN_ID")"
    # #357: if the DM turn ended on a tool call / 3rd-person status line, its final reply text is
    # empty — fall back to the player-facing narration the engine logged this beat so the chat is
    # never blank on a resolved move (engine stays the sole writer; this only READS its log).
    worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
    # #720: route the per-beat DM reply through record_dm_reply (engine_logged stamp on success).
    record_dm_reply "$CAMPAIGN_ID" "$DMSG" beat; DM_TURNS=$((DM_TURNS + 1))
    # C — soft clock-tick backstop: advance one phase via the engine only if the DM left the
    # clock frozen this beat (engine stays the sole writer; defers to the DM's in-fiction pacing).
    worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
  else
    # F12-13: idle ceiling — stop a player-less session instead of spinning `sleep 2` forever.
    if [ $((SECONDS - last_activity)) -ge "$MAX_IDLE" ]; then
      echo "[play] idle ${MAX_IDLE}s with no player move — stopping (relaunch when ready; raise WORLDOS_PLAY_MAX_IDLE to wait longer)."
      # F12-10: an idle stop is a CLEAN stop (not a crash) — write "stopped"/idle, set the flag so the
      # EXIT trap does not relabel it "failed".
      provider_status_set stopped idle "WorldOS DM stopped after ${MAX_IDLE}s with no player move. Relaunch to continue (raise WORLDOS_PLAY_MAX_IDLE to wait longer)."
      PROVIDER_STOPPED_CLEANLY=1
      break
    fi
    sleep 2
  fi
done
