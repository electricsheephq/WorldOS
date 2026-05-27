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
    echo "Close the existing ClawDnD window, or run with another port:" >&2
    echo "  ./clawdnd-play.command baldurs-gate '' 8766" >&2
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
