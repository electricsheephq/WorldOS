#!/usr/bin/env bash
# Deterministic built-app smoke provider.
#
# This is a dev/test-only provider path for proving app/viewer wiring without
# Claude, Codex, OpenClaw, network calls, or model auth. It still seeds campaign
# state through the engine's Python API; the viewer remains a reader plus /move.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "--dry-run" ]; then
  printf 'scripted provider dry-run: requires no Claude, Codex, or OpenClaw\n'
  printf 'gate=%s world=%s run=%s port=%s\n' \
    "${WORLDOS_ENABLE_SCRIPTED_PROVIDER:-0}" \
    "${CLAWDND_WORLD:-baldurs-gate}" \
    "${CLAWDND_RUN_ID:-scripted-smoke}" \
    "${CLAWDND_PLAY_PORT:-8765}"
  exit 0
fi

if [ "${WORLDOS_ENABLE_SCRIPTED_PROVIDER:-0}" != "1" ]; then
  printf '[scripted-provider] WORLDOS_ENABLE_SCRIPTED_PROVIDER=1 is required.\n' >&2
  exit 64
fi

WORLD="${CLAWDND_WORLD:-baldurs-gate}"
RUN="${CLAWDND_RUN_ID:-scripted-smoke-$(date +%H%M%S)}"
PORT="${CLAWDND_PLAY_PORT:-8765}"
STATE_DIR="$ROOT/play-state/$RUN"
TRACE_DIR="$STATE_DIR/scripted-provider"
MOVES="$STATE_DIR/player_moves.jsonl"
CHAT="$STATE_DIR/chat.jsonl"
VIEWER_LOG="$STATE_DIR/viewer.log"
TRACE="$TRACE_DIR/trace.ndjson"
VPID_FILE="$STATE_DIR/.viewer.pid"

mkdir -p "$TRACE_DIR"
: > "$MOVES"
: > "$CHAT"
: > "$TRACE"

json_append() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys, time
path, role, text = sys.argv[1:4]
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"role": role, "text": text, "at": time.time()}) + "\n")
PY
}

trace() {
  python3 - "$TRACE" "$1" "$2" <<'PY'
import json, sys, time
path, event, detail = sys.argv[1:4]
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"event": event, "detail": detail, "at": time.time()}) + "\n")
PY
}

BOOTSTRAP_JSON="$(
  CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$WORLD" <<'PY'
import json, sys
import server

world = sys.argv[1]
campaign_id = server.start_world(world)["campaign_id"]
server.start_session(campaign_id, title="Scripted smoke")
available = server.list_canon_characters(campaign_id, playable_only=True).get("available", [])
name = (available[0] or {}).get("name") if available else "Charming Latham"
pc = server.load_canon_character(campaign_id, name, kind="player", add_to_party=True)
if not isinstance(pc, dict) or pc.get("error"):
    pc = server.load_canon_character(campaign_id, "Charming Latham", kind="player", add_to_party=True)
if not isinstance(pc, dict) or pc.get("error"):
    raise SystemExit("could not seat a scripted player")
opening = (
    f"You are {pc.get('name') or name}, standing under a steady lantern at the edge of the road. "
    "The smoke provider is awake, the table is listening, and the next move is yours."
)
server.log_event(campaign_id, "narration", opening)
print(json.dumps({"campaign_id": campaign_id, "player": pc, "opening": opening}))
PY
)"

CAMPAIGN_ID="$(python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["campaign_id"])' <<<"$BOOTSTRAP_JSON")"
OPENING="$(python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["opening"])' <<<"$BOOTSTRAP_JSON")"
PLAYER_NAME="$(python3 -c 'import json,sys;print((json.loads(sys.stdin.read())["player"].get("name") or "Hero"))' <<<"$BOOTSTRAP_JSON")"
json_append "$CHAT" "dm" "$OPENING"
trace "bootstrap" "campaign=$CAMPAIGN_ID player=$PLAYER_NAME"

viewer_supervisor() {
  while :; do
    WORLDOS_STATE_DIR="$STATE_DIR" CLAWDND_STATE_DIR="$STATE_DIR" \
    WORLDOS_VIEWER_CHAT="$CHAT" CLAWDND_VIEWER_CHAT="$CHAT" \
    WORLDOS_PLAYER_MOVES="$MOVES" CLAWDND_PLAYER_MOVES="$MOVES" \
      python3 viewer/server.py "" "$PORT" >> "$VIEWER_LOG" 2>&1 &
    local vp=$!
    echo "$vp" > "$VPID_FILE"
    wait "$vp" 2>/dev/null || true
    sleep 1
  done
}

viewer_supervisor &
SUP=$!
cleanup() {
  kill "$SUP" 2>/dev/null || true
  [ -f "$VPID_FILE" ] && kill "$(cat "$VPID_FILE" 2>/dev/null)" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

trace "viewer_started" "port=$PORT"
processed=0

while :; do
  count="$(grep -c . "$MOVES" 2>/dev/null || true)"
  if [ "${count:-0}" -gt "$processed" ]; then
    line="$(tail -n 1 "$MOVES")"
    reply="$(
      CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$CAMPAIGN_ID" "$line" <<'PY'
import json, sys
import server

campaign_id, raw = sys.argv[1], sys.argv[2]
try:
    move = json.loads(raw)
except json.JSONDecodeError:
    move = {"text": raw}
text = str(move.get("text") or move.get("label") or move.get("name") or "continue").strip()
reply = (
    f"The table accepts your move: {text}. "
    "The lantern brightens once, confirming the scripted smoke loop handled /move deterministically."
)
server.log_event(campaign_id, "narration", reply)
print(reply)
PY
)"
    json_append "$CHAT" "player" "$line"
    json_append "$CHAT" "dm" "$reply"
    trace "move_resolved" "$line"
    processed="$count"
  fi
  sleep 1
done
