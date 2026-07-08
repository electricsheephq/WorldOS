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
# Golden-spine mode: when WORLDOS_ADVENTURE_ID is set, the DM cold-opens an AUTHORED adventure
# (start_adventure — pre-seeded world/arc/companion/quests) instead of generating a world live.
# This is the path that actually runs the 3-act spine end-to-end (see continue-lets-update plan).
ADVENTURE_ID="${WORLDOS_ADVENTURE_ID:-}"
[ -n "$ADVENTURE_ID" ] && echo "[duo] AUTHORED-ADVENTURE mode: start_adventure(\"$ADVENTURE_ID\") at BEATS=$BEATS"
BUDGET="${5:-0.80}"
EX_TEMPFAIL=75
CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

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
WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
# The player facade is a near-free no-tool agent; its model is a separate knob (default sonnet,
# so behavior is unchanged) kept consistent with the party harness's WORLDOS_ACTOR_MODEL.
WORLDOS_ACTOR_MODEL="$(worldos_env ACTOR_MODEL sonnet)"
SCORE_SCRIPT="$(worldos_env SCORE_SCRIPT qa/score.sh)"
ASSERT_BEHAVIORAL_SCRIPT="$(worldos_env ASSERT_BEHAVIORAL_SCRIPT qa/assert_behavioral.py)"
# Opus's high-effort cold-open world-build costs ~$2.4 on its first turn (measured 2026-06-06); a low
# caller per-turn budget (sweep $2.00, fast_probe $0.80) would trip error_max_budget_usd on the duo
# cold-open the same way the .app backend did. Floor the per-turn cap for an Opus DM so the cold-open
# always lands. A CAP, not a spend — routine duo turns spend far less.
case "$WORLDOS_DM_MODEL" in
  *opus*) if awk "BEGIN{exit !($BUDGET < 4.0)}"; then echo "[duo] opus: flooring per-turn budget \$$BUDGET -> \$4.00 (cold-open headroom)"; BUDGET=4.00; fi ;;
esac

# GLM-only settings profile (Wave-1 1C). Sourced AFTER the model vars resolve and
# BEFORE any timeout/budget/retry knob is consumed below, mirroring how
# lib_beat_driver.sh is sourced above. For a Claude run (the default) this is a
# TOTAL no-op — worldos_apply_glm_profile returns immediately when neither role is
# GLM, so the shipped Claude path stays byte-for-byte unchanged. For a GLM run it
# sources ~/.openclaw/secrets/glm.env and raises the cold-open/beat timeouts +
# DM/player retry ceilings (GLM is slower than Opus/Sonnet). See qa/glm_profile.sh.
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile

# --- Lean-per-beat context (PERF, default OFF → byte-identical to today). --------------
# MIRRORS scripts/play.sh exactly (and shares its ONE implementation via the
# worldos_dm_lean_args helper in qa/lib_beat_driver.sh). With WORLDOS_LEAN_BEATS=1, the DM's
# CONTINUING beats (beats 1..N below — NOT the cold open D1) start a FRESH claude session (a
# new --session-id, NO transcript replay) seeded with a re-ground directive: the DM re-grounds
# from the engine's persisted truth via scene_context (state/director/events/companion_arcs +
# the recent player-facing narration TAIL) instead of from the growing transcript. This is the
# whole point of the flag — and the duo QA harness USED to ignore it (its DM turn always
# `--resume`d the full transcript), so the lean path could never be validated through the duo
# runner that qa/release_gate.sh uses. DEFAULT 1 (lean is now STANDARD — validated: ~10–27×
# context drop, story quality held at 4.4); set WORLDOS_LEAN_BEATS=0 to force the legacy
# --resume path (byte-identical to pre-lean). The recent-narration tail depth mirrors
# play.sh's WORLDOS_LEAN_TAIL (default 8).
WORLDOS_LEAN_BEATS="${WORLDOS_LEAN_BEATS:-1}"
WORLDOS_LEAN_TAIL="${WORLDOS_LEAN_TAIL:-8}"
T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$RUN"
CHECKPOINT="$STATE_DIR/.duo_checkpoint.json"
CHECKPOINT_MANIFEST="$STATE_DIR/.duo_checkpoint_offsets.json"
CHECKPOINT_SLOT="duo_checkpoint"
LOCKDIR="$STATE_DIR/.duo_run.lock"
mkdir -p "$T" "$STATE_DIR"

RESUME_MODE=0
LAST_COMPLETED_BEAT=-1
START_BEAT=1
DSID=""
PSID=""
CAMPAIGN_ID=""

duo_quota_protocol_seen() {
  grep -qiE 'api_error_status"[[:space:]]*:[[:space:]]*429|session limit|HTTP 429|hit your (session|usage) limit' "$@" 2>/dev/null
}

duo_quota_error_seen() {
  grep -qiE 'api_error_status"[[:space:]]*:[[:space:]]*429|session limit|HTTP 429|hit your (session|usage) limit|rate[_ -]?limit|too many requests' "$@" 2>/dev/null
}

duo_release_lock() {
  rm -rf "$LOCKDIR" 2>/dev/null || true
}

duo_acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCKDIR/pid"
    trap duo_release_lock EXIT
    return 0
  fi
  local oldpid=""
  oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "[duo] another qa/run_duo.sh process is already using $STATE_DIR (pid $oldpid); use a different run id or wait for it to finish" >&2
    exit 2
  fi
  echo "[duo] removing stale run lock at $LOCKDIR" >&2
  rm -rf "$LOCKDIR" 2>/dev/null || true
  if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "[duo] could not acquire run lock at $LOCKDIR" >&2
    exit 2
  fi
  printf '%s\n' "$$" > "$LOCKDIR/pid"
  trap duo_release_lock EXIT
}

duo_engine_slot() {
  local action="$1" campaign_id="$2"
  [ -n "$campaign_id" ] || return 1
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$action" "$campaign_id" "$CHECKPOINT_SLOT" <<'PY' >/dev/null
import sys
import server

action, campaign_id, slot = sys.argv[1], sys.argv[2], sys.argv[3]
if action == "save":
    server.save_slot(campaign_id, slot)
elif action == "load":
    server.load_slot(campaign_id, slot)
else:
    raise SystemExit(f"unknown checkpoint slot action: {action}")
PY
}

duo_write_manifest() {
  python3 - "$CHECKPOINT_MANIFEST" "$ROOT" "$COMBINED" "$CHAT" "$MOVES" "$TOOLTIMING_PATH" "$T/$RUN.dm.err" "$T/$RUN.player.err" "$T" "$RUN" "$1" <<'PY'
import glob
import json
import os
import sys
from pathlib import Path

manifest, root, *rest = sys.argv[1:]
combined, chat, moves, tooltiming, dm_err, player_err, tdir, run, beat = rest
root_path = Path(root)

def rel(p: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(root_path.resolve()))
    except ValueError:
        return str(Path(p))

files = {}
for raw in [combined, chat, moves, tooltiming, dm_err, player_err]:
    p = Path(raw)
    files[rel(raw)] = p.stat().st_size if p.exists() else 0

dm_files = []
for raw in sorted(glob.glob(str(Path(tdir) / f"{run}.dm.*.jsonl"))):
    dm_files.append(rel(raw))

payload = {
    "last_completed_beat": int(beat),
    "files": files,
    "dm_files": dm_files,
}
path = Path(manifest)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, path)
PY
}

duo_restore_manifest() {
  [ -s "$CHECKPOINT_MANIFEST" ] || return 0
  python3 - "$CHECKPOINT_MANIFEST" "$ROOT" "$T" "$RUN" <<'PY'
import glob
import json
import os
import sys
from pathlib import Path

manifest, root, tdir, run = sys.argv[1:]
root_path = Path(root)
data = json.loads(Path(manifest).read_text(encoding="utf-8"))
files = data.get("files") or {}
for rel, size in files.items():
    path = root_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = int(size)
    if not path.exists():
        if keep == 0:
            path.write_bytes(b"")
        continue
    with path.open("r+b") as handle:
        handle.truncate(keep)

kept = {str((root_path / rel).resolve()) for rel in (data.get("dm_files") or [])}
for raw in glob.glob(str(Path(tdir) / f"{run}.dm.*.jsonl")):
    if str(Path(raw).resolve()) not in kept:
        try:
            os.remove(raw)
        except FileNotFoundError:
            pass
PY
}

duo_write_checkpoint() {
  local beat="$1"
  [ -n "$CAMPAIGN_ID" ] || return 0
  if ! duo_engine_slot save "$CAMPAIGN_ID"; then
    echo "[duo] checkpoint write failed: could not save checkpoint slot for campaign $CAMPAIGN_ID" >&2
    exit 2
  fi
  if ! duo_write_manifest "$beat"; then
    echo "[duo] checkpoint write failed: could not write checkpoint manifest for beat $beat" >&2
    exit 2
  fi
  if ! python3 - "$CHECKPOINT" "$beat" "$PSID" "$DSID" "$CAMPAIGN_ID" "$WORLD" "$PLAYER_PROMPT_FILE" "$BEATS" "$BUDGET" "$CURRENT_SHA" <<'PY'
import json
import os
import sys
from pathlib import Path

path, beat, player_sid, dm_sid, campaign_id, world, persona, total_beats, budget, sha = sys.argv[1:]
payload = {
    "last_completed_beat": int(beat),
    "player_session_id": player_sid,
    "dm_session_id": dm_sid,
    "campaign_id": campaign_id,
    "world": world,
    "persona": persona,
    "total_beats": int(total_beats),
    "budget": budget,
    "sha": sha,
}
p = Path(path)
tmp = p.with_suffix(p.suffix + ".tmp")
tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, p)
PY
  then
    echo "[duo] checkpoint write failed: could not write $CHECKPOINT" >&2
    exit 2
  fi
  LAST_COMPLETED_BEAT="$beat"
  START_BEAT=$((LAST_COMPLETED_BEAT + 1))
}

duo_delete_checkpoint() {
  rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" "$CHECKPOINT_MANIFEST" "$CHECKPOINT_MANIFEST.tmp" 2>/dev/null || true
}

duo_last_dm_chat() {
  python3 - "$CHAT" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
last = ""
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("role") == "dm" and row.get("text") and not row.get("beat_failed"):
            last = row.get("text") or ""
print(last)
PY
}

# #1414: the CONTAMINATED-marker writer for every abort path below (QUOTA/INFRA aborts never
# reach the normal scoring tail, so without this a contaminated run silently landed NO row at
# all — the manual-append gap docs/RUNBOOK-INDEX.md calls out). FAIL LOUD like every other write
# here: a failed marker write is itself a failed run (Universal Run Contract, docs/OPERATIONS.md
# "No row = no run") — never `|| echo WARN`. Writes surface=engine-duo, behavioral=CONTAMINATED,
# NO lens numbers (the watcher contract: infra-fail => no citable row).
duo_persist_contaminated() {  # $1 = reason string
  local reason="$1"; local cb_arg=()
  [ "${LAST_COMPLETED_BEAT:--1}" -ge 0 ] && cb_arg=(--completed-beats "$LAST_COMPLETED_BEAT")
  if ! python3 "$ROOT/qa/scores_persist.py" duo \
      --run-id "$RUN" --build-sha "$CURRENT_SHA" --dm-model "$WORLDOS_DM_MODEL" \
      --beats "$BEATS" ${cb_arg[@]+"${cb_arg[@]}"} \
      --source-path "$T/$RUN" --contaminated-reason "$reason"; then
    echo "[duo] FATAL: scores_db CONTAMINATED-marker write failed — a failed row write is a failed run per the Universal Run Contract (docs/OPERATIONS.md). See the error above." >&2
    exit 1
  fi
}

duo_acquire_lock

if [ -s "$CHECKPOINT" ]; then
  if ! jq -e . "$CHECKPOINT" >/dev/null 2>&1; then
    echo "[duo] checkpoint at $CHECKPOINT is invalid JSON; delete the checkpoint to restart" >&2
    exit 2
  fi
  ck_sha="$(jq -r '.sha // ""' "$CHECKPOINT")"
  if [ "$ck_sha" != "$CURRENT_SHA" ]; then
    echo "[duo] checkpoint sha $ck_sha != current $CURRENT_SHA — a resume across code changes is invalid; delete the checkpoint to restart" >&2
    exit 2
  fi
  ck_world="$(jq -r '.world // ""' "$CHECKPOINT")"
  ck_persona="$(jq -r '.persona // ""' "$CHECKPOINT")"
  ck_beats="$(jq -r '.total_beats // ""' "$CHECKPOINT")"
  if [ "$ck_world" != "$WORLD" ] || [ "$ck_persona" != "$PLAYER_PROMPT_FILE" ] || [ "$ck_beats" != "$BEATS" ]; then
    echo "[duo] checkpoint invocation mismatch (checkpoint world=$ck_world persona=$ck_persona total_beats=$ck_beats; current world=$WORLD persona=$PLAYER_PROMPT_FILE total_beats=$BEATS); delete the checkpoint to restart" >&2
    exit 2
  fi
  RESUME_MODE=1
  LAST_COMPLETED_BEAT="$(jq -r '.last_completed_beat' "$CHECKPOINT")"
  PSID="$(jq -r '.player_session_id' "$CHECKPOINT")"
  DSID="$(jq -r '.dm_session_id' "$CHECKPOINT")"
  CAMPAIGN_ID="$(jq -r '.campaign_id' "$CHECKPOINT")"
  START_BEAT=$((LAST_COMPLETED_BEAT + 1))
fi

if [ "$RESUME_MODE" != "1" ]; then
  rm -rf "$STATE_DIR/campaigns" 2>/dev/null
fi
# #892 follow-up: keep the cold-open `claude -p` (the DM) off the macOS keychain + off any /Volumes
# TCC prompt so the duo QA harness runs headless. GATED no-op without an env/file credential (so the
# Terminal/keychain path is byte-unchanged). Called ONCE here, after STATE_DIR, before the first DM
# `claude -p` below. Defined in qa/lib_beat_driver.sh (sourced above). Never fails the run.
worldos_isolate_claude_auth

# DM gets the engine (state dir patched in); the player gets an EMPTY strict config.
DM_CFG="$STATE_DIR/dm.mcp.json"; PLAYER_CFG="$STATE_DIR/player.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$MOVES"  # the player's structured moves (It.1)
# Wave-1 per-tool timing sidecar (Round-2 wiring): the engine appends one {ts,tool,wall_ms,ok,
# campaign_id} line per tool call to this ABSOLUTE path — but only because we set
# WORLDOS_TOOLTIMING_PATH in the engine MCP env below (it is a no-op when that var is unset, so
# production play pays nothing). latency_rollup.py reads it via --tooltiming to split tool-exec vs
# generation. Truncate at run start so a re-run never appends to a stale sidecar.
TOOLTIMING_PATH="$ROOT/$T/$RUN.tooltiming.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$TOOLTIMING_PATH"
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" "$ROOT" "$TOOLTIMING_PATH" <<'PY'
import json, sys, os
cfg_path, state, out, root, tooltiming = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
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
    if name == "worldos-engine":
        srv.setdefault("env", {})["WORLDOS_STATE_DIR"] = state
        # Wave-1 per-tool timing: tell the engine where to write its tool-timing sidecar.
        srv["env"]["WORLDOS_TOOLTIMING_PATH"] = tooltiming
        # Parity with scripts/play.sh: pin the engine tools (un-defer) so the DM stops burning
        # ~2 ToolSearch round-trips/beat re-discovering them. Set WORLDOS_ENGINE_ALWAYSLOAD=0 for
        # the deferred baseline (the latency A/B arm).
        if os.environ.get("WORLDOS_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
PY
# The player gets ONLY the constrained move facade (worldos-player): it acts through
# tools, never free-text narration; moves land in $MOVES for the orchestrator to relay.
python3 - "$ROOT" "$STATE_DIR" "$MOVES" "$PLAYER_CFG" <<'PY'
import json, sys
root, state, moves, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
json.dump({"mcpServers": {"worldos-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"WORLDOS_STATE_DIR": state, "WORLDOS_PLAYER_MOVES": moves}}}}, open(out, "w"))
PY

if [ "$RESUME_MODE" = "1" ]; then
  if ! duo_engine_slot load "$CAMPAIGN_ID"; then
    echo "[duo] checkpoint could not restore campaign $CAMPAIGN_ID from slot $CHECKPOINT_SLOT; delete the checkpoint to restart" >&2
    exit 2
  fi
  if ! duo_restore_manifest; then
    echo "[duo] checkpoint could not restore transcript offsets from $CHECKPOINT_MANIFEST; delete the checkpoint to restart" >&2
    exit 2
  fi
else
  DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
fi
# The DM's campaign id (for the lean re-ground). Resolved AFTER the cold-open D1 mints the
# world (start_world writes $STATE_DIR/campaigns/<id>/). Declared here (empty) so the DM
# turn()'s lean branch can reference it safely under `set -u` even during the cold open —
# when it's empty, worldos_dm_lean_args no-ops and the normal --resume path is used.
[ "$RESUME_MODE" = "1" ] || CAMPAIGN_ID=""
DM_BRIEF="$(cat qa/play_dm_duo.txt)"; PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"
COMBINED="$T/$RUN.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$COMBINED"
# A clean two-sided conversation log (the player agent's turns AND the DM's), so the
# dashboard can show the PROTAGONIST acting — not just the DM narrating. The DM's own
# stream (COMBINED) doesn't echo the player's turns, so we capture both sides here.
CHAT="$T/$RUN.chat.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$CHAT"
# chatlog is the SHARED lib implementation (qa/lib_beat_driver.sh, reads ambient $CHAT at call
# time). SYN-01/F12-7: a local 2-arg override here used to shadow it AFTER sourcing the lib,
# silently discarding worldos_chatlog_dm's {"fallback_recovered":true} honesty stamp — never
# re-define chatlog in a runner.
if [ "$RESUME_MODE" = "1" ]; then
  echo "[duo] resume checkpoint: run=$RUN last_completed=$LAST_COMPLETED_BEAT next_beat=$START_BEAT campaign=$CAMPAIGN_ID dm=$DSID player=$PSID"
else
  echo "[duo] run=$RUN world=$WORLD beats=$BEATS dm=$DSID player=$PSID"
fi

# $1=role(player|dm) $2=session-id $3=first?(1/0) $4=message ; echoes the agent's reply text
turn() {
  local role="$1" sid="$2" first="$3" msg="$4" out resume=() extra=() rc=0
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$role" = "dm" ]; then
    # LEAN beats (WORLDOS_LEAN_BEATS=1): a continuing DM beat starts a FRESH session + a
    # re-ground directive instead of --resume-ing the full transcript — the SAME implementation
    # scripts/play.sh uses, via the shared worldos_dm_lean_args helper (qa/lib_beat_driver.sh),
    # so the two harnesses can't drift. CAMPAIGN_ID is resolved after the opening beat (it's
    # empty during the cold open D1, so lean correctly no-ops there); the helper ALSO only fires
    # on a continuing beat (first=0) and no-ops on the cold open (first!=0). When lean doesn't
    # fire (flag off / cold open / unknown id) the helper leaves both arrays empty and we keep
    # the --resume/--session-id behavior set above unchanged.
    worldos_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$WORLDOS_LEAN_TAIL"
    if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${WORLDOS_DM_LEAN_SESSION[@]}")
      extra=("${WORLDOS_DM_LEAN_EXTRA[@]}")
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
    # #835 Increment 1 — Live Composition flag (default OFF behind WORLDOS_STREAM_BEATS). Shared
    # helpers (qa/lib_beat_driver.sh) build WORLDOS_STREAM_FLAG = (--include-partial-messages) ONLY
    # when streaming is on; off → empty array → the splice expands to nothing → byte-identical to
    # today. The stream tailer is launched against $out before the call and killed after (no-op when
    # off); it is a sidecar (a crash never affects the beat).
    worldos_stream_flag_arg
    worldos_stream_tailer_start "$out" "$STATE_DIR"
    worldos_timeout "$beat_timeout" \
      claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
        --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
        ${WORLDOS_STREAM_FLAG[@]+"${WORLDOS_STREAM_FLAG[@]}"} \
        --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
    rc=$?
    worldos_stream_tailer_stop
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
      --model "$WORLDOS_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format json 2>> "$T/$RUN.player.err" \
      | jq -r '.result // ""' 2>/dev/null
  fi
}

# A turn, with TRANSIENT-AWARE retry on empty output. A blip shouldn't silently truncate a run —
# but the RIGHT number of retries depends on WHY the turn came back empty:
#   • TRANSIENT (server-side HTTP 500/502/503/529, an "overloaded"/429 rate-limit, an rc=124
#     timeout, an empty-result blip) — retry up to WORLDOS_DM_MAX_ATTEMPTS (default 4) total, with
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
  max="${WORLDOS_DM_MAX_ATTEMPTS:-4}"
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
    # mode is `--session-id $2`, so it populates WORLDOS_DM_RETRY_SESSION with a FRESH `--session-id
    # <uuid>` we hand back to turn as the new sid. Continuing beats ($3=0) use --resume (safe to
    # repeat) — the helper leaves the array empty and we retry verbatim; lean continuing beats already
    # mint their own fresh id inside turn(). The empty-output trigger also fires on a timeout, which
    # worldos_dm_final_text turns into an empty reply.
    if [ "${3:-}" = "1" ]; then
      worldos_dm_remint_session_on_retry --session-id "$2"
      local _fresh="$2"
      [ "${#WORLDOS_DM_RETRY_SESSION[@]}" -ge 2 ] && _fresh="${WORLDOS_DM_RETRY_SESSION[1]}"
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
MCURSOR_FILE="$STATE_DIR/.mcursor"
if [ "$RESUME_MODE" = "1" ]; then
  _move_count="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; _move_count="${_move_count:-0}"
  echo "$_move_count" > "$MCURSOR_FILE"
else
  echo 0 > "$MCURSOR_FILE"
fi
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
# BOUNDED player-intro retry (Wave-1 1C). player_move already nudges ONCE inside a
# single turn if the facade produced no move-tool call; but a slower model (GLM) can
# still return an empty intro at beat 0 even with no timeout. Rather than hard-abort
# on the first empty intro, retry the whole say()-tagged intro up to
# WORLDOS_PLAYER_MAX_ATTEMPTS (default 3, raised to 5 by the GLM profile) times, and
# only abort if STILL empty after the last attempt. The intro stays a say()-tagged
# move through player_move (NOT raw prose) so the behavioral gate
# `player_turns_structured` still sees a structured first turn (a raw-text intro caps
# all G5 lenses ≤2.5 — see the say()-vs-prose note below).
if [ "$RESUME_MODE" != "1" ]; then
PLAYER_INTRO_PROMPT="$PLAYER_BRIEF

This is the very start — the world isn't built and the scene isn't set yet. Introduce your character with a SINGLE say(\"…\"): who they are and what they want. Do NOT do()/attack/cast yet — wait for the DM to open the scene. One say(), nothing else."
WORLDOS_PLAYER_MAX_ATTEMPTS="${WORLDOS_PLAYER_MAX_ATTEMPTS:-3}"
PMSG=""
_pintro_attempt=1
while [ -z "$PMSG" ] && [ "$_pintro_attempt" -le "$WORLDOS_PLAYER_MAX_ATTEMPTS" ]; do
  [ "$_pintro_attempt" -gt 1 ] && echo "[duo] player produced no intro — retry $_pintro_attempt/${WORLDOS_PLAYER_MAX_ATTEMPTS}…" >&2
  PMSG="$(player_move 1 "$PLAYER_INTRO_PROMPT")"
  _pintro_attempt=$((_pintro_attempt + 1))
done
echo "[duo] player intro: ${PMSG:0:120}…"
[ -z "$PMSG" ] && { echo "[duo] player produced no intro after $WORLDOS_PLAYER_MAX_ATTEMPTS attempts — aborting" >&2; exit 1; }
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
worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
echo "[duo] DM opened: ${DMSG:0:120}…"
# #842 Fix E (quota circuit-breaker): if the DM cold-open hit the account session limit (HTTP 429),
# the per-attempt stream-json ($COMBINED) and/or the DM stderr ($T/$RUN.dm.err) carry the
# "session limit" / "HTTP 429" marker. An empty/recovered reply on top of a 429 is NOT a product
# failure — it is an INFRA abort. Detect it BEFORE scoring so we never burn the 3-lens scorer on a
# quota corpse (or, worse, cap-RED a quota'd run as a 2.5 product score). Log a marker the VM sweep
# greps for and exit rc=75 / EX_TEMPFAIL (distinct from the rc=1 genuine-no-opening abort below).
if duo_quota_protocol_seen "$COMBINED" "$T/$RUN.dm.err" || duo_quota_error_seen "$T/$RUN.dm.err" "$STATE_DIR/.dm_last_result"; then
  echo "[duo] QUOTA ABORT — DM cold-open hit the account session limit (HTTP 429). Skipping scoring; this is an INFRA abort, NOT a product measurement." >&2
  echo "[duo] throttled at beat 0 — re-invoke the same command in a fresh window to resume" >&2
  duo_persist_contaminated "QUOTA ABORT at cold-open (HTTP 429 session limit)"
  exit "$EX_TEMPFAIL"
fi
# SYN-01: an empty resolved reply is a FAILED beat (error-class result, recycled-only prose, or
# nothing recovered). Record the wrapper-authored VISIBLE failure row — never the error text,
# never a blank/hidden row — then abort loudly as before.
if [ -z "$DMSG" ]; then
  worldos_chatlog_dm_failed
  echo "[duo] DM produced no opening — aborting (see $COMBINED)" >&2
  duo_persist_contaminated "DM produced no opening at cold-open (empty resolved reply)"
  exit 1
fi
worldos_chatlog_dm "$DMSG"

# Resolve the campaign id the cold open just minted (for the lean re-ground; harmless when
# WORLDOS_LEAN_BEATS=0). D1's start_world wrote the snapshot to
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
if [ "$WORLDOS_LEAN_BEATS" = "1" ]; then
  if [ -n "$CAMPAIGN_ID" ]; then
    echo "[duo] lean-beats ON — beats 2+ re-ground via scene_context (campaign=$CAMPAIGN_ID), no transcript replay"
  else
    echo "[duo] lean-beats ON but campaign id not found under $STATE_DIR/campaigns — beats use the normal resume path" >&2
  fi
fi
duo_write_checkpoint 0
else
  DMSG="$(duo_last_dm_chat)"
  if [ -z "$DMSG" ]; then
    echo "[duo] checkpoint has no prior DM chat row to continue from; delete the checkpoint to restart" >&2
    exit 2
  fi
  echo "[duo] resumed after beat $LAST_COMPLETED_BEAT; continuing with prior DM context: ${DMSG:0:100}…"
fi

# Alternate player <-> DM for BEATS rounds. Each beat is now BEAT-AWARE (decision §A):
# read the clock + location at the START of the beat, pick the ONE moment-specific runbook
# for this beat (scene-intro / reversal / climax / travel-peopling / rising-action) instead
# of the old constant "keep the world moving" paragraph, then after the DM beat run the soft
# clock-tick backstop (decision §C) so a frozen clock advances ONE phase via the engine.
if [ "$START_BEAT" -le "$BEATS" ]; then
for b in $(seq "$START_BEAT" "$BEATS"); do
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
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  echo "[duo] beat $b DM: ${DMSG:0:100}…"
  # SYN-01: an empty resolved reply is a FAILED beat — record the visible failure row (counted
  # by assert_behavioral's dm_beat_honesty) instead of masking with error text/recycled prose.
  if [ -z "$DMSG" ]; then
    worldos_chatlog_dm_failed
    if duo_quota_protocol_seen "$STATE_DIR/.dm_last_result" "$COMBINED" "$T/$RUN.dm.err" || duo_quota_error_seen "$STATE_DIR/.dm_last_result" "$T/$RUN.dm.err"; then
      echo "[duo] QUOTA ABORT — DM beat $b hit the account session limit / rate limit. Skipping scoring; this is an INFRA abort, NOT a product measurement." >&2
      echo "[duo] throttled at beat $b — re-invoke the same command in a fresh window to resume" >&2
      duo_persist_contaminated "QUOTA ABORT at beat $b (HTTP 429 / rate limit)"
      exit "$EX_TEMPFAIL"
    fi
    # #1285: worldos_chatlog_dm_failed stamps $STATE_DIR/.run_infra_invalid.json once the
    # CONSECUTIVE failure streak crosses WORLDOS_INFRA_INVALID_STREAK — a quota window / host
    # death mid-run (rri-a1-duo/duo2), not a product defect. Abort NOW (same rc=2 INFRA ABORT
    # contract as the cold-open quota check above) instead of letting the loop grind on and the
    # scorer measure a contaminated transcript. A single/occasional failed beat (below the
    # streak) still just breaks the loop as before — unchanged behavior.
    if [ -s "$STATE_DIR/.run_infra_invalid.json" ]; then
      cp "$STATE_DIR/.run_infra_invalid.json" "$T/$RUN.infra_invalid.json" 2>/dev/null || true
      echo "[duo] INFRA ABORT — $WORLDOS_DM_BEATS_FAILED_STREAK consecutive DM beat failures at beat $b (see $T/$RUN.infra_invalid.json). Skipping scoring; this is an INFRA collapse, NOT a product measurement." >&2
      duo_persist_contaminated "INFRA ABORT — $WORLDOS_DM_BEATS_FAILED_STREAK consecutive DM beat failures at beat $b"
      exit 2
    fi
    echo "[duo] DM went silent at beat $b; stopping early"
    break
  fi
  worldos_chatlog_dm "$DMSG"

  # C — soft clock-tick backstop: if the DM didn't move the clock this beat, advance one
  # phase via the engine (sole writer). Defers to the DM when it advanced time in-fiction.
  worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
  duo_write_checkpoint "$b"
done
fi

# #1285 defense-in-depth: if the streak was stamped but a caller path didn't already exit 2 above
# (e.g. the threshold was crossed on the LAST beat and the loop simply ended rather than hitting
# the in-loop abort), copy the sentinel alongside the run artifacts now — BEFORE scoring — so
# worldos_run_infra_valid / assert_behavioral.py's run_infra_valid gate can still find it and
# flip the run FATAL instead of letting a contaminated transcript reach a scored end silently.
[ -s "$STATE_DIR/.run_infra_invalid.json" ] && cp "$STATE_DIR/.run_infra_invalid.json" "$T/$RUN.infra_invalid.json" 2>/dev/null

# Wrap + score the DM transcript (it carries the narration + all tool calls).
turn dm "$DSID" 0 "We are out of time. Bring this beat to a clean stopping point and call end_session with a one-line summary." >/dev/null
if duo_quota_protocol_seen "$STATE_DIR/.dm_last_result" "$COMBINED" "$T/$RUN.dm.err" || duo_quota_error_seen "$STATE_DIR/.dm_last_result" "$T/$RUN.dm.err"; then
  echo "[duo] QUOTA ABORT — throttled after beat $LAST_COMPLETED_BEAT during session wrap-up. Skipping scoring; this is an INFRA abort, NOT a product measurement." >&2
  echo "[duo] throttled at beat $LAST_COMPLETED_BEAT — re-invoke the same command in a fresh window to resume" >&2
  duo_persist_contaminated "QUOTA ABORT during session wrap-up (after beat $LAST_COMPLETED_BEAT)"
  exit "$EX_TEMPFAIL"
fi
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
# Lenses. Mechanical + Angry-DM (5e rules-fidelity) score the DM distill `$RUN.md` — the tool
# stream (→ tool / ← result) where the MECHANICS live; Tolkien scores the two-sided $PLAY
# (scene-craft must be judged on the actual back-and-forth).
#
# #1040: the two LIGHT lenses (mechanical ~4 KB rubric, tolkien ~12 KB) run CONCURRENTLY (they
# finish in ~60–150s, so the second adds no wall-clock). The Angry-DM lens (rubric ~32 KB) is the
# HEAVY one — it LEGITIMATELY takes ~400s on a combat-dense transcript (single-turn generation,
# MEASURED 402s). Running it concurrently with the others made the calls share API throughput so the
# heaviest one routinely blew past the timeout and produced NOTHING (the false "combat-scorer hang").
# So: score the two light lenses in parallel, WAIT, THEN score Angry-DM ALONE — full throughput, lands
# near its ~400s baseline, comfortably under score.sh's 600s guard. Adds ~the angrydm time in extra
# wall-clock vs the old all-parallel, but the old way silently LOST the mech lens on every combat run.
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && "$SCORE_SCRIPT" "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
wait
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50
# #842 Fix F (caller half): score.sh now FAILS FAST on a 429, writing a {"quota_exhausted":true,…}
# sentinel into its OUT and exiting rc=2 — so the scorer can quota-trip even when the DM cold-open
# itself didn't (e.g. the account hits the limit AFTER the play, during scoring). Any lens carrying
# that sentinel is NOT a valid scorecard — short-circuit to the same QUOTA ABORT path Fix E uses
# (log the marker the VM sweep greps + exit rc=75 / EX_TEMPFAIL) instead of scoring/gating on a quota corpse.
for _scf in "$T/$RUN.tolkien.json" "$T/$RUN.score.json" "$T/$RUN.angrydm.json"; do
  if [ -f "$_scf" ] && jq -e '.quota_exhausted == true' "$_scf" >/dev/null 2>&1; then
    echo "[duo] QUOTA ABORT — the scorer hit the account session limit (HTTP 429) on $(basename "$_scf"). Skipping the gate + scorecards; INFRA abort, NOT a product measurement." >&2
    echo "[duo] throttled at beat $LAST_COMPLETED_BEAT — re-invoke the same command in a fresh window to resume" >&2
    exit "$EX_TEMPFAIL"
  fi
done
# SCORER-INTEGRITY (WS0a) — a scorer FAILURE must NOT read as GREEN/passing or as a blank no-score.
# score.sh used to exit rc=1 on GENERIC retry-exhaustion WITHOUT writing anything, leaving the lens
# file missing/empty → the line-~560 `jq -r '.overall//"?"'` printed BLANK with no failure marker, so
# a failed scoring masqueraded as a silent valid no-score (observed live: 'story-craft= mechanical=
# angry-dm= behavioral=GREEN'). score.sh now ALWAYS leaves a valid JSON lens file (an {error:scorer_failed}
# sentinel on exhaustion; {quota_exhausted} on a 429 — the latter already short-circuited above). Here we
# VALIDATE each of the 3 lens files (exists + non-empty + valid JSON + NUMERIC .overall, not a sentinel)
# via the shared worldos_validate_lens_file helper, and mark the run a DISTINCT 'unscorable' status if
# any lens is not a trustworthy numeric scorecard. 'unscorable' is NEITHER green nor a blank no-score.
UNSCORABLE=0
UNSCORABLE_DETAIL=""
for _lens in "tolkien:$T/$RUN.tolkien.json" "mechanical:$T/$RUN.score.json" "angry-dm:$T/$RUN.angrydm.json"; do
  _lname="${_lens%%:*}"; _lpath="${_lens#*:}"
  _lstatus="$(worldos_validate_lens_file "$_lpath")"
  if [ "$_lstatus" != "ok" ]; then
    UNSCORABLE=1
    UNSCORABLE_DETAIL="${UNSCORABLE_DETAIL}${UNSCORABLE_DETAIL:+, }${_lname}=${_lstatus}"
    echo "[duo] SCORER FAILURE — lens '${_lname}' is NOT a valid scorecard (${_lstatus}): $(basename "$_lpath"). This run is UNSCORABLE (a scorer failure, NOT a valid no-score)." >&2
  fi
done
if [ "$UNSCORABLE" = "1" ]; then
  echo "[duo] RUN STATUS: unscorable — one or more lenses failed to score (${UNSCORABLE_DETAIL}). A blank/sentinel lens value is a scorer FAILURE, not a passing or no-score run." >&2
fi
# Behavioral gate — flip RED on a structurally broken run (treat it like software).
python3 "$ASSERT_BEHAVIORAL_SCRIPT" "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
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
# --tooltiming folds in the Wave-1 per-tool sidecar (per-kind beat means + tool-exec-vs-generation
# split); harmless when the sidecar is absent/empty (the rollup degrades those fields to null).
if python3 qa/latency_rollup.py --dir "$T" --run "$RUN" --tooltiming "$TOOLTIMING_PATH" --out "$LATENCY_JSON" >/dev/null 2>&1; then
  LAT_SUMMARY="$(jq -r '"s/beat="+(.s_per_beat|tostring)+" cold-open="+(.coldopen_s|tostring)+"s turns/beat="+(.turns_per_beat|tostring)+(if .combat_s_per_beat then " combat="+(.combat_s_per_beat|tostring)+"s" else "" end)+(if .social_s_per_beat then " social="+(.social_s_per_beat|tostring)+"s" else "" end)+(if .tool_exec_pct then " tool="+((.tool_exec_pct*100)|floor|tostring)+"%" else "" end)+(if .slowest_tool then " slowest="+.slowest_tool else "" end)' "$LATENCY_JSON" 2>/dev/null)"
else
  LAT_SUMMARY="latency=unavailable"
fi
# #1414: auto-persist the scores_ledger row at clean completion — FAIL LOUD (never `|| echo WARN`;
# a failed write is a failed run per the Universal Run Contract, docs/OPERATIONS.md "No row = no
# run"). Covers BOTH a GREEN and a behavioral-RED finish (RED is a real product measurement, not
# an abort — only the QUOTA/INFRA abort paths above skip this in favor of the CONTAMINATED marker).
DUO_GATE_ARG=()
if [ "${GATE:-0}" != "0" ]; then
  DUO_GATE_ARG=(--gate-reason "$GATE_REASON")
fi
DUO_UNSCORABLE_ARG=()
[ "$UNSCORABLE" = "1" ] && DUO_UNSCORABLE_ARG=(--unscorable-detail "$UNSCORABLE_DETAIL")
if ! python3 "$ROOT/qa/scores_persist.py" duo \
    --run-id "$RUN" --build-sha "$CURRENT_SHA" --dm-model "$WORLDOS_DM_MODEL" \
    --actor-model "$WORLDOS_ACTOR_MODEL" --beats "$BEATS" --completed-beats "$LAST_COMPLETED_BEAT" \
    --behavioral "$([ "$GATE" = 0 ] && echo GREEN || echo RED)" \
    --story-json "$T/$RUN.tolkien.json" --mech-json "$T/$RUN.score.json" --angry-json "$T/$RUN.angrydm.json" \
    --latency-json "$LATENCY_JSON" --persona "$PLAYER_PROMPT_FILE" --source-path "$T/$RUN.md" \
    ${DUO_GATE_ARG[@]+"${DUO_GATE_ARG[@]}"} ${DUO_UNSCORABLE_ARG[@]+"${DUO_UNSCORABLE_ARG[@]}"} \
    --infra-note "checkpoint=$([ "$RESUME_MODE" = 1 ] && echo resumed || echo fresh); last_completed_beat=$LAST_COMPLETED_BEAT"; then
  echo "[duo] FATAL: scores_db row write failed — a failed write is a failed run per the Universal Run Contract (docs/OPERATIONS.md). See the error above." >&2
  exit 1
fi
# WS0a: print each lens via the shared validator so a scorer FAILURE shows as FAILED (not a blank
# that misreads as a valid no-score). `worldos_lens_display` echoes the numeric .overall for a valid
# card, else FAILED:<status> (missing|invalid|sentinel|nonnumeric). When ANY lens failed, the line
# carries an explicit status=unscorable so a downstream reader / a human can never mistake it for GREEN.
echo "[duo] done. story-craft=$(worldos_lens_display "$T/$RUN.tolkien.json") mechanical=$(worldos_lens_display "$T/$RUN.score.json") angry-dm=$(worldos_lens_display "$T/$RUN.angrydm.json") behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)$([ "$UNSCORABLE" = 1 ] && echo ' status=unscorable') ${LAT_SUMMARY:-}"
duo_delete_checkpoint
exit $GATE
