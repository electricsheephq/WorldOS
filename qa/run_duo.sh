#!/usr/bin/env bash
# TWO-AGENT WorldOS QA: a DM agent and a SEPARATE player agent play against each
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
# Golden-spine mode: when CLAWDND_ADVENTURE_ID is set, the DM cold-opens an AUTHORED adventure
# (start_adventure — pre-seeded world/arc/companion/quests) instead of generating a world live.
# This is the path that actually runs the 3-act spine end-to-end (see continue-lets-update plan).
ADVENTURE_ID="${CLAWDND_ADVENTURE_ID:-}"
[ -n "$ADVENTURE_ID" ] && echo "[duo] AUTHORED-ADVENTURE mode: start_adventure(\"$ADVENTURE_ID\") at BEATS=$BEATS"
BUDGET="${5:-0.80}"

# ── Root + IS_SANDBOX preflight (the real beat-0 blocker, 2026-06-03) ─────────────────
# claude refuses `--dangerously-skip-permissions` (which `--permission-mode bypassPermissions`
# maps to) when running as root, UNLESS IS_SANDBOX=1. On a root QA host (the 32GB support VM) every
# claude turn would otherwise return an empty `.result` and the run aborts with a confusing
# "player produced no intro — aborting". Fail LOUDLY with the fix instead of silently. sweep_v2.sh
# exports IS_SANDBOX=1 itself; a STANDALONE duo on the VM needs `IS_SANDBOX=1 bash qa/run_duo.sh …`.
if [ "$(id -u)" = "0" ] && [ -z "${IS_SANDBOX:-}" ]; then
  echo "[duo] FATAL: running as root without IS_SANDBOX=1 — claude refuses --dangerously-skip-permissions as root." >&2
  echo "[duo]        re-run as: IS_SANDBOX=1 bash qa/run_duo.sh $*" >&2
  exit 2
fi

# The DM model is an env var so A/B-testing Opus vs sonnet for structural adherence is a
# one-flag flip (decision-dm-driver.md §3 "model choice as an orthogonal lever"). Default opus (DECIDED 2026-06-06).
CLAWDND_DM_MODEL="$(worldos_env DM_MODEL opus)"
# The player facade is a near-free no-tool agent; its model is a separate knob (default sonnet,
# so behavior is unchanged) kept consistent with the party harness's WORLDOS_ACTOR_MODEL.
CLAWDND_ACTOR_MODEL="$(worldos_env ACTOR_MODEL sonnet)"
SCORE_SCRIPT="$(worldos_env SCORE_SCRIPT qa/score.sh)"
# Opus's high-effort cold-open world-build costs ~$2.4 on its first turn (measured 2026-06-06); a low
# caller per-turn budget (sweep $2.00, fast_probe $0.80) would trip error_max_budget_usd on the duo
# cold-open the same way the .app backend did. Floor the per-turn cap for an Opus DM so the cold-open
# always lands. A CAP, not a spend — routine duo turns spend far less.
case "$CLAWDND_DM_MODEL" in
  *opus*) if awk "BEGIN{exit !($BUDGET < 4.0)}"; then echo "[duo] opus: flooring per-turn budget \$$BUDGET -> \$4.00 (cold-open headroom)"; BUDGET=4.00; fi ;;
esac

# --- Lean-per-beat context (PERF, default OFF → byte-identical to today). --------------
# MIRRORS scripts/play.sh exactly (and shares its ONE implementation via the
# worldos_dm_lean_args helper in qa/lib_beat_driver.sh). With CLAWDND_LEAN_BEATS=1, the DM's
# CONTINUING beats (beats 1..N below — NOT the cold open D1) start a FRESH claude session (a
# new --session-id, NO transcript replay) seeded with a re-ground directive: the DM re-grounds
# from the engine's persisted truth via scene_context (state/director/events/companion_arcs +
# the recent player-facing narration TAIL) instead of from the growing transcript. This is the
# whole point of the flag — and the duo QA harness USED to ignore it (its DM turn always
# `--resume`d the full transcript), so the lean path could never be validated through the duo
# runner that qa/release_gate.sh uses. DEFAULT 1 (lean is now STANDARD — validated: ~10–27×
# context drop, story quality held at 4.4); set CLAWDND_LEAN_BEATS=0 to force the legacy
# --resume path (byte-identical to pre-lean). The recent-narration tail depth mirrors
# play.sh's CLAWDND_LEAN_TAIL (default 8).
CLAWDND_LEAN_BEATS="${CLAWDND_LEAN_BEATS:-1}"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$RUN"
mkdir -p "$T" "$STATE_DIR"; rm -rf "$STATE_DIR/campaigns" 2>/dev/null
# #892 follow-up: keep the cold-open `claude -p` (the DM) off the macOS keychain + off any /Volumes
# TCC prompt so the duo QA harness runs headless. GATED no-op without an env/file credential (so the
# Terminal/keychain path is byte-unchanged). Called ONCE here, after STATE_DIR, before the first DM
# `claude -p` below. Defined in qa/lib_beat_driver.sh (sourced above). Never fails the run.
worldos_isolate_claude_auth

# DM gets the engine (state dir patched in); the player gets an EMPTY strict config.
DM_CFG="$STATE_DIR/dm.mcp.json"; PLAYER_CFG="$STATE_DIR/player.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"  # the player's structured moves (It.1)
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" "$ROOT" <<'PY'
import json, sys, os
cfg_path, state, out, root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.load(open(cfg_path))
# RE-ROOT every MCP server's `--directory` at THIS repo ($ROOT) so the DM engine,
# rules, and voice run the SAME code as the rest of the harness + the snapshot writer
# (the player facade is already launched from $ROOT). The committed qa.mcp.example.json template
# may hardcode a DIFFERENT absolute checkout (e.g. a stale sibling clone); if the DM
# engine runs older models.py than the writer, EVERY DM tool call fails with
# "extra_forbidden" on the newer snapshot fields and the DM silently falls back to
# narrating — no real travel/combat/state (the version-skew that RED-capped two sessions).
for name, srv in cfg.get("mcpServers", {}).items():
    args = srv.get("args", [])
    if "--directory" in args:
        i = args.index("--directory")
        raw = args[i + 1].rstrip("/")
        if raw.startswith("./"):
            raw = raw[2:]
        if "/servers/" in raw:
            pkg = raw.rsplit("/servers/", 1)[1]
        elif raw.startswith("servers/"):
            pkg = raw[len("servers/"):]
        else:
            pkg = raw
        args[i + 1] = f"{root}/servers/{pkg}"
    if name == "clawdnd-engine":
        srv.setdefault("env", {})["CLAWDND_STATE_DIR"] = state
        # Parity with scripts/play.sh: pin the engine tools (un-defer) so the DM stops burning
        # ~2 ToolSearch round-trips/beat re-discovering them. Set CLAWDND_ENGINE_ALWAYSLOAD=0 for
        # the deferred baseline (the latency A/B arm).
        if os.environ.get("CLAWDND_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
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
# The DM's campaign id (for the lean re-ground). Resolved AFTER the cold-open D1 mints the
# world (start_world writes $STATE_DIR/campaigns/<id>/). Declared here (empty) so the DM
# turn()'s lean branch can reference it safely under `set -u` even during the cold open —
# when it's empty, worldos_dm_lean_args no-ops and the normal --resume path is used.
CAMPAIGN_ID=""
DM_BRIEF="$(cat qa/play_dm_duo.txt)"; PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"
COMBINED="$T/$RUN.jsonl"; : > "$COMBINED"
# A clean two-sided conversation log (the player agent's turns AND the DM's), so the
# dashboard can show the PROTAGONIST acting — not just the DM narrating. The DM's own
# stream (COMBINED) doesn't echo the player's turns, so we capture both sides here.
CHAT="$T/$RUN.chat.jsonl"; : > "$CHAT"
# chatlog is the SHARED lib implementation (qa/lib_beat_driver.sh, reads ambient $CHAT at call
# time). SYN-01/F12-7: a local 2-arg override here used to shadow it AFTER sourcing the lib,
# silently discarding worldos_chatlog_dm's {"fallback_recovered":true} honesty stamp — never
# re-define chatlog in a runner.
echo "[duo] run=$RUN world=$WORLD beats=$BEATS dm=$DSID player=$PSID"

# $1=role(player|dm) $2=session-id $3=first?(1/0) $4=message ; echoes the agent's reply text
turn() {
  local role="$1" sid="$2" first="$3" msg="$4" out resume=() extra=() rc=0
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$role" = "dm" ]; then
    # LEAN beats (CLAWDND_LEAN_BEATS=1): a continuing DM beat starts a FRESH session + a
    # re-ground directive instead of --resume-ing the full transcript — the SAME implementation
    # scripts/play.sh uses, via the shared worldos_dm_lean_args helper (qa/lib_beat_driver.sh),
    # so the two harnesses can't drift. CAMPAIGN_ID is resolved after the opening beat (it's
    # empty during the cold open D1, so lean correctly no-ops there); the helper ALSO only fires
    # on a continuing beat (first=0) and no-ops on the cold open (first!=0). When lean doesn't
    # fire (flag off / cold open / unknown id) the helper leaves both arrays empty and we keep
    # the --resume/--session-id behavior set above unchanged.
    worldos_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
    if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
      extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
    fi
    # EFFORT TIER (shared helper, qa/lib_beat_driver.sh) — SAME implementation play.sh uses, so the
    # two harnesses can't drift: --effort max on the cold open (one-time world-build), --effort
    # medium on continuing beats (the bulk — cuts thinking-latency). Keyed off the SAME `first`
    # signal as lean. DM turn ONLY — the player branch below never gets --effort.
    worldos_dm_effort_arg "$first"
    out="$T/$RUN.dm.$(date +%s%N).jsonl"
    # F12-11 (audit 2026-06-11): the DM turn was UNBOUNDED here — run_duo had no per-beat deadline at
    # all, so a wedged DM beat (hung MCP startup, a stuck model call) hung the whole sweep, and
    # turn_retry's empty-output retry never fired because a hang never RETURNS empty (it never
    # returns). Bound the DM turn through the SAME worldos_timeout shim + worldos_dm_timeout tier the
    # product lanes use (cold-open vs routine, model-aware), so a hang is killed at the deadline (rc=124)
    # → no result event → worldos_dm_final_text echoes empty → turn_retry's empty-output retry fires.
    # Player turn stays unbounded (it is a fast facade turn and was never the hang source).
    local beat_timeout; beat_timeout="$(worldos_dm_timeout "$first")"
    worldos_timeout "$beat_timeout" \
      claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
        --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
        --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
    rc=$?
    cat "$out" >> "$COMBINED"
    # F12-11: surface the REAL failure cause on a nonzero rc with NO error-class result (a timeout
    # rc=124 writes no result event; a CLI crash; a rate-limit exit) — these were MASKED because
    # worldos_dm_final_text below reports only an error-class RESULT event, so a hang/timeout left the
    # structured cause buried in $out and the duo loop showed only "empty turn". The
    # is-error guard avoids a DOUBLE report when the result IS error-class (final_text handles that
    # one). Read-only; echoes a "[dm-attempt] …" reason (+ the 401/403 re-auth hint) to stderr. The
    # rc==0 path is byte-identical to before.
    if [ "$rc" -ne 0 ] && ! worldos_dm_result_is_error "$out"; then
      worldos_report_attempt_failure "$out" "$rc"
    fi
    # SYN-01: the shared classification front door (qa/lib_beat_driver.sh) — notes $out for the
    # caller's worldos_resolve_dm_reply and echoes NOTHING on an error-class result (a 401's
    # "result" text is the API's error string, never a reply), so turn_retry's empty-only retry
    # now fires on error results too instead of chatting them as DM prose.
    worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
  else
    claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format json 2>> "$T/$RUN.player.err" \
      | jq -r '.result // ""' 2>/dev/null
  fi
}

# A turn, with TRANSIENT-AWARE retry on empty output. A blip shouldn't silently truncate a run —
# but the RIGHT number of retries depends on WHY the turn came back empty:
#   • TRANSIENT (server-side HTTP 500/502/503/529, an "overloaded"/429 rate-limit, an rc=124
#     timeout, an empty-result blip) — retry up to CLAWDND_DM_MAX_ATTEMPTS (default 4) total, with
#     a short 3s/8s/20s backoff between, so a 500 CLUSTER no longer aborts a 2-3h overnight run
#     (the gs-ember-18b beat-4 death: a 500 + a single retry that also 500'd killed the whole run).
#   • REAL / fail-fast (a 401/403 auth error, a deterministic bad turn) — do NOT hammer it 4×: take
#     the ONE historical retry (which also re-mints a cold-open session id — see below) and stop.
#     An auth failure stays loudly NON-retryable via worldos_report_attempt_failure's re-auth hint.
# The classifier reads the SAME (out, rc) the just-finished attempt persisted to
# $STATE_DIR/.dm_last_result + .dm_last_rc (the turn helpers run in $(...) subshells, so a local rc
# can't escape — the files are the subshell-safe channel). Bounded by the attempt cap → never loops
# forever. Echoes the reply text (possibly empty after the last attempt).
turn_retry() {
  local r last_out last_rc transient attempt max
  max="${CLAWDND_DM_MAX_ATTEMPTS:-4}"
  # SYN-01: pre-beat log-tail mark — ONCE per beat, BEFORE attempt 1 (the retries must not
  # re-mark: attempt 1's logged prose still counts as this beat's), so the resolve path can
  # tell a GENUINE #357 recovery from RECYCLED pre-beat prose. File-based (subshell-safe).
  worldos_dm_prebeat_mark "$STATE_DIR"
  r="$(turn "$@")"
  attempt=1
  while [ -z "$r" ] && [ "$attempt" -lt "$max" ]; do
    # Classify WHY attempt #$attempt came back empty, from what it just persisted.
    last_out="$(cat "$STATE_DIR/.dm_last_result" 2>/dev/null | tail -n1)"
    last_rc="$(cat "$STATE_DIR/.dm_last_rc" 2>/dev/null | tail -n1)"; last_rc="${last_rc:-0}"
    transient=0
    worldos_dm_failure_is_transient "$last_out" "$last_rc" && transient=1
    # FAIL-FAST: a REAL failure (auth/deterministic) gets exactly the ONE historical retry, never 4×.
    # We allow that single retry (attempt==1) because it ALSO re-mints a cold-open session id that an
    # auth-failed-but-registered attempt 1 would otherwise collide on; past that, stop and don't mask
    # a deterministic failure as flakiness. (worldos_report_attempt_failure already surfaced the
    # 401/403 re-auth hint when the attempt ran.)
    if [ "$transient" != "1" ] && [ "$attempt" -ge 2 ]; then
      echo "[duo] empty turn ($1) — failure looks REAL (not transient); not retrying further." >&2
      break
    fi
    if [ "$transient" = "1" ]; then
      echo "[duo] empty turn ($1) — TRANSIENT failure (rc=$last_rc); retry $((attempt + 1))/$max after backoff…" >&2
      worldos_dm_retry_backoff "$attempt"
    else
      echo "[duo] empty turn ($1) — retrying once…" >&2
    fi
    # A cold-open ($3=1) retry must NOT reuse $2's already-registered --session-id (a failed but
    # registered attempt → "Session ID … is already in use." → empty output again). F12-11: re-mint
    # via the SHARED worldos_dm_remint_session_on_retry (qa/lib_beat_driver.sh) — the SAME re-mint
    # implementation scripts/play.sh + play_party.sh use, so the three harnesses can't drift. The
    # helper inspects the prior turn's resume MODE (which `turn` built from $3): on a cold open the
    # mode is `--session-id $2`, so it populates CLAWDND_DM_RETRY_SESSION with a FRESH `--session-id
    # <uuid>` we hand back to turn as the new sid. Continuing beats ($3=0) use --resume (safe to
    # repeat) — the helper leaves the array empty and we retry verbatim; lean continuing beats already
    # mint their own fresh id inside turn(). The empty-output trigger also fires on a timeout, which
    # worldos_dm_final_text turns into an empty reply.
    if [ "${3:-}" = "1" ]; then
      worldos_dm_remint_session_on_retry --session-id "$2"
      local _fresh="$2"
      [ "${#CLAWDND_DM_RETRY_SESSION[@]}" -ge 2 ] && _fresh="${CLAWDND_DM_RETRY_SESSION[1]}"
      r="$(turn "$1" "$_fresh" "$3" "${@:4}")"
    else
      r="$(turn "$@")"
    fi
    attempt=$((attempt + 1))
  done
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

# P0: the player introduces their character with a SINGLE say() — who they are + what they're after.
# They do NOT act yet: the world isn't built and the scene isn't set, so "firing off" actions into a
# void reads as the PLAYER authoring the story (owner live-QA: "the player just starts making up
# story; there's no intro"). The DM opens the scene next (D1); the player's first real action comes
# at beat 1.
#
# The intro goes through say() (a TAGGED [say] move via player_move), NOT raw prose: the behavioral
# gate `player_turns_structured` requires every player turn to be a facade move, and a raw-text intro
# trips it RED (which caps all G5 lenses ≤2.5). An earlier "plain-text intro" experiment (#636) did
# exactly that — it ALSO mis-diagnosed the real beat-0 blocker, which is `IS_SANDBOX=1`: on a root QA
# host claude refuses `--dangerously-skip-permissions` → empty turn → "player produced no intro". The
# root guard above enforces that, so the say()-based intro lands cleanly and stays a tagged move.
PMSG="$(player_move 1 "$PLAYER_BRIEF

This is the very start — the world isn't built and the scene isn't set yet. Introduce your character with a SINGLE say(\"…\"): who they are and what they want. Do NOT do()/attack/cast yet — wait for the DM to open the scene. One say(), nothing else.")"
echo "[duo] player intro: ${PMSG:0:120}…"
[ -z "$PMSG" ] && { echo "[duo] player produced no intro — aborting" >&2; exit 1; }
chatlog player "$PMSG"

# D1: DM spins up the world and opens the scene around the player's concept. Golden-spine mode
# (ADVENTURE_ID set) swaps the world-gen setup for an AUTHORED adventure run; the else-branch is
# byte-identical to the long-standing world-gen directive (default path unchanged).
if [ -n "$ADVENTURE_ID" ]; then
  SETUP_DIRECTIVE="Load the AUTHORED ADVENTURE now: call start_adventure(\"$ADVENTURE_ID\") — it seeds the pre-authored world, locations, voiced NPCs, the authored companion, and the opening quest, and returns its premise + a 3-ACT structure (also read its adventure.md scenes). Then seat THEIR character as the PLAYER CHARACTER (the PC) from their intro — for an authored adventure you MAY create_character to fit ITS setting (its world is its own, not the canon BG roster); NEVER seat the player as a companion or NPC. OPEN ACT 1 — human-scale and personal — and then RUN THE AUTHORED ARC across the WHOLE session as a living 3-act story: when the authored companion's meeting lands, recruit_companion them on-screen (voiced, a real wound); DRIVE deliberately toward each act's climax (never linger in one scene); at a rest, play a REAL camp_scene where the companion speaks and the player's choices MOVE their approval (record_decision with approval_tags / adjust_attitude, then check_companion_arc to fire gates); and resolve quests with complete_quest(evolves_to=…, callback_in_days=…) so threads ECHO into later acts. End by handing the moment to the player."
else
  SETUP_DIRECTIVE="Do the setup now: start_world(\"$WORLD\"), start_session, then seat THEIR character as the PLAYER CHARACTER (the PC). The player ALWAYS plays a REAL, LIVING CANON NPC — their persona names one (e.g. Aubree, a Flaming Fist ranger). Seat that exact figure via load_canon_character(their canon name, kind=\"player\", add_to_party=true) so they get a real backstory + ingested portrait — NEVER create_character / invent a custom PC, NEVER seat the player's own character as a companion or NPC, and NEVER a canon-DEAD figure (a corpse like Dal Lightspark is rejected as a PC; if the seat returns an error, pick a living canon NPC instead). A companion is a DIFFERENT character the player MEETS. Then OPEN the scene — human-scale and personal — grounded in the world's canon, responding to their stated intent. A companion should ENTER as part of that opening scene: someone the player MEETS on-screen (voiced, with a real wound and a reason they fall in together) — recruit_companion / load_canon_character(kind=\"companion\") as that meeting lands, NOT a silent name dropped into the party before the player has met anyone. End by handing the moment to the player."
fi
DMSG="$(turn_retry dm "$DSID" 1 "$DM_BRIEF

Begin the session. The player agent introduces their character and opening intent:

$PMSG

$SETUP_DIRECTIVE OUTPUT DISCIPLINE — your final reply IS the opening scene: write it as 2nd-person in-fiction PROSE + quoted dialogue ONLY. NEVER narrate your own setup/process — no \"State is grounded\", no \"the cold open is on the dashboard\", no \"Closing my turn on the scene\", no 3rd-person status line. The very first words the player reads must be INSIDE the fiction.")"
# #357: recover the engine's logged narration if the DM turn ended on a tool call / status
# line (empty final reply) — so a tool-final-but-narrated turn isn't mistaken for silence.
worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$CLAWDND_DM_REPLY"
echo "[duo] DM opened: ${DMSG:0:120}…"
# SYN-01: an empty resolved reply is a FAILED beat (error-class result, recycled-only prose, or
# nothing recovered). Record the wrapper-authored VISIBLE failure row — never the error text,
# never a blank/hidden row — then abort loudly as before.
if [ -z "$DMSG" ]; then
  worldos_chatlog_dm_failed
  echo "[duo] DM produced no opening — aborting (see $COMBINED)" >&2
  exit 1
fi
worldos_chatlog_dm "$DMSG"

# Resolve the campaign id the cold open just minted (for the lean re-ground; harmless when
# CLAWDND_LEAN_BEATS=0). D1's start_world wrote the snapshot to
# $STATE_DIR/campaigns/<id>/snapshot.json. The run wipes $STATE_DIR/campaigns at setup, so one
# campaign is EXPECTED — but a cold-open start_world RETRY (or the DM mistakenly re-calling
# start_world) can mint a SECOND, PARALLEL campaign in the same state dir. The old largest-
# snapshot / first-dir heuristics could then select the WRONG one, and a transcript-free lean
# beat re-grounding against it folds a DIFFERENT save's opening scene into scene_context — the
# #640 cross-chronicle contamination. So pin the LEAN id to the ENGINE-authoritative LIVE save
# (the most-recently-played campaign in this world), not a file-size/dir-order guess. Empty ⇒
# the DM turn's lean branch no-ops and the normal --resume path is used (no regression).
CAMPAIGN_ID="$(worldos_live_campaign_id "$ROOT" "$STATE_DIR" "$WORLD")"
if [ -z "$CAMPAIGN_ID" ]; then
  # Defensive fallback (engine unreachable / no world_id match): the sole campaign subdir.
  CAMPAIGN_SNAP="$(worldos_snapshot_path "$STATE_DIR")"
  if [ -n "$CAMPAIGN_SNAP" ]; then
    CAMPAIGN_ID="$(basename "$(dirname "$CAMPAIGN_SNAP")")"
  elif [ -d "$STATE_DIR/campaigns" ]; then
    CAMPAIGN_ID="$(find "$STATE_DIR/campaigns" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | head -n1)"
  fi
fi
if [ "$CLAWDND_LEAN_BEATS" = "1" ]; then
  if [ -n "$CAMPAIGN_ID" ]; then
    echo "[duo] lean-beats ON — beats 2+ re-ground via scene_context (campaign=$CAMPAIGN_ID), no transcript replay"
  else
    echo "[duo] lean-beats ON but campaign id not found under $STATE_DIR/campaigns — beats use the normal resume path" >&2
  fi
fi

# Alternate player <-> DM for BEATS rounds. Each beat is now BEAT-AWARE (decision §A):
# read the clock + location at the START of the beat, pick the ONE moment-specific runbook
# for this beat (scene-intro / reversal / climax / travel-peopling / rising-action) instead
# of the old constant "keep the world moving" paragraph, then after the DM beat run the soft
# clock-tick backstop (decision §C) so a frozen clock advances ONE phase via the engine.
for b in $(seq 1 "$BEATS"); do
  # Progression snapshot at the START of this beat (drives both the runbook + the tick).
  PROG_PRE="$(worldos_read_progress "$STATE_DIR")"
  PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
  PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
  PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"

  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) for this beat using your tools — say / do / request_check / cast_spell / use_item / attack (look or my_sheet first if useful). Tools only.")"
  echo "[duo] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[duo] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"

  RUNBOOK="$(worldos_runbook_for_beat "$b" "$BEATS" "$PREV_LOC" "$STATE_DIR")"
  echo "[duo] beat $b runbook: ${RUNBOOK%% (*}…"
  # Campaign Director (#72): surface what the campaign OWES this beat (untracked hook -> add_quest,
  # silent NPC to voice, due consequence) so the DM is reminded structurally (closes the add_quest
  # reach-for gap). Empty when nothing's owed -> no change to the prompt.
  DIRECTOR="$(worldos_director_advisory "$ROOT" "$STATE_DIR")"
  [ -n "$DIRECTOR" ] && echo "[duo] beat $b director: ${DIRECTOR:0:80}…"
  # Quest & Arc engine, Layer 3: surface any stumble-into EVENT whose contract-safe trigger holds
  # this beat (a set flag / faction rep / reached day) so the DM STAGES the decisional in-character
  # instead of leaving it dark (the present_events reach-for gap — same fix as the Director block
  # above). Read-only; empty when nothing's available -> no change to the prompt.
  EVENT_ADV="$(worldos_event_advisory "$ROOT" "$STATE_DIR")"
  [ -n "$EVENT_ADV" ] && echo "[duo] beat $b event: ${EVENT_ADV:0:80}…"
  DMSG="$(turn_retry dm "$DSID" 0 "The player does:

$PMSG

Resolve it through the engine (roll/cast/attack as needed), then PLAY the next beat as a full lived scene — NOT a fragment: any NPC (or the companion) in the scene SPEAKS at least one quoted line in their own voice; let them push back, hesitate, lie, or counter when it's real (don't just grant every ask); and weave the open moment back to the player INTO the scene — never a bare 'Your move.' / 'What do you do?' on its own line.

$RUNBOOK

$DIRECTOR

$EVENT_ADV")"
  # #357: recover engine-logged narration before the silence check, so a turn that ended on a
  # tool call but logged real prose isn't mis-flagged as a silent DM (and isn't blank in chat).
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$CLAWDND_DM_REPLY"
  echo "[duo] beat $b DM: ${DMSG:0:100}…"
  # SYN-01: an empty resolved reply is a FAILED beat — record the visible failure row (counted
  # by assert_behavioral's dm_beat_honesty) instead of masking with error text/recycled prose.
  if [ -z "$DMSG" ]; then
    worldos_chatlog_dm_failed
    echo "[duo] DM went silent at beat $b; stopping early"
    break
  fi
  worldos_chatlog_dm "$DMSG"

  # C — soft clock-tick backstop: if the DM didn't move the clock this beat, advance one
  # phase via the engine (sole writer). Defers to the DM when it advanced time in-fiction.
  worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
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
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && "$SCORE_SCRIPT" "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50 &
wait
# Behavioral gate — flip RED on a structurally broken run (treat it like software).
python3 qa/assert_behavioral.py "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
# Honest scoring: a gate-RED (non-progressing/structurally broken) run must NOT display as 4.1.
# CAP both recorded scorecards to ≤2.5 / INVALID and annotate WHY (the failed checks), so a dead
# scene can't masquerade as prestige play. Engine/scoring untouched on a GREEN run.
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  worldos_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
# F13-4 (#753): derive the latency ledger (s_per_beat / coldopen_s / turns_per_beat) from the
# per-beat $RUN.dm.<ns>.jsonl transcripts this run already wrote — the missing #753 budget ledger.
# Non-fatal: a derivation hiccup must never fail an otherwise-good run. The JSON is the handoff
# for scores_db.add_run(**{s_per_beat,coldopen_s,turns_per_beat}); the figures also print here.
LATENCY_JSON="$T/$RUN.latency.json"
if python3 qa/latency_rollup.py --dir "$T" --run "$RUN" --out "$LATENCY_JSON" >/dev/null 2>&1; then
  LAT_SUMMARY="$(jq -r '"s/beat="+(.s_per_beat|tostring)+" cold-open="+(.coldopen_s|tostring)+"s turns/beat="+(.turns_per_beat|tostring)' "$LATENCY_JSON" 2>/dev/null)"
else
  LAT_SUMMARY="latency=unavailable"
fi
echo "[duo] done. story-craft=$(jq -r '.overall//"?"' "$T/$RUN.tolkien.json" 2>/dev/null) mechanical=$(jq -r '.overall//"?"' "$T/$RUN.score.json" 2>/dev/null) angry-dm=$(jq -r '.overall//"?"' "$T/$RUN.angrydm.json" 2>/dev/null) behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED) ${LAT_SUMMARY:-}"
exit $GATE
