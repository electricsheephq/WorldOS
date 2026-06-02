#!/usr/bin/env bash
# Shared launcher helpers for the double-clickable local play scripts.
#
# Keep this dependency-free: these helpers run before uv/Claude/Python environments
# are provisioned, so failures need to be clear in a plain macOS Terminal window.

# Make user-installed tools findable regardless of how play was started.
# A GUI launch (Finder/Dock → LaunchServices, or the native app's provider bridge) inherits
# launchd's minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so claude (~/.local/bin) and
# uv/python3 (Homebrew) aren't on PATH and clawdnd_missing_commands would fail closed. Prepend
# the standard macOS tool locations so the dashboard launches identically from a Terminal, a
# double-click, or the app. Idempotent — skips any dir already on PATH; skips dirs that don't exist.
clawdnd_augment_path() {
  local d
  for d in "$HOME/.local/bin" /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin; do
    [ -d "$d" ] || continue
    case ":$PATH:" in
      *":$d:"*) ;;
      *) PATH="$d:$PATH" ;;
    esac
  done
  export PATH
}
clawdnd_augment_path

clawdnd_missing_commands() {
  local missing=() cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "ClawDnD cannot start yet: missing command(s): ${missing[*]}" >&2
    echo >&2
    echo "Install the missing tools, then double-click the launcher again." >&2
    echo "Required for dashboard play: python3, claude, uv, jq, curl." >&2
    return 1
  fi
}

clawdnd_port_available() {
  local port="$1"
  case "$port" in
    ''|*[!0-9]*)
      return 1
      ;;
  esac
  if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    return 1
  fi
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

clawdnd_choose_port() {
  local requested="$1" explicit="${2:-0}" p
  case "$requested" in
    ''|*[!0-9]*)
      echo "Invalid dashboard port: $requested" >&2
      return 1
      ;;
  esac
  if [ "$requested" -lt 1 ] || [ "$requested" -gt 65535 ]; then
    echo "Dashboard port must be between 1 and 65535: $requested" >&2
    return 1
  fi
  if clawdnd_port_available "$requested"; then
    printf '%s\n' "$requested"
    return 0
  fi
  if [ "$explicit" = "1" ]; then
    echo "Port $requested is already in use." >&2
    echo "Close the existing WorldOS window, or run with another port:" >&2
    echo "  ./worldos-play.command baldurs-gate '' 8766" >&2
    return 1
  fi
  for p in $(seq $((requested + 1)) $((requested + 40))); do
    if clawdnd_port_available "$p"; then
      echo "Port $requested is already in use; using $p instead." >&2
      printf '%s\n' "$p"
      return 0
    fi
  done
  echo "Could not find a free local dashboard port near $requested." >&2
  echo "Close an existing viewer/monitor, or pass a port explicitly." >&2
  return 1
}

# --- Single-flight launch lock --------------------------------------------------------------
# Refuse a SECOND concurrent ensemble cold-open from this checkout, so two play_party.sh runs
# can't collide on session ids / the viewer port. (Observed under memory pressure: launching a
# second cold-open while one was already running failed with "Session ID already in use".)
#
# Mechanism: an atomic `mkdir` lock dir holding the owner's PID, with PID-liveness staleness.
# Deliberately portable and flock-FREE — stock macOS has no flock(1) (the collision was seen on a
# Mac), and more decisively, the viewer supervisor in play_party.sh is an orphan-surviving respawn
# loop: a flock fd inherited by it would stay held FOREVER if the main process is OOM-killed
# (SIGKILL skips the cleanup trap). Recording the MAIN pid instead lets the next launch notice the
# owner is gone (`kill -0` fails) and reclaim the lock. `mkdir`/`kill -0`/`rm` behave identically
# on macOS and Linux, so there is one code path and the behavioral test runs everywhere.
#
# Knob: CLAWDND_LAUNCH_LOCK_WAIT = seconds to wait for a LIVE holder before rejecting (default 5;
# 0 = reject immediately). The short wait lets the native app's "restart" (terminate-old then
# start-new) succeed — the old run releases on SIGTERM and the new one acquires within the window.
clawdnd_acquire_launch_lock() {
  local root="$1" lock="$1/play-state/.launch.lock" waited=0 held_pid
  local wait="${CLAWDND_LAUNCH_LOCK_WAIT:-5}"
  case "$wait" in ''|*[!0-9]*) wait=5 ;; esac   # tolerate a bad value → default
  if ! mkdir -p "$root/play-state" 2>/dev/null; then
    echo "[play-party] could not create $root/play-state for the launch lock." >&2
    return 1
  fi
  while :; do
    if mkdir "$lock" 2>/dev/null; then
      printf '%s\n' "$$" > "$lock/pid"          # won the atomic create → we own it
      return 0
    fi
    if [ ! -d "$lock" ]; then
      # mkdir failed but the lock dir does NOT exist → this is NOT contention (read-only checkout,
      # missing parent, disk full). Fail fast with a clear message instead of spinning forever.
      echo "[play-party] could not create the launch lock at $lock — check permissions/disk space." >&2
      return 1
    fi
    held_pid="$(cat "$lock/pid" 2>/dev/null || true)"
    if [ -n "$held_pid" ] && kill -0 "$held_pid" 2>/dev/null; then
      :                                         # a LIVE cold-open holds it → wait, then reject
    elif [ -z "$held_pid" ] && [ "$waited" -lt "$wait" ]; then
      :                                         # owner mid-acquire (mkdir done, pid not yet written)
                                                # → wait a beat and re-read; do NOT reclaim (race-safe)
    else
      # dead owner (gone, e.g. OOM-killed before cleanup), or a lock that never recorded a pid
      echo "[play-party] clearing a stale launch lock (previous holder pid ${held_pid:-none} is gone)." >&2
      rm -rf "$lock" 2>/dev/null
      continue
    fi
    if [ "$waited" -ge "$wait" ]; then
      echo "[play-party] another WorldOS cold-open is already running (pid ${held_pid:-?}) — refusing to start a second so they can't collide. Stop it first, or wait for it to finish." >&2
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

# Release the launch lock, but ONLY if we are the recorded owner — a rejected second launch must
# never delete the winner's lock. Safe to call unconditionally from cleanup. $1 = repo ROOT.
clawdnd_release_launch_lock() {
  local lock="$1/play-state/.launch.lock"
  if [ "$(cat "$lock/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -rf "$lock" 2>/dev/null
  fi
  return 0
}
