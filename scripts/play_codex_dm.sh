#!/usr/bin/env bash
# Run Codex as a Dungeon Master provider for a live WorldOS/OpenWorlds session.
#
# This is the DM counterpart to play_codex_actor.sh. The actor wrapper is a
# constrained player facade; this wrapper owns the live viewer, full engine/rules
# MCP contract, DM narration chat, and player move resolution loop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "[codex-dm-provider] $*" >&2
  exit 2
}

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run"; shift ;;
  --smoke) MODE="smoke"; shift ;;
  -h|--help)
    cat <<'EOF'
Usage: scripts/play_codex_dm.sh [--dry-run|--smoke]

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
  CLAWDND_PLAY_HERO
  CLAWDND_CODEX_MODEL
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
command -v jq >/dev/null 2>&1 || fail "jq is required"
if [ "$MODE" = "run" ]; then
  command -v codex >/dev/null 2>&1 || fail "codex CLI is required for real provider runs"
fi

if [ "$MODE" = "smoke" ]; then
  STATE_ROOT="${CLAWDND_STATE_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/clawdnd-codex-dm-smoke.XXXXXX")}"
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
CHAT="$RUN_DIR/chat.jsonl"
CONFIG="$PROVIDER_DIR/codex-dm.toml"
PROMPT_FILE="$PROVIDER_DIR/dm-prompt.md"
STDOUT_LOG="$PROVIDER_DIR/codex-dm.stdout.jsonl"
STDERR_LOG="$PROVIDER_DIR/codex-dm.stderr.log"
LAST_MESSAGE="$PROVIDER_DIR/codex-dm.last.txt"
VIEWER_LOG="$RUN_DIR/viewer.log"
VIEWER_URL="http://127.0.0.1:$CLAWDND_PLAY_PORT/openworlds/"

mkdir -p "$PROVIDER_DIR"
touch "$MOVES" "$CHAT"

python3 - "$ROOT" "$RUN_DIR" "$CONFIG" <<'PY'
import json
import sys
from pathlib import Path

root, state_dir, out = sys.argv[1:]
servers = [
    (
        "clawdnd-engine",
        f"{root}/servers/engine",
        "server.py",
        {"CLAWDND_STATE_DIR": state_dir},
    ),
    (
        "clawdnd-rules",
        f"{root}/servers/rules",
        "server.py",
        {"CLAWDND_RULES_OFFLINE": "1"},
    ),
    (
        "clawdnd-voice",
        f"{root}/servers/voice",
        "server.py",
        {"CLAWDND_TTS_BACKEND": "null"},
    ),
]
lines = []
for name, directory, script, env in servers:
    lines.extend(
        [
            f"[mcp_servers.{name}]",
            'command = "uv"',
            "args = " + json.dumps(["run", "--directory", directory, "python", script]),
            "env = " + "{"
            + ", ".join(f"{key} = {json.dumps(value)}" for key, value in env.items())
            + "}",
            "required = true",
            'default_tools_approval_mode = "approve"',
            "",
        ]
    )
Path(out).write_text("\n".join(lines), encoding="utf-8")
PY

summary() {
  python3 - "$MODE" "$ROOT" "$STATE_ROOT" "$CLAWDND_WORLD" "$CLAWDND_RUN_ID" "$CLAWDND_PLAY_PORT" "$CONFIG" "$MOVES" "$CHAT" "$VIEWER_URL" "${CLAWDND_PLAY_HERO:-}" <<'PY'
import json
import sys
from pathlib import Path

mode, root, state_root, world, run_id, port, config, moves, chat, viewer_url, hero_raw = sys.argv[1:]
hero = {}
if hero_raw.strip():
    try:
        parsed = json.loads(hero_raw)
        if isinstance(parsed, dict):
            hero = parsed
        else:
            hero = {"raw": hero_raw}
    except json.JSONDecodeError:
        hero = {"raw": hero_raw}
print(json.dumps({
    "ok": True,
    "mode": mode,
    "provider": "codex",
    "role": "dm",
    "repo": root,
    "state_root": state_root,
    "world": world,
    "run_id": run_id,
    "port": int(port),
    "viewer_url": viewer_url,
    "config": str(Path(config).resolve(strict=False)),
    "moves": str(Path(moves).resolve(strict=False)),
    "chat": str(Path(chat).resolve(strict=False)),
    "hero": hero,
}, indent=2, sort_keys=True))
PY
}

discover_active_campaign_id() {
  python3 - "$RUN_DIR" "${HERO_CAMP:-}" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
preferred = sys.argv[2].strip()
if preferred:
    print(preferred)
    raise SystemExit(0)

candidates = []
for snap in (run_dir / "campaigns").glob("*/snapshot.json"):
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
    except Exception:
        continue
    cid = str(data.get("id") or snap.parent.name).strip()
    if not cid:
        continue
    chars = data.get("characters") or {}
    has_player = any(isinstance(ch, dict) and ch.get("kind") == "player" for ch in chars.values())
    active = bool(data.get("active_session_id"))
    try:
        mtime = snap.stat().st_mtime
    except OSError:
        mtime = 0
    candidates.append((active, has_player, mtime, cid))

if candidates:
    candidates.sort()
    print(candidates[-1][3])
PY
}

campaign_tool_hint() {
  local cid="${1:-}"
  if [ -n "${cid//[[:space:]]/}" ]; then
    printf 'Live campaign_id: "%s". Call scene_context("%s") first; do not discover campaign state with shell commands, rg, find, or filesystem reads.\n' "$cid" "$cid"
  else
    printf 'If the live campaign_id is unknown, call list_campaigns once, then scene_context for the active campaign. Do not discover campaign state with shell commands, rg, find, or filesystem reads.\n'
  fi
}

echo "[codex-dm-provider] run=$CLAWDND_RUN_ID world=$CLAWDND_WORLD port=$CLAWDND_PLAY_PORT mode=$MODE"
echo "[codex-dm-provider] config=$CONFIG"
echo "[codex-dm-provider] moves=$MOVES"
echo "[codex-dm-provider] chat=$CHAT"

if [ "$MODE" != "run" ]; then
  summary
  exit 0
fi

CODEX_MODEL="${CLAWDND_CODEX_MODEL:-}"
MODEL_ARGS=()
if [ -n "${CODEX_MODEL//[[:space:]]/}" ]; then
  MODEL_ARGS=(--model "$CODEX_MODEL")
fi
export CLAWDND_STATE_DIR="$RUN_DIR"
export WORLDOS_STATE_DIR="$RUN_DIR"
export CLAWDND_RULES_OFFLINE=1
export CLAWDND_TTS_BACKEND=null

HERO_CAMP=""
HERO_PC_ID=""
HERO_PC_NAME=""
HERO_PC_RACE=""
HERO_PC_CLASS=""
if [ -n "${CLAWDND_PLAY_HERO:-}" ]; then
  HERO_SEED_JSON="$(CLAWDND_STATE_DIR="$RUN_DIR" WORLDOS_STATE_DIR="$RUN_DIR" uv run --directory "$ROOT/servers/engine" python - "$CLAWDND_WORLD" "$CLAWDND_PLAY_HERO" <<'PY'
import json
import sys

import server

world, spec_raw = sys.argv[1], sys.argv[2]
try:
    spec = json.loads(spec_raw)
except json.JSONDecodeError as exc:
    sys.stderr.write(f"native hero spec is not valid JSON: {exc}\n")
    sys.exit(1)
if not isinstance(spec, dict):
    sys.stderr.write("native hero spec must be a JSON object\n")
    sys.exit(1)
if not spec.get("canon"):
    sys.stderr.write("Codex DM provider currently supports native roster canon hero specs only\n")
    sys.exit(1)
canon_name = str(spec.get("name") or "").strip()
if not canon_name:
    sys.stderr.write("native roster canon hero spec is missing name\n")
    sys.exit(1)

started = server.start_world(world)
camp = started.get("campaign_id") if isinstance(started, dict) else ""
if not camp:
    sys.stderr.write("start_world did not return a campaign_id\n")
    sys.exit(1)
server.start_session(camp, title=f"Codex DM: {canon_name}")
rec = server.load_canon_character(camp, canon_name, kind="player", add_to_party=True)
if not isinstance(rec, dict) or rec.get("error"):
    sys.stderr.write("canon pickup failed: " + str((rec or {}).get("error") if isinstance(rec, dict) else rec) + "\n")
    sys.exit(1)

print(json.dumps({
    "campaign_id": camp,
    "pc": {
        "id": rec.get("id") or "",
        "name": rec.get("name") or canon_name,
        "race": str(rec.get("race") or ""),
        "class": str(rec.get("class") or ""),
    },
}))
PY
)" || fail "native-selected hero pre-seed failed"
  HERO_CAMP="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.campaign_id // ""')"
  HERO_PC_ID="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.id // ""')"
  HERO_PC_NAME="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.name // ""')"
  HERO_PC_RACE="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.race // ""')"
  HERO_PC_CLASS="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.class // ""')"
  [ -n "$HERO_CAMP" ] || fail "native-selected hero pre-seed returned no campaign"
  echo "[codex-dm-provider] seeded native-selected hero: $HERO_PC_NAME ($HERO_PC_RACE $HERO_PC_CLASS) in campaign $HERO_CAMP"
fi

chatlog() {
  python3 - "$CHAT" "$1" "$2" <<'PY'
import json
import sys

path, role, text = sys.argv[1:]
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"role": role, "text": text}) + "\n")
PY
}

codex_dm_turn() {
  local prompt="$1"
  printf '%s\n' "$prompt" > "$PROMPT_FILE"
  : > "$LAST_MESSAGE"
  local status=0
  codex exec \
    --ignore-user-config \
    --ignore-rules \
    --sandbox read-only \
    --json \
    ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --cd "$ROOT" \
    --output-last-message "$LAST_MESSAGE" \
    -c "mcp_servers.clawdnd-engine.command=\"uv\"" \
    -c "mcp_servers.clawdnd-engine.args=[\"run\",\"--directory\",\"$ROOT/servers/engine\",\"python\",\"server.py\"]" \
    -c "mcp_servers.clawdnd-engine.env_vars=[\"CLAWDND_STATE_DIR\"]" \
    -c "mcp_servers.clawdnd-engine.required=true" \
    -c "mcp_servers.clawdnd-engine.default_tools_approval_mode=\"approve\"" \
    -c "mcp_servers.clawdnd-rules.command=\"uv\"" \
    -c "mcp_servers.clawdnd-rules.args=[\"run\",\"--directory\",\"$ROOT/servers/rules\",\"python\",\"server.py\"]" \
    -c "mcp_servers.clawdnd-rules.env_vars=[\"CLAWDND_RULES_OFFLINE\"]" \
    -c "mcp_servers.clawdnd-rules.required=true" \
    -c "mcp_servers.clawdnd-rules.default_tools_approval_mode=\"approve\"" \
    -c "mcp_servers.clawdnd-voice.command=\"uv\"" \
    -c "mcp_servers.clawdnd-voice.args=[\"run\",\"--directory\",\"$ROOT/servers/voice\",\"python\",\"server.py\"]" \
    -c "mcp_servers.clawdnd-voice.env_vars=[\"CLAWDND_TTS_BACKEND\"]" \
    -c "mcp_servers.clawdnd-voice.required=true" \
    -c "mcp_servers.clawdnd-voice.default_tools_approval_mode=\"approve\"" \
    - < "$PROMPT_FILE" \
    > >(tee -a "$STDOUT_LOG" >/dev/null) \
    2> >(tee -a "$STDERR_LOG" >&2) || status=$?
  [ "$status" -eq 0 ] || return "$status"

  local last
  last="$(cat "$LAST_MESSAGE" 2>/dev/null || true)"
  [ -n "${last//[[:space:]]/}" ] || return 3
  printf '%s' "$last"
}

LOG_EVENT_TOOL_RULE="Tool argument rule: for log_event narration, omit the speaker argument entirely. For dialogue, pass a real non-empty character id or name. Never pass JSON null for speaker or any optional string field."
STATE_DISCOVERY_RULE="State discovery rule: after reading skills/dungeon-master/SKILL.md, use clawdnd-engine/clawdnd-rules MCP tools for live game state. Do not use shell commands, rg, find, or filesystem reads to discover campaign state."

VPID_FILE="$RUN_DIR/.viewer.pid"
viewer_supervisor() {
  while :; do
    WORLDOS_STATE_DIR="$RUN_DIR" CLAWDND_STATE_DIR="$RUN_DIR" \
    WORLDOS_VIEWER_CHAT="$CHAT" CLAWDND_VIEWER_CHAT="$CHAT" \
    WORLDOS_PLAYER_MOVES="$MOVES" CLAWDND_PLAYER_MOVES="$MOVES" \
      python3 viewer/server.py "" "$CLAWDND_PLAY_PORT" >> "$VIEWER_LOG" 2>&1 &
    local vp=$!
    echo "$vp" > "$VPID_FILE"
    wait "$vp" 2>/dev/null
    sleep 1
  done
}
viewer_supervisor & SUP=$!
_cleanup() {
  kill "$SUP" 2>/dev/null || true
  [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null || true
}
trap _cleanup EXIT
trap '_cleanup; exit 130' INT TERM

echo "WorldOS Codex DM provider -> $VIEWER_URL"
echo "  Save dir: $RUN_DIR"

MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"
MCURSOR="${MCURSOR:-0}"

if [ -n "$HERO_CAMP" ]; then
  OPENING_PROMPT="$(cat <<EOF
You are the Dungeon Master for a solo WorldOS / ClawDnD adventure in world "$CLAWDND_WORLD".

Before acting, read skills/dungeon-master/SKILL.md and follow its live-world contract. Use the clawdnd-engine tools as the sole writer of game state, clawdnd-rules for rules grounding, and clawdnd-voice only if needed with the null backend.

$LOG_EVENT_TOOL_RULE
$STATE_DISCOVERY_RULE

Native-selected canon hero already seated:
- campaign_id: "$HERO_CAMP"
- player: "$HERO_PC_NAME" ($HERO_PC_RACE $HERO_PC_CLASS), id "$HERO_PC_ID"

Start from that already-seated player:
- call get_state("$HERO_CAMP") FIRST to read the campaign;
- do NOT call start_world, start_session, or load_canon_character for the player;
- open a 2nd-person, player-facing scene centered on "$HERO_PC_NAME" with real sensory details, at least one quoted NPC line, and a clear open moment.

Your final reply must be non-empty opening narration for the player, not a setup note and not a tool-only ending.
EOF
)"
else
  OPENING_PROMPT="$(cat <<EOF
You are the Dungeon Master for a solo WorldOS / ClawDnD adventure in world "$CLAWDND_WORLD".

Before acting, read skills/dungeon-master/SKILL.md and follow its live-world contract. Use the clawdnd-engine tools as the sole writer of game state, clawdnd-rules for rules grounding, and clawdnd-voice only if needed with the null backend.

$LOG_EVENT_TOOL_RULE
$STATE_DISCOVERY_RULE

Start a live solo session:
- call start_world("$CLAWDND_WORLD");
- call start_session for the campaign;
- choose a playable canon character via list_canon_characters(playable_only=true) and load_canon_character(..., kind="player", add_to_party=true);
- open a 2nd-person, player-facing scene with real sensory details, at least one quoted NPC line, and a clear open moment.

Your final reply must be non-empty opening narration for the player, not a setup note and not a tool-only ending.
EOF
)"
fi

if ! OPENING="$(codex_dm_turn "$OPENING_PROMPT")"; then
  fail "Codex DM opening turn failed; see $STDERR_LOG"
fi
chatlog dm "$OPENING"

ACTIVE_CAMPAIGN_ID="$(discover_active_campaign_id)"
CAMPAIGN_TOOL_HINT="$(campaign_tool_hint "$ACTIVE_CAMPAIGN_ID")"

DM_TURNS=1
while true; do
  [ "$DM_TURNS" -ge "$CLAWDND_PLAY_MAX_TURNS" ] && break
  total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"
  total="${total:-0}"
  if [ "$total" -gt "$MCURSOR" ]; then
    new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"
    MCURSOR="$total"
    PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
    [ -z "$PMSG" ] && continue
    chatlog player "$PMSG"
    ACTIVE_CAMPAIGN_ID="$(discover_active_campaign_id)"
    CAMPAIGN_TOOL_HINT="$(campaign_tool_hint "$ACTIVE_CAMPAIGN_ID")"
    if ! REPLY="$(codex_dm_turn "You are the Dungeon Master mid-session. Re-ground from the engine state first, then resolve this player move through the engine/rules tools and reply with 2nd-person player-facing narration.

$LOG_EVENT_TOOL_RULE
$STATE_DISCOVERY_RULE
$CAMPAIGN_TOOL_HINT

Player move:
$PMSG")"; then
      fail "Codex DM move turn failed; see $STDERR_LOG"
    fi
    chatlog dm "$REPLY"
    DM_TURNS=$((DM_TURNS + 1))
  else
    sleep 2
  fi
done
