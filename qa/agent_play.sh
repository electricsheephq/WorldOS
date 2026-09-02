#!/usr/bin/env bash
# AGENT-PLAY — a DM-ONLY beat loop against an ALREADY-RUNNING QA sandbox.
#
# qa/run_adventure.sh and qa/run_duo.sh run BOTH sides as `claude -p` processes bound to their own
# private engine. Nothing let an EXTERNAL player (an agent, or a human at the viewer) drive the DM
# beat by beat on a sandbox engine that is already up. This is that missing half: the DM keeps
# run_adventure's exact per-beat contract (hermetic env, brief + arc addendum, model pin, lean/effort/
# timeout tiers, transient retry, quest_progress stamping) — the PLAYER is you.
#
#   qa/agent_play.sh start  --engine http://127.0.0.1:8876 --state /tmp/worldos-qa-sandbox/play1/state \
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
#   `say` so the DM narrates what you found there, then /click again — the DM re-grounds on live
#   engine state (get_state/look_around) every beat, so it always sees where the walk left you.
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
    local sid="$1" first="$2" msg="$3" resume=() extra=()
    [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
    worldos_dm_lean_args "$first" "$CAMPAIGN_ID" "$WORLDOS_LEAN_TAIL"
    if [ "${#WORLDOS_DM_LEAN_SESSION[@]}" -gt 0 ]; then resume=("${WORLDOS_DM_LEAN_SESSION[@]}"); extra=("${WORLDOS_DM_LEAN_EXTRA[@]}"); fi
    worldos_dm_effort_arg "$first"
    worldos_stream_flag_arg
    local cmd
    # shlex.quote via python3, NOT printf %q: bash 3.2's %q emits raw bytes for non-ASCII and the
    # DM prompt is full of em-dashes — the escaped form must stay valid UTF-8 to be logged/read back.
    # NOTE the \n escaping: the DM prompt is multi-line, and the log is one command per line.
    cmd="$(python3 -c 'import shlex,sys; print(" ".join(shlex.quote(a) for a in sys.argv[1:]).replace(chr(10), chr(92)+"n"))' \
      timeout "$(worldos_dm_timeout "$first")" "${DUO_ENV[@]}" claude -p "$msg" \
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
  local state cid budget model beats
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
  python3 -c 'import json,os,sys
p,=sys.argv[1:2]
json.dump(dict(zip(sys.argv[2::2], sys.argv[3::2])), open(p+".tmp","w"), indent=2)
os.replace(p+".tmp", p)' "$SESSION" \
    run "$RUN" engine "$ENGINE" state_dir "$state" campaign_id "$cid" \
    quest_title "${WORLDOS_AGENT_PLAY_QUEST:-The Crypt Below}" dm_model "$model" budget "$budget" \
    beats "$beats" beats_used 0 chat_cursor 0 chat_path "$state/chat.jsonl" \
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
  ap_bind
  chatlog player "$TEXT"                     # the viewer's play screen shows the player line too
  ap_do_beat "$TEXT"; local rc=$?
  # Keep the `serve` cursor past everything we just wrote, so the two entry points can never
  # double-answer the same line.
  local lines=0; [ -f "$CHAT" ] && lines="$(wc -l < "$CHAT" | tr -d ' ')"
  ap_sset chat_cursor "${lines:-0}" >/dev/null
  return $rc
}

# ONE player line -> ONE DM beat (the shared body of `say` and `serve`). $1 = the player's text.
ap_do_beat() {
  local ptext="$1" beat used total dsid prog prev_day prev_tod prev_loc runbook director event_adv
  used="$(ap_sget beats_used 0)"; total="$(ap_sget beats 20)"
  beat=$((used + 1))
  if [ "$total" -gt 0 ] && [ "$beat" -gt "$total" ]; then
    echo "[agent-play] beat budget exhausted ($used/$total) — raise it with \`start --beats N\` or a new run." >&2
    return 3
  fi
  dsid="$(ap_sget dm_session_id)"
  prog="$(worldos_read_progress "$STATE_DIR" || true)"
  prev_day="$(printf '%s' "$prog" | cut -f1)"; prev_day="${prev_day:-1}"
  prev_tod="$(printf '%s' "$prog" | cut -f2)"; prev_tod="${prev_tod:-morning}"
  prev_loc="$(printf '%s' "$prog" | cut -f5)"
  runbook="$(worldos_runbook_for_beat "$beat" "$total" "$prev_loc" "$STATE_DIR" || true)"
  director="$(worldos_director_advisory "$ROOT" "$STATE_DIR" || true)"
  event_adv="$(worldos_event_advisory "$ROOT" "$STATE_DIR" || true)"
  ap_beat "$dsid" 0 "The player does:

[say] $ptext

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

# serve — the foreground loop the owner instance's org.worldos.owner-dm LaunchAgent runs. Tails the
# viewer's chat.jsonl for NEW player lines and answers each with exactly ONE DM beat through the same
# ap_do_beat path `say` uses. The consumed-line cursor is persisted in the session file, so a restart
# never re-answers a line it already answered.
AP_SERVE_STOP=0
ap_serve() {
  if [ ! -s "$SESSION" ]; then
    [ -n "$ENGINE" ] && [ -n "$STATE_IN" ] || { echo "[agent-play] serve on a fresh run needs --engine and --state (or run \`start\` first)" >&2; exit 2; }
    ap_start
  fi
  ap_bind
  local max="${MAX_BEATS_IN:-0}" served=0 cursor lines row role text label
  label="$max"; [ "$max" -gt 0 ] || label="unlimited"
  trap 'AP_SERVE_STOP=1' TERM INT
  ap_sset stopped "" >/dev/null                 # a fresh serve un-stops the run
  echo "[agent-play] serving run=$RUN chat=$CHAT campaign=$CAMPAIGN_ID dm=$WORLDOS_DM_MODEL max_beats=$label"
  while [ "$AP_SERVE_STOP" = "0" ]; do
    # `stop --run <name>` stamps the session, which is how another shell asks this loop to exit
    # (SIGTERM is the other way; the LaunchAgent uses that one).
    if [ -n "$(ap_sget stopped)" ]; then echo "[agent-play] session marked stopped — exiting serve"; break; fi
    cursor="$(ap_sget chat_cursor 0)"; cursor="${cursor:-0}"
    lines=0; [ -f "$CHAT" ] && lines="$(wc -l < "$CHAT" | tr -d ' ')"
    if [ "${lines:-0}" -le "$cursor" ]; then
      if [ "$max" -gt 0 ] && [ "$served" -ge "$max" ]; then break; fi
      # --dry-run DRAINS and exits (a test must not idle forever); the real loop idle-polls at 2s.
      if [ "$DRY_RUN" = "1" ]; then break; fi
      sleep 2 || true
      continue
    fi
    row="$(sed -n "$((cursor + 1))p" "$CHAT")"
    role="$(printf '%s' "$row" | python3 -c 'import json,sys
try: print((json.loads(sys.stdin.read()) or {}).get("role") or "")
except Exception: print("")')"
    if [ "$role" != "player" ]; then
      ap_sset chat_cursor "$((cursor + 1))" >/dev/null   # our own dm rows + anything else: skip
      continue
    fi
    text="$(printf '%s' "$row" | python3 -c 'import json,sys
try: print((json.loads(sys.stdin.read()) or {}).get("text") or "")
except Exception: print("")')"
    echo "[agent-play] player line $((cursor + 1)): ${text:0:100}"
    ap_do_beat "$text" || true
    served=$((served + 1))
    # Advance by exactly ONE row: our own dm reply lands at the END of the file and is skipped by the
    # role check when we reach it. Jumping to the file length here would swallow any player line that
    # queued up BEHIND the one we just answered.
    ap_sset chat_cursor "$((cursor + 1))" >/dev/null
    if [ "$max" -gt 0 ] && [ "$served" -ge "$max" ]; then break; fi
  done
  echo "[agent-play] serve stopped (beats served: $served)"
  return 0
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
}

ap_stop() {
  ap_require_session
  # shellcheck source=lib_beat_driver.sh
  . "$ROOT/qa/lib_beat_driver.sh"
  STATE_DIR="$(ap_sget state_dir)"
  worldos_stream_tailer_kill_pidfile "$STATE_DIR" 2>/dev/null || true
  ap_sset stopped "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >/dev/null
  echo "[agent-play] stopped run=$RUN (artifacts kept in $RUN_DIR; engine + viewer untouched)"
  echo "[agent-play] a running \`serve\` for this run exits at its next poll (or on SIGTERM)"
}

case "$SUB" in
  start) ap_start ;;
  say) ap_say ;;
  serve) ap_serve ;;
  status) ap_status ;;
  stop) ap_stop ;;
  *) echo "[agent-play] unknown subcommand '$SUB'. Try --help." >&2; exit 2 ;;
esac
