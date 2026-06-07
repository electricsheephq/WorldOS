#!/usr/bin/env bash
# Run Codex as a constrained ClawDnD player actor.
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

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run"; shift ;;
  --smoke) MODE="smoke"; shift ;;
  -h|--help)
    cat <<'EOF'
Usage: scripts/play_codex_actor.sh [--dry-run|--smoke]

Required environment:
  CLAWDND_PROVIDER=codex
  CLAWDND_WORLD
  CLAWDND_RUN_ID
  CLAWDND_PLAY_PORT
  CLAWDND_PLAY_BUDGET
  CLAWDND_PLAY_SESSION_BUDGET
  CLAWDND_PLAY_MAX_TURNS

Optional:
  CLAWDND_PLAY_COMPANIONS
  CLAWDND_ACTOR_ID
  CLAWDND_ACTOR_ROLE
  WORLDOS_CODEX_MODEL (default: gpt-5.5; set to auto/default/cli-default to let Codex CLI choose)
  CLAWDND_CODEX_MODEL (legacy fallback)
  CLAWDND_STATE_ROOT
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

require_env CLAWDND_PROVIDER
PROVIDER_LOWER="$(printf '%s' "$CLAWDND_PROVIDER" | tr '[:upper:]' '[:lower:]')"
[ "$PROVIDER_LOWER" = "codex" ] || fail "CLAWDND_PROVIDER must be codex"
require_env CLAWDND_WORLD
require_env CLAWDND_RUN_ID
require_env CLAWDND_PLAY_PORT
require_env CLAWDND_PLAY_BUDGET
require_env CLAWDND_PLAY_SESSION_BUDGET
require_env CLAWDND_PLAY_MAX_TURNS

[[ "$CLAWDND_PLAY_PORT" =~ ^[0-9]+$ ]] || fail "CLAWDND_PLAY_PORT must be an integer"
if [ "$CLAWDND_PLAY_PORT" -lt 1 ] || [ "$CLAWDND_PLAY_PORT" -gt 65535 ]; then
  fail "CLAWDND_PLAY_PORT out of range: $CLAWDND_PLAY_PORT"
fi
[[ "$CLAWDND_PLAY_MAX_TURNS" =~ ^[0-9]+$ ]] || fail "CLAWDND_PLAY_MAX_TURNS must be an integer"
for budget_name in CLAWDND_PLAY_BUDGET CLAWDND_PLAY_SESSION_BUDGET; do
  [[ "${!budget_name}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "$budget_name must be a positive decimal"
done
[[ "$CLAWDND_RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]] || fail "CLAWDND_RUN_ID may only contain letters, numbers, '.', '_' and '-'"

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"
if [ "$MODE" = "run" ]; then
  command -v codex >/dev/null 2>&1 || fail "codex CLI is required for real provider runs"
fi

if [ "$MODE" = "smoke" ]; then
  STATE_ROOT="${CLAWDND_STATE_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/clawdnd-codex-smoke.XXXXXX")}"
else
  STATE_ROOT="${CLAWDND_STATE_ROOT:-$ROOT/play-state}"
fi
STATE_ROOT="$(python3 - "$STATE_ROOT" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
)"

RUN_DIR="$STATE_ROOT/$CLAWDND_RUN_ID"
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
    "CLAWDND_STATE_DIR": state_dir,
    "CLAWDND_PLAYER_MOVES": moves,
    "CLAWDND_ACTOR_ID": "",
    "CLAWDND_ACTOR_ROLE": "player",
}
out_path = Path(out)
out_path.write_text(
    "\n".join(
        [
            "[mcp_servers.clawdnd-player]",
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
You are the Codex player actor for ClawDnD run "$CLAWDND_RUN_ID" in world "$CLAWDND_WORLD".

Hard boundary:
- You are a player character, not the DM, narrator, QA harness, campaign author, or engine writer.
- Do not edit files, campaign snapshots, engine store files, QA state, content, skills, prompts, rubrics, or world canon.
- Use only the clawdnd-player tools exposed to you.
- Emit legal player moves only: say, do, clarify, request_check, cast_spell, use_item, attack.
- Read-only tools like look and my_sheet are allowed for grounding.
- If no character or scene is available, ask one concise clarify question through the facade and stop.

Session caps:
- per-turn budget: $CLAWDND_PLAY_BUDGET
- session budget: $CLAWDND_PLAY_SESSION_BUDGET
- max turns: $CLAWDND_PLAY_MAX_TURNS

Act once through the player facade, then stop.
EOF

summary() {
  python3 - "$MODE" "$ROOT" "$STATE_ROOT" "$CLAWDND_WORLD" "$CLAWDND_RUN_ID" "$CLAWDND_PLAY_PORT" "$CONFIG" "$MOVES" <<'PY'
import json
import sys
from pathlib import Path

mode, root, state_root, world, run_id, port, config, moves = sys.argv[1:]
print(json.dumps({
    "ok": True,
    "mode": mode,
    "provider": "codex",
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

echo "[codex-provider] run=$CLAWDND_RUN_ID world=$CLAWDND_WORLD port=$CLAWDND_PLAY_PORT mode=$MODE"
echo "[codex-provider] config=$CONFIG"
echo "[codex-provider] moves=$MOVES"

if [ "$MODE" != "run" ]; then
  summary
  exit 0
fi

# codex exec is pinned to the repo cwd and explicit per-run MCP overrides so
# app/provider proofs do not depend on ambient project config. Pin a
# ChatGPT-account-supported provider model unless the operator explicitly
# selects another one. The Codex CLI account default can drift to a model this
# auth surface rejects, so app playability should not depend on that default.
CODEX_MODEL="${WORLDOS_CODEX_MODEL:-${CLAWDND_CODEX_MODEL:-gpt-5.5}}"
MODEL_ARGS=()
case "$(printf '%s' "$CODEX_MODEL" | tr '[:upper:]' '[:lower:]')" in
  ""|auto|default|cli-default) ;;
  *) MODEL_ARGS=(--model "$CODEX_MODEL") ;;
esac
export CLAWDND_STATE_DIR="$RUN_DIR"
export CLAWDND_PLAYER_MOVES="$MOVES"
export CLAWDND_ACTOR_ID="${CLAWDND_ACTOR_ID:-}"
export CLAWDND_ACTOR_ROLE="${CLAWDND_ACTOR_ROLE:-player}"

codex exec \
  --sandbox read-only \
  --json \
  ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
  --cd "$ROOT" \
  --output-last-message "$LAST_MESSAGE" \
  -c "mcp_servers.clawdnd-player.command=\"uv\"" \
  -c "mcp_servers.clawdnd-player.args=[\"run\",\"--directory\",\"$ROOT/servers/engine\",\"python\",\"player_server.py\"]" \
  -c "mcp_servers.clawdnd-player.env_vars=[\"CLAWDND_STATE_DIR\",\"CLAWDND_PLAYER_MOVES\",\"CLAWDND_ACTOR_ID\",\"CLAWDND_ACTOR_ROLE\"]" \
  -c "mcp_servers.clawdnd-player.required=true" \
  -c "mcp_servers.clawdnd-player.default_tools_approval_mode=\"approve\"" \
  -c "mcp_servers.clawdnd-player.enabled_tools=[\"say\",\"do\",\"clarify\",\"request_check\",\"cast_spell\",\"use_item\",\"attack\",\"look\",\"my_sheet\"]" \
  - < "$PROMPT_FILE" \
  > >(tee -a "$STDOUT_LOG") \
  2> >(tee -a "$STDERR_LOG" >&2)
