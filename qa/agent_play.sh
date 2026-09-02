#!/usr/bin/env bash
# AGENT-PLAY — a DM-ONLY beat loop against an ALREADY-RUNNING QA sandbox.
#
# qa/run_adventure.sh and qa/run_duo.sh run BOTH sides as `claude -p` processes bound to their own
# private engine. Nothing let an EXTERNAL player (an agent, or a human at the viewer) drive the DM
# beat by beat on a sandbox engine that is already up. This is that missing half: the DM keeps
# run_adventure's exact per-beat contract (hermetic env, brief + arc addendum, model pin, lean/effort/
# timeout tiers, transient retry, quest_progress stamping) — the PLAYER is you.
#
#   qa/agent_play.sh start  --engine http://127.0.0.1:8876 --state /Users/m1/Codex/worldos-qa-sandbox/play1/state \
#                           --run smoke1 [--campaign adventure_demo_v1] [--dm-model opus] [--beats 20]
#   qa/agent_play.sh say    --run smoke1 "I look around the crypt and ready my blade."
#   qa/agent_play.sh serve  --run smoke1 --engine … --state … [--dm-model id] [--max-beats N]
#   qa/agent_play.sh status --run smoke1
#   qa/agent_play.sh stop   --run smoke1
#   …any subcommand + --dry-run  → everything EXCEPT the claude call (the exact command is printed;
#                                  `serve --dry-run` drains the chat tail and exits instead of idling).
#
# INTERLEAVING A PLAYTHROUGH — `say` is SPEECH/INTENT; MOVEMENT stays on the QA + viewer channels:
#   * talk / act / fight  → `qa/agent_play.sh say --run <r> "<what you say or do>"`  (one DM beat)
#   * walk the party      → the QA channel `POST http://127.0.0.1:<qa-port>/click {"x":…,"y":…}`
#                           (qa/qa_sandbox.py's --qa-port; qa/walk_test.py is the reference client)
#                           or the viewer's `POST http://127.0.0.1:<engine-port>/move` intent, which
#                           the engine's player facade drains from $WORLDOS_PLAYER_MOVES.
#   This script NEVER reimplements movement. A normal playthrough is: /click to walk somewhere, then
#   `serve` also tails $WORLDOS_PLAYER_MOVES, turning each viewer intent into one player line and
#   one DM beat. Under launchd, `stop` SIGTERMs the recorded serve PID; KeepAlive=false means
#   launchd does not restart it. The DM re-grounds on live engine state every beat.
#
# The play screen updates because every line is appended to the viewer's chat log
# ($WORLDOS_VIEWER_CHAT = <state>/chat.jsonl, set by qa/qa_sandbox.py) in the viewer's two-sided
# format: one JSON object per line, {"role":"player"|"dm","text":"…"}; DM rows also carry
# {"engine_logged":true} when the same prose reached the engine session log, so the client renders
# it once (via /events) instead of twice. `serve` tails that SAME file, so a human typing in the
# viewer and an agent calling `say` drive the identical loop — it is what the installed owner
# instance's `org.worldos.owner-dm` LaunchAgent runs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

usage() { sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

SUB="${1:-}"; shift || true
case "$SUB" in -h|--help|help|"") usage; exit 0 ;; esac

RUN=""; ENGINE=""; STATE_IN=""; CAMPAIGN_IN=""; DM_MODEL_IN=""; BEATS_IN=""; BUDGET_IN=""
MAX_BEATS_IN=""; DRY_RUN=0; TEXT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --run) RUN="${2:-}"; shift 2 ;;
    --engine) ENGINE="${2:-}"; shift 2 ;;
    --state) STATE_IN="${2:-}"; shift 2 ;;
    --campaign) CAMPAIGN_IN="${2:-}"; shift 2 ;;
    --dm-model) DM_MODEL_IN="${2:-}"; shift 2 ;;
    --beats) BEATS_IN="${2:-}"; shift 2 ;;
    --max-beats) MAX_BEATS_IN="${2:-}"; shift 2 ;;
    --budget) BUDGET_IN="${2:-}"; shift 2 ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; TEXT="${1:-}"; shift || true ;;
    -*) echo "[agent-play] unknown flag: $1" >&2; exit 2 ;;
    *) TEXT="$1"; shift ;;
  esac
done
[ -n "$RUN" ] || { echo "[agent-play] missing --run <name>" >&2; exit 2; }

RUNS_ROOT="${WORLDOS_AGENT_PLAY_ROOT:-qa/agent_play_runs}"   # repo-relative by default; absolute is fine
case "$RUNS_ROOT" in /*) RUN_DIR="$RUNS_ROOT/$RUN" ;; *) RUN_DIR="$ROOT/$RUNS_ROOT/$RUN" ;; esac
SESSION="$RUN_DIR/session.json"
HEARTBEAT="$RUN_DIR/serve.heartbeat"
STARTING="$RUN_DIR/serve.starting"

# ── session file helpers (the durable binding; `say`/`serve`/`status`/`stop` need only --run) ────
ap_sget() { python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
v=d.get(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else "")
sys.stdout.write("" if v is None else str(v))' "$SESSION" "$1" "${2:-}"; }
ap_sset() { python3 -c 'import json,os,sys
p=sys.argv[1]
try: d=json.load(open(p))
except Exception: d={}
for i in range(2,len(sys.argv),2): d[sys.argv[i]]=sys.argv[i+1]
tmp=p+".tmp"; json.dump(d,open(tmp,"w"),indent=2); os.replace(tmp,p)' "$SESSION" "$@"; }

ap_require_session() {
  [ -s "$SESSION" ] || { echo "[agent-play] no session for run '$RUN' ($SESSION) — run \`start\` first." >&2; exit 2; }
}

ap_line_count() { local p="$1"; [ -f "$p" ] && wc -l < "$p" | tr -d ' ' || echo 0; }
ap_budget_message() {
  local used total; used="$(ap_sget beats_used 0)"; total="$(ap_sget beats 20)"
  echo "beat budget exhausted ($used/$total) — extend with --beats or start a new run"
}
ap_budget_available() {
  local used total; used="$(ap_sget beats_used 0)"; total="$(ap_sget beats 20)"
  if [ "$total" -gt 0 ] && [ "$used" -ge "$total" ]; then
    echo "[agent-play] $(ap_budget_message)" >&2
    return 2
  fi
}
ap_pid_live() {
  local pid="$1" stat
  case "$pid" in ""|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  stat="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
  case "$stat" in *Z*) return 1 ;; esac
  [ -n "$stat" ]
}
ap_pid_lstart() {
  local pid="$1"
  case "$pid" in ""|*[!0-9]*) return 1 ;; esac
  ps -o lstart= -p "$pid" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}
ap_serve_owned() {
  local pid expected actual
  pid="$(ap_sget serve_pid)"; expected="$(ap_sget serve_lstart)"
  [ -n "$expected" ] && ap_pid_live "$pid" || return 1
  actual="$(ap_pid_lstart "$pid")"
  [ -n "$actual" ] && [ "$actual" = "$expected" ]
}
ap_kill_tree() {
  local signal="$1" pid="$2" child children
  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  for child in $children; do ap_kill_tree "$signal" "$child"; done
  kill -s "$signal" "$pid" 2>/dev/null || true
}

# ── beat wiring (only `start`/`say`/`serve` need it) ─────────────────────────────────────────────
ap_bind() {
  mkdir -p "$RUN_DIR"
  # shellcheck source=lib_beat_driver.sh
  . "$ROOT/qa/lib_beat_driver.sh"
  # shellcheck source=lib_adventure_dm.sh
  . "$ROOT/qa/lib_adventure_dm.sh"
  ADV_LOG_TAG="agent-play"
  STATE_DIR="$(ap_sget state_dir)"
  CAMPAIGN_ID="$(ap_sget campaign_id)"
  QUEST_TITLE="$(ap_sget quest_title)"
  ENGINE="${ENGINE:-$(ap_sget engine)}"
  WORLDOS_DM_MODEL="${DM_MODEL_IN:-$(ap_sget dm_model)}"
  BUDGET="$(ap_sget budget 4.00)"
  T="$RUN_DIR"                             # adv_dm_turn writes $T/$RUN.dm.*.jsonl (absolute)
  CHAT="$(ap_sget chat_path)"
  MOVES="$(ap_sget moves_path)"
  if [ -z "$MOVES" ]; then
    MOVES="${WORLDOS_PLAYER_MOVES:-$STATE_DIR/player_moves.jsonl}"
    ap_sset moves_path "$MOVES" move_cursor "$(ap_line_count "$MOVES")" >/dev/null
  fi
  COMBINED="$T/$RUN.jsonl"
  TRACE="$T/$RUN.quest_trace.json"
  DM_CFG="$RUN_DIR/dm.mcp.json"
  ADV_TELEMETRY_FAIL_FILE="$RUN_DIR/.telemetry_fails"
  WORLDOS_LEAN_BEATS="${WORLDOS_LEAN_BEATS:-1}"
  export WORLDOS_STATE_DIR="$STATE_DIR"
  adv_dm_hermetic_env
  worldos_isolate_claude_auth
  adv_dm_write_mcp_config "$ROOT" "$STATE_DIR" "$DM_CFG" "$T/$RUN.tooltiming.jsonl"
  [ "$DRY_RUN" = "1" ] && ap_install_dry_run_turn
  return 0
}

# --dry-run: exercise EVERYTHING except the claude call. The exact command that would have run is
# printed to stderr and appended to <run-dir>/dryrun_cmds.log (what the unit test asserts on).
ap_install_dry_run_turn() {
  adv_dm_turn() {
    local sid="$1" first="$2" msg="$3" resume=() extra=() safe_env=() env_arg
    [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
    worldos_dm_lean_args "$first" "$CAMPAIGN_ID" "$WORLDOS_LEAN_TAIL"
    if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then resume=("${WORLDOS_DM_LEAN_SESSION[@]}"); extra=("${WORLDOS_DM_LEAN_EXTRA[@]}"); fi
    worldos_dm_effort_arg "$first"
    worldos_stream_flag_arg
    local cmd
    for env_arg in "${DUO_ENV[@]}"; do
      case "$env_arg" in CLAUDE_CODE_OAUTH_TOKEN=*) safe_env+=(CLAUDE_CODE_OAUTH_TOKEN=\<redacted\>) ;; *) safe_env+=("$env_arg") ;; esac
    done
    # shlex.quote via python3, NOT printf %q: bash 3.2's %q emits raw bytes for non-ASCII and the
    # DM prompt is full of em-dashes — the escaped form must stay valid UTF-8 to be logged/read back.
    # NOTE the \n escaping: the DM prompt is multi-line, and the log is one command per line.
    cmd="$(python3 -c 'import shlex,sys; print(" ".join(shlex.quote(a) for a in sys.argv[1:]).replace(chr(10), chr(92)+"n"))' \
      timeout "$(worldos_dm_timeout "$first")" "${safe_env[@]}" claude -p "$msg" \
      ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" \
      --strict-mcp-config --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} \
      --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      ${WORLDOS_STREAM_FLAG[@]+"${WORLDOS_STREAM_FLAG[@]}"} --output-format stream-json --verbose)"
    echo "[agent-play] DRY-RUN dm beat (first=$first) would exec: $cmd" >&2
    printf '%s\n' "$cmd" >> "$RUN_DIR/dryrun_cmds.log"
    printf '%s' "(dry-run DM beat — no claude was called)"
  }
}

# The shared beat driver inspects $? after a deliberately-failing claude/timeout call and drives its
# OWN retry, so it is written for `set -uo pipefail`. errexit would abort the retry subshell — run
# the beat (and the fail-open quest poll) with errexit off, then restore it.
AP_DM_REPLY=""
ap_beat() {  # $1=sid $2=first? $3=msg
  set +e
  AP_DM_REPLY="$(adv_dm_turn_retry "$1" "$2" "$3")"
  worldos_resolve_dm_reply "$AP_DM_REPLY" "$STATE_DIR"; AP_DM_REPLY="$WORLDOS_DM_REPLY"
  set -e
}
AP_QUEST_STATUS=""
ap_poll() { set +e; AP_QUEST_STATUS="$(adv_quest_poll "$1")"; set -e; AP_QUEST_STATUS="${AP_QUEST_STATUS:-active}"; }

# One-line beat summary: beat n, quest status, combat active?, stamps.
ap_beat_summary() {
  local beat="$1" combat stamps
  combat="$( { curl -s -m 5 "$ENGINE/combat-surface" 2>/dev/null || true; } | python3 -c 'import json,sys
try: print("yes" if (json.load(sys.stdin).get("encounter") or {}).get("active") else "no")
except Exception: print("unknown")' 2>/dev/null)"
  combat="${combat:-unknown}"
  stamps="$(python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(",".join(s.get("stage","") for s in d.get("stamps",[])) or "-")' "$TRACE" 2>/dev/null || echo -)"
  echo "[agent-play] beat $beat | quest=$AP_QUEST_STATUS | combat_active=$combat | stamps=$stamps"
}

# Append the DM reply to chat.jsonl in the viewer's format (record_dm_reply stamps engine_logged
# when the same prose is already in the engine session log, so the play screen renders it once).
ap_record_dm() {
  local beat="$1"
  if [ -z "${AP_DM_REPLY//[[:space:]]/}" ]; then
    worldos_chatlog_dm_failed
    echo "[agent-play] DM produced no narration at beat $beat" >&2
    return 1
  fi
  record_dm_reply "$CAMPAIGN_ID" "$AP_DM_REPLY" "beat $beat"
  return 0
}

# ── subcommands ─────────────────────────────────────────────────────────────────────────────────
ap_start() {
  [ -n "$ENGINE" ] || { echo "[agent-play] start needs --engine <url>" >&2; exit 2; }
  [ -n "$STATE_IN" ] || { echo "[agent-play] start needs --state <sandbox state dir>" >&2; exit 2; }
  [ -d "$STATE_IN" ] || { echo "[agent-play] state dir not found: $STATE_IN" >&2; exit 2; }
  mkdir -p "$RUN_DIR"
  local state cid budget model beats chat_path moves_path chat_cursor move_cursor
  # shellcheck source=lib_beat_driver.sh
  . "$ROOT/qa/lib_beat_driver.sh"
  state="$(cd "$STATE_IN" && pwd)"
  cid="$CAMPAIGN_IN"
  [ -n "$cid" ] || cid="$(worldos_live_campaign_id "$ROOT" "$state" || true)"
  [ -n "$cid" ] || { echo "[agent-play] could not resolve a live campaign in $state — pass --campaign <id>" >&2; exit 2; }
  model="${DM_MODEL_IN:-$(worldos_env DM_MODEL opus)}"
  budget="${BUDGET_IN:-4.00}"
  # Opus cold-open headroom floor (same rationale as run_adventure/run_duo).
  case "$model" in *opus*) if awk "BEGIN{exit !($budget < 4.0)}"; then budget=4.00; fi ;; esac
  beats="${BEATS_IN:-20}"
  chat_path="$state/chat.jsonl"
  moves_path="${WORLDOS_PLAYER_MOVES:-$state/player_moves.jsonl}"
  chat_cursor="$(ap_line_count "$chat_path")"
  move_cursor="$(ap_line_count "$moves_path")"
  python3 -c 'import json,os,sys
p,=sys.argv[1:2]
json.dump(dict(zip(sys.argv[2::2], sys.argv[3::2])), open(p+".tmp","w"), indent=2)
os.replace(p+".tmp", p)' "$SESSION" \
    run "$RUN" engine "$ENGINE" state_dir "$state" campaign_id "$cid" \
    quest_title "${WORLDOS_AGENT_PLAY_QUEST:-The Crypt Below}" dm_model "$model" budget "$budget" \
    beats "$beats" beats_used 0 chat_cursor "$chat_cursor" chat_path "$chat_path" \
    move_cursor "$move_cursor" moves_path "$moves_path" serve_pid "" \
    serve_lstart "" \
    dm_session_id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
    created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" stopped ""
  ap_bind
  : > "$COMBINED"
  curl -s -m 5 -o /dev/null "$ENGINE/combat-surface" || echo "[agent-play] WARN: $ENGINE did not answer /combat-surface — is the sandbox up?" >&2
  echo "[agent-play] run=$RUN campaign=$CAMPAIGN_ID state=$STATE_DIR dm=$WORLDOS_DM_MODEL beats=$beats chat=$CHAT"

  # The opening beat — run_adventure's brief + arc addendum verbatim; the setup directive differs on
  # ONE axis by design: agent_play ATTACHES to a possibly mid-arc campaign, so the DM opens where the
  # party actually STANDS (run_adventure always opens at the seeded camp, because it just seeded it).
  local dsid brief directive
  dsid="$(ap_sget dm_session_id)"
  brief="$(adv_dm_brief "$CAMPAIGN_ID" "$QUEST_TITLE")"
  directive="GROUND on the live campaign now: get_state(\"$CAMPAIGN_ID\"), look_around(\"$CAMPAIGN_ID\"), get_quests(\"$CAMPAIGN_ID\"). Do NOT build, seed or reset the world — it already exists and may be MID-ARC. OPEN the scene EXACTLY WHERE THE PARTY ACTUALLY STANDS right now (the location, foes and quest progress you just read — NOT the camp, unless that is where they are), and hand the open moment straight back to the player."
  ap_beat "$dsid" 1 "$brief

A live player (an agent or a human) is at the keyboard. They have not spoken yet — you open.

$directive OUTPUT DISCIPLINE — your final reply IS the opening scene: 2nd-person in-fiction PROSE + quoted dialogue ONLY. Never narrate your own setup/process."
  ap_record_dm 0 || exit 1
  ap_poll 0
  printf '%s\n' "$AP_DM_REPLY"
  ap_beat_summary 0
}

ap_say() {
  ap_require_session
  [ -n "$TEXT" ] || { echo "[agent-play] say needs the player text: qa/agent_play.sh say --run $RUN \"…\"" >&2; exit 2; }
  ap_budget_available || exit $?
  ap_bind
  if ap_serve_owned; then
    python3 - "$MOVES" "$TEXT" <<'PY'
import fcntl, json, os, sys
path, text = sys.argv[1:]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "a", encoding="utf-8") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    handle.write(json.dumps({"role": "player", "kind": "say", "text": text}) + "\n")
    handle.flush(); os.fsync(handle.fileno())
PY
    echo "[agent-play] queued for serve"
    return 0
  fi
  chatlog player "$TEXT"                     # the viewer's play screen shows the player line too
  ap_do_beat "say" "$TEXT" ""; local rc=$?
  # Keep the `serve` cursor past everything we just wrote, so the two entry points can never
  # double-answer the same line.
  local lines=0; [ -f "$CHAT" ] && lines="$(wc -l < "$CHAT" | tr -d ' ')"
  ap_sset chat_cursor "${lines:-0}" >/dev/null
  return $rc
}

# ONE player intent -> ONE DM beat. $1=kind $2=display text $3=structured payload (optional).
ap_do_beat() {
  local kind="$1" ptext="$2" payload="$3" beat used total dsid prog prev_day prev_tod prev_loc runbook director event_adv action
  used="$(ap_sget beats_used 0)"; total="$(ap_sget beats 20)"
  beat=$((used + 1))
  ap_budget_available || return $?
  dsid="$(ap_sget dm_session_id)"
  prog="$(worldos_read_progress "$STATE_DIR" || true)"
  prev_day="$(printf '%s' "$prog" | cut -f1)"; prev_day="${prev_day:-1}"
  prev_tod="$(printf '%s' "$prog" | cut -f2)"; prev_tod="${prev_tod:-morning}"
  prev_loc="$(printf '%s' "$prog" | cut -f5)"
  runbook="$(worldos_runbook_for_beat "$beat" "$total" "$prev_loc" "$STATE_DIR" || true)"
  director="$(worldos_director_advisory "$ROOT" "$STATE_DIR" || true)"
  event_adv="$(worldos_event_advisory "$ROOT" "$STATE_DIR" || true)"
  if [ -n "$payload" ]; then
    action="The player submits this structured move intent exactly as recorded:
$payload

Honor its top-level kind; do not reinterpret an action as speech."
  else
    action="The player does:

[$kind] $ptext"
  fi
  ap_beat "$dsid" 0 "$action

Resolve it through the engine (roll/attack/travel as needed), then PLAY the next beat as a full lived scene — any NPC or companion in the scene SPEAKS at least one quoted line; weave the open moment back to the player. Mark quest objectives with complete_objective as they land, and run real combat in the crypt/throne hall.

$runbook

$director

$event_adv"
  ap_record_dm "$beat" || return 1
  worldos_soft_tick "$ROOT" "$STATE_DIR" "$prev_day" "$prev_tod"
  ap_poll "$beat"
  ap_sset beats_used "$beat" last_quest_status "$AP_QUEST_STATUS" >/dev/null
  printf '%s\n' "$AP_DM_REPLY"
  ap_beat_summary "$beat"
  return 0
}

ap_do_clarify() {
  local payload="$1" dsid used
  dsid="$(ap_sget dm_session_id)"; used="$(ap_sget beats_used 0)"
  ap_beat "$dsid" 0 "The player asks this NON-TURN clarification:
$payload

Answer only what the character can perceive or know. Do not resolve an action, advance the scene, spend a turn, or advance time. Return concise in-fiction prose or quoted dialogue."
  ap_record_dm "clarify" || return 1
  printf '%s\n' "$AP_DM_REPLY"
  echo "[agent-play] clarify answered | beats=$used/$(ap_sget beats 20)"
}

# serve — the foreground loop the owner instance's org.worldos.owner-dm LaunchAgent runs. Tails the
# viewer's chat.jsonl for NEW player lines and answers each with exactly ONE DM beat through the same
# ap_do_beat path `say` uses. The consumed-line cursor is persisted in the session file, so a restart
# never re-answers a line it already answered.
AP_SERVE_STOP=0
ap_serve_cleanup() {
  rm -f "$HEARTBEAT" "$STARTING"
  [ "$(ap_sget serve_pid)" = "$$" ] && ap_sset serve_pid "" serve_lstart "" >/dev/null || true
}
ap_move_text() {
  python3 -c 'import json,sys
try: m=json.loads(sys.stdin.read())
except Exception: raise SystemExit(1)
if m.get("role") != "player": raise SystemExit(1)
kind=str(m.get("kind") or "move")
if isinstance(m.get("x"), int) and isinstance(m.get("y"), int):
    print(f"[{kind}] walks to ({m[chr(120)]},{m[chr(121)]})")
elif kind == "set_seed_param":
    force=" force=true" if m.get("force") is True else ""
    param=json.dumps(m.get("param")); value=json.dumps(m.get("value"))
    print(f"[{kind}] param={param} value={value}{force}")
else:
    detail=next((str(m[k]) for k in ("text","name","target","target_id","skill","weapon") if m.get(k) not in (None,"")), "acts")
    print(f"[{kind}] {detail}")'
}
ap_move_kind() {
  python3 -c 'import json,sys
try: m=json.loads(sys.stdin.read())
except Exception: raise SystemExit(1)
if m.get("role") != "player": raise SystemExit(1)
print(str(m.get("kind") or "move"))'
}
ap_consume_move() {
  local next="$1" row="$2" text="$3" kind="$4"
  python3 - "$SESSION" "$CHAT" "$MOVES" "$next" "$row" "$text" "$kind" <<'PY'
import hashlib, json, os, sys
session_path, chat_path, moves_path, cursor, raw, text, kind = sys.argv[1:]
move_id = hashlib.sha256(f"{moves_path}:{cursor}:{raw}".encode()).hexdigest()
seen = False
try:
    with open(chat_path, encoding="utf-8") as handle:
        seen = any((json.loads(line).get("move_id") == move_id) for line in handle if line.strip())
except (FileNotFoundError, ValueError):
    pass
if not seen:
    with open(chat_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"role":"player", "text":text, "move_id":move_id, "move_kind":kind}) + "\n")
        handle.flush(); os.fsync(handle.fileno())
data = json.load(open(session_path)); data["move_cursor"] = cursor
tmp = session_path + ".tmp"
with open(tmp, "w") as handle:
    json.dump(data, handle, indent=2); handle.flush(); os.fsync(handle.fileno())
os.replace(tmp, session_path)
PY
}
ap_serve() {
  mkdir -p "$RUN_DIR"; rm -f "$HEARTBEAT"; : > "$STARTING"
  trap 'rm -f "$HEARTBEAT" "$STARTING"' EXIT
  if [ ! -s "$SESSION" ]; then
    [ -n "$ENGINE" ] && [ -n "$STATE_IN" ] || { echo "[agent-play] serve on a fresh run needs --engine and --state (or run \`start\` first)" >&2; exit 2; }
    ap_start
  fi
  ap_bind
  local max="${MAX_BEATS_IN:-0}" served=0 cursor lines move_cursor move_lines row role text kind move_id label source rc=0 lstart
  label="$max"; [ "$max" -gt 0 ] || label="unlimited"
  trap 'AP_SERVE_STOP=1' TERM INT
  trap 'ap_serve_cleanup' EXIT
  lstart="$(ap_pid_lstart "$$")"; [ -n "$lstart" ] || { echo "[agent-play] cannot identify serve process" >&2; return 1; }
  ap_sset stopped "" serve_pid "$$" serve_lstart "$lstart" >/dev/null
  rm -f "$STARTING"
  echo "[agent-play] serving run=$RUN chat=$CHAT moves=$MOVES campaign=$CAMPAIGN_ID dm=$WORLDOS_DM_MODEL max_beats=$label"
  while [ "$AP_SERVE_STOP" = "0" ]; do
    touch "$HEARTBEAT"                       # explicit serving-state marker, refreshed every poll
    # `stop --run <name>` stamps the session, which is how another shell asks this loop to exit
    # (SIGTERM is the other way; the LaunchAgent uses that one).
    if [ -n "$(ap_sget stopped)" ]; then echo "[agent-play] session marked stopped — exiting serve"; break; fi
    cursor="$(ap_sget chat_cursor 0)"; cursor="${cursor:-0}"
    move_cursor="$(ap_sget move_cursor 0)"; move_cursor="${move_cursor:-0}"
    lines="$(ap_line_count "$CHAT")"; move_lines="$(ap_line_count "$MOVES")"
    source=""
    if [ "${lines:-0}" -gt "$cursor" ]; then
      source="chat"; row="$(sed -n "$((cursor + 1))p" "$CHAT")"
      role="$(printf '%s' "$row" | python3 -c 'import json,sys
try: print((json.loads(sys.stdin.read()) or {}).get("role") or "")
except Exception: print("")')"
      if [ "$role" != "player" ]; then ap_sset chat_cursor "$((cursor + 1))" >/dev/null; continue; fi
      move_id="$(printf '%s' "$row" | python3 -c 'import json,sys
try: print((json.loads(sys.stdin.read()) or {}).get("move_id") or "")
except Exception: print("")')"
      if [ -n "$move_id" ]; then ap_sset chat_cursor "$((cursor + 1))" >/dev/null; continue; fi
      kind="say"
      text="$(printf '%s' "$row" | python3 -c 'import json,sys
try: print((json.loads(sys.stdin.read()) or {}).get("text") or "")
except Exception: print("")')"
    elif [ "${move_lines:-0}" -gt "$move_cursor" ]; then
      source="move"; row="$(sed -n "$((move_cursor + 1))p" "$MOVES")"
      text="$(printf '%s' "$row" | ap_move_text 2>/dev/null || true)"
      kind="$(printf '%s' "$row" | ap_move_kind 2>/dev/null || true)"
      if [ -z "$text" ] || [ -z "$kind" ]; then ap_sset move_cursor "$((move_cursor + 1))" >/dev/null; continue; fi
    else
      if [ "$max" -gt 0 ] && [ "$served" -ge "$max" ]; then break; fi
      # --dry-run DRAINS and exits (a test must not idle forever); the real loop idle-polls at 2s.
      if [ "$DRY_RUN" = "1" ]; then break; fi
      sleep 2 || true
      continue
    fi
    if [ "$kind" != "clarify" ] && ! ap_budget_available; then
      chatlog dm "[system] $(ap_budget_message)" '{"system":true}'
      rc=2
      break
    fi
    echo "[agent-play] player $source: ${text:0:100}"
    if [ "$source" = "chat" ]; then ap_sset chat_cursor "$((cursor + 1))" >/dev/null
    else ap_consume_move "$((move_cursor + 1))" "$row" "$text" "$kind"; fi
    if [ "${WORLDOS_AGENT_PLAY_TEST_CRASH_AFTER_CONSUME:-0}" = "1" ]; then kill -KILL "$$"; fi
    if [ "$kind" = "clarify" ]; then ap_do_clarify "$row" || true
    else ap_do_beat "$kind" "$text" "$row" || true; served=$((served + 1)); fi
    if [ "$max" -gt 0 ] && [ "$served" -ge "$max" ]; then break; fi
  done
  ap_serve_cleanup
  trap - EXIT TERM INT
  echo "[agent-play] serve stopped (beats served: $served)"
  return "$rc"
}

ap_status() {
  ap_require_session
  local state cid used total spend trace_path status stamps
  state="$(ap_sget state_dir)"; cid="$(ap_sget campaign_id)"
  used="$(ap_sget beats_used 0)"; total="$(ap_sget beats 20)"
  trace_path="$RUN_DIR/$RUN.quest_trace.json"
  status="$(WORLDOS_STATE_DIR="$state" uv run --directory "$ROOT/servers/engine" python "$ROOT/qa/quest_progress.py" \
              "$state" "$cid" --beat "$used" --trace "$trace_path" --quest-title "$(ap_sget quest_title)" 2>/dev/null \
            | tail -n1 || true)"
  status="${status#quest_status=}"
  stamps="$(python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(",".join(s.get("stage","") for s in d.get("stamps",[])) or "-")' "$trace_path" 2>/dev/null || echo -)"
  spend="$(python3 -c 'import glob,json,sys
tot=0.0
for f in glob.glob(sys.argv[1]):
    for ln in open(f, encoding="utf-8", errors="replace"):
        try: row=json.loads(ln)
        except Exception: continue
        if isinstance(row,dict) and row.get("type")=="result": tot+=float(row.get("total_cost_usd") or 0)
print(f"{tot:.4f}")' "$RUN_DIR/$RUN.dm.*.jsonl" 2>/dev/null || echo 0)"
  echo "run=$RUN campaign=$cid state=$state"
  echo "beats=$used/$total  quest=${status:-unknown}  stamps=$stamps  spend_usd=$spend"
  echo "chat=$(ap_sget chat_path)  session=$SESSION  stopped=$(ap_sget stopped)"
  if [ -f "$HEARTBEAT" ]; then echo "serve_heartbeat=PRESENT path=$HEARTBEAT"; elif [ -f "$STARTING" ]; then echo "serve_heartbeat=STARTING path=$HEARTBEAT"; else echo "serve_heartbeat=MISSING path=$HEARTBEAT"; fi
}

ap_stop() {
  ap_require_session
  # shellcheck source=lib_beat_driver.sh
  . "$ROOT/qa/lib_beat_driver.sh"
  local stopped pid i closeout
  STATE_DIR="$(ap_sget state_dir)"
  worldos_stream_tailer_kill_pidfile "$STATE_DIR" 2>/dev/null || true
  stopped="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ap_sset stopped "$stopped" >/dev/null
  pid="$(ap_sget serve_pid)"
  if ap_serve_owned; then
    ap_kill_tree TERM "$pid"
    i=0
    while ap_serve_owned; do
      if [ "$i" -ge 40 ]; then ap_kill_tree KILL "$pid"; fi
      [ "$i" -lt 50 ] || { echo "[agent-play] owned serve pid $pid did not exit" >&2; return 1; }
      sleep 0.1; i=$((i + 1))
    done
  elif [ -n "$pid" ]; then
    echo "[agent-play] stale/unowned serve pid $pid not signaled" >&2
  fi
  ap_sset serve_pid "" serve_lstart "" >/dev/null
  closeout="$RUN_DIR/closeout.json"
  python3 - "$SESSION" "$RUN_DIR/$RUN.quest_trace.json" "$RUN_DIR/$RUN.dm.*.jsonl" "$closeout" <<'PY'
import glob, json, os, sys
session_path, trace_path, dm_glob, out = sys.argv[1:]
s = json.load(open(session_path))
try: trace = json.load(open(trace_path))
except Exception: trace = {}
spend = 0.0
for path in glob.glob(dm_glob):
    for line in open(path, encoding="utf-8", errors="replace"):
        try: row = json.loads(line)
        except Exception: continue
        if isinstance(row, dict) and row.get("type") == "result": spend += float(row.get("total_cost_usd") or 0)
payload = {"beats": {"used": int(s.get("beats_used") or 0), "limit": int(s.get("beats") or 0)},
           "chat_path": s.get("chat_path"), "quest_status": trace.get("quest_status") or s.get("last_quest_status") or "unknown",
           "spend_usd": round(spend, 4), "stamps": [x.get("stage", "") for x in trace.get("stamps", [])],
           "stopped_at": s.get("stopped")}
tmp = out + ".tmp"; json.dump(payload, open(tmp, "w"), indent=2); os.replace(tmp, out)
PY
  ap_sset closeout_path "$closeout" >/dev/null
  echo "[agent-play] stopped run=$RUN (artifacts kept in $RUN_DIR; engine + viewer untouched)"
  echo "[agent-play] closeout=$closeout"
}

case "$SUB" in
  start) ap_start ;;
  say) ap_say ;;
  serve) ap_serve ;;
  status) ap_status ;;
  stop) ap_stop ;;
  *) echo "[agent-play] unknown subcommand '$SUB'. Try --help." >&2; exit 2 ;;
esac
