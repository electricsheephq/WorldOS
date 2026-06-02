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
#   WORLDOS_DM_MODEL           DM model (default sonnet). CLAWDND_PLAY_BUDGET caps each DM turn.
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
# Part-A cold-open mint deadline (seconds). The #356 banner spawns the DM cold open, whose
# --effort max world-build runs ~280–400s (qa/lib_beat_driver.sh WORLDOS_COLDOPEN_TIMEOUT=400);
# the old 210s poll (70 × 3s) was SHORTER than a max-effort cold open, so a slow-but-healthy
# mint timed out as a spurious FAIL. Give the poll a 420s budget (just past the cold-open
# deadline), env-overridable for fast inner loops.
PART_A_DEADLINE="${WOS_APP_PART_A_DEADLINE:-420}"
if [ "$KEEP_MINTED_BACKEND" = "1" ] && [ "$PART" != "A" ]; then
  printf '[uipt-app] WOS_APP_KEEP_MINTED_BACKEND=1 requires WOS_APP_PART=A; refusing to mix kept native backend with part B.\n' >&2
  exit 2
fi

PW_DIR="$ROOT/qa/playwright"
APP_BUNDLE="$ROOT/dist/WorldOS.app"
PREFERRED_PORT="${WOS_APP_PREFERRED_PORT:-8765}"   # matches RootView.swift @AppStorage("preferredPort") default
DM_MODEL="$(worldos_env DM_MODEL sonnet)"
PLAYER_MODEL="$(worldos_env UIPT_PLAYER_MODEL sonnet)"
PERSONA_FILE="$ROOT/qa/play_player_browser_${PERSONA}.txt"

RUNDIR="$ROOT/qa/ui_playtest_runs/$RUN"
NATIVE_DIR="$RUNDIR/native"
PLAYERDIR="$RUNDIR/player"
rm -rf "$RUNDIR" 2>/dev/null
mkdir -p "$NATIVE_DIR" "$PLAYERDIR/screenshots" "$PLAYERDIR/a11y"

BUILD_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
VERSION="$( ([ -f "$ROOT/VERSION" ] && cat "$ROOT/VERSION") \
            || git -C "$ROOT" describe --tags --always 2>/dev/null \
            || echo "unknown")"
log() { printf '[uipt-app] %s\n' "$*"; }
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

###############################################################################################
# PART A — NATIVE-TRANSITION GATE (re-verifies #356)
###############################################################################################
PART_A_RESULT="skipped"; PART_A_MINTED_PORT=""; PART_A_RUNDIR=""; PART_A_KEPT_BACKEND="false"; PART_A_FIRST_TURN_READY="false"; PART_A_FAILURE_BUCKET=""; PART_A_FAILURE_DETAIL=""
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
  a_log() { printf '%s\n' "$*" | tee -a "$tlog"; }

  # Raise WorldOS to front (AppKit activate — NOT System Events, no TCC dialog), verify it is
  # z-order 0, then CGEvent-click the calibrated RESUME → PLAY CTA center. Idempotent — safe to
  # call repeatedly until the bridge mints a session. Writes its steps to $tlog.
  click_play_cta() {
    python3 - "$tlog" "$APP_BUNDLE" <<'PY' || echo "[A] CGEvent click attempt FAILED" >> "$tlog"
import os, sys, time, Quartz
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
b = win["kCGWindowBounds"]; X, Y, W, H = b["X"], b["Y"], b["Width"], b["Height"]
cx, cy = X + W - 219, Y + 204          # calibrated CTA center (right-aligned button)
say(f"[A] window X={X} Y={Y} W={W} H={H}; front={front!r}; click=({cx:.0f},{cy:.0f})")
def post(ev): Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (cx, cy), Quartz.kCGMouseButtonLeft)); time.sleep(0.10)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (cx, cy), Quartz.kCGMouseButtonLeft)); time.sleep(0.08)
post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp,   (cx, cy), Quartz.kCGMouseButtonLeft))
say("[A] click posted.")
PY
  }

  if [ "${WOS_APP_SKIP_BUILD:-0}" != "1" ]; then
    a_log "[A] pkill WorldOSApp + THIS checkout's stale viewers, rm -rf $APP_BUNDLE, fresh build…"
    if [ "${WOS_APP_NO_GLOBAL_KILL:-0}" = "1" ]; then
      a_log "[A] WOS_APP_NO_GLOBAL_KILL=1 — preserving other WorldOSApp processes."
    else
      pkill -x WorldOSApp >/dev/null 2>&1 || true
    fi
    # ONLY reap viewers spawned from THIS repo root — NEVER a blanket `pkill -f viewer/server.py`
    # (that would kill unrelated services on this host, e.g. the evaOS desktop-bridge squatting
    # 8765 on the Tailscale iface, or another checkout's viewer). Match the absolute repo path.
    pkill -f "$ROOT/viewer/server.py" >/dev/null 2>&1 || true
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
  for _ in $(seq 1 80); do
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

  # BEFORE screenshot + baseline play-state set (so a NEW run dir is detectable post-click).
  screenshot "$NATIVE_DIR/before.png" && a_log "[A] before.png captured" || a_log "[A] before.png deferred to orchestrator"
  local before_dirs; before_dirs="$(ls -1 "$ROOT/play-state" 2>/dev/null | sort || true)"

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
  a_log "[A] raising WorldOS to front + CGEvent-clicking the RESUME → PLAY CTA (with retries)…"
  click_play_cta   # first attempt (the poll re-clicks if the focus-race ate it)

  # ASSERT the mint. Poll up to PART_A_DEADLINE (default 420s, env WOS_APP_PART_A_DEADLINE) for
  # BOTH: (i) a NEW play-state run dir AND (ii) a NEW viewer (a DIFFERENT port than the launcher)
  # whose /session-surface reports can_act:true. The DM cold-open mints the campaign; can_act flips
  # once the move-sink is set AND the viewer binds the live campaign (auto-follow), which happens
  # early in the cold-open turn — but the budget must outlast the max-effort cold open (~280–400s),
  # so the old 210s poll was a spurious FAIL on a slow-but-healthy mint. RE-CLICK every ~24s while
  # nothing has minted: on a busy multi-app desktop another window can steal focus between the
  # activate and the CGEvent, swallowing the click — a re-click recovers it.
  local part_a_polls=$(( PART_A_DEADLINE / 3 )); [ "$part_a_polls" -lt 1 ] && part_a_polls=1
  a_log "[A] polling for a minted live session (new run dir + can_act:true on a new port; deadline ${PART_A_DEADLINE}s / ${part_a_polls} polls)…"
  local minted_port="" minted_run="" can_act="false"
  for i in $(seq 1 "$part_a_polls"); do
    # (i) a new play-state dir
    local now_dirs new_dir
    now_dirs="$(ls -1 "$ROOT/play-state" 2>/dev/null | sort || true)"
    new_dir="$(comm -13 <(printf '%s\n' "$before_dirs") <(printf '%s\n' "$now_dirs") 2>/dev/null | head -1)"
    # Re-click if nothing has started after ~24s and ~48s (idempotent: clicking RESUME→PLAY again
    # before the mint just re-issues startPlay; once a run dir exists we stop clicking).
    if [ -z "$new_dir" ] && { [ "$i" = "6" ] || [ "$i" = "12" ]; }; then
      a_log "[A] no mint yet after $((i*4))s — re-clicking the CTA (focus-race recovery)…"
      click_play_cta
    fi
    # (ii) ANY of THIS repo's viewers (other than the launcher) now reporting can_act:true. We
    # enumerate the live viewer ports from the running processes (robust to the actual port the
    # PortFinder chose) instead of guessing a fixed range.
    local p surf ca
    for p in $(repo_viewer_ports "$ROOT"); do
      [ "$p" = "$launcher_port" ] && continue
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
  case "$PART_B_PROVIDER" in
    claude)
      (
        export PATH="$PATH_NOOPEN"
        export WORLDOS_DM_MODEL="$DM_MODEL" CLAWDND_DM_MODEL="$DM_MODEL"
        export CLAWDND_PLAY_PORT="$b_port"
        export CLAWDND_PLAY_BUDGET="${CLAWDND_PLAY_BUDGET:-1.50}"
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
        export CLAWDND_CODEX_MODEL="${WOS_APP_CODEX_DM_MODEL:-$codex_default_model}"
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
  for i in $(seq 1 120); do   # up to ~6 min for the full cold-open
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
      if [ "$saw_canact" = "1" ] && [ "$saw_pc" = "1" ] && [ "$i" -ge 50 ]; then
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

You have a budget of about $BEATS actions for this whole session. Spend them trying to start and play the story, reporting friction as you go. When you have either (a) genuinely gotten stuck after reporting it, or (b) played a few real turns and seen enough to judge the experience, you may stop — give a final 1-2 sentence verdict and, if you got stuck, call give_up. Start now.
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
      codex_player_model="${WOS_APP_CODEX_PLAYER_MODEL:-$codex_default_model}"
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
        -c "mcp_servers.clawdnd-uiplayer.enabled_tools=[\"screenshot\",\"a11y_tree\",\"click\",\"type\",\"key\",\"wait\",\"report_bug\",\"give_up\"]" \
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
  python3 - "$RUNDIR/meta.json" "$RUN" "$WORLD" "$PERSONA" "$b_port" "$BEATS" "$BUDGET" "$player_cost" "$player_rc" "$BUILD_SHA" "$VERSION" "$PART_B_PROVIDER" "$PLAYER_AGENT" <<'PY'
import json, sys, datetime
out, run, world, persona, port, beats, budget, cost, rc, sha, ver, provider, player_agent = sys.argv[1:14]
json.dump({
    "run": run, "world": world, "persona": persona, "port": int(port),
    "beats_cap": int(beats), "budget_usd": float(budget),
    "player_cost_usd": round(float(cost or 0), 4), "player_rc": int(rc),
    "build_sha": sha, "version": ver,
    "provider": provider, "player_agent": player_agent,
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
python3 - "$RUNDIR/run.json" "$RUN" "$WORLD" "$PERSONA" "$BEATS" "$BUDGET" "$BUILD_SHA" "$VERSION" \
          "$PART" "$PART_A_RESULT" "${PART_A_RUNDIR:-}" "${PART_A_MINTED_PORT:-}" \
          "$PART_A_KEPT_BACKEND" "$PART_A_FIRST_TURN_READY" "$PART_B_RESULT" "$PART_B_SCORE_PASS" \
          "$FINAL_DM_SPEND" "$PART_B_PLAYER_COST" "$TOTAL_SPEND" \
          "$PART_A_FAILURE_BUCKET" "$PART_A_FAILURE_DETAIL" "$PART_B_FAILURE_BUCKET" "$PART_B_FAILURE_DETAIL" \
          "$PART_B_PROVIDER" "$PLAYER_AGENT" <<'PY'
import json, sys, datetime
(out, run, world, persona, beats, budget, sha, ver, part, a_res, a_run, a_port,
 a_kept, a_first_turn_ready, b_res, b_score_pass, dm_spend, player_cost, total) = sys.argv[1:20]
a_bucket, a_detail, b_bucket, b_detail = sys.argv[20:24]
provider, player_agent = sys.argv[24:26]
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
               "original_result": b_res,
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
