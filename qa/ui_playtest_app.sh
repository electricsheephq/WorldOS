#!/usr/bin/env bash
# WorldOS AI PLAYTESTER — the BUILT .app surface (§8.2, P0). Issue: WorldOS-OPERATING-GOAL.md.
#
# WHY THIS EXISTS (the failure §0 of the operating goal): qa/ui_playtest.sh boots its OWN
# viewer/server.py with CLAWDND_PLAYER_MOVES set → can_act:true → a PLAYABLE surface the
# harness wires up for itself. The shipped dist/WorldOS.app launches the viewer WITHOUT that
# env → read-only "director's view" → a surface the user can NEVER reach by launching the app.
# Every "all green" run there tested the wrong thing. THIS harness tests the BUILT .app surface
# and the byte-identical backend the app shells — never a harness-wired port (P0).
#
# Two parts, one run:
#   (A) NATIVE-TRANSITION GATE — re-verifies release-blocker #356.
#       pkill the app + THIS checkout's stale viewers; rm -rf dist/WorldOS.app; fresh
#       `script/build_and_run.sh` (off the CURRENT checkout's HEAD → must include the #356 banner
#       fix). Launch the .app, DISCOVER its read-only launcher viewer's actual port (the .app's
#       PortFinder walks up from preferredPort 8765, which is often already taken on this host —
#       we never assume 8765), RAISE the .app frontmost (AppKit activate — not System-Events UI
#       scripting, so no TCC dialog) and CGEvent-click the launcher's primary play CTA
#       ("RESUME → PLAY"; re-clicking on a focus-race miss). Then assert the bridge MINTED a
#       provider session: a NEW play-state/<run> dir appears AND a NEW viewer (a different port)
#       whose /session-surface reports can_act:true. Screenshot before+after (best-effort;
#       see screenshot()). PASS = a live, playable session; FAIL = stuck read-only. The honest
#       #356 re-verify on the BUILT app — PASS/FAIL is the session-surface ground truth, never a
#       screenshot. (NOTE: the CGEvent click is reliable on a PRISTINE, frontmost launcher — i.e.
#       a fresh build+launch, which is exactly what this gate does — but is flaky to re-issue
#       against an already-clicked/dirtied app instance on a busy multi-app desktop.)
#
#   (B) PERSONA LOOP — the playable surface the .app reaches once #356 mints the session.
#       Launch scripts/play_party.sh <world> <run> <port> (the EXACT command the native
#       startProviderSession bridge shells; solo → execs scripts/play.sh, which boots a LIVE
#       viewer with CLAWDND_PLAYER_MOVES set + its own DM cold-open + DM resolver loop). Then
#       drive that viewer with the real Playwright palette as <persona> for <beats> moves. This
#       is byte-identical backend to the .app — the honest play surface, NOT a harness-only port.
#
# Usage:   qa/ui_playtest_app.sh <run> <world> <persona> <beats> <budget>
# Example: qa/ui_playtest_app.sh smoke1 baldurs-gate newbie 6 4.00
#   <beats>  = max player palette ACTIONS before part B stops (a soft cap; player may give_up).
#   <budget> = USD cap for the WHOLE run (parts A + B; we stop part B early if A+DM already spent it).
#
# Flags (env):
#   WOS_APP_SKIP_BUILD=1       reuse an already-running .app (skip pkill/rebuild) — for fast inner loop.
#   WOS_APP_NO_GLOBAL_KILL=1   do not pkill other WorldOSApp processes (used for takeover smoke).
#   WOS_APP_PART=A|B|AB        run only part A, only part B, or both (default AB).
#   WOS_APP_SELECTED_PROVIDER=codex|scripted|claude|openclaw
#                               set the native app's provider preference before minting a session.
#   WOS_APP_PLAYER_AGENT=claude|codex
#                               part B UI-driving agent (default: claude for legacy compatibility).
#   WOS_APP_KEEP_MINTED_BACKEND=1
#                               part A only: leave the .app-minted provider backend alive so
#                               an operator can continue a short built-app gameplay playtest.
#                               Also waits for first-turn readiness: seated actor, enabled actions,
#                               and visible narration/chat, not merely can_act:true.
#   WORLDOS_DM_MODEL           DM model (default opus — DECIDED 2026-06-06, see docs/MODEL-TIERING-STRATEGY.md). CLAWDND_PLAY_BUDGET caps each DM turn.
#
# Produces under qa/ui_playtest_runs/<run>/:
#   native/  before.png after.png transition.json transition.log       (part A)
#   player/screenshots/*.png player/a11y/*.txt bugs.ndjson actions.ndjson
#   console.ndjson network.ndjson score.json summary.md meta.json        (part B)
#   run.json  — top-level {build_sha, version, part_a, part_b, $spend}.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
. "$ROOT/qa/lib_beat_driver.sh"  # worldos_env + shared helpers (snapshot path, cost, etc.)

RUN="${1:-app-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
PERSONA="${3:-newbie}"
BEATS="${4:-6}"
BUDGET="${5:-4.00}"
PART="$(worldos_env APP_PART "${WOS_APP_PART:-AB}")"
KEEP_MINTED_BACKEND="${WOS_APP_KEEP_MINTED_BACKEND:-0}"
SELECTED_PROVIDER="${WOS_APP_SELECTED_PROVIDER:-}"
PLAYER_AGENT="${WOS_APP_PLAYER_AGENT:-claude}"
NATIVE_AUTOSTART="${WOS_APP_NATIVE_AUTOSTART:-0}"
CODEX_HOME_FOR_APP="${WOS_APP_CODEX_HOME:-${CODEX_HOME:-}}"
# Part-A cold-open mint deadline (seconds). The #356 banner spawns the DM cold open, whose
# --effort max world-build runs ~280–400s. FIX 3 (#623): the old FLAT 420 was SHORTER than the
# DM cold-open's OWN model-aware timeout (clawdnd_dm_timeout 1 = 500 opus / 550 non-opus), so a
# healthy-but-slow cold open in the 420–500s band was abandoned by THIS poll ~80s before the DM
# itself would have given up — a coin-flip flaky leg. Derive the deadline FROM that same tier
# (cold-open timeout + a ~90s mint/IO margin) so the poll always outlasts the cold open it waits
# on. lib_beat_driver.sh is already sourced (line 62), so clawdnd_dm_timeout is in scope. The
# WORLDOS_COLDOPEN_TIMEOUT env flows through clawdnd_dm_timeout; the explicit
# WOS_APP_PART_A_DEADLINE override still wins (fast inner loops).
_part_a_coldopen_tier="$(CLAWDND_DM_MODEL="$(worldos_env DM_MODEL opus)" clawdnd_dm_timeout 1)"
case "$_part_a_coldopen_tier" in ''|*[!0-9]*) _part_a_coldopen_tier=500 ;; esac
PART_A_DEADLINE="${WOS_APP_PART_A_DEADLINE:-$(( _part_a_coldopen_tier + 90 ))}"
# Launcher-viewer readiness window (seconds). The .app's built-in viewer must answer
# /openworlds/ 200 before we click/auto-start. Two regimes:
#  * CLICK path (default): a launcher UI shell serves /openworlds/ 200 almost immediately,
#    THEN the CTA click triggers the cold-open (whose ~400s mint is covered by the separate
#    PART_A_DEADLINE poll). A short 40s window is correct here (cold Swift start + viewer
#    spawn + first art-root scan over ~2.3k images).
#  * AUTOSTART path: the .app spawns play.sh DIRECTLY — there is NO pre-session launcher UI.
#    play.sh's viewer_supervisor relaunches the viewer until the campaign is minted, so
#    /openworlds/ only returns 200 AFTER the DM cold-open mints (~280-400s). A 40s window
#    would spuriously FAIL as `no_launcher` long before the mint. So the autostart window
#    must cover the cold-open: default it to the cold-open mint budget (PART_A_DEADLINE).
# Both env-overridable via WOS_APP_LAUNCHER_WAIT_S.
if [ "$NATIVE_AUTOSTART" = "1" ]; then
  LAUNCHER_WAIT_S="${WOS_APP_LAUNCHER_WAIT_S:-$PART_A_DEADLINE}"
else
  LAUNCHER_WAIT_S="${WOS_APP_LAUNCHER_WAIT_S:-40}"
fi
if [ "$KEEP_MINTED_BACKEND" = "1" ] && [ "$PART" != "A" ]; then
  printf '[uipt-app] WOS_APP_KEEP_MINTED_BACKEND=1 requires WOS_APP_PART=A; refusing to mix kept native backend with part B.\n' >&2
  exit 2
fi
if [ "$NATIVE_AUTOSTART" = "1" ] && [ "${WOS_APP_SKIP_BUILD:-0}" = "1" ]; then
  printf '[uipt-app] WOS_APP_NATIVE_AUTOSTART=1 requires a fresh app launch; disable WOS_APP_SKIP_BUILD or use the click path.\n' >&2
  exit 2
fi

PW_DIR="$ROOT/qa/playwright"
APP_BUNDLE="$ROOT/dist/WorldOS.app"
PREFERRED_PORT="${WOS_APP_PREFERRED_PORT:-8765}"   # matches RootView.swift @AppStorage("preferredPort") default
DM_MODEL="$(worldos_env DM_MODEL opus)"
PLAYER_MODEL="$(worldos_env UIPT_PLAYER_MODEL sonnet)"
PERSONA_FILE="$ROOT/qa/play_player_browser_${PERSONA}.txt"

RUNDIR="$ROOT/qa/ui_playtest_runs/$RUN"
NATIVE_DIR="$RUNDIR/native"
PLAYERDIR="$RUNDIR/player"
rm -rf "$RUNDIR" 2>/dev/null
mkdir -p "$NATIVE_DIR" "$PLAYERDIR/screenshots" "$PLAYERDIR/a11y"
NATIVE_LAUNCHER_STATE_DIR="$RUNDIR/native-launcher-state"

# #735: each persona REUSES its play-state store (play-state/$RUN and the Part-B store
# play-state/${RUN}-b). The ': >' truncations in play.sh only reset the sidecars — never the
# campaigns/ tree — so a RE-RUN mints a SECOND seated campaign on top of the prior run's save.
# Two seated saves with equal recency in one store was the precondition for the active-PC flip
# (the engine/viewer live-campaign resolvers then disagreed on the tie). Wipe BOTH stores for
# this run prefix before launch so each persona mints into a CLEAN store -> exactly one seated
# campaign. Guarded: only fire when $RUN is non-empty so the glob can never widen to all of
# play-state/. bash 3.2-clean (no globstar / no arrays needed).
if [ -n "$RUN" ]; then
  rm -rf "$ROOT"/play-state/"$RUN" "$ROOT"/play-state/"$RUN"-b 2>/dev/null
fi

_DEFAULTS_SENTINEL="__worldos_defaults_missing__"
ORIGINAL_SELECTED_PROVIDER="$(defaults read dev.clawdnd.app selectedProvider 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_STATE_DIR="$(defaults read dev.clawdnd.app stateDir 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_DEFAULT_WORLD="$(defaults read dev.clawdnd.app defaultWorld 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_CODEX_HOME="$(defaults read dev.clawdnd.app codexHome 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_QA_AUTOSTART_PROVIDER="$(defaults read dev.clawdnd.app qaAutoStartProvider 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_QA_AUTOSTART_WORLD="$(defaults read dev.clawdnd.app qaAutoStartWorld 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"
ORIGINAL_QA_AUTOSTART_RUN_ID="$(defaults read dev.clawdnd.app qaAutoStartRunID 2>/dev/null || printf '%s' "$_DEFAULTS_SENTINEL")"

restore_app_default() {
  local key="$1" value="$2"
  if [ "$value" = "$_DEFAULTS_SENTINEL" ]; then
    defaults delete dev.clawdnd.app "$key" >/dev/null 2>&1 || true
  else
    defaults write dev.clawdnd.app "$key" "$value" >/dev/null 2>&1 || true
  fi
}

restore_app_defaults() {
  restore_app_default selectedProvider "$ORIGINAL_SELECTED_PROVIDER"
  restore_app_default stateDir "$ORIGINAL_STATE_DIR"
  restore_app_default defaultWorld "$ORIGINAL_DEFAULT_WORLD"
  restore_app_default codexHome "$ORIGINAL_CODEX_HOME"
  restore_app_default qaAutoStartProvider "$ORIGINAL_QA_AUTOSTART_PROVIDER"
  restore_app_default qaAutoStartWorld "$ORIGINAL_QA_AUTOSTART_WORLD"
  restore_app_default qaAutoStartRunID "$ORIGINAL_QA_AUTOSTART_RUN_ID"
}
trap restore_app_defaults EXIT

BUILD_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
VERSION="$( ([ -f "$ROOT/VERSION" ] && cat "$ROOT/VERSION") \
            || git -C "$ROOT" describe --tags --always 2>/dev/null \
            || echo "unknown")"
log() { printf '[uipt-app] %s\n' "$*"; }
provider_family() {
  case "${1:-}" in
    claude) echo "anthropic" ;;
    codex) echo "codex-openai" ;;
    openclaw) echo "openclaw" ;;
    scripted) echo "scripted" ;;
    *) echo "" ;;
  esac
}
provider_auth_surface() {
  case "${1:-}" in
    claude) echo "claude-cli" ;;
    codex) echo "codex-cli" ;;
    openclaw) echo "openclaw-cli" ;;
    scripted) echo "dev-scripted" ;;
    *) echo "" ;;
  esac
}
case "$PLAYER_AGENT" in
  claude|codex) ;;
  *)
    printf '[uipt-app] WOS_APP_PLAYER_AGENT must be claude or codex (got %s)\n' "$PLAYER_AGENT" >&2
    exit 2
    ;;
esac
log "run=$RUN world=$WORLD persona=$PERSONA beats=$BEATS budget=\$$BUDGET part=$PART player_agent=$PLAYER_AGENT"
log "build_sha=$BUILD_SHA version=$VERSION repo=$ROOT"
if [ -n "$SELECTED_PROVIDER" ]; then
  case "$SELECTED_PROVIDER" in
    claude|codex|openclaw|scripted)
      defaults write dev.clawdnd.app selectedProvider "$SELECTED_PROVIDER" >/dev/null 2>&1 || true
      log "selected provider preference set to $SELECTED_PROVIDER"
      if [ "$SELECTED_PROVIDER" = "codex" ] && [ -n "${CODEX_HOME_FOR_APP//[[:space:]]/}" ]; then
        defaults write dev.clawdnd.app codexHome "$CODEX_HOME_FOR_APP" >/dev/null 2>&1 || true
        log "native app Codex home seeded to $CODEX_HOME_FOR_APP"
      fi
      ;;
    *)
      printf '[uipt-app] WOS_APP_SELECTED_PROVIDER must be claude, codex, openclaw, or scripted (got %s)\n' "$SELECTED_PROVIDER" >&2
      exit 2
      ;;
  esac
fi
PART_B_PROVIDER="${SELECTED_PROVIDER:-claude}"
case "$PART_B_PROVIDER" in
  claude|codex) ;;
  scripted|openclaw)
    case "$PART" in
      A) ;;
      *)
        printf '[uipt-app] Part B supports WOS_APP_SELECTED_PROVIDER=claude|codex only (got %s)\n' "$PART_B_PROVIDER" >&2
        exit 2
        ;;
    esac
    ;;
esac

# Agent-readable failure buckets for built-app smoke. Keep these crisp and stable; the
# detailed shell/native result still travels separately as original_result.
APP_FAILURE_BUCKETS_JSON='["no_app","no_launcher","no_provider","no_art","no_actor","no_actions","move_rejected","no_narration","console_error","permission_prompt"]'

bucket_pair() { printf '%s|%s\n' "$1" "$2"; }

classify_native_failure() {  # $1=result $2=can_act $3=surface_json $4=app_status_json
  python3 "$ROOT/qa/app_failure_buckets.py" native \
    --result "$1" \
    --can-act "${2:-false}" \
    --surface-json "${3:-{}}" \
    --app-status-json "${4:-{}}"
}

classify_part_b_readiness_failure() {  # $1=saw_canact $2=saw_pc $3=chat_lines
  python3 "$ROOT/qa/app_failure_buckets.py" part-b-readiness \
    --saw-canact "${1:-0}" \
    --saw-pc "${2:-0}" \
    --chat-lines "${3:-0}"
}

classify_part_b_failure_from_artifacts() {  # $1=run_dir $2=fallback_result
  python3 "$ROOT/qa/app_failure_buckets.py" part-b-artifacts \
    --run-dir "$1" \
    --fallback-result "${2:-FAIL}"
}

classify_part_b_score_failure() {  # $1=score_json
  python3 "$ROOT/qa/app_failure_buckets.py" part-b-score \
    --score-json "${1:-}"
}

set_bucket_pair() {  # $1=A|B $2='bucket|detail'
  local part="$1" pair="$2" bucket detail
  bucket="${pair%%|*}"; detail="${pair#*|}"
  if [ "$part" = "A" ]; then
    PART_A_FAILURE_BUCKET="$bucket"; PART_A_FAILURE_DETAIL="$detail"
  else
    PART_B_FAILURE_BUCKET="$bucket"; PART_B_FAILURE_DETAIL="$detail"
  fi
}

codex_supports_mcp_override_config() {
  local raw major minor patch
  raw="$(codex --version 2>/dev/null | head -1 || true)"
  if [[ "$raw" =~ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    patch="${BASH_REMATCH[3]}"
    [ "$major" -gt 0 ] && return 0
    [ "$minor" -gt 120 ] && return 0
    [ "$minor" -eq 120 ] && [ "$patch" -ge 0 ] && return 0
  fi
  return 1
}

# --- never let a play script pop a browser on the owner's screen --------------------------
# scripts/play*.sh call `open`/`xdg-open` once the dashboard serves. Shim `open` to a no-op so
# the BACKEND runs but no window is forced onto the owner. (The .app's OWN window in part A is
# intentional — that is the surface under test.)
NOOPEN="$RUNDIR/.noopen"; mkdir -p "$NOOPEN"
printf '#!/bin/sh\nexit 0\n' > "$NOOPEN/open"; chmod +x "$NOOPEN/open"
PATH_NOOPEN="$NOOPEN:$PATH"

# --- screenshot via screencapture. The harness's OWN process tree usually lacks Screen
# Recording TCC, so a direct screencapture returns "could not create image from display". The
# parent (Claude Code / its MCP host) DOES have it. We try a direct capture first; if it fails,
# we DROP A REQUEST FILE the orchestrator fulfills via its screen-recording-granted path. Either
# way the gate's PASS/FAIL never depends on the image — the image is evidence, the run-dir +
# session-surface are ground truth. -----------------------------------------------------------
screenshot() {  # $1 = output png
  local out="$1"
  /usr/sbin/screencapture -x "$out" >/dev/null 2>&1 && [ -s "$out" ] && return 0
  # fallback: leave a marker the orchestrator can pick up (best-effort; non-fatal)
  printf '%s\n' "$out" >> "$NATIVE_DIR/screenshot-requests.txt"
  return 1
}

# Spent-so-far across every claude -p this run wrote (part A mint + part B DM/companions live
# under play-state/<run>*/dm.combined.jsonl; the part B PLAYER agent reports its own cost). Sums
# total_cost_usd from every dm.combined.jsonl under play-state for THIS run prefix. Read-only.
dm_spend() {
  local total=0 f
  for f in "$ROOT"/play-state/"$RUN"*/dm.combined.jsonl "$ROOT"/play-state/"$RUN"*/dm.jsonl; do
    [ -f "$f" ] || continue
    local c; c="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$f" 2>/dev/null)"
    total="$(awk -v a="$total" -v b="${c:-0}" 'BEGIN{printf "%.4f", a+b}')"
  done
  printf '%s' "$total"
}

# FIX 3 (#623): Part-A cold-open LIVENESS. A poll that abandons a healthy-but-slow cold open at a
# flat deadline is a coin-flip flaky leg; a poll that just inflates the timeout blindly lets a
# DEAD cold open hang. So distinguish the two: the cold open is "alive" while its DM stream is
# still being written (dm.combined.jsonl mtime advanced within the freshness window) OR the
# play.sh/play_party.sh DM process for this run is still up. A dead/never-started cold open (no
# run dir, stale log, AND no proc) is NOT alive → it fails PROMPTLY at the deadline.
COLDOPEN_LIVENESS_WINDOW_S="${WOS_APP_COLDOPEN_LIVENESS_WINDOW_S:-120}"
# Portable file mtime in epoch seconds (BSD stat on macOS, GNU stat on the Linux VM). Echoes ''
# when the file is absent/unreadable.
_file_mtime_epoch() {  # $1=path
  local p="$1"
  [ -f "$p" ] || return 1
  stat -f %m "$p" 2>/dev/null || stat -c %Y "$p" 2>/dev/null || return 1
}
# rc 0 if the cold-open for run dir $1 still shows forward progress (fresh DM stream OR a live DM
# proc). Empty $1 (nothing minted yet) ⇒ not-live (rc 1) so a never-started cold open fails fast.
coldopen_is_live() {  # $1=run_dir_name
  local run_dir="$1"
  [ -n "$run_dir" ] || return 1
  # (i) DM stream freshness: dm.combined.jsonl mtime advanced within the window.
  local log mt now
  log="$ROOT/play-state/$run_dir/dm.combined.jsonl"
  mt="$(_file_mtime_epoch "$log")" || mt=""
  if [ -n "$mt" ]; then
    now="$(date +%s)"
    if [ $(( now - mt )) -le "$COLDOPEN_LIVENESS_WINDOW_S" ]; then
      return 0
    fi
  fi
  # (ii) DM process still alive (the play.sh/play_party.sh loop carries the run id positionally,
  # the SAME signal the teardown pkill matches). A live proc = the cold open is still working.
  if pgrep -f " $run_dir " >/dev/null 2>&1 \
     || pgrep -f "play_party.sh .* $run_dir" >/dev/null 2>&1 \
     || pgrep -f "play.sh .* $run_dir" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

###############################################################################################
# PART A — NATIVE-TRANSITION GATE (re-verifies #356)
###############################################################################################
PART_A_RESULT="skipped"; PART_A_MINTED_PORT=""; PART_A_RUNDIR=""; PART_A_KEPT_BACKEND="false"; PART_A_FIRST_TURN_READY="false"; PART_A_FAILURE_BUCKET=""; PART_A_FAILURE_DETAIL=""
seed_native_launcher_state() {
  rm -rf "$NATIVE_LAUNCHER_STATE_DIR"
  mkdir -p "$NATIVE_LAUNCHER_STATE_DIR"
  CLAWDND_STATE_DIR="$NATIVE_LAUNCHER_STATE_DIR" WORLDOS_STATE_DIR="$NATIVE_LAUNCHER_STATE_DIR" \
    uv run --directory "$ROOT/servers/engine" python - "$WORLD" > "$NATIVE_DIR/launcher-state-seed.json" <<'PY'
import json
import sys

import server

world = sys.argv[1]
started = server.start_world(world)
campaign_id = started.get("campaign_id") if isinstance(started, dict) else ""
if not campaign_id:
    raise SystemExit("start_world did not return a campaign_id")
server.start_session(campaign_id, title="Built-app handoff launcher seed")

pc = {}
try:
    roster = server.list_canon_characters(campaign_id, playable_only=True).get("available") or []
except Exception:
    roster = []
names = [str(row.get("name") or "").strip() for row in roster if isinstance(row, dict)]
for name in [n for n in names if n] + ["Charming Latham", "Alfira"]:
    try:
        candidate = server.load_canon_character(campaign_id, name, kind="player", add_to_party=True)
    except Exception:
        continue
    if isinstance(candidate, dict) and not candidate.get("error") and candidate.get("id"):
        pc = candidate
        break

opening = "The table is lit, the road is waiting, and the next real choice belongs to you."
server.log_event(campaign_id, "narration", opening)
print(json.dumps({
    "schema": "worldos.native-launcher-seed.v1",
    "world": world,
    "campaign_id": campaign_id,
    "player": {
        "id": pc.get("id") if isinstance(pc, dict) else "",
        "name": pc.get("name") if isinstance(pc, dict) else "",
        "kind": pc.get("kind") if isinstance(pc, dict) else "",
    },
    "opening": opening,
}, indent=2, sort_keys=True))
PY
}
write_part_a_transition() {
  local result="$1" run="${2:-}" port="${3:-}" can_act="${4:-false}" surf="${5:-{}}" kept="${6:-$PART_A_KEPT_BACKEND}" first_turn_ready="${7:-$PART_A_FIRST_TURN_READY}" app_status="${8:-{}}"
  python3 - "$NATIVE_DIR/transition.json" "$result" "$BUILD_SHA" "$VERSION" \
            "$run" "$port" "$can_act" "$surf" \
            "$kept" "$first_turn_ready" "$app_status" "$PART_A_FAILURE_BUCKET" "$PART_A_FAILURE_DETAIL" <<'PY'
import json, sys, datetime
out, result, sha, ver, run, port, can_act, surf, kept, first_turn_ready, app_status, bucket, detail = sys.argv[1:14]
try: surf_obj = json.loads(surf)
except Exception: surf_obj = {"raw": surf}
try: app_status_obj = json.loads(app_status)
except Exception: app_status_obj = {"raw": app_status}
json.dump({
    "gate": "native_transition_356",
    "result": result,
    "original_result": result,
    "failure_bucket": bucket or None,
    "failure_detail": detail or None,
    "build_sha": sha, "version": ver,
    "minted_run_dir": run or None, "minted_port": int(port) if port else None,
    "kept_backend_alive": kept == "true",
    "first_turn_ready": first_turn_ready == "true",
    "can_act_after_click": can_act == "true",
    "session_surface_after": surf_obj,
    "app_status_after": app_status_obj,
    "app_status_launcher_json": "native/app-status.launcher.json",
    "app_status_minted_json": "native/app-status.minted.json",
    "before_png": "native/before.png", "after_png": "native/after.png",
    "at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
}, open(out, "w"), indent=2)
PY
}
run_part_a() {
  log "=== PART A: native-transition gate (re-verifies #356) ==="
  local tlog="$NATIVE_DIR/transition.log"; : > "$tlog"
  local before_dirs; before_dirs="$(ls -1 "$ROOT/play-state" 2>/dev/null | sort || true)"
  local autostart_run=""
  a_log() { printf '%s\n' "$*" | tee -a "$tlog"; }

  # Raise WorldOS to front (AppKit activate — NOT System Events, no TCC dialog), verify it is
  # z-order 0, then CGEvent-click the calibrated RESUME → PLAY CTA center. Idempotent — safe to
  # call repeatedly until the bridge mints a session. Writes its steps to $tlog.
  click_play_cta() {
    python3 - "$tlog" "$APP_BUNDLE" <<'PY' || echo "[A] CGEvent click attempt FAILED" >> "$tlog"
import os, sys, time, Quartz
from collections import deque
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
)
from ApplicationServices import AXIsProcessTrusted
from AppKit import (NSRunningApplication, NSWorkspace,
                    NSApplicationActivateIgnoringOtherApps, NSApplicationActivateAllWindows)
tlog = open(sys.argv[1], "a")
target_bundle = os.path.realpath(sys.argv[2])
def say(m): print(m); tlog.write(m+"\n"); tlog.flush()
if not AXIsProcessTrusted():
    say("[A] WARN: AXIsProcessTrusted() False — synthetic clicks may be dropped.")
def worldos_app():
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("dev.clawdnd.app")
    exact = []
    for app in apps:
        bundle = app.bundleURL()
        if bundle is not None and os.path.realpath(bundle.path()) == target_bundle:
            exact.append(app)
    if exact:
        return exact[0]
    if len(apps) == 1:
        return apps[0]
    if apps:
        say(f"[A] ERROR: {len(apps)} WorldOS apps are running but none matches {target_bundle}")
        return None
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if "WorldOS" in (a.localizedName() or ""): return a
    return None
def front_owner_and_win():
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    layer0 = [w for w in wins if w.get("kCGWindowLayer") == 0]
    front = layer0[0].get("kCGWindowOwnerName") if layer0 else None
    win = next((w for w in layer0 if "WorldOS" in (w.get("kCGWindowOwnerName") or "")), None)
    return front, win
app = worldos_app()
if app is None:
    say("[A] ERROR: WorldOS running application not found"); sys.exit(3)
# Activate, then click IMMEDIATELY (minimize the focus-race window). Verify frontmost first.
win = None
for attempt in range(8):
    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows)
    time.sleep(0.6)
    front, win = front_owner_and_win()
    if win is not None and front and "WorldOS" in front:
        break
    say(f"[A] WorldOS not frontmost (front={front!r}) — re-activating…")
if win is None:
    say("[A] ERROR: no WorldOS layer-0 window after activation"); sys.exit(3)
front, win = front_owner_and_win()
if not (front and "WorldOS" in front):
    say(f"[A] WARN: WorldOS slipped from front (front={front!r}) just before click — clicking anyway.")

def ax_attr(el, attr):
    try:
        err, value = AXUIElementCopyAttributeValue(el, attr, None)
    except Exception:
        return None
    return value if err == 0 else None

def find_resume_button(pid):
    app_el = AXUIElementCreateApplication(pid)
    roots = ax_attr(app_el, "AXWindows") or []
    queue = deque(roots)
    seen = 0
    deadline = time.monotonic() + 4.0
    while queue and seen < 5000 and time.monotonic() < deadline:
        el = queue.popleft()
        seen += 1
        role = str(ax_attr(el, kAXRoleAttribute) or "")
        title = str(ax_attr(el, kAXTitleAttribute) or "")
        desc = str(ax_attr(el, kAXDescriptionAttribute) or "")
        value = str(ax_attr(el, kAXValueAttribute) or "")
        haystack = " ".join([title, desc, value]).lower()
        if "button" in role.lower() and ("resume" in haystack or "continue" in haystack) and "play" in haystack:
            return el, title or desc or value, seen
        children = ax_attr(el, kAXChildrenAttribute)
        if children:
            queue.extend(children)
    return None, "", seen

target, label, scanned = find_resume_button(app.processIdentifier())
if target is not None:
    err = AXUIElementPerformAction(target, kAXPressAction)
    if err == 0:
        say(f"[A] AX pressed Resume → play button ({label!r}; scanned {scanned} nodes).")
        sys.exit(0)
    say(f"[A] WARN: AX found Resume → play ({label!r}) but press returned {err}; falling back to CGEvent click.")

b = win["kCGWindowBounds"]; X, Y, W, H = b["X"], b["Y"], b["Width"], b["Height"]
cx, cy = X + W - 319, Y + 204          # fallback CTA center (right-aligned button)
say(f"[A] window X={X} Y={Y} W={W} H={H}; front={front!r}; click=({cx:.0f},{cy:.0f})")
def post(ev): Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (cx, cy), Quartz.kCGMouseButtonLeft)); time.sleep(0.10)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (cx, cy), Quartz.kCGMouseButtonLeft)); time.sleep(0.08)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp,   (cx, cy), Quartz.kCGMouseButtonLeft))
say("[A] click posted.")
PY
  }

  if [ "${WOS_APP_SKIP_BUILD:-0}" != "1" ]; then
    a_log "[A] seeding isolated launcher state ($WORLD) at ${NATIVE_LAUNCHER_STATE_DIR}..."
    if seed_native_launcher_state >> "$tlog" 2>&1; then
      defaults write dev.clawdnd.app stateDir "$NATIVE_LAUNCHER_STATE_DIR" >/dev/null 2>&1 || true
      defaults write dev.clawdnd.app defaultWorld "$WORLD" >/dev/null 2>&1 || true
      if [ "$NATIVE_AUTOSTART" = "1" ]; then
        autostart_run="play-$(date +%Y%m%d%H%M%S)"
        defaults write dev.clawdnd.app qaAutoStartProvider "${SELECTED_PROVIDER:-claude}" >/dev/null 2>&1 || true
        defaults write dev.clawdnd.app qaAutoStartWorld "$WORLD" >/dev/null 2>&1 || true
        defaults write dev.clawdnd.app qaAutoStartRunID "$autostart_run" >/dev/null 2>&1 || true
        a_log "[A] native QA auto-start requested: provider=${SELECTED_PROVIDER:-claude} run=$autostart_run."
      fi
      a_log "[A] launcher state seeded; native app will not depend on old local saves."
    else
      a_log "[A] launcher-state seed FAILED — see $NATIVE_DIR/launcher-state-seed.json and $tlog"
      PART_A_RESULT="seed_failed"
      set_bucket_pair A "$(classify_native_failure "$PART_A_RESULT" false '{}' '{}')"
      write_part_a_transition "$PART_A_RESULT"
      return 1
    fi
    a_log "[A] pkill WorldOSApp + THIS checkout's stale viewers, rm -rf $APP_BUNDLE, fresh build…"
    if [ "${WOS_APP_NO_GLOBAL_KILL:-0}" = "1" ]; then
      a_log "[A] WOS_APP_NO_GLOBAL_KILL=1 — preserving other WorldOSApp processes."
    else
      pkill -x WorldOSApp >/dev/null 2>&1 || true
    fi
    # ONLY reap viewers spawned from THIS repo root — NEVER a blanket `pkill -f viewer/server.py`
    # (that would kill unrelated services on this host, e.g. the evaOS desktop-bridge squatting
    # 8765 on the Tailscale iface, or another checkout's viewer). This includes provider viewers
    # launched as relative `python3 viewer/server.py` with CWD=$ROOT.
    kill_repo_viewers "$ROOT"
    sleep 1
    rm -rf "$APP_BUNDLE"
    # Fresh build off the CURRENT checkout's HEAD (must include #356). build_and_run.sh sets
    # WORLDOS_REPO_ROOT=$ROOT so the .app runs THIS checkout's viewer (the fixed JSX is served
    # live as text/babel — no separate ui:build step). open is shimmed: the .app still launches
    # (build_and_run.sh uses /usr/bin/open -n directly, not PATH `open`), but no extra windows.
    if ! PATH="$PATH_NOOPEN" \
         WORLDOS_NO_STOP_EXISTING="${WOS_APP_NO_GLOBAL_KILL:-0}" \
         WORLDOS_PREFER_LAUNCH_ROOTS=1 \
         "$ROOT/script/build_and_run.sh" run >> "$tlog" 2>&1; then
      a_log "[A] BUILD/LAUNCH FAILED — see $tlog"; PART_A_RESULT="build_failed"; set_bucket_pair A "$(classify_native_failure "$PART_A_RESULT" false '{}' '{}')"; write_part_a_transition "$PART_A_RESULT"; return 1
    fi
  else
    a_log "[A] WOS_APP_SKIP_BUILD=1 — reusing the running .app (no rebuild)."
  fi

  # DISCOVER the launcher viewer's actual port. We do NOT assume the preferred port (8765): the
  # app's PortFinder walks UP from preferredPort, and on this host 8765 is often already taken by
  # an unrelated service (evaOS bridge on the Tailscale iface), so the launcher commonly lands on
  # 8766+. The launcher viewer is THIS repo's viewer/server.py process; its port is the last
  # numeric token of its argv. Wait for it to appear AND answer /openworlds/ 200.
  local launcher_port="" ready=0
  local launcher_polls=$(( LAUNCHER_WAIT_S * 2 )); [ "$launcher_polls" -lt 1 ] && launcher_polls=1
  a_log "[A] waiting up to ${LAUNCHER_WAIT_S}s for the launcher viewer to answer /openworlds/ 200…"
  for _ in $(seq 1 "$launcher_polls"); do
    launcher_port="$(launcher_port_of "$ROOT")"
    if [ -n "$launcher_port" ] && \
       [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$launcher_port/openworlds/" 2>/dev/null)" = "200" ]; then
      ready=1; break
    fi
    sleep 0.5
  done
  [ "$ready" = "1" ] || { a_log "[A] launcher viewer never came up (discovered port='${launcher_port:-none}')"; PART_A_RESULT="no_launcher"; set_bucket_pair A "$(classify_native_failure "$PART_A_RESULT" false '{}' '{}')"; write_part_a_transition "$PART_A_RESULT"; return 1; }
  app_pid_for_bundle "$APP_BUNDLE" >/dev/null 2>&1 || { a_log "[A] target WorldOSApp bundle is not running"; PART_A_RESULT="app_not_running"; set_bucket_pair A "$(classify_native_failure "$PART_A_RESULT" false '{}' '{}')"; write_part_a_transition "$PART_A_RESULT"; return 1; }
  a_log "[A] launcher viewer ready on $launcher_port; can_act(before)=$(curl -s "http://127.0.0.1:$launcher_port/session-surface" | jq -c '{can_act,is_live_view,live}' 2>/dev/null)"
  curl -s --max-time 3 "http://127.0.0.1:$launcher_port/app-status" \
    | jq . > "$NATIVE_DIR/app-status.launcher.json" 2>/dev/null || printf '{}\n' > "$NATIVE_DIR/app-status.launcher.json"

  # BEFORE screenshot. The baseline play-state set is captured before launch so native
  # auto-start sessions that mint immediately are still detectable.
  screenshot "$NATIVE_DIR/before.png" && a_log "[A] before.png captured" || a_log "[A] before.png deferred to orchestrator"

  # CGEvent-click the launcher's primary play CTA.
  #  - RAISE WorldOS to front first. A synthetic CGEvent click is delivered to whatever window is
  #    FRONTMOST at that screen point; on a busy desktop other app windows (Claude/Codex/etc.)
  #    overlap the button's coordinate and SWALLOW the click (observed: a click that never
  #    reached the .app). We activate via NSRunningApplication.activate (AppKit) — an
  #    app-activation API, NOT System Events UI scripting, so it does NOT pop a TCC dialog — and
  #    verify WorldOS is z-order index 0 before clicking, retrying activation a few times.
  #  - We compute the click point from the .app's window geometry (CGWindowList — no AX dialog):
  #    the "RESUME → PLAY" button is right-aligned in the ContinueBanner just below the top nav
  #    (button center ≈ window_right-219pt, window_top+204pt; corroborated by the baseline
  #    agent's (1692,340) and re-observed at (1701,344) here). AXIsProcessTrusted is True so the
  #    synthetic click lands.
  if [ "$NATIVE_AUTOSTART" = "1" ]; then
    a_log "[A] native QA auto-start is driving startProviderSession; skipping desktop click."
  else
    a_log "[A] raising WorldOS to front + CGEvent-clicking the RESUME → PLAY CTA (with retries)…"
    click_play_cta   # first attempt (the poll re-clicks if the focus-race ate it)
  fi

  # ASSERT the mint. Poll up to PART_A_DEADLINE (default 420s, env WOS_APP_PART_A_DEADLINE) for
  # BOTH: (i) a NEW play-state run dir AND (ii) a NEW viewer (a DIFFERENT port than the launcher)
  # whose /session-surface reports can_act:true. The DM cold-open mints the campaign; can_act flips
  # once the move-sink is set AND the viewer binds the live campaign (auto-follow), which happens
  # early in the cold-open turn — but the budget must outlast the max-effort cold open (~280–400s),
  # so the old 210s poll was a spurious FAIL on a slow-but-healthy mint. RE-CLICK every ~24s while
  # nothing has minted: on a busy multi-app desktop another window can steal focus between the
  # activate and the CGEvent, swallowing the click — a re-click recovers it.
  local part_a_polls=$(( PART_A_DEADLINE / 3 )); [ "$part_a_polls" -lt 1 ] && part_a_polls=1
  # FIX 3 (#623): the poll is LIVENESS-AWARE. The hard deadline is PART_A_DEADLINE (now derived
  # from the cold-open tier, so it already outlasts the cold open). Past it, we grant a BOUNDED
  # grace extension ONLY while the cold open is still making forward progress (coldopen_is_live:
  # fresh DM stream OR a live DM proc) — a healthy-but-slow mint finishes instead of a coin-flip
  # FAIL. A DEAD/never-started cold open (no run dir, stale log, no proc) is NOT live, so the
  # grace never triggers and it fails PROMPTLY at the deadline. The grace is capped so a
  # pathologically-slow-but-"alive" cold open still terminates. The PASS condition below
  # (can_act:true && minted_run) is UNCHANGED — the real integrity assertion.
  local part_a_grace_cap_s="${WOS_APP_PART_A_GRACE_CAP_S:-$(( COLDOPEN_LIVENESS_WINDOW_S * 3 ))}"
  local part_a_start; part_a_start="$(date +%s)"
  local part_a_hard_deadline=$(( part_a_start + PART_A_DEADLINE ))
  local part_a_max_deadline=$(( part_a_hard_deadline + part_a_grace_cap_s ))
  a_log "[A] polling for a minted live session (new run dir + can_act:true on a new port; deadline ${PART_A_DEADLINE}s, liveness grace ≤${part_a_grace_cap_s}s)…"
  local minted_port="" minted_run="" can_act="false"
  local i=0 grace_logged=0
  while :; do
    i=$(( i + 1 ))
    # (i) a new play-state dir
    local now_dirs new_dir
    now_dirs="$(ls -1 "$ROOT/play-state" 2>/dev/null | sort || true)"
    new_dir="$(comm -13 <(printf '%s\n' "$before_dirs") <(printf '%s\n' "$now_dirs") 2>/dev/null | head -1)"
    if [ -n "$autostart_run" ] && [ -d "$ROOT/play-state/$autostart_run" ]; then
      new_dir="$autostart_run"
    fi
    # Re-click if nothing has started after ~24s and ~48s (idempotent: clicking RESUME→PLAY again
    # before the mint just re-issues startPlay; once a run dir exists we stop clicking).
    if [ "$NATIVE_AUTOSTART" != "1" ] && [ -z "$new_dir" ] && { [ "$i" = "6" ] || [ "$i" = "12" ]; }; then
      a_log "[A] no mint yet after $((i*4))s — re-clicking the CTA (focus-race recovery)…"
      click_play_cta
    fi
    # (ii) ANY of THIS repo's viewers (other than the launcher) now reporting can_act:true. We
    # enumerate the live viewer ports from the running processes (robust to the actual port the
    # PortFinder chose) instead of guessing a fixed range.
    local p surf ca
    for p in $(repo_viewer_ports "$ROOT"); do
      [ "$p" = "$launcher_port" ] && [ "$NATIVE_AUTOSTART" != "1" ] && continue
      [ -n "$new_dir" ] || continue
      port_matches_run "$p" "$new_dir" || continue
      surf="$(curl -s --max-time 2 "http://127.0.0.1:$p/session-surface" 2>/dev/null)" || continue
      [ -z "$surf" ] && continue
      ca="$(printf '%s' "$surf" | jq -r '.can_act // false' 2>/dev/null)"
      if [ "$ca" = "true" ]; then minted_port="$p"; can_act="true"; fi
    done
    [ -n "$new_dir" ] && minted_run="$new_dir"
    if [ "$can_act" = "true" ] && [ -n "$minted_run" ]; then
      a_log "[A] MINTED: run=$minted_run port=$minted_port can_act=true (after ${i} polls)"
      break
    fi
    # Deadline + liveness gate. Before the hard deadline: keep polling. Past it: keep polling ONLY
    # while the cold open is demonstrably alive (and still under the grace cap); otherwise STOP and
    # fall through to the FAIL classification — a dead/never-started cold open fails promptly.
    local _now; _now="$(date +%s)"
    if [ "$_now" -ge "$part_a_hard_deadline" ]; then
      if [ "$_now" -ge "$part_a_max_deadline" ]; then
        a_log "[A] grace cap reached (${part_a_grace_cap_s}s past the ${PART_A_DEADLINE}s deadline) — giving up the mint poll."
        break
      fi
      if coldopen_is_live "$minted_run"; then
        if [ "$grace_logged" = "0" ]; then
          a_log "[A] past the ${PART_A_DEADLINE}s deadline but the cold open is STILL LIVE (run='${minted_run:-none}') — extending within the ${part_a_grace_cap_s}s grace."
          grace_logged=1
        fi
      else
        a_log "[A] past the ${PART_A_DEADLINE}s deadline and the cold open is NOT live (run='${minted_run:-none}', stale stream + no DM proc) — failing promptly."
        break
      fi
    fi
    sleep 3
  done

  # AFTER screenshot (shows the live table if the transition worked).
  sleep 1; screenshot "$NATIVE_DIR/after.png" && a_log "[A] after.png captured" || a_log "[A] after.png deferred to orchestrator"

  # Capture the minted surface BEFORE teardown (so transition.json records the real can_act state,
  # not an empty post-kill fetch).
  PART_A_MINTED_PORT="$minted_port"; PART_A_RUNDIR="$minted_run"
  local surf_final="{}"
  [ -n "$minted_port" ] && surf_final="$(curl -s "http://127.0.0.1:$minted_port/session-surface" 2>/dev/null | jq -c '{can_act,is_live_view,live,campaignId,enabledActions}' 2>/dev/null || echo '{}')"
  local app_status_final="{}"
  if [ -n "$minted_port" ]; then
    if curl -s --max-time 3 "http://127.0.0.1:$minted_port/app-status" \
        | jq . > "$NATIVE_DIR/app-status.minted.json" 2>/dev/null; then
      app_status_final="$(jq -c . "$NATIVE_DIR/app-status.minted.json" 2>/dev/null || echo '{}')"
    else
      printf '{}\n' > "$NATIVE_DIR/app-status.minted.json"
    fi
  else
    printf '{}\n' > "$NATIVE_DIR/app-status.minted.json"
  fi

  if [ "$KEEP_MINTED_BACKEND" = "1" ] && [ -n "$minted_run" ] && [ -n "$minted_port" ]; then
    a_log "[A] keep-alive proof: waiting for first-turn readiness (actor + enabled actions + narration)…"
    local ready_surf actor enabled_count narration_count chat_lines
    for _ in $(seq 1 80); do
      ready_surf="$(curl -s --max-time 2 "http://127.0.0.1:$minted_port/session-surface" 2>/dev/null || echo '{}')"
      actor="$(printf '%s' "$ready_surf" | jq -r '.actionModel.actor.name // ""' 2>/dev/null)"
      enabled_count="$(printf '%s' "$ready_surf" | jq -r '(.enabledActions // []) | length' 2>/dev/null)"
      narration_count="$(printf '%s' "$ready_surf" | jq -r '[.recentEvents[]? | select(.kind == "narration" or .kind == "dialogue")] | length' 2>/dev/null)"
      chat_lines="$(wc -l < "$ROOT/play-state/$minted_run/chat.jsonl" 2>/dev/null | tr -d ' ')"
      if [ -n "$actor" ] && [ "${enabled_count:-0}" -gt 0 ] && { [ "${narration_count:-0}" -gt 0 ] || [ "${chat_lines:-0}" -gt 0 ]; }; then
        PART_A_FIRST_TURN_READY="true"
        surf_final="$(printf '%s' "$ready_surf" | jq -c '{can_act,is_live_view,live,campaignId,enabledActions,actor:.actionModel.actor,recentEvents}' 2>/dev/null || printf '%s' "$ready_surf")"
        curl -s --max-time 3 "http://127.0.0.1:$minted_port/app-status" \
          | jq . > "$NATIVE_DIR/app-status.minted.json" 2>/dev/null || true
        app_status_final="$(jq -c . "$NATIVE_DIR/app-status.minted.json" 2>/dev/null || echo '{}')"
        a_log "[A] keep-alive proof ready: actor=$actor enabled=$enabled_count narration_events=${narration_count:-0} chat_lines=${chat_lines:-0}."
        break
      fi
      sleep 3
    done
    [ "$PART_A_FIRST_TURN_READY" = "true" ] || a_log "[A] keep-alive proof timed out before full first-turn readiness; continuing with native-transition result."
  fi

  # Tear down the minted session's BACKEND by default so its DM loop can't keep spending toward
  # the .app's own $15 session budget (we only needed the cold-open to prove can_act:true).
  # For takeover gameplay proof, WOS_APP_KEEP_MINTED_BACKEND=1 intentionally leaves that exact
  # app-minted backend alive so the operator can continue a short built-app playtest from the
  # same session. The .app process stays up either way. When tearing down, we match BOTH the
  # state-dir path AND the run-id POSITIONAL arg (`play[_party].sh <world> <run> <port>`) — the
  # supervisor + DM-loop bash procs carry the run id positionally, NOT the play-state path, so a
  # path-only pkill leaves the viewer-respawning supervisor alive. We do NOT touch the launcher.
  if [ -n "$minted_run" ]; then
    if [ "$KEEP_MINTED_BACKEND" = "1" ]; then
      PART_A_KEPT_BACKEND="true"
      a_log "[A] WOS_APP_KEEP_MINTED_BACKEND=1 — keeping minted backend alive for gameplay proof (run=$minted_run port=${minted_port:-unknown})."
    else
      a_log "[A] tearing down minted backend (run=$minted_run) — cold-open was enough."
      pkill -f "play-state/$minted_run/" 2>/dev/null || true
      pkill -f "play_party.sh .* $minted_run" 2>/dev/null || true
      pkill -f "play.sh .* $minted_run" 2>/dev/null || true
      pkill -f " $minted_run " 2>/dev/null || true
      [ -n "$minted_port" ] && pkill -f "server.py .* $minted_port\$" 2>/dev/null || true
      sleep 1
    fi
  fi
  if [ "$can_act" = "true" ] && [ -n "$minted_run" ]; then
    PART_A_RESULT="PASS"
    PART_A_FAILURE_BUCKET=""; PART_A_FAILURE_DETAIL=""
    a_log "[A] RESULT: PASS — #356 banner minted a live DM (can_act:true). surface=$surf_final"
  else
    PART_A_RESULT="FAIL"
    set_bucket_pair A "$(classify_native_failure "$PART_A_RESULT" "$can_act" "$surf_final" "$app_status_final")"
    a_log "[A] RESULT: FAIL — stuck read-only (no minted can_act:true session). minted_run='${minted_run:-none}' port='${minted_port:-none}' surface=$surf_final"
  fi

  write_part_a_transition "$PART_A_RESULT" "${minted_run:-}" "${minted_port:-}" "$can_act" "$surf_final" "$PART_A_KEPT_BACKEND" "$PART_A_FIRST_TURN_READY" "$app_status_final"
  a_log "[A] wrote $NATIVE_DIR/transition.json"
  [ "$PART_A_RESULT" = "PASS" ]
}

###############################################################################################
# PART B — PERSONA LOOP (the .app-faithful backend + the real palette persona)
###############################################################################################
PART_B_RESULT="skipped"; PART_B_PLAYER_COST="0"; PART_B_SCORE_PASS="false"; PART_B_FAILURE_BUCKET=""; PART_B_FAILURE_DETAIL=""
# FIX 1 (#623 false-cap): a NON-zero player PROCESS exit (a harness/player CRASH) that is NOT a
# 429 is INCONCLUSIVE evidence — a "re-measure", not a product-quality FAIL. This flag threads
# that fact into run.json (part_b.harness_error) so release_readiness.py RED-caps it as an
# evidence gap, never as a score_pass quality fail. Default false; only a non-zero, non-quota
# player_rc flips it true.
PART_B_HARNESS_ERROR="false"
run_part_b() {
  log "=== PART B: persona loop on the .app-faithful backend ==="
  [ -f "$PERSONA_FILE" ] || { log "[B] no persona brief at $PERSONA_FILE — skipping"; PART_B_RESULT="no_persona"; set_bucket_pair B "$(bucket_pair no_actor "persona brief missing: $PERSONA_FILE")"; return 1; }
  [ -d "$PW_DIR/node_modules/playwright" ] || {
    log "[B] Playwright not installed at $PW_DIR. Run: (cd qa/playwright && npm install && npx playwright install chromium)"
    PART_B_RESULT="no_playwright"; set_bucket_pair B "$(bucket_pair no_provider "Playwright palette dependency is missing")"; return 1; }

  # Budget guard: if part A + its DM already spent the run budget, skip B (we never exceed budget).
  local spent; spent="$(dm_spend)"
  if awk -v s="${spent:-0}" -v b="$BUDGET" 'BEGIN{exit !(s+0 >= b+0)}'; then
    log "[B] run budget \$$BUDGET already reached (spent ~\$$spent in part A) — skipping persona loop."
    PART_B_RESULT="budget_exhausted"; set_bucket_pair B "$(bucket_pair no_provider "run budget exhausted before provider playtest")"; return 1
  fi
  # The PLAYER agent gets whatever budget remains (it drives /move; the DM cost is the bulk).
  local player_budget; player_budget="$(awk -v s="${spent:-0}" -v b="$BUDGET" 'BEGIN{r=b-s; if(r<0.5)r=0.5; printf "%.2f", r}')"

  # Faithful backend: a SEPARATE play-state run (so it never collides with part A's mint). The
  # Claude lane still uses the native bridge's legacy play_party.sh/play.sh path; the Codex lane
  # uses the Codex DM provider wrapper that owns the same live viewer + /move sink contract.
  # We point the palette persona at THAT viewer.
  local b_run="${RUN}-b"
  local b_port; b_port="$(pick_free_port $((PREFERRED_PORT+20)))" || { log "[B] no free port"; PART_B_RESULT="no_port"; set_bucket_pair B "$(bucket_pair no_provider "no free localhost port for faithful backend")"; return 1; }
  local b_url="http://127.0.0.1:$b_port/openworlds/"
  log "[B] launching faithful backend: provider=$PART_B_PROVIDER run=$b_run port=$b_port DM=$DM_MODEL"

  # Cap the backend so it can never overshoot the run budget. CLAWDND_PLAY_SESSION_BUDGET is the
  # aggregate DM ceiling; CLAWDND_PLAY_MAX_TURNS bounds turns; per-turn cap keeps each beat small.
  local sess_cap; sess_cap="$(awk -v r="$player_budget" 'BEGIN{c=r-0.20; if(c<0.50)c=0.50; printf "%.2f", c}')"
  local codex_default_model
  codex_default_model="${CLAWDND_CODEX_MODEL:-}"
  local part_b_provider_family part_b_auth_surface part_b_dm_model part_b_player_model part_b_scorer_provider part_b_scorer_model
  part_b_provider_family="$(provider_family "$PART_B_PROVIDER")"
  part_b_auth_surface="$(provider_auth_surface "$PART_B_PROVIDER")"
  part_b_scorer_provider="deterministic-ui-playtest"
  part_b_scorer_model="qa/ui_playtest_score.py"
  case "$PART_B_PROVIDER" in
    claude)
      part_b_dm_model="$DM_MODEL"
      part_b_player_model="$PLAYER_MODEL"
      ;;
    codex)
      part_b_dm_model="${WOS_APP_CODEX_DM_MODEL:-${codex_default_model:-gpt-5.5}}"
      part_b_player_model="${WOS_APP_CODEX_PLAYER_MODEL:-${codex_default_model:-gpt-5.5}}"
      ;;
    *)
      part_b_dm_model=""
      part_b_player_model=""
      ;;
  esac
  case "$PART_B_PROVIDER" in
    claude)
      (
        export PATH="$PATH_NOOPEN"
        export WORLDOS_DM_MODEL="$DM_MODEL" CLAWDND_DM_MODEL="$DM_MODEL"
        export CLAWDND_PLAY_PORT="$b_port"
        # Per-turn cap scales to the DM model: the Opus max-effort cold-open world-build needs ~$12;
        # the Sonnet-tuned $1.50 cap trips error_max_budget_usd on the Opus cold-open → no PC seated.
        # CAP, not spend — routine beats spend far less; sess_cap still bounds total DM spend.
        case "$DM_MODEL" in *opus*) : "${CLAWDND_PLAY_BUDGET:=12.00}" ;; *) : "${CLAWDND_PLAY_BUDGET:=1.50}" ;; esac
        export CLAWDND_PLAY_BUDGET
        export CLAWDND_PLAY_SESSION_BUDGET="$sess_cap"
        export CLAWDND_PLAY_MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-$((BEATS + 4))}"
        export CLAWDND_PLAY_MAX_IDLE="${CLAWDND_PLAY_MAX_IDLE:-600}"
        exec "$ROOT/scripts/play_party.sh" "$WORLD" "$b_run" "$b_port" >> "$RUNDIR/backend.log" 2>&1
      ) &
      ;;
    codex)
      (
        export PATH="$PATH_NOOPEN"
        export CLAWDND_PROVIDER=codex
        export CLAWDND_WORLD="$WORLD"
        export CLAWDND_RUN_ID="$b_run"
        export CLAWDND_PLAY_PORT="$b_port"
        export CLAWDND_PLAY_BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"
        export CLAWDND_PLAY_SESSION_BUDGET="$sess_cap"
        export CLAWDND_PLAY_MAX_TURNS="${CLAWDND_PLAY_MAX_TURNS:-$((BEATS + 4))}"
        export CLAWDND_CODEX_MODEL="$part_b_dm_model"
        export WORLDOS_CODEX_MODEL="$part_b_dm_model"
        exec "$ROOT/scripts/play_codex_dm.sh" >> "$RUNDIR/backend.log" 2>&1
      ) &
      ;;
  esac
  B_BACKEND=$!
  # Clean teardown: kill the backend subshell, THEN the play.sh supervisor + DM-loop bash procs
  # (matched by the run-id positional — they don't carry the play-state path, and the supervisor
  # respawns the viewer, so a path-only kill leaves it alive), then the viewer + state dir, belt
  # and suspenders. The launcher and other runs are untouched (unique run id `${RUN}-b`).
  b_cleanup() {
    pkill -TERM -P "$B_BACKEND" 2>/dev/null || true
    kill "$B_BACKEND" 2>/dev/null || true
    pkill -f "play_party.sh $WORLD $b_run" 2>/dev/null || true
    pkill -f "play.sh $WORLD $b_run" 2>/dev/null || true
    pkill -f "$WORLD $b_run " 2>/dev/null || true
    pkill -f "play-state/$b_run/" 2>/dev/null || true
    pkill -f "server.py .* $b_port\$" 2>/dev/null || true
  }
  trap 'b_cleanup' RETURN

  # Wait for the backend to be GENUINELY READY for a player — not merely can_act:true. The
  # move-sink (→ can_act:true) and the campaign exist EARLY in the DM cold-open, but the PC isn't
  # SEATED and the opening narration isn't written until the cold-open turn FINISHES. A smoke run
  # proved that launching the persona on can_act alone drops it into a "no active character" /
  # empty-scene limbo (a race, not a real bug). A real player waits for the opening scene, so we
  # gate on ALL THREE: (1) viewer 200 + can_act:true, (2) a seated PLAYER character in the
  # snapshot, (3) opening narration written to chat.jsonl (the DM's cold-open reply). Falls back
  # to "can_act + seated PC" if chat stays empty past a grace window (so an empty-narration bug
  # like #357 still lets the loop proceed and the persona can REPORT the empty scene as a bug).
  local b_state="$ROOT/play-state/$b_run"
  log "[B] waiting for the backend to be player-ready (can_act + seated PC + opening narration)…"
  local ready=0 ca pc chat_lines saw_canact=0 saw_pc=0
  # Slow DMs (e.g. an Opus max-effort cold-open) seat the PC well before the opening narration
  # finishes — so the wait + the no-narration grace are env-configurable. Defaults preserve the
  # historic ~6min cap / ~2.5min grace; raise them for an Opus cold-open so the harness waits for
  # the narration instead of grace-proceeding into an empty scene (3s per poll).
  local ready_polls="${WOS_APP_PLAYER_READY_POLLS:-120}"
  local grace_polls="${WOS_APP_NARRATION_GRACE_POLLS:-50}"
  # An Opus cold-open finishes its narration ~300s (vs Sonnet ~280s at the wire); the Sonnet-tuned
  # 50-poll (~150s) grace would grace-proceed into an empty scene. Default an Opus DM to a longer
  # wait so the persona sees the real opening (env still overrides either tier).
  case "$DM_MODEL" in *opus*) ready_polls="${WOS_APP_PLAYER_READY_POLLS:-200}"; grace_polls="${WOS_APP_NARRATION_GRACE_POLLS:-130}" ;; esac
  for i in $(seq 1 "$ready_polls"); do   # up to ~ready_polls*3s for the full cold-open
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "$b_url" 2>/dev/null)" = "200" ]; then
      ca="$(curl -s --max-time 2 "http://127.0.0.1:$b_port/session-surface" 2>/dev/null | jq -r '.can_act // false' 2>/dev/null)"
      [ "$ca" = "true" ] && saw_canact=1
      # seated PLAYER character?
      pc="$(_seated_player_count "$b_state")"; pc="${pc:-0}"
      [ "$pc" -ge 1 ] && saw_pc=1
      # opening narration?
      chat_lines=0
      if [ -f "$b_state/chat.jsonl" ]; then
        chat_lines="$(grep -c . "$b_state/chat.jsonl" 2>/dev/null || true)"
        chat_lines="${chat_lines:-0}"
      fi
      if [ "$saw_canact" = "1" ] && [ "$saw_pc" = "1" ] && [ "${chat_lines:-0}" -ge 1 ]; then
        ready=1; log "[B] player-ready: can_act:true, seated PC ($pc), chat lines=$chat_lines (after $((i*3))s)."; break
      fi
      # Grace fallback: can_act + seated PC but STILL no narration after ~2.5min → proceed anyway
      # (the persona will report the empty opening scene — a real finding, e.g. #357).
      if [ "$saw_canact" = "1" ] && [ "$saw_pc" = "1" ] && [ "$i" -ge "$grace_polls" ]; then
        ready=1; log "[B] proceeding without opening narration after $((i*3))s (chat empty — persona will judge it; possible #357)."; break
      fi
    fi
    kill -0 "$B_BACKEND" 2>/dev/null || { log "[B] backend exited early — see $RUNDIR/backend.log"; break; }
    sleep 3
  done
  if [ "$ready" != "1" ]; then
    log "[B] backend never became player-ready (can_act=$saw_canact seatedPC=$saw_pc) — see $RUNDIR/backend.log"
    PART_B_RESULT="backend_not_ready"; set_bucket_pair B "$(classify_part_b_readiness_failure "$saw_canact" "$saw_pc" "${chat_lines:-0}")"; return 1
  fi
  log "[B] backend player-ready on $b_port. Pointing the palette persona at it."

  # PLAYER MCP config: ONLY the Playwright palette (strict). The persona sees ONLY the screen.
  local player_cfg="$RUNDIR/player.mcp.json"
  python3 - "$PW_DIR" "$b_url" "$RUNDIR" "$(worldos_env UIPT_CHANNEL "")" "$PERSONA" "$player_cfg" <<'PY'
import json, sys
pw_dir, url, rundir, channel, persona, out = sys.argv[1:7]
json.dump({"mcpServers": {"clawdnd-uiplayer": {
    "command": "node", "args": [f"{pw_dir}/palette_server.js"],
    "env": {"CLAWDND_UIPT_URL": url, "CLAWDND_UIPT_RUNDIR": rundir,
            "CLAWDND_UIPT_CHANNEL": channel, "CLAWDND_UIPT_PERSONA": persona},
}}}, open(out, "w"))
PY

  local persona_brief; persona_brief="$(cat "$PERSONA_FILE")"
  local psid; psid="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  local player_out="$PLAYERDIR/player.jsonl"
  local player_prompt="$PLAYERDIR/player.prompt.md"
  local player_last="$PLAYERDIR/player.last.txt"
  cat > "$player_prompt" <<EOF
$persona_brief

You have a budget of about $BEATS actions for this whole session. Spend them trying to start and play the story, reporting friction as you go. Waiting for the DM to narrate is FREE and does not use your budget — a rich beat can take a few minutes, and the spinner / streaming text is PROGRESS, not a hang, so keep waiting rather than abandoning a beat. When you have played a few real turns and seen enough to judge the experience, call finish with your honest satisfaction (1-10) and a 1-2 sentence verdict. Call give_up ONLY if you are genuinely BLOCKED — a dead control, an error, or the DM truly stalled with no narration — NOT because a beat is slow. Either way, also end your final message with a line exactly like: Satisfaction: N/10. Start now.
EOF
  log "[B] player agent starting (agent=$PLAYER_AGENT persona=$PERSONA, ~$BEATS actions, budget \$$player_budget)…"
  case "$PLAYER_AGENT" in
    claude)
      claude -p "$(cat "$player_prompt")" \
        --session-id "$psid" --mcp-config "$player_cfg" --strict-mcp-config \
        --model "$PLAYER_MODEL" --permission-mode bypassPermissions --max-budget-usd "$player_budget" \
        --output-format stream-json --verbose > "$player_out" 2>> "$PLAYERDIR/player.err"
      ;;
    codex)
      : > "$player_last"
      export CLAWDND_UIPT_URL="$b_url"
      export CLAWDND_UIPT_RUNDIR="$RUNDIR"
      local uipt_channel
      uipt_channel="$(worldos_env UIPT_CHANNEL "")"
      export CLAWDND_UIPT_CHANNEL="$uipt_channel"
      export CLAWDND_UIPT_PERSONA="$PERSONA"
      local codex_player_model
      codex_player_model="$part_b_player_model"
      local codex_player_model_args=()
      if [ -n "${codex_player_model//[[:space:]]/}" ]; then
        codex_player_model_args=(--model "$codex_player_model")
      fi
      # Keep this lane self-contained: support-VM evidence must not mutate CODEX_HOME
      # with `codex mcp add`. Codex CLI 0.120.0 supports the same per-invocation
      # TOML dot-notation overrides used by scripts/play_codex_dm.sh and
      # scripts/play_codex_actor.sh.
      if ! codex_supports_mcp_override_config; then
        printf '[uipt-app] WOS_APP_PLAYER_AGENT=codex requires Codex CLI >= 0.120.0 for codex exec -c mcp_servers.* overrides; upgrade Codex CLI or use WOS_APP_PLAYER_AGENT=claude.\n' >&2
        return 1
      fi
      codex exec \
        --ignore-user-config \
        --ignore-rules \
        --sandbox read-only \
        --json \
        ${codex_player_model_args[@]+"${codex_player_model_args[@]}"} \
        --cd "$ROOT" \
        --output-last-message "$player_last" \
        -c "mcp_servers.clawdnd-uiplayer.command=\"node\"" \
        -c "mcp_servers.clawdnd-uiplayer.args=[\"$PW_DIR/palette_server.js\"]" \
        -c "mcp_servers.clawdnd-uiplayer.env_vars=[\"CLAWDND_UIPT_URL\",\"CLAWDND_UIPT_RUNDIR\",\"CLAWDND_UIPT_CHANNEL\",\"CLAWDND_UIPT_PERSONA\"]" \
        -c "mcp_servers.clawdnd-uiplayer.required=true" \
        -c "mcp_servers.clawdnd-uiplayer.default_tools_approval_mode=\"approve\"" \
        -c "mcp_servers.clawdnd-uiplayer.enabled_tools=[\"screenshot\",\"a11y_tree\",\"click\",\"type\",\"key\",\"wait\",\"report_bug\",\"give_up\",\"finish\"]" \
        - < "$player_prompt" > "$player_out" 2>> "$PLAYERDIR/player.err"
      ;;
  esac
  local player_rc=$?
  log "[B] player agent finished (rc=$player_rc)."

  # Persist the final live surface BEFORE teardown. The release gate reads this disk artifact
  # for palette-live, rather than probing a port after cleanup and accidentally grading the
  # harness instead of the product surface.
  local final_surface="$RUNDIR/session_surface.final.json"
  if ! curl -s --max-time 4 "http://127.0.0.1:$b_port/session-surface" \
        | jq . > "$final_surface" 2>/dev/null; then
    printf '{}\n' > "$final_surface"
  fi

  local player_verdict player_cost
  if [ "$PLAYER_AGENT" = "codex" ]; then
    player_verdict="$(cat "$player_last" 2>/dev/null || true)"
  else
    player_verdict="$(jq -rs 'map(select(.type=="result"))[-1].result // ""' "$player_out" 2>/dev/null)"
  fi
  player_cost="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$player_out" 2>/dev/null)"
  PART_B_PLAYER_COST="${player_cost:-0}"

  b_cleanup; trap - RETURN

  # meta.json (the scorer reads it) + score + summary via the EXISTING scorer (unchanged).
  python3 - "$RUNDIR/meta.json" "$RUN" "$WORLD" "$PERSONA" "$b_port" "$BEATS" "$BUDGET" "$player_cost" "$player_rc" "$BUILD_SHA" "$VERSION" "$PART_B_PROVIDER" "$PLAYER_AGENT" "$part_b_provider_family" "$part_b_auth_surface" "$part_b_dm_model" "$part_b_player_model" "$part_b_scorer_provider" "$part_b_scorer_model" <<'PY'
import json, sys, datetime
(
    out, run, world, persona, port, beats, budget, cost, rc, sha, ver,
    provider, player_agent, provider_family, auth_surface, dm_model,
    player_model, scorer_provider, scorer_model,
) = sys.argv[1:20]
json.dump({
    "run": run, "world": world, "persona": persona, "port": int(port),
    "beats_cap": int(beats), "budget_usd": float(budget),
    "player_cost_usd": round(float(cost or 0), 4), "player_rc": int(rc),
    "build_sha": sha, "version": ver,
    "provider": provider, "player_agent": player_agent,
    "provider_family": provider_family, "auth_surface": auth_surface,
    "dm_model": dm_model, "player_model": player_model,
    "scorer_provider": scorer_provider, "scorer_model": scorer_model,
    "surface": f"built-app-faithful-backend ({provider} provider, {player_agent} player)",
    "session_surface_path": "session_surface.final.json",
    "finished_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
}, open(out, "w"), indent=2)
PY
  log "[B] scoring + summarizing…"
  python3 "$ROOT/qa/ui_playtest_score.py" "$RUNDIR" "$player_verdict" 2>> "$RUNDIR/score.err"
  if [ "$player_rc" -eq 0 ] && [ -f "$RUNDIR/score.json" ]; then
    PART_B_RESULT="PASS"
    if python3 - "$RUNDIR/score.json" <<'PY'
import json, sys
try:
    score = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if score.get("pass") is True else 1)
PY
    then
    PART_B_SCORE_PASS="true"
    else
      PART_B_SCORE_PASS="false"
      set_bucket_pair B "$(classify_part_b_score_failure "$RUNDIR/score.json")"
    fi
  else
    PART_B_RESULT="FAIL"
    PART_B_SCORE_PASS="false"
    set_bucket_pair B "$(classify_part_b_failure_from_artifacts "$RUNDIR" "$PART_B_RESULT")"
    # FIX 1 (#623 false-cap): discriminate a HARNESS/player CRASH from a quality fail. The
    # discriminator is player_rc (the PROCESS exit), NEVER the quality score — a persona that
    # PLAYS to completion and scores low (incl. give_up) exits rc=0 and flows through the
    # unchanged score_pass quality gate above. Only a NON-ZERO player-process exit is a crash.
    # A 429/session-limit is its OWN honest infra path (release_readiness infra_abort_hint /
    # the sweep's QUOTA_ABORT), so exclude it here — only a NON-quota crash is "inconclusive".
    if [ "$player_rc" -ne 0 ] \
       && ! grep -qriE "session limit|HTTP 429|hit your (session|usage) limit" \
            "$RUNDIR/backend.log" "$PLAYERDIR/player.err" 2>/dev/null; then
      PART_B_HARNESS_ERROR="true"
      log "[B] player_rc=$player_rc (non-quota harness/player crash) — marking part_b.harness_error=true (INCONCLUSIVE, re-measure; NOT a quality fail)"
    fi
  fi
  [ -f "$RUNDIR/summary.md" ] && { echo "----- part B summary.md -----"; cat "$RUNDIR/summary.md"; }
}

# A free port at/above $1 (probes 30 candidates). Echoes the port or fails.
pick_free_port() {
  local start="${1:-8800}" p
  for p in $(seq "$start" $((start+30))); do
    if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "$p"; return 0; fi
    exec 3>&- 2>/dev/null || true
  done
  return 1
}

# Every port served by a viewer/server.py whose WORKING DIRECTORY is repo root $1 (one per line,
# ascending). We match on CWD, not the argv path, because the .app launches its TWO viewers
# differently: the read-only LAUNCHER via `startViewer` uses an ABSOLUTE path (<root>/viewer/
# server.py), while the MINTED provider viewer is spawned by scripts/play*.sh with a RELATIVE
# path (`python3 viewer/server.py`) and CWD=repo root. Matching CWD catches BOTH and excludes
# viewers from other checkouts / unrelated services (e.g. the evaOS bridge on 8765). The port is
# the last numeric token of the argv. We resolve PIDs via pgrep then read each full command via
# `ps -o command=` (pgrep -af can truncate). Robust to whatever port PortFinder actually chose.
repo_viewer_ports() {
  local root="$1" rootp pid cwd cmd
  rootp="$(cd "$root" 2>/dev/null && pwd -P)"; rootp="${rootp:-$root}"
  for pid in $(pgrep -f 'viewer/server.py' 2>/dev/null); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    # Match EITHER an absolute argv path under repo root (the LAUNCHER, whose CWD is the .app's
    # temp dir, not the repo) OR CWD == repo root (the MINTED provider viewer, launched by
    # play*.sh with a RELATIVE `viewer/server.py`). Either way it is THIS checkout's viewer; this
    # excludes other checkouts (e.g. /Users/lume/ClawDnD-val) and unrelated services.
    if printf '%s' "$cmd" | grep -qF "$rootp/viewer/server.py" \
       || printf '%s' "$cmd" | grep -qF "$root/viewer/server.py" \
       || [ "$cwd" = "$rootp" ] || [ "$cwd" = "$root" ]; then
      printf '%s\n' "$cmd" | grep -oE '[0-9]{4,5}' | tail -1
    fi
  done | sort -un
}

kill_repo_viewers() {
  local root="$1" rootp pid cwd cmd
  rootp="$(cd "$root" 2>/dev/null && pwd -P)"; rootp="${rootp:-$root}"
  for pid in $(pgrep -f 'viewer/server.py' 2>/dev/null); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    if printf '%s' "$cmd" | grep -qF "$rootp/viewer/server.py" \
       || printf '%s' "$cmd" | grep -qF "$root/viewer/server.py" \
       || [ "$cwd" = "$rootp" ] || [ "$cwd" = "$root" ]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}

port_matches_run() {
  local port="$1" run="$2" status
  [ -n "${port//[[:space:]]/}" ] || return 1
  [ -n "${run//[[:space:]]/}" ] || return 1
  status="$(curl -s --max-time 2 "http://127.0.0.1:$port/app-status" 2>/dev/null)" || return 1
  [ -n "$status" ] || return 1
  printf '%s' "$status" | jq -e --arg run "$run" '
    (.live.run_id // "") == $run
    or ((.viewer.state_root // "") | endswith("/play-state/" + $run))
  ' >/dev/null 2>&1
}

# The launcher viewer's port = the LOWEST repo-viewer port (the .app starts the read-only
# launcher first; any minted provider viewer lands on a HIGHER free port). Echoes it or nothing.
launcher_port_of() {
  repo_viewer_ports "$1" | head -1
}

app_pid_for_bundle() {
  local bundle="$1" bin pid cmd found=0
  bin="$bundle/Contents/MacOS/WorldOSApp"
  for pid in $(pgrep -x WorldOSApp 2>/dev/null || true); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    case "$cmd" in
      "$bin"*) printf '%s\n' "$pid"; found=1 ;;
    esac
  done
  [ "$found" = "1" ] || return 1
}

# Count seated PLAYER characters in a play-state run's campaign snapshot (the DM cold-open seats
# the human PC via load_canon_character/create_character; until it does, the table shows "no
# active character"). $1 = play-state run dir. Echoes an integer (0 if no snapshot yet). Read-only.
_seated_player_count() {
  local snap; snap="$(clawdnd_snapshot_path "$1" 2>/dev/null)"
  [ -n "$snap" ] || { echo 0; return 0; }
  python3 - "$snap" <<'PY' 2>/dev/null || echo 0
import json, sys
try: s = json.load(open(sys.argv[1]))
except Exception: print(0); sys.exit(0)
chars = s.get("characters", {}) or {}
print(sum(1 for c in chars.values() if isinstance(c, dict) and c.get("kind") == "player"))
PY
}

###############################################################################################
# DRIVE
###############################################################################################
case "$PART" in
  A)  run_part_a || true ;;
  B)  run_part_b || true ;;
  *)  run_part_a || true; run_part_b || true ;;
esac

# Top-level run.json — the structured truth (P0): tagged {build_sha, version}, both parts, $spend.
FINAL_DM_SPEND="$(dm_spend)"
TOTAL_SPEND="$(awk -v a="${FINAL_DM_SPEND:-0}" -v b="${PART_B_PLAYER_COST:-0}" 'BEGIN{printf "%.4f", a+b}')"
TOP_PROVIDER_FAMILY="$(provider_family "$PART_B_PROVIDER")"
TOP_AUTH_SURFACE="$(provider_auth_surface "$PART_B_PROVIDER")"
TOP_DM_MODEL="$DM_MODEL"
TOP_PLAYER_MODEL="$PLAYER_MODEL"
if [ "$PART_B_PROVIDER" = "codex" ]; then
  _codex_default="${CLAWDND_CODEX_MODEL:-}"
  TOP_DM_MODEL="${WOS_APP_CODEX_DM_MODEL:-${_codex_default:-gpt-5.5}}"
  TOP_PLAYER_MODEL="${WOS_APP_CODEX_PLAYER_MODEL:-${_codex_default:-gpt-5.5}}"
fi
python3 - "$RUNDIR/run.json" "$RUN" "$WORLD" "$PERSONA" "$BEATS" "$BUDGET" "$BUILD_SHA" "$VERSION" \
          "$PART" "$PART_A_RESULT" "${PART_A_RUNDIR:-}" "${PART_A_MINTED_PORT:-}" \
          "$PART_A_KEPT_BACKEND" "$PART_A_FIRST_TURN_READY" "$PART_B_RESULT" "$PART_B_SCORE_PASS" \
          "$FINAL_DM_SPEND" "$PART_B_PLAYER_COST" "$TOTAL_SPEND" \
          "$PART_A_FAILURE_BUCKET" "$PART_A_FAILURE_DETAIL" "$PART_B_FAILURE_BUCKET" "$PART_B_FAILURE_DETAIL" \
          "$PART_B_PROVIDER" "$PLAYER_AGENT" "$TOP_PROVIDER_FAMILY" "$TOP_AUTH_SURFACE" "$TOP_DM_MODEL" "$TOP_PLAYER_MODEL" \
          "deterministic-ui-playtest" "qa/ui_playtest_score.py" "$PART_B_HARNESS_ERROR" <<'PY'
import json, sys, datetime
(out, run, world, persona, beats, budget, sha, ver, part, a_res, a_run, a_port,
 a_kept, a_first_turn_ready, b_res, b_score_pass, dm_spend, player_cost, total) = sys.argv[1:20]
a_bucket, a_detail, b_bucket, b_detail = sys.argv[20:24]
provider, player_agent = sys.argv[24:26]
provider_family, auth_surface, dm_model, player_model, scorer_provider, scorer_model = sys.argv[26:32]
# FIX 1 (#623 false-cap): appended as the LAST trailing argv so the existing positional slices
# above are untouched (the two literals at argv[30]/[31] are pre-existing/unused; the flag is
# argv[32]). A NON-quota player_rc!=0 crash sets this true → run.json part_b.harness_error, which
# release_readiness.py reads to RED-cap the persona as INCONCLUSIVE (evidence gap), NOT a
# score_pass quality fail. Bounds-guarded so it defaults false when absent (Part-A-only / older
# runs that predate this trailing arg).
b_harness_error = (sys.argv[32] == "true") if len(sys.argv) > 32 else False
json.dump({
    "run": run, "world": world, "persona": persona, "beats_cap": int(beats),
    "budget_usd": float(budget), "build_sha": sha, "version": ver, "part": part,
    "part_a": {"gate": "native_transition_356", "result": a_res,
               "original_result": a_res,
               "failure_bucket": a_bucket or None,
               "failure_detail": a_detail or None,
               "minted_run_dir": a_run or None, "minted_port": int(a_port) if a_port else None,
               "kept_backend_alive": a_kept == "true",
               "first_turn_ready": a_first_turn_ready == "true"},
    "part_b": {"persona_loop": b_res, "score_pass": b_score_pass == "true",
               "provider": provider, "player_agent": player_agent,
               "provider_family": provider_family, "auth_surface": auth_surface,
               "dm_model": dm_model, "player_model": player_model,
               "scorer_provider": scorer_provider, "scorer_model": scorer_model,
               "original_result": b_res,
               "harness_error": b_harness_error,
               "failure_bucket": b_bucket or None,
               "failure_detail": b_detail or None},
    "spend_usd": {"dm_and_companions": round(float(dm_spend or 0), 4),
                  "player_agent": round(float(player_cost or 0), 4),
                  "total": round(float(total or 0), 4)},
    "surface": f"BUILT dist/WorldOS.app (part A) + {provider} provider/{player_agent} player backend (part B)",
    "at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
}, open(out, "w"), indent=2)
PY

log "=== DONE. dir=$RUNDIR ==="
log "part A (#356 gate): $PART_A_RESULT   part B (persona loop): $PART_B_RESULT"
log "spend: DM ~\$$FINAL_DM_SPEND + player ~\$$PART_B_PLAYER_COST = ~\$$TOTAL_SPEND (budget \$$BUDGET)"
[ -f "$RUNDIR/run.json" ] && { echo "----- run.json -----"; cat "$RUNDIR/run.json"; }

EXIT_OK=1
case "$PART" in
  A)
    [ "$PART_A_RESULT" = "PASS" ] || EXIT_OK=0
    ;;
  B)
    [ "$PART_B_RESULT" = "PASS" ] && [ "$PART_B_SCORE_PASS" = "true" ] || EXIT_OK=0
    ;;
  *)
    [ "$PART_A_RESULT" = "PASS" ] || EXIT_OK=0
    [ "$PART_B_RESULT" = "PASS" ] && [ "$PART_B_SCORE_PASS" = "true" ] || EXIT_OK=0
    ;;
esac
[ "$EXIT_OK" = "1" ]
