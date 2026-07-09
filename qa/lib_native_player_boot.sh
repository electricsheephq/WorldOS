#!/usr/bin/env bash
# lib_native_player_boot.sh — shared boot helpers for the native-window player harnesses
# (qa/ui_playtest_player.sh, the T3 LLM gate, and qa/player_smoke.sh, the #1443 deterministic
# post-build smoke). Extracted 2026-07-09 so the cross-Space launch fix (#1443) lives in ONE
# place both runners source, instead of drifting across two copies.
#
# Source this, don't execute it: `. "$ROOT/qa/lib_native_player_boot.sh"`

# --- locate the installed MCP SDK (worktrees have no node_modules — accept an explicit override
# or the canonical checkout's qa/playwright install). $PW_DIR must be set by the caller. -----------
find_sdk_node_modules() {
  local c
  for c in "${WORLDOS_NPT_NODE_MODULES:-}" \
           "${PW_DIR:-}/node_modules" \
           "/Users/lume/WorldOS/qa/playwright/node_modules"; do
    [ -n "$c" ] && [ -d "$c/@modelcontextprotocol" ] && { echo "$c"; return 0; }
  done
  return 1
}

# --- locate the player app ----------------------------------------------------------------------
find_player_app() {
  local c
  for c in "${WORLDOS_PLAYER_APP:-}" \
           "$HOME/Applications/WorldOSPlayer.app" \
           "/Applications/WorldOSPlayer.app" \
           "$HOME/worldos-session-notes/w5a-build/WorldOSPlayer.app"; do
    [ -n "$c" ] && [ -d "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

# --- free port in 8990-8999 ----------------------------------------------------------------------
pick_port() {
  local p
  for p in $(seq 8990 8999); do
    if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "$p"; return 0; fi
    exec 3>&- 2>/dev/null || true
  done
  return 1
}

# --- #1456 owner-active guard ---------------------------------------------------------------------
# Player QA drives synthetic clicks/keys and captures the screen — running it while the owner is
# actively using the Mac hijacks their session (the #1456 report). Refuse to launch when the console
# user had HID input activity within the last threshold, UNLESS FORCE_PLAYER_QA=1. HIDIdleTime is
# nanoseconds since the last HID event, read from ioreg. Prints a distinct "SMOKE-DEFERRED (owner
# active)" line and returns PLAYER_QA_OWNER_ACTIVE_RC so the caller can exit with a distinct code.
# A missing/unreadable HIDIdleTime is treated as idle (proceed) rather than blocking a headless box.
PLAYER_QA_OWNER_ACTIVE_RC=75
owner_active_guard() {
  local threshold="${WORLDOS_PLAYER_IDLE_THRESHOLD:-120}"
  if [ "${FORCE_PLAYER_QA:-0}" = "1" ]; then
    echo "[guard] FORCE_PLAYER_QA=1 — bypassing owner-active guard." >&2
    return 0
  fi
  local idle_ns idle_s
  idle_ns="$(ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print $NF; exit}')"
  if ! [[ "$idle_ns" =~ ^[0-9]+$ ]]; then
    echo "[guard] WARN: could not read HIDIdleTime (got '$idle_ns') — proceeding (assume idle)." >&2
    return 0
  fi
  idle_s=$(( idle_ns / 1000000000 ))
  if [ "$idle_s" -lt "$threshold" ]; then
    echo "SMOKE-DEFERRED (owner active): last input ${idle_s}s ago (< ${threshold}s idle). Set FORCE_PLAYER_QA=1 to override." >&2
    return "$PLAYER_QA_OWNER_ACTIVE_RC"
  fi
  echo "[guard] owner idle ${idle_s}s (>= ${threshold}s) — OK to launch player QA." >&2
  return 0
}

# --- #1456 windowed launch args -------------------------------------------------------------------
# Force the Unity standalone player WINDOWED at a fixed size (never fullscreen) so a QA run never
# takes over the display. -screen-fullscreen/-screen-width/-screen-height are Unity's built-in
# standalone-player command-line args, honored before the app's own window setup — no Unity/C#
# change needed. Emits one arg per line; callers read it into an array. Size is env-overridable.
# #1466 FIX B: an optional first arg is a run-dir log path — when given, appends Unity's own
# -logFile <path> so THIS run's player log lands in the run dir instead of the shared, overwritten-
# every-launch default (~/Library/Logs/worldos/WorldOSPlayer/Player.log).
player_windowed_launch_args() {
  local logfile="${1:-}"
  local args=(-screen-fullscreen 0 \
    -screen-width "${WORLDOS_PLAYER_WIN_W:-1280}" \
    -screen-height "${WORLDOS_PLAYER_WIN_H:-800}")
  [ -n "$logfile" ] && args+=(-logFile "$logfile")
  printf '%s\n' "${args[@]}"
}

# --- #1466 FIX B: fallback copy of Unity's own default player log ----------------------------------
# Belt-and-suspenders for -logFile above: if it was ignored, stripped, or the run predates this fix,
# copy the shared default log location (overwritten on every launch — Player-prev.log is the PRIOR
# run) into THIS run's dir under distinct names so it's never confused with the -logFile capture.
# Silent no-op if the source dir/files don't exist (e.g. never launched, or a non-default log path).
copy_player_log_fallback() {
  local dest="$1"
  local src_dir="$HOME/Library/Logs/worldos/WorldOSPlayer"
  [ -d "$dest" ] || return 0
  [ -f "$src_dir/Player.log" ] && cp -f "$src_dir/Player.log" "$dest/Player.log.fallback" 2>/dev/null
  [ -f "$src_dir/Player-prev.log" ] && cp -f "$src_dir/Player-prev.log" "$dest/Player-prev.log.fallback" 2>/dev/null
  return 0
}
