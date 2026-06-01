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
for cmd in python3 uv; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf '[scripted-provider] %s is required.\n' "$cmd" >&2
    exit 127
  fi
done

WORLD="${CLAWDND_WORLD:-baldurs-gate}"
RUN="${CLAWDND_RUN_ID:-scripted-smoke-$(date +%H%M%S)}"
PORT="${CLAWDND_PLAY_PORT:-8765}"
STATE_DIR="$ROOT/play-state/$RUN"
TRACE_DIR="$STATE_DIR/scripted-provider"
MOVES="$STATE_DIR/player_moves.jsonl"
CHAT="$STATE_DIR/chat.jsonl"
VIEWER_LOG="$STATE_DIR/viewer.log"
TRACE="$TRACE_DIR/trace.ndjson"
SUMMARY="$TRACE_DIR/summary.json"
VPID_FILE="$STATE_DIR/.viewer.pid"

mkdir -p "$TRACE_DIR"
: > "$MOVES"
: > "$CHAT"
: > "$TRACE"

json_append() {
  python3 - "$1" "$2" "$3" "${4:-}" <<'PY'
import json, sys, time
path, role, text, extra_raw = sys.argv[1:5]
extra = {}
if extra_raw.strip():
    try:
        parsed = json.loads(extra_raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid chat extra_json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("chat extra_json must be an object")
    extra = parsed
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"role": role, "text": text, "at": time.time(), **extra}) + "\n")
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

trace_json() {
  python3 - "$TRACE" "$1" "$2" "$3" <<'PY'
import json, sys, time
path, event, beat, detail = sys.argv[1:5]
try:
    parsed_detail = json.loads(detail)
except json.JSONDecodeError:
    parsed_detail = detail
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "event": event,
        "beat": int(beat),
        "detail": parsed_detail,
        "at": time.time(),
    }) + "\n")
PY
}

write_summary() {
  python3 - "$SUMMARY" "$CAMPAIGN_ID" "$PLAYER_NAME" "$WORLD" "$RUN" "$PORT" "$processed" <<'PY'
import json, sys, time
path, campaign_id, player_name, world, run_id, port, processed = sys.argv[1:8]
payload = {
    "schema": "worldos.scripted-provider-summary.v1",
    "provider": "scripted",
    "deterministic": True,
    "model_free": True,
    "world": world,
    "run_id": run_id,
    "campaign_id": campaign_id,
    "player": player_name,
    "port": int(port),
    "resolved_move_count": int(processed),
    "updated_at": time.time(),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

move_chat_text() {
  python3 - "$1" <<'PY'
import json, sys
raw = sys.argv[1]
try:
    move = json.loads(raw)
except json.JSONDecodeError:
    print(raw)
    raise SystemExit
kind = str(move.get("kind") or "do").strip()
text = str(move.get("text") or move.get("label") or move.get("name") or "continue").strip()
print(f"[{kind}] {text}" if kind and text else text or raw)
PY
}

BOOTSTRAP_JSON="$(
  CLAWDND_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$WORLD" "${CLAWDND_PLAY_HERO:-}" <<'PY'
import json, sys
import server

world, hero_raw = sys.argv[1], sys.argv[2]
campaign_id = server.start_world(world)["campaign_id"]
server.start_session(campaign_id, title="Scripted smoke")

def default_canon_name() -> str:
    available = server.list_canon_characters(campaign_id, playable_only=True).get("available", [])
    return ((available[0] or {}).get("name") if available else "") or "Charming Latham"

spec = {}
if hero_raw.strip():
    try:
        parsed = json.loads(hero_raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"native hero spec is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("native hero spec must be a JSON object")
    spec = parsed

if spec.get("canon"):
    name = str(spec.get("name") or "").strip()
    if not name:
        raise SystemExit("native roster canon hero spec is missing name")
    pc = server.load_canon_character(campaign_id, name, kind="player", add_to_party=True)
elif spec:
    try:
        level = int(spec.get("level", 1) or 1)
    except (TypeError, ValueError):
        level = 1
    pc = server.create_character(
        campaign_id,
        spec.get("name") or "Unnamed Hero",
        kind="player",
        race=spec.get("race", "") or "",
        class_name=spec.get("class", "") or "",
        level=level,
        abilities=spec.get("abilities") or None,
        background=spec.get("background", "") or "",
        skills=spec.get("skills") or None,
        apply_srd_defaults=True,
    )
else:
    name = default_canon_name()
    pc = server.load_canon_character(campaign_id, name, kind="player", add_to_party=True)
    if not isinstance(pc, dict) or pc.get("error"):
        pc = server.load_canon_character(campaign_id, "Charming Latham", kind="player", add_to_party=True)
if not isinstance(pc, dict) or pc.get("error"):
    raise SystemExit("could not seat a scripted player")
opening = (
    f"You are {pc.get('name') or spec.get('name') or 'Hero'}, standing under a steady lantern at the edge of the road. "
    "The smoke provider is awake, the table is listening, and the next move is yours."
)
server.log_event(campaign_id, "narration", opening)
print(json.dumps({"campaign_id": campaign_id, "player": pc, "opening": opening}))
PY
)"

CAMPAIGN_ID="$(python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["campaign_id"])' <<<"$BOOTSTRAP_JSON")"
OPENING="$(python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["opening"])' <<<"$BOOTSTRAP_JSON")"
PLAYER_NAME="$(python3 -c 'import json,sys;print((json.loads(sys.stdin.read())["player"].get("name") or "Hero"))' <<<"$BOOTSTRAP_JSON")"
json_append "$CHAT" "dm" "$OPENING" '{"engine_logged":true}'
trace "bootstrap" "campaign=$CAMPAIGN_ID player=$PLAYER_NAME"
processed=0
write_summary

viewer_supervisor() {
  while :; do
    WORLDOS_STATE_DIR="$STATE_DIR" CLAWDND_STATE_DIR="$STATE_DIR" \
    WORLDOS_VIEWER_CHAT="$CHAT" CLAWDND_VIEWER_CHAT="$CHAT" \
    WORLDOS_PLAYER_MOVES="$MOVES" CLAWDND_PLAYER_MOVES="$MOVES" \
    WORLDOS_PROVIDER=scripted CLAWDND_PROVIDER=scripted \
    WORLDOS_BROWSER_CONSOLE_LOG="${WORLDOS_BROWSER_CONSOLE_LOG:-}" \
    WORLDOS_BROWSER_NETWORK_LOG="${WORLDOS_BROWSER_NETWORK_LOG:-}" \
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

while :; do
  count="$(grep -c . "$MOVES" 2>/dev/null || true)"
  if [ "${count:-0}" -gt "$processed" ]; then
    while IFS= read -r line; do
      [ -n "$line" ] || continue
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
      beat=$((processed + 1))
      json_append "$CHAT" "player" "$(move_chat_text "$line")"
      json_append "$CHAT" "dm" "$reply" '{"engine_logged":true}'
      trace_json "move_resolved" "$beat" "$line"
      processed=$((processed + 1))
      write_summary
    done < <(sed -n "$((processed + 1)),${count}p" "$MOVES")
    processed="$count"
  fi
  sleep 1
done
