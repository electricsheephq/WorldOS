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
  --seed-smoke) MODE="seed-smoke"; shift ;;
  -h|--help)
    cat <<'EOF'
Usage: scripts/play_codex_dm.sh [--dry-run|--smoke|--seed-smoke]

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
  CLAWDND_PLAY_CANON_HERO (canon name, or origin spec such as template:rolan-evoker)
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
CLAWDND_LEAN_BEATS="${CLAWDND_LEAN_BEATS:-1}"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
[[ "$CLAWDND_LEAN_BEATS" =~ ^[01]$ ]] || fail "CLAWDND_LEAN_BEATS must be 0 or 1"
if [ "$CLAWDND_LEAN_BEATS" = "1" ]; then
  [[ "$CLAWDND_LEAN_TAIL" =~ ^[0-9]+$ ]] || fail "CLAWDND_LEAN_TAIL must be an integer when CLAWDND_LEAN_BEATS=1"
  [ "$CLAWDND_LEAN_TAIL" -ge 1 ] || fail "CLAWDND_LEAN_TAIL must be >= 1 when CLAWDND_LEAN_BEATS=1"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v uv >/dev/null 2>&1 || fail "uv is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"
if [ "$MODE" = "run" ]; then
  command -v codex >/dev/null 2>&1 || fail "codex CLI is required for real provider runs"
fi

if [ "$MODE" = "smoke" ] || [ "$MODE" = "seed-smoke" ]; then
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
PROVIDER_STATUS="$RUN_DIR/provider_status.json"

mkdir -p "$PROVIDER_DIR"
touch "$MOVES" "$CHAT"
exec > >(tee -a "$PROVIDER_DIR/provider-wrapper.stdout.log") \
  2> >(tee -a "$PROVIDER_DIR/provider-wrapper.stderr.log" >&2)

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
  python3 - "$MODE" "$ROOT" "$STATE_ROOT" "$CLAWDND_WORLD" "$CLAWDND_RUN_ID" "$CLAWDND_PLAY_PORT" "$CONFIG" "$MOVES" "$CHAT" "$VIEWER_URL" "${CLAWDND_PLAY_HERO:-}" "${CLAWDND_PLAY_CANON_HERO:-}" "$CODEX_MODEL" "$CLAWDND_LEAN_BEATS" "$CLAWDND_LEAN_TAIL" "$CLAWDND_PLAY_MAX_TURNS" "$GIT_SHA" <<'PY'
import json
import sys
from pathlib import Path

(
    mode, root, state_root, world, run_id, port, config, moves, chat, viewer_url,
    hero_raw, fallback_hero, model, lean_beats, lean_tail, max_turns, sha,
) = sys.argv[1:]
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
elif fallback_hero.strip():
    fallback = fallback_hero.strip()
    if fallback.startswith(("template:", "pickup:")) or fallback in {"nobody_l1", "veteran_l5"}:
        hero = {"origin": fallback}
    else:
        hero = {"canon": True, "name": fallback}
print(json.dumps({
    "ok": True,
    "mode": mode,
    "provider": "codex",
    "provider_family": "codex-openai",
    "auth_surface": "codex-cli",
    "role": "dm",
    "repo": root,
    "state_root": state_root,
    "world": world,
    "run_id": run_id,
    "port": int(port),
    "viewer_url": viewer_url,
    "model": model,
    "wrapper": "scripts/play_codex_dm.sh",
    "sha": sha,
    "lean_beats": lean_beats == "1",
    "lean_tail": int(lean_tail) if str(lean_tail).isdigit() else lean_tail,
    "turn_cap": int(max_turns) if str(max_turns).isdigit() else max_turns,
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
    if [ "${CLAWDND_LEAN_BEATS:-1}" = "1" ]; then
      printf 'Live campaign_id: "%s". Call scene_context(campaign_id="%s", recent_narration=%s) first; do not discover campaign state with shell commands, rg, find, or filesystem reads.\n' "$cid" "$cid" "$CLAWDND_LEAN_TAIL"
    else
      printf 'Live campaign_id: "%s". Call scene_context(campaign_id="%s") first; do not discover campaign state with shell commands, rg, find, or filesystem reads.\n' "$cid" "$cid"
    fi
  else
    printf 'If the live campaign_id is unknown, call list_campaigns once, then scene_context for the active campaign. Do not discover campaign state with shell commands, rg, find, or filesystem reads.\n'
  fi
}

codex_lean_reground_rule() {
  local cid="${1:-}"
  if [ "${CLAWDND_LEAN_BEATS:-1}" != "1" ]; then
    printf 'Codex lean re-ground rule: lean mode is disabled for this run, but still use clawdnd-engine state as truth and do not infer campaign state from transcript memory.\n'
    return 0
  fi
  if [ -n "${cid//[[:space:]]/}" ]; then
    printf 'Codex lean re-ground rule: each Codex provider turn is a fresh invocation. Your FIRST clawdnd-engine call after this prompt MUST be scene_context(campaign_id="%s", recent_narration=%s); resolve the player move from that live state plus rules tools, not from replaying chat history or reading files.\n' "$cid" "$CLAWDND_LEAN_TAIL"
  else
    printf 'Codex lean re-ground rule: each Codex provider turn is a fresh invocation. Your FIRST clawdnd-engine calls MUST discover the active campaign with list_campaigns once, then scene_context(recent_narration=%s); resolve the player move from live state plus rules tools, not from replaying chat history or reading files.\n' "$CLAWDND_LEAN_TAIL"
  fi
}

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
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"

echo "[codex-dm-provider] run=$CLAWDND_RUN_ID world=$CLAWDND_WORLD port=$CLAWDND_PLAY_PORT mode=$MODE"
echo "[codex-dm-provider] config=$CONFIG"
echo "[codex-dm-provider] moves=$MOVES"
echo "[codex-dm-provider] chat=$CHAT"
echo "[codex-dm-provider] lean_beats=$CLAWDND_LEAN_BEATS recent_narration=$CLAWDND_LEAN_TAIL"

if [ "$MODE" = "dry-run" ] || [ "$MODE" = "smoke" ]; then
  summary
  exit 0
fi
validate_codex_service_tier

export CLAWDND_STATE_DIR="$RUN_DIR"
export WORLDOS_STATE_DIR="$RUN_DIR"
export CLAWDND_RULES_OFFLINE=1
export CLAWDND_TTS_BACKEND=null

HERO_CAMP=""
HERO_PC_ID=""
HERO_PC_NAME=""
HERO_PC_RACE=""
HERO_PC_CLASS=""
HERO_PC_SUBCLASS=""
HERO_PC_LEVEL=""
HERO_PC_SPELLS=""
HERO_SEED_JSON="$(CLAWDND_STATE_DIR="$RUN_DIR" WORLDOS_STATE_DIR="$RUN_DIR" uv run --directory "$ROOT/servers/engine" python - "$CLAWDND_WORLD" "${CLAWDND_PLAY_HERO:-}" "${CLAWDND_PLAY_CANON_HERO:-Alfira}" <<'PY'
import json
import sys

import server

world, spec_raw, fallback_name = sys.argv[1], sys.argv[2], sys.argv[3]
explicit = False
canon_name = ""
origin_spec = ""
name_override = ""
if spec_raw.strip():
    explicit = True
    try:
        spec = json.loads(spec_raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"native hero spec is not valid JSON: {exc}\n")
        sys.exit(1)
    if not isinstance(spec, dict):
        sys.stderr.write("native hero spec must be a JSON object\n")
        sys.exit(1)
    origin_raw = str(spec.get("origin") or spec.get("template") or "").strip()
    if origin_raw:
        if spec.get("template") and ":" not in origin_raw:
            origin_raw = f"template:{origin_raw}"
        origin_spec = origin_raw
        name_override = str(spec.get("name") or "").strip()
    elif spec.get("canon"):
        canon_name = str(spec.get("name") or "").strip()
        if not canon_name:
            sys.stderr.write("native roster canon hero spec is missing name\n")
            sys.exit(1)
    else:
        sys.stderr.write("Codex DM provider hero spec must include canon=true or origin/template\n")
        sys.exit(1)
else:
    fallback = fallback_name.strip() or "Alfira"
    if fallback.startswith(("template:", "pickup:")) or fallback in {"nobody_l1", "veteran_l5"}:
        origin_spec = fallback
    else:
        canon_name = fallback

started = server.start_world(world)
camp = started.get("campaign_id") if isinstance(started, dict) else ""
if not camp:
    sys.stderr.write("start_world did not return a campaign_id\n")
    sys.exit(1)
title_name = origin_spec or canon_name
server.start_session(camp, title=f"Codex DM: {title_name}")

rec = {}
seed_source = "canon"
errors = []
if origin_spec:
    seed_source = "origin"
    try:
        candidate = server.start_character(camp, origin=origin_spec, name=name_override)
    except Exception as exc:
        sys.stderr.write(f"origin pickup failed for {origin_spec!r}: {exc}\n")
        sys.exit(1)
    if isinstance(candidate, dict) and not candidate.get("error") and candidate.get("id"):
        rec = candidate
    else:
        sys.stderr.write(f"origin pickup failed for {origin_spec!r}: {candidate}\n")
        sys.exit(1)
else:
    names = [canon_name]
    if not explicit:
        try:
            roster = server.list_canon_characters(camp, playable_only=True).get("available") or []
        except Exception:
            roster = []
        for row in roster:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if name and name not in names:
                names.append(name)

    for name in names:
        try:
            candidate = server.load_canon_character(camp, name, kind="player", add_to_party=True)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if isinstance(candidate, dict) and not candidate.get("error") and candidate.get("id"):
            rec = candidate
            canon_name = name
            break
        errors.append(f"{name}: {(candidate or {}).get('error') if isinstance(candidate, dict) else candidate}")

    if not isinstance(rec, dict) or rec.get("error"):
        sys.stderr.write("canon pickup failed: " + "; ".join(errors[:5]) + "\n")
        sys.exit(1)

pc_id = str(rec.get("id") or "")
pc_full = {}
if pc_id:
    try:
        pc_full = server.get_character(camp, pc_id)
    except Exception:
        pc_full = {}
    if not isinstance(pc_full, dict) or pc_full.get("error"):
        pc_full = {}
classes = pc_full.get("classes") if isinstance(pc_full, dict) else []
head_class = classes[0] if isinstance(classes, list) and classes and isinstance(classes[0], dict) else {}
spells = []
if isinstance(pc_full, dict):
    seen = set()
    for spell_list in (pc_full.get("spells_known") or [], pc_full.get("spells_prepared") or []):
        for spell in spell_list:
            name = str(spell).strip()
            if name and name.lower() not in seen:
                spells.append(name)
                seen.add(name.lower())

print(json.dumps({
    "campaign_id": camp,
    "seed_source": seed_source,
    "origin": origin_spec,
    "pc": {
        "id": pc_id,
        "name": rec.get("name") or pc_full.get("name") or canon_name or name_override,
        "race": str(rec.get("race") or pc_full.get("race") or ""),
        "class": str(rec.get("class") or head_class.get("name") or ""),
        "level": rec.get("level") or head_class.get("level") or "",
        "subclass": head_class.get("subclass") or "",
        "spells": spells,
    },
}))
PY
)" || fail "solo player pre-seed failed"
HERO_CAMP="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.campaign_id // ""')"
HERO_PC_ID="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.id // ""')"
HERO_PC_NAME="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.name // ""')"
HERO_PC_RACE="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.race // ""')"
HERO_PC_CLASS="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.class // ""')"
HERO_PC_LEVEL="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.level // ""')"
HERO_PC_SUBCLASS="$(printf '%s' "$HERO_SEED_JSON" | jq -r '.pc.subclass // ""')"
HERO_PC_SPELLS="$(printf '%s' "$HERO_SEED_JSON" | jq -r '(.pc.spells // []) | join(", ")')"
[ -n "$HERO_CAMP" ] || fail "solo player pre-seed returned no campaign"
echo "[codex-dm-provider] seeded solo player: $HERO_PC_NAME ($HERO_PC_RACE level $HERO_PC_LEVEL $HERO_PC_CLASS ${HERO_PC_SUBCLASS:+/$HERO_PC_SUBCLASS}) in campaign $HERO_CAMP"

if [ "$MODE" = "seed-smoke" ]; then
  python3 - "$HERO_SEED_JSON" "$GIT_SHA" <<'PY'
import json
import sys

seed_json, sha = sys.argv[1], sys.argv[2]
print(json.dumps({
    "ok": True,
    "mode": "seed-smoke",
    "provider": "codex",
    "role": "dm",
    "sha": sha,
    "seed": json.loads(seed_json),
}, indent=2, sort_keys=True))
PY
  exit 0
fi

chatlog() {
  python3 - "$CHAT" "$1" "$2" "${3:-}" <<'PY'
import json
import sys

path, role, text, extra_json = sys.argv[1:]
row = {"role": role, "text": text}
if extra_json:
    try:
        extra = json.loads(extra_json)
    except ValueError as exc:
        raise SystemExit(f"invalid chatlog extra_json: {exc}") from exc
    if not isinstance(extra, dict):
        raise SystemExit(f"invalid chatlog extra_json: expected object, got {type(extra).__name__}")
    row.update(extra)
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row) + "\n")
PY
}

write_provider_status() {
  local status="$1" reason="$2" detail="$3"
  python3 - "$PROVIDER_STATUS" "$status" "$reason" "$detail" "$CLAWDND_PROVIDER" "$CLAWDND_PLAY_MAX_TURNS" "$DM_TURNS" "$CODEX_MODEL" "${WORLDOS_ACTOR_MODEL:-${CLAWDND_ACTOR_MODEL:-}}" "${WORLDOS_SCORER_MODEL:-${CLAWDND_SCORER_MODEL:-}}" "$CLAWDND_LEAN_BEATS" "$CLAWDND_LEAN_TAIL" "$GIT_SHA" "${HERO_SEED_JSON:-}" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

path, status, reason, detail, provider, max_turns, turns, model, player_model, scorer_model, lean_beats, lean_tail, sha, seed_json = sys.argv[1:]
path = Path(path)
fixture = {}
if seed_json.strip():
    try:
        seed = json.loads(seed_json)
    except json.JSONDecodeError:
        seed = {}
    if isinstance(seed, dict):
        fixture = {
            "seed_source": seed.get("seed_source"),
            "origin": seed.get("origin"),
            "pc": seed.get("pc") if isinstance(seed.get("pc"), dict) else {},
        }
payload = {
    "schema": "worldos.provider-status.v1",
    "provider": provider,
    "provider_family": "codex-openai",
    "auth_surface": "codex-cli",
    "model": model,
    "player_model": player_model,
    "scorer_model": scorer_model,
    "wrapper": "scripts/play_codex_dm.sh",
    "sha": sha,
    "fixture": fixture,
    "lean_beats": lean_beats == "1",
    "lean_tail": int(lean_tail) if str(lean_tail).isdigit() else lean_tail,
    "status": status,
    "reason": reason,
    "detail": detail,
    "max_turns": int(max_turns) if str(max_turns).isdigit() else max_turns,
    "dm_turns": int(turns) if str(turns).isdigit() else turns,
    "updated_at": time.time(),
}
tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
with tmp_path.open("w", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
tmp_path.replace(path)
try:
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
except OSError:
    dir_fd = None
if dir_fd is not None:
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
PY
}

log_engine_narration() {
  local campaign_id="$1" text="$2"
  [ -n "${campaign_id//[[:space:]]/}" ] || return 1
  [ -n "${text//[[:space:]]/}" ] || return 1
  CLAWDND_STATE_DIR="$RUN_DIR" WORLDOS_STATE_DIR="$RUN_DIR" \
    uv run --directory "$ROOT/servers/engine" python - "$campaign_id" "$text" <<'PY'
import sys

import server

campaign_id, text = sys.argv[1], sys.argv[2]
server.log_event(campaign_id, "narration", text)
PY
}

record_dm_reply() {
  local campaign_id="$1" text="$2" phase="$3"
  if log_engine_narration "$campaign_id" "$text"; then
    chatlog dm "$text" '{"engine_logged":true}'
  else
    echo "[codex-dm-provider] warning: could not record ${phase} narration through engine" >&2
    chatlog dm "$text"
  fi
}

codex_dm_turn() {
  local prompt="$1"
  printf '%s\n' "$prompt" > "$PROMPT_FILE"
  : > "$LAST_MESSAGE"
  local status=0
  codex exec \
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
LIVE_PROGRESS_LOG_RULE="Live progress rule: after you know the live campaign and scene, call log_event(kind=\"narration\", text=\"...\") once with a short, non-duplicate, player-facing progress beat before any longer resolution work. This is how /events shows visible story progress while your turn is still running. The progress beat MUST be 2nd-person prose addressed to \"you\" (a vivid one-line teaser of where the player stands or what they sense) — it is rendered STRAIGHT into the player's Chronicle. NEVER log a 3rd-person scene summary, a \"Cold open —\"/\"Scene:\"/\"Setup:\" header, a \"Choice: X or Y\" branch list, bracketed stage directions, or any director/planning note: that scaffolding is your private scratchpad and shatters immersion if it reaches the player. Keep the final reply as the full 2nd-person scene; do not copy this progress beat verbatim, because the wrapper records the final reply through the engine after the turn."
LIVE_DIALOGUE_LOG_RULE="Live dialogue rule: in this Codex app-provider wrapper, do not call log_event(kind=\"dialogue\"). Put quoted NPC speech inside a narration progress beat or your final reply instead; the wrapper records the final reply after the turn, and narration-only live events avoid provider safety cancellation without hiding dialogue from the player."
OPENING_LOG_EVENT_RULE="Opening progress rule: during the opening, after get_state establishes the already-seated player and live scene, write one short sensory progress beat through log_event(kind=\"narration\", text=\"...\") before deeper setup or rules work. Do not log the full opening this way; your final reply must still be non-empty opening narration for the player."
DM_CONTRACT_RULE="Self-contained DM contract: you are already inside the WorldOS Dungeon Master provider. Do not read skill files, ~/.codex skills, repo docs, or use shell commands for instructions during this live app turn. Use clawdnd-engine as the sole writer of campaign state, clawdnd-rules for rules grounding, and clawdnd-voice only if needed with the null backend. Final output must be 2nd-person player-facing narration."
DM_VOICE_RULE="Voice rule: use a warm, fair, generous storyteller voice with Baldur's Gate 3 prestige narration energy; spotlight the player, say yes-and to clever ideas, and never invent dice, rules outcomes, or campaign state that should come from engine/rules tools."
STATE_DISCOVERY_RULE="State discovery rule: use clawdnd-engine/clawdnd-rules MCP tools for live game state. Do not use shell commands, rg, find, or filesystem reads to discover campaign state."
STARTUP_MUTATION_RULE="Startup mutation rule: the wrapper has already seated the one player before you are called. Before the first player-facing narration, do not call start_world, start_session, start_character, load_canon_character, create_character, or recruit_companion. Introduce scene NPCs in narration first; create or load a tracked NPC only after the player engages them."
SOCIAL_CHECK_TARGET_RULE="Social check target rule: call social_check only when scene_context already shows a real tracked npc_id for the target. Do not call load_canon_character or create_character solely to manufacture a social-check target during the same turn. If the target is not already tracked, do not use persuasion, deception, intimidation, or another attitude-moving social skill. Use a non-attitude skill_check such as investigation or perception for what the player can infer, then narrate the scene-local response; persist a new NPC later only when the player keeps engaging them."
RULES_LOOKUP_RULE="Rules lookup rule: during the opening turn, do not call lookup_class or other rules lookups just to restate the pre-seated player's class/race; get_state already includes enough player-facing identity for the opener. Use clawdnd-rules only when resolving an actual rule, spell, item, condition, or monster question."
PARLEY_TOOL_RULE="Parley tool rule: when using generate_parley_options, pass an explicit skills array such as persuasion, insight, performance, intimidation, deception. Do not rely on include_alignment or an implicit 'any' skill."
REWARD_MUTATION_RULE="Reward mutation rule: Do not call award_xp, grant_xp, level_up, or reward-granting mutation tools in this built-app provider proof path. If a moment deserves reward accounting, mention the fictional consequence in final narration and persist only memory/decision context with persist_beat."
OPENING_PERSIST_BEAT_RULE="Opening persist rule: do not call persist_beat during the opening turn. Opening state is recorded by the wrapper after your final reply; persist only on later turns after an actual player move has been resolved."
MOVE_PERSIST_BEAT_RULE="Move persist rule: This is a post-move turn: at least one real player move has been accepted and relayed below. If this beat produced durable memory, decision, or time changes, call persist_beat only after you have resolved the move. Do not put player-facing prose into persist_beat events; the wrapper records your final reply through the engine. When calling persist_beat with memories, each memory must be an object with character_id and fact fields. Do not pass memory strings."
OPENING_PROGRESS_TEXT="The first scene gathers around you; voices, risks, and choices come into focus."
MOVE_PROGRESS_TEXTS=(
  "Your choice takes hold; nearby voices, risks, and consequences begin to answer."
  "The world turns with your action; the scene shifts toward its answer."
  "Your move lands; attention gathers around what changes next."
  "Momentum carries through the scene; consequences are beginning to surface."
)

choose_move_progress_text() {
  local idx="${1:-0}"
  [[ "$idx" =~ ^[0-9]+$ ]] || idx=0
  local count="${#MOVE_PROGRESS_TEXTS[@]}"
  printf '%s\n' "${MOVE_PROGRESS_TEXTS[$((idx % count))]}"
}
CLAWDND_PLAY_COMPANIONS="${CLAWDND_PLAY_COMPANIONS:-}"   # default empty — the codex/solo lane (ui_playtest_app) doesn't set it; set -u would otherwise abort
if [ -n "${CLAWDND_PLAY_COMPANIONS//[[:space:]]/}" ]; then
  COMPANION_TOOL_RULE="Companion rule: only add companions named by CLAWDND_PLAY_COMPANIONS (${CLAWDND_PLAY_COMPANIONS}). Do not add any other companion to the party."
else
  COMPANION_TOOL_RULE="Companion rule: this is a solo provider launch. Do not call load_canon_character with kind=\"companion\" or add any companion to the party; stage canon NPCs in narration only unless the player later recruits them."
fi

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

DM_TURNS=0
write_provider_status "running" "active" "Codex DM provider is running."

MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"
MCURSOR="${MCURSOR:-0}"

if [ -n "${HERO_CAMP//[[:space:]]/}" ]; then
  log_engine_narration "$HERO_CAMP" "$OPENING_PROGRESS_TEXT" \
    || echo "[codex-dm-provider] warning: could not record immediate opening progress narration" >&2
fi

if [ -n "$HERO_CAMP" ]; then
  OPENING_PROMPT="$(cat <<EOF
You are the Dungeon Master for a solo WorldOS / ClawDnD adventure in world "$CLAWDND_WORLD".

$DM_CONTRACT_RULE
$DM_VOICE_RULE
$LOG_EVENT_TOOL_RULE
$LIVE_PROGRESS_LOG_RULE
$LIVE_DIALOGUE_LOG_RULE
$OPENING_LOG_EVENT_RULE
$STATE_DISCOVERY_RULE
$STARTUP_MUTATION_RULE
$SOCIAL_CHECK_TARGET_RULE
$RULES_LOOKUP_RULE
$PARLEY_TOOL_RULE
$REWARD_MUTATION_RULE
$OPENING_PERSIST_BEAT_RULE
$COMPANION_TOOL_RULE

Native-selected hero already seated:
- campaign_id: "$HERO_CAMP"
- player: "$HERO_PC_NAME" ($HERO_PC_RACE level $HERO_PC_LEVEL $HERO_PC_CLASS${HERO_PC_SUBCLASS:+ / $HERO_PC_SUBCLASS}), id "$HERO_PC_ID"
${HERO_PC_SPELLS:+- known/prepared spells from engine sheet: $HERO_PC_SPELLS}

Start from that already-seated player:
- call get_state("$HERO_CAMP") FIRST to read the campaign;
- do NOT call start_world, start_session, start_character, or load_canon_character for the player;
- open a 2nd-person, player-facing scene centered on "$HERO_PC_NAME" with real sensory details, at least one quoted NPC line, and a clear open moment.

Your final reply must be non-empty opening narration for the player, not a setup note and not a tool-only ending.
EOF
)"
else
  OPENING_PROMPT="$(cat <<EOF
You are the Dungeon Master for a solo WorldOS / ClawDnD adventure in world "$CLAWDND_WORLD".

$DM_CONTRACT_RULE
$DM_VOICE_RULE
$LOG_EVENT_TOOL_RULE
$LIVE_PROGRESS_LOG_RULE
$LIVE_DIALOGUE_LOG_RULE
$OPENING_LOG_EVENT_RULE
$STATE_DISCOVERY_RULE
$STARTUP_MUTATION_RULE
$SOCIAL_CHECK_TARGET_RULE
$RULES_LOOKUP_RULE
$PARLEY_TOOL_RULE
$REWARD_MUTATION_RULE
$OPENING_PERSIST_BEAT_RULE
$COMPANION_TOOL_RULE

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

ACTIVE_CAMPAIGN_ID="$(discover_active_campaign_id)"
record_dm_reply "$ACTIVE_CAMPAIGN_ID" "$OPENING" "opening"
CAMPAIGN_TOOL_HINT="$(campaign_tool_hint "$ACTIVE_CAMPAIGN_ID")"

DM_TURNS=1
write_provider_status "running" "active" "Codex DM provider is running."
while true; do
  if [ "$DM_TURNS" -ge "$CLAWDND_PLAY_MAX_TURNS" ]; then
    write_provider_status "stopped" "turn_cap" "Codex DM stopped after reaching the configured max turns. Increase Max turns or start a new provider-backed session to continue."
    sleep "${WORLDOS_PROVIDER_STOP_GRACE_SECONDS:-${CLAWDND_PROVIDER_STOP_GRACE_SECONDS:-20}}"
    break
  fi
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
    CODEX_LEAN_REGROUND_RULE="$(codex_lean_reground_rule "$ACTIVE_CAMPAIGN_ID")"
    MOVE_PROGRESS_TEXT="$(choose_move_progress_text "$DM_TURNS")"
    log_engine_narration "$ACTIVE_CAMPAIGN_ID" "$MOVE_PROGRESS_TEXT" \
      || echo "[codex-dm-provider] warning: could not record immediate move progress narration" >&2
    if ! REPLY="$(codex_dm_turn "You are the Dungeon Master mid-session. Re-ground from the engine state first, then resolve this player move through the engine/rules tools and reply with 2nd-person player-facing narration.

$DM_CONTRACT_RULE
$DM_VOICE_RULE
$LOG_EVENT_TOOL_RULE
$LIVE_PROGRESS_LOG_RULE
$LIVE_DIALOGUE_LOG_RULE
$STATE_DISCOVERY_RULE
$CAMPAIGN_TOOL_HINT
$CODEX_LEAN_REGROUND_RULE
$SOCIAL_CHECK_TARGET_RULE
$RULES_LOOKUP_RULE
$PARLEY_TOOL_RULE
$REWARD_MUTATION_RULE
$MOVE_PERSIST_BEAT_RULE
$COMPANION_TOOL_RULE

Player move:
$PMSG")"; then
      fail "Codex DM move turn failed; see $STDERR_LOG"
    fi
    record_dm_reply "$ACTIVE_CAMPAIGN_ID" "$REPLY" "move"
    DM_TURNS=$((DM_TURNS + 1))
    write_provider_status "running" "active" "Codex DM provider is running."
  else
    sleep 2
  fi
done
