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

# --- #1443 launch-into-same-Space discipline ------------------------------------------------------
# macOS opens a newly-launched app's window on whichever Space is CURRENT at launch time. The T3
# finding was WorldOSPlayer opening onto (or drifting to) a different Space than the harness, which
# then made `screencapture -l <id>` refuse to rasterize it. We cannot reliably know which terminal
# app is running this script (Terminal/iTerm/VSCode/a detached `claude -p` parent), so instead of
# guessing a name, we ask macOS which app is CURRENTLY frontmost and re-activate exactly that one —
# re-asserting "whatever GUI context is in front, stays in front" right before we spawn the player.
# This is the simplest mechanism that needs no app-name knowledge and no Unity/C# change: it just
# closes the race window between an earlier Space switch (e.g. a previous run's cleanup) and this
# run's launch. Never fails loud — a failure here just means we skip the pin, not abort the run;
# native_palette_core.js's activate-before-capture (#1443) is the real belt-and-suspenders fix that
# makes screenshot/click work even if the player DOES end up on a different Space.
activate_current_space_context() {
  local front
  front="$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)"
  if [ -n "$front" ]; then
    osascript -e "tell application \"$front\" to activate" >/dev/null 2>&1 || true
    sleep 0.3
  fi
}
