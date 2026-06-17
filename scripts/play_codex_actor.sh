#!/usr/bin/env bash
# Run Codex as a constrained WorldOS player actor.
#
# The native app launches this through the provider environment contract. This
# wrapper never writes campaign snapshots or QA/canon content; it only creates
# provider-local logs/config and lets Codex emit moves through player_server.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "[codex-provider] $*" >&2
  exit 2
}

codex_config_path() {
  if [ -n "${CODEX_HOME:-}" ]; then
    printf '%s/config.toml\n' "$CODEX_HOME"
  elif [ -n "${HOME:-}" ]; then
    printf '%s/.codex/config.toml\n' "$HOME"
  fi
}

codex_top_level_service_tier() {
  python3 - "$1" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    text = path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("["):
        break
    match = re.match(r"""service_tier\s*=\s*(['"]?)([^'"\s#]+)\1""", line)
    if match:
        print(match.group(2))
        break
PY
}

validate_codex_service_tier() {
  local config tier
  config="$(codex_config_path)"
  [ -n "${config//[[:space:]]/}" ] || return 0
  [ -f "$config" ] || return 0
  tier="$(codex_top_level_service_tier "$config")"
  case "$tier" in
    ""|fast|flex) return 0 ;;
    *)
      fail "Codex CLI config drift: service_tier must be unset, 'fast', or 'flex' in $config for codex-cli >=0.128.0; found '$tier'. Run scripts/codex_qa_home.sh and set CODEX_HOME, or update the selected Codex config."
      ;;
  esac
}

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run"; shift ;;
  --smoke) MODE="smoke"; shift ;;
  -h|--help)
    cat <<'EOF'
Usage: scripts/play_codex_actor.sh [--dry-run|--smoke]

Required environment:
  WORLDOS_PROVIDER=codex
  WORLDOS_WORLD
  WORLDOS_RUN_ID
  WORLDOS_PLAY_PORT
  WORLDOS_PLAY_BUDGET
  WORLDOS_PLAY_SESSION_BUDGET
  WORLDOS_PLAY_MAX_TURNS

Optional:
  WORLDOS_PLAY_COMPANIONS
  WORLDOS_ACTOR_ID
  WORLDOS_ACTOR_ROLE
  WORLDOS_CODEX_MODEL (default: gpt-5.5; set to auto/default/cli-default to let Codex CLI choose)
  WORLDOS_CODEX_MODEL (legacy fallback)
  WORLDOS_STATE_ROOT
EOF
    exit 0
    ;;
  --*) fail "unknown option: $1" ;;
esac
[ "$#" -eq 0 ] || fail "unexpected argument: $1"

require_env() {
  local name="$1" value="${!1:-}"
  [ -n "${value//[[:space:]]/}" ] || fail "missing required env: $name"
}

require_env WORLDOS_PROVIDER
PROVIDER_LOWER="$(printf '%s' "$WORLDOS_PROVIDER" | tr '[:upper:]' '[:lower:]')"
[ "$PROVIDER_LOWER" = "codex" ] || fail "WORLDOS_PROVIDER must be codex"
require_env WORLDOS_WORLD
require_env WORLDOS_RUN_ID
require_env WORLDOS_PLAY_PORT
require_env WORLDOS_PLAY_BUDGET
require_env WORLDOS_PLAY_SESSION_BUDGET
require_env WORLDOS_PLAY_MAX_TURNS

[[ "$WORLDOS_PLAY_PORT" =~ ^[0-9]+$ ]] || fail "WORLDOS_PLAY_PORT must be an integer"
if [ "$WORLDOS_PLAY_PORT" -lt 1 ] || [ "$WORLDOS_PLAY_PORT" -gt 65535 ]; then
  fail "WORLDOS_PLAY_PORT out of range: $WORLDOS_PLAY_PORT"
fi
[[ "$WORLDOS_PLAY_MAX_TURNS" =~ ^[0-9]+$ ]] || fail "WORLDOS_PLAY_MAX_TURNS must be an integer"
for budget_name in WORLDOS_PLAY_BUDGET WORLDOS_PLAY_SESSION_BUDGET; do
  [[ "${!budget_name}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "$budget_name must be a positive decimal"
done
[[ "$WORLDOS_RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]] || fail "WORLDOS_RUN_ID may only contain letters, numbers, '.', '_' and '-'"

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"
if [ "$MODE" = "run" ]; then
  command -v codex >/dev/null 2>&1 || fail "codex CLI is required for real provider runs"
fi

if [ "$MODE" = "smoke" ]; then
  STATE_ROOT="${WORLDOS_STATE_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/worldos-codex-smoke.XXXXXX")}"
else
  STATE_ROOT="${WORLDOS_STATE_ROOT:-$ROOT/play-state}"
fi
STATE_ROOT="$(python3 - "$STATE_ROOT" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
)"

RUN_DIR="$STATE_ROOT/$WORLDOS_RUN_ID"
PROVIDER_DIR="$RUN_DIR/codex-provider"
MOVES="$RUN_DIR/player_moves.jsonl"
CONFIG="$PROVIDER_DIR/codex-player.toml"
PROMPT_FILE="$PROVIDER_DIR/prompt.md"
STDOUT_LOG="$PROVIDER_DIR/codex.stdout.jsonl"
STDERR_LOG="$PROVIDER_DIR/codex.stderr.log"
LAST_MESSAGE="$PROVIDER_DIR/codex.last.txt"

mkdir -p "$PROVIDER_DIR"
touch "$MOVES"

python3 - "$ROOT" "$RUN_DIR" "$MOVES" "$CONFIG" <<'PY'
import json
import sys
from pathlib import Path

root, state_dir, moves, out = sys.argv[1:]
env = {
    "WORLDOS_STATE_DIR": state_dir,
    "WORLDOS_PLAYER_MOVES": moves,
    "WORLDOS_ACTOR_ID": "",
    "WORLDOS_ACTOR_ROLE": "player",
}
out_path = Path(out)
out_path.write_text(
    "\n".join(
        [
            "[mcp_servers.worldos-player]",
            'command = "uv"',
            "args = " + json.dumps(
                ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"]
            ),
            "env = " + "{"
            + ", ".join(f"{key} = {json.dumps(value)}" for key, value in env.items())
            + "}",
            "required = true",
            "enabled_tools = "
            + json.dumps(
                [
                    "say",
                    "do",
                    "clarify",
                    "request_check",
                    "cast_spell",
                    "use_item",
                    "attack",
                    "look",
                    "my_sheet",
                ]
            ),
            'default_tools_approval_mode = "approve"',
            "",
        ]
    ),
    encoding="utf-8",
)
PY

cat > "$PROMPT_FILE" <<EOF
You are the Codex player actor for WorldOS run "$WORLDOS_RUN_ID" in world "$WORLDOS_WORLD".

Hard boundary:
- You are a player character, not the DM, narrator, QA harness, campaign author, or engine writer.
- Do not edit files, campaign snapshots, engine store files, QA state, content, skills, prompts, rubrics, or world canon.
- Use only the worldos-player tools exposed to you.
- Emit legal player moves only: say, do, clarify, request_check, cast_spell, use_item, attack.
- Read-only tools like look and my_sheet are allowed for grounding.
- If no character or scene is available, ask one concise clarify question through the facade and stop.

Session caps:
- per-turn budget: $WORLDOS_PLAY_BUDGET
- session budget: $WORLDOS_PLAY_SESSION_BUDGET
- max turns: $WORLDOS_PLAY_MAX_TURNS

Act once through the player facade, then stop.
EOF

summary() {
  python3 - "$MODE" "$ROOT" "$STATE_ROOT" "$WORLDOS_WORLD" "$WORLDOS_RUN_ID" "$WORLDOS_PLAY_PORT" "$CONFIG" "$MOVES" <<'PY'
import json
import sys
from pathlib import Path

mode, root, state_root, world, run_id, port, config, moves = sys.argv[1:]
print(json.dumps({
    "ok": True,
    "mode": mode,
    "provider": "codex",
    "provider_family": "codex-openai",
    "auth_surface": "codex-cli",
    "repo": root,
    "state_root": state_root,
    "world": world,
    "run_id": run_id,
    "port": int(port),
    "config": str(Path(config).resolve(strict=False)),
    "moves": str(Path(moves).resolve(strict=False)),
}, indent=2, sort_keys=True))
PY
}

echo "[codex-provider] run=$WORLDOS_RUN_ID world=$WORLDOS_WORLD port=$WORLDOS_PLAY_PORT mode=$MODE"
echo "[codex-provider] config=$CONFIG"
echo "[codex-provider] moves=$MOVES"

if [ "$MODE" != "run" ]; then
  summary
  exit 0
fi
validate_codex_service_tier

# codex exec is pinned to the repo cwd and explicit per-run MCP overrides so
# app/provider proofs do not depend on ambient project config. Pin a
# ChatGPT-account-supported provider model unless the operator explicitly
# selects another one. The Codex CLI account default can drift to a model this
# auth surface rejects, so app playability should not depend on that default.
CODEX_MODEL="${WORLDOS_CODEX_MODEL:-gpt-5.5}"
MODEL_ARGS=()
case "$(printf '%s' "$CODEX_MODEL" | tr '[:upper:]' '[:lower:]')" in
  ""|auto|default|cli-default) ;;
  *) MODEL_ARGS=(--model "$CODEX_MODEL") ;;
esac
export WORLDOS_STATE_DIR="$RUN_DIR"
export WORLDOS_PLAYER_MOVES="$MOVES"
export WORLDOS_ACTOR_ID="${WORLDOS_ACTOR_ID:-}"
export WORLDOS_ACTOR_ROLE="${WORLDOS_ACTOR_ROLE:-player}"

codex exec \
  --sandbox read-only \
  --json \
  ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
  --cd "$ROOT" \
  --output-last-message "$LAST_MESSAGE" \
  -c "mcp_servers.worldos-player.command=\"uv\"" \
  -c "mcp_servers.worldos-player.args=[\"run\",\"--directory\",\"$ROOT/servers/engine\",\"python\",\"player_server.py\"]" \
  -c "mcp_servers.worldos-player.env_vars=[\"WORLDOS_STATE_DIR\",\"WORLDOS_PLAYER_MOVES\",\"WORLDOS_ACTOR_ID\",\"WORLDOS_ACTOR_ROLE\"]" \
  -c "mcp_servers.worldos-player.required=true" \
  -c "mcp_servers.worldos-player.default_tools_approval_mode=\"approve\"" \
  -c "mcp_servers.worldos-player.enabled_tools=[\"say\",\"do\",\"clarify\",\"request_check\",\"cast_spell\",\"use_item\",\"attack\",\"look\",\"my_sheet\"]" \
  - < "$PROMPT_FILE" \
  > >(tee -a "$STDOUT_LOG") \
  2> >(tee -a "$STDERR_LOG" >&2)
