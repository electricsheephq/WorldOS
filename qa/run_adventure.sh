#!/usr/bin/env bash
# ARC-DIRECTED WorldOS adventure eval (A-series A-T). A derivative of qa/run_duo.sh: two
# gateway-free `claude -p` sessions (a DM agent + a purposeful PLAYER agent) play against each
# other, mediated only by the shared engine state. The difference from run_duo is the SPINE:
#
#   * the world is NOT built by the DM cold-open — it is SEEDED once, deterministically, by
#     qa/seed_adventure_demo.py (the one-call Diablo-1 quest-loop fixture: camp <-> tavern
#     (Keeper Maera) <-> shop, camp <-> crypt (goblins) <-> throne hall (the Goblin Boss),
#     the 4-objective quest "The Crypt Below"). The campaign id is FIXED: adventure_demo_v1.
#   * the player pursues that ONE quest to COMPLETION (qa/play_player_adventure.txt) — speak to
#     Maera, travel to the crypt, clear the goblins, slay the boss, return for the reward.
#   * the run is arc-directed with a ~20-beat budget and a COMPLETION SHORT-CIRCUIT: after each
#     beat qa/quest_progress.py stamps the arc stages into <run>.quest_trace.json and reports the
#     quest status; the moment the quest leaves "active" the loop stops (a completed loop needs no
#     filler beats). qa/adventure_eval.py aggregates N such runs.
#
# Usage:   qa/run_adventure.sh <run-id> [beats] [budget] [player-persona]
#          qa/run_adventure.sh --help
#          qa/run_adventure.sh <run-id> --dry-run   # seed + wire + poll, NO claude (smoke path)
# Example: qa/run_adventure.sh adv1 20 4.00 qa/play_player_adventure.txt
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}
case "${1:-}" in
  -h|--help|help) usage; exit 0 ;;
  "") echo "[adventure] missing <run-id>. Try --help." >&2; exit 2 ;;
esac

# Shared beat-driver helpers (the SAME implementation run_duo/play use, so they can't drift).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"
# Shared adventure-DM primitives (hermetic env, brief, DM beat + retry, quest poll) — the SAME
# implementation qa/agent_play.sh uses, so the DM-only agent loop can never drift from this runner.
# shellcheck source=lib_adventure_dm.sh
. "$ROOT/qa/lib_adventure_dm.sh"

RUN="$1"; shift
BEATS="20"; BUDGET="4.00"; PLAYER_PROMPT_FILE="qa/play_player_adventure.txt"; DRY_RUN=0
# Positional [beats] [budget] [persona] with a --dry-run/-n flag accepted anywhere.
_pos=()
for a in "$@"; do
  case "$a" in
    -n|--dry-run) DRY_RUN=1 ;;
    *) _pos+=("$a") ;;
  esac
done
[ "${#_pos[@]}" -ge 1 ] && BEATS="${_pos[0]}"
[ "${#_pos[@]}" -ge 2 ] && BUDGET="${_pos[1]}"
[ "${#_pos[@]}" -ge 3 ] && PLAYER_PROMPT_FILE="${_pos[2]}"

CAMPAIGN_ID="adventure_demo_v1"   # FIXED by the seeder (its docstring guarantees this id)
SEEDER="$ROOT/qa/seed_adventure_demo.py"   # ABSOLUTE — the seeder's uv --directory contract
QUEST_TITLE="The Crypt Below"
EX_TEMPFAIL=75
CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# ── Root + IS_SANDBOX preflight (same real beat-0 blocker as run_duo) ───────────────────────────
if [ "$(id -u)" = "0" ] && [ -z "${IS_SANDBOX:-}" ]; then
  echo "[adventure] FATAL: running as root without IS_SANDBOX=1 — claude refuses --dangerously-skip-permissions as root." >&2
  echo "[adventure]        re-run as: IS_SANDBOX=1 bash qa/run_adventure.sh $RUN ..." >&2
  exit 2
fi

WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
WORLDOS_ACTOR_MODEL="$(worldos_env ACTOR_MODEL sonnet)"
SCORE_SCRIPT="$(worldos_env SCORE_SCRIPT qa/score.sh)"
ASSERT_BEHAVIORAL_SCRIPT="$(worldos_env ASSERT_BEHAVIORAL_SCRIPT qa/assert_behavioral.py)"
# Opus cold-open headroom floor (same rationale as run_duo — a low per-turn cap trips the DM grounding turn).
case "$WORLDOS_DM_MODEL" in
  *opus*) if awk "BEGIN{exit !($BUDGET < 4.0)}"; then echo "[adventure] opus: flooring per-turn budget \$$BUDGET -> \$4.00"; BUDGET=4.00; fi ;;
esac
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile

# Lean-per-beat context (default ON, byte-identical to run_duo).
WORLDOS_LEAN_BEATS="${WORLDOS_LEAN_BEATS:-1}"

# -- ARC MODE (2026-09-02, G2 lever) ------------------------------------------------------------
# This runner drives a PRE-SEEDED arc: the cast, the map and the foes already exist. Three directives
# the shared duo path injects every beat pull AGAINST that seed and were measured driving the three
# failed Opus-5 arc runs -- the SCENE-INTRO "named face who SPEAKS" mandate (which minted an invented
# grief-NPC on beat 0 in 3/3 runs), the duo brief's "new named faces enter and speak" obligation, and
# the MIDPOINT REVERSAL line the DM answered with an invented monster. ARC MODE suppresses/rewrites
# exactly those three. It is scoped to THIS runner: qa/run_duo.sh never sets it, so the duo runbook
# and the duo brief stay byte-identical.
export WORLDOS_ARC_MODE=1

# ── HERMETIC SESSIONS (#1656 root cause) ───────────────────────────────────────────────────────────
# Sets DUO_CFG / DUO_TOK / DUO_ENV / WORLDOS_LEAN_TAIL. The implementation (and the why) lives in
# qa/lib_adventure_dm.sh, shared with qa/agent_play.sh so the two runners cannot drift.
adv_dm_hermetic_env

# The transcripts dir is threaded via WORLDOS_TRANSCRIPTS_DIR (a REPO-RELATIVE path; default
# qa/transcripts) so the adventure_eval launcher (--transcripts-dir) and this runner can never
# desync on WHERE the per-run artifacts land. Keep it repo-relative: it is composed as $ROOT/$T
# below and used relative to the $ROOT cwd during scoring.
T="${WORLDOS_TRANSCRIPTS_DIR:-qa/transcripts}"; STATE_DIR="$ROOT/qa/state/$RUN"
CHECKPOINT="$STATE_DIR/.adv_checkpoint.json"
CHECKPOINT_SLOT="adv_checkpoint"
LOCKDIR="$STATE_DIR/.adv_run.lock"
TRACE="$ROOT/$T/$RUN.quest_trace.json"   # quest_progress writes stamps straight to the transcripts prefix
mkdir -p "$T" "$STATE_DIR"

RESUME_MODE=0
LAST_COMPLETED_BEAT=-1
START_BEAT=1
DSID=""
PSID=""

adv_release_lock() { rm -rf "$LOCKDIR" 2>/dev/null || true; }
adv_acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then printf '%s\n' "$$" > "$LOCKDIR/pid"; trap adv_release_lock EXIT; return 0; fi
  local oldpid=""; oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "[adventure] another run is using $STATE_DIR (pid $oldpid); use a different run id" >&2; exit 2
  fi
  rm -rf "$LOCKDIR" 2>/dev/null || true
  mkdir "$LOCKDIR" 2>/dev/null || { echo "[adventure] could not acquire lock $LOCKDIR" >&2; exit 2; }
  printf '%s\n' "$$" > "$LOCKDIR/pid"; trap adv_release_lock EXIT
}

# In-process engine checkpoint slot (mirrors run_duo's duo_engine_slot).
adv_engine_slot() {
  local action="$1"
  WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python - "$action" "$CAMPAIGN_ID" "$CHECKPOINT_SLOT" <<'PY' >/dev/null
import sys, server
action, campaign_id, slot = sys.argv[1], sys.argv[2], sys.argv[3]
if action == "save": server.save_slot(campaign_id, slot)
elif action == "load": server.load_slot(campaign_id, slot)
else: raise SystemExit(f"unknown slot action {action!r}")
PY
}

# ── fresh-run hygiene (item 9) ───────────────────────────────────────────────────────────────────
# A rerun of a COMPLETED run-id must NOT inherit the prior run's quest trace or result sidecars:
# quest_progress stamps are idempotent, so a stale <run>.quest_trace.json carrying a quest_completed
# stamp would make the aggregator read a FALSE completion, and stale gate/lens/summary sidecars would
# be re-aggregated as THIS run's. Truncate them on a FRESH run only — the RESUME path deliberately
# keeps the trace + checkpoint to continue the same run. ($COMBINED/$CHAT/$MOVES/$TOOLTIMING are
# truncated separately below; this covers the transcripts-prefix artifacts they don't.)
adv_clean_stale_artifacts() {
  rm -f \
    "$TRACE" \
    "$T/$RUN.gate.txt" \
    "$T/$RUN.score.json" "$T/$RUN.tolkien.json" "$T/$RUN.angrydm.json" \
    "$T/$RUN.adventure.json" "$T/$RUN.latency.json" "$T/$RUN.state.json" \
    "$T/$RUN.play.md" "$T/$RUN.md" \
    "$T/$RUN.quest.log" "$T/$RUN.quest.err" "$T/$RUN.seed.err" "$T/$RUN.seed_species.json" \
    "$T/$RUN.dm.err" "$T/$RUN.player.err" \
    2>/dev/null || true
  rm -f "$T/$RUN".dm.*.jsonl "$T/$RUN".player.*.jsonl 2>/dev/null || true
}

# ── seed the campaign (fresh run) ───────────────────────────────────────────────────────────────
adv_seed() {
  rm -rf "$STATE_DIR/campaigns" 2>/dev/null
  local out cid
  out="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory "$ROOT/servers/engine" python "$SEEDER" "$STATE_DIR" 2>"$T/$RUN.seed.err")"
  cid="$(printf '%s\n' "$out" | tail -n1 | tr -d '[:space:]')"
  if [ "$cid" != "$CAMPAIGN_ID" ]; then
    echo "[adventure] FATAL: seed did not yield campaign id '$CAMPAIGN_ID' (got '$cid'). See $T/$RUN.seed.err" >&2
    cat "$T/$RUN.seed.err" >&2 || true
    exit 1
  fi
  echo "[adventure] seeded $CAMPAIGN_ID into $STATE_DIR ($(printf '%s' "$out" | tail -n1))"
  adv_write_seed_species
}

# The SEEDED bestiary species, read off the FRESH seed snapshot (before any DM beat can add to it)
# and written to <run>.seed_species.json. The arc behavioral gate compares every DM spawn_monster
# against THIS list, so the allowed cast is derived from the seed rather than hard-coded in the gate:
# re-seed with different foes and the gate follows. `creature_slug` is the engine's own stable TYPE
# key ("Goblin Warrior" -> goblin-warrior), which is also what spawn_monster's canonical result name
# slugifies to -- so the comparison is exact, not a name-substring guess.
SEED_SPECIES="$ROOT/$T/$RUN.seed_species.json"
adv_write_seed_species() {
  # Unlink FIRST: a rerun of the same run-id whose generation then fails must not leave the PREVIOUS
  # run's manifest on disk — the gate would read a stale species list and return a false verdict
  # while the warning below claims the spawn rule stood down.
  rm -f "$SEED_SPECIES" 2>/dev/null || true
  python3 - "$STATE_DIR" "$SEED_SPECIES" <<'SEEDPY' || echo "[adventure] WARN: could not write the seed-species manifest; the arc spawn gate stands down" >&2
import glob, json, re, sys
state_dir, out = sys.argv[1:3]
species = {}
for sp in glob.glob(f"{state_dir}/campaigns/*/snapshot.json"):
    try:
        d = json.loads(open(sp, encoding="utf-8").read())
    except Exception:
        continue
    for ch in (d.get("characters") or {}).values():
        if not isinstance(ch, dict) or ch.get("kind") != "monster":
            continue
        name = str(ch.get("name") or "")
        slug = str(ch.get("creature_slug") or "") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug:
            species.setdefault(slug, name)
if not species:
    raise SystemExit("no seeded monsters found in the snapshot")
json.dump({"species": sorted(species), "names": [species[s] for s in sorted(species)]},
          open(out, "w", encoding="utf-8"), indent=2)
print(f"[adventure] seeded species: {', '.join(sorted(species))}")
SEEDPY
}

# ── quest telemetry + completion short-circuit (between beats) ───────────────────────────────────
# adv_quest_poll / adv_telemetry_note_fail now live in qa/lib_adventure_dm.sh (shared with
# qa/agent_play.sh); this run process still owns its own fail counter.
ADV_TELEMETRY_FAIL_FILE="$STATE_DIR/.telemetry_fails"
rm -f "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || true   # each run process counts its own telemetry fails

adv_acquire_lock

# Resume support (the checkpoint binds the live seeded campaign via the engine slot).
if [ -s "$CHECKPOINT" ] && jq -e . "$CHECKPOINT" >/dev/null 2>&1; then
  ck_sha="$(jq -r '.sha // ""' "$CHECKPOINT")"
  ck_beats="$(jq -r '.total_beats // ""' "$CHECKPOINT")"
  ck_persona="$(jq -r '.persona // ""' "$CHECKPOINT")"
  if [ "$ck_sha" = "$CURRENT_SHA" ] && [ "$ck_beats" = "$BEATS" ] && [ "$ck_persona" = "$PLAYER_PROMPT_FILE" ]; then
    RESUME_MODE=1
    LAST_COMPLETED_BEAT="$(jq -r '.last_completed_beat' "$CHECKPOINT")"
    PSID="$(jq -r '.player_session_id' "$CHECKPOINT")"
    DSID="$(jq -r '.dm_session_id' "$CHECKPOINT")"
    START_BEAT=$((LAST_COMPLETED_BEAT + 1))
    echo "[adventure] resuming: last_completed=$LAST_COMPLETED_BEAT next=$START_BEAT"
  else
    echo "[adventure] checkpoint mismatch (sha/beats/persona differ from this invocation). The run" >&2
    echo "[adventure]   dir + lock are SAFE to reuse — the lock auto-releases on exit (trap), so no" >&2
    echo "[adventure]   cleanup is needed. To restart this run-id from scratch, delete the checkpoint:" >&2
    echo "[adventure]   rm -f $CHECKPOINT" >&2
    exit 2
  fi
fi

if [ "$RESUME_MODE" != "1" ]; then
  adv_clean_stale_artifacts   # fresh run: a rerun of a completed run-id must not inherit stale state
  adv_seed
fi

worldos_isolate_claude_auth

# DM gets the engine (state dir patched in); the player gets the constrained move facade.
DM_CFG="$STATE_DIR/dm.mcp.json"; PLAYER_CFG="$STATE_DIR/player.mcp.json"
MOVES="$STATE_DIR/player_moves.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$MOVES"
TOOLTIMING_PATH="$ROOT/$T/$RUN.tooltiming.jsonl"
[ "$RESUME_MODE" = "1" ] || : > "$TOOLTIMING_PATH"
adv_dm_write_mcp_config "$ROOT" "$STATE_DIR" "$DM_CFG" "$TOOLTIMING_PATH"
python3 - "$ROOT" "$STATE_DIR" "$MOVES" "$PLAYER_CFG" <<'PY'
import json, sys
root, state, moves, out = sys.argv[1:5]
json.dump({"mcpServers": {"worldos-player": {"command": "uv",
  "args": ["run", "--directory", f"{root}/servers/engine", "python", "player_server.py"],
  "env": {"WORLDOS_STATE_DIR": state, "WORLDOS_PLAYER_MOVES": moves}}}}, open(out, "w"))
PY

if [ "$RESUME_MODE" = "1" ]; then
  if ! adv_engine_slot load; then
    echo "[adventure] could not restore campaign $CAMPAIGN_ID from slot $CHECKPOINT_SLOT; delete $CHECKPOINT to restart" >&2; exit 2
  fi
else
  DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
fi

# The DM brief = the shared duo brief + a short ARC ADDENDUM (the ONLY brief-plumbing run_duo
# exposes is `cat qa/play_dm_duo.txt`; we CONCATENATE the addendum, we do not fork the brief).
# Both live in qa/lib_adventure_dm.sh (shared with qa/agent_play.sh), including the ARC MODE
# rewrite of the duo brief's "new named faces enter and speak" obligation.
DM_BRIEF="$(adv_dm_brief "$CAMPAIGN_ID" "$QUEST_TITLE")" || exit 2
PLAYER_BRIEF="$(cat "$PLAYER_PROMPT_FILE")"

COMBINED="$T/$RUN.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$COMBINED"
CHAT="$T/$RUN.chat.jsonl"; [ "$RESUME_MODE" = "1" ] || : > "$CHAT"

echo "[adventure] run=$RUN campaign=$CAMPAIGN_ID beats=$BEATS budget=\$$BUDGET persona=$PLAYER_PROMPT_FILE dm=$WORLDOS_DM_MODEL"

# ── DRY-RUN SMOKE PATH: seed + wire + one telemetry poll, NO claude ─────────────────────────────
# The smoke-check the CLAUDE.md test policy allows: prove the seed + MCP-config + quest-telemetry
# wiring end-to-end WITHOUT spending any LLM budget. Runs the real engine (local-allowed) only.
if [ "$DRY_RUN" = "1" ]; then
  echo "[adventure] DRY RUN — seeded + configs built; polling quest telemetry once (no claude)…"
  status="$(adv_quest_poll 0)"; poll_rc=$?
  # Item 18: a smoke check must FAIL LOUD when the telemetry contract is violated — otherwise a
  # broken poll ships as "telemetry OK". Report FAILED + exit nonzero instead of the OK banner.
  if [ "$poll_rc" -ne 0 ]; then
    echo "[adventure] DRY-RUN FAILED: quest telemetry violated its contract (see $T/$RUN.quest.err)" >&2
    exit 1
  fi
  echo "[adventure] dry-run quest status: ${status:-<unknown>}  (trace: $TRACE)"
  [ -s "$DM_CFG" ] && echo "[adventure] dry-run OK: dm.mcp.json + player.mcp.json written"
  echo "[adventure] dry-run plan: intro -> DM ground -> $BEATS beats (short-circuit on quest!=active) -> score"
  exit 0
fi

# P0: the player introduces their character with a SINGLE say() (a tagged move — the behavioral gate
# requires structured player turns). They do NOT act yet.
turn() {  # $1=role $2=sid $3=first? $4=msg ; echoes the reply text (mirrors run_duo's turn)
  local role="$1" sid="$2" first="$3" msg="$4" resume=()
  if [ "$role" = "dm" ]; then
    adv_dm_turn "$sid" "$first" "$msg"
  else
    [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
    # The player's result envelope is TEE'd before jq: it carries the CONCRETE model the API served,
    # which qa/model_provenance.py reads back (the `sonnet` alias drifts — recording only the alias is
    # what made the 2026-09-02 model swap invisible). jq still consumes the same bytes on stdout.
    "${DUO_ENV[@]}" claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
      --model "$WORLDOS_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format json 2>> "$T/$RUN.player.err" \
      | tee "$T/$RUN.player.$(date +%s%N).jsonl" | jq -r '.result // ""' 2>/dev/null
  fi
}

turn_retry() {  # transient-aware empty-output retry (DM-only; every caller passes role "dm")
  adv_dm_turn_retry "$2" "$3" "$4"
}

MCURSOR_FILE="$STATE_DIR/.mcursor"
if [ "$RESUME_MODE" = "1" ]; then
  _mc="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; echo "${_mc:-0}" > "$MCURSOR_FILE"
else echo 0 > "$MCURSOR_FILE"; fi
player_move() {  # $1=first $2=prompt ; relays ONLY the structured moves made this turn
  local first="$1" prompt="$2" cur total new
  turn player "$PSID" "$first" "$prompt" >/dev/null
  cur=$(cat "$MCURSOR_FILE" 2>/dev/null || echo 0); cur=${cur:-0}
  total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  if [ "$total" -le "$cur" ]; then
    turn player "$PSID" 0 "You didn't act. Take your action THROUGH YOUR TOOLS now — say / do / request_check / attack / cast_spell / use_item. Tools only, no prose." >/dev/null
    total=$(wc -l < "$MOVES" 2>/dev/null | tr -d ' '); total=${total:-0}
  fi
  new="$(tail -n +"$((cur + 1))" "$MOVES" 2>/dev/null)"; echo "$total" > "$MCURSOR_FILE"
  [ -n "$new" ] && printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text)") | join("  ")' 2>/dev/null
}

DMSG=""
if [ "$RESUME_MODE" != "1" ]; then
  PLAYER_INTRO_PROMPT="$PLAYER_BRIEF

This is the very start — you are already in the party at the camp above the crypt. Introduce Aidan with a SINGLE say(\"…\"): who you are and that you mean to take the crypt job. Do NOT travel/attack yet — the DM opens the scene next. One say(), nothing else."
  WORLDOS_PLAYER_MAX_ATTEMPTS="${WORLDOS_PLAYER_MAX_ATTEMPTS:-3}"
  PMSG=""; _pa=1
  while [ -z "$PMSG" ] && [ "$_pa" -le "$WORLDOS_PLAYER_MAX_ATTEMPTS" ]; do
    [ "$_pa" -gt 1 ] && echo "[adventure] player produced no intro — retry $_pa/${WORLDOS_PLAYER_MAX_ATTEMPTS}…" >&2
    PMSG="$(player_move 1 "$PLAYER_INTRO_PROMPT")"; _pa=$((_pa + 1))
  done
  [ -z "$PMSG" ] && { echo "[adventure] player produced no intro — aborting" >&2; exit 1; }
  echo "[adventure] player intro: ${PMSG:0:120}…"
  chatlog player "$PMSG"

  SETUP_DIRECTIVE="GROUND on the pre-seeded campaign now: get_state(\"$CAMPAIGN_ID\"), look_around(\"$CAMPAIGN_ID\"), get_quests(\"$CAMPAIGN_ID\"). Do NOT build or reset the world — it already exists. OPEN the scene at the camp clearing around Aidan and the quest \"$QUEST_TITLE\": set the tavern door (Keeper Maera) and the crypt door in front of the player, and hand the first live moment back to them (where do they go first — the tavern to hear Maera out, or straight for the crypt?)."
  DMSG="$(turn_retry dm "$DSID" 1 "$DM_BRIEF

The player agent introduces their character and intent:

$PMSG

$SETUP_DIRECTIVE OUTPUT DISCIPLINE — your final reply IS the opening scene: 2nd-person in-fiction PROSE + quoted dialogue ONLY. Never narrate your own setup/process.")"
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  if [ -z "$DMSG" ]; then worldos_chatlog_dm_failed; echo "[adventure] DM produced no opening — aborting" >&2; exit 1; fi
  echo "[adventure] DM opened: ${DMSG:0:120}…"
  worldos_chatlog_dm "$DMSG"
  adv_quest_poll 0 >/dev/null   # stamp the baseline (beat 0)
else
  DMSG="$(python3 - "$CHAT" <<'PY'
import json,sys
last=""
for line in open(sys.argv[1]).read().splitlines():
    try: row=json.loads(line)
    except ValueError: continue
    if row.get("role")=="dm" and row.get("text") and not row.get("beat_failed"): last=row["text"]
print(last)
PY
)"
fi

# ── the arc-directed beat loop (with the completion short-circuit) ───────────────────────────────
adv_write_checkpoint() {
  local beat="$1"
  adv_engine_slot save || { echo "[adventure] checkpoint slot save failed" >&2; return 1; }
  python3 - "$CHECKPOINT" "$beat" "$PSID" "$DSID" "$CAMPAIGN_ID" "$PLAYER_PROMPT_FILE" "$BEATS" "$BUDGET" "$CURRENT_SHA" <<'PY'
import json,sys
from pathlib import Path
path,beat,psid,dsid,cid,persona,beats,budget,sha=sys.argv[1:]
p=Path(path); tmp=p.with_suffix(p.suffix+".tmp")
tmp.write_text(json.dumps({"last_completed_beat":int(beat),"player_session_id":psid,"dm_session_id":dsid,
  "campaign_id":cid,"persona":persona,"total_beats":int(beats),"budget":budget,"sha":sha},indent=2)+"\n")
import os; os.replace(tmp,p)
PY
  LAST_COMPLETED_BEAT="$beat"; START_BEAT=$((beat + 1))
}

COMPLETED_STATUS="active"
if [ "$START_BEAT" -le "$BEATS" ]; then
for b in $(seq "$START_BEAT" "$BEATS"); do
  PROG_PRE="$(worldos_read_progress "$STATE_DIR")"
  PREV_DAY="$(printf '%s' "$PROG_PRE" | cut -f1)"; PREV_DAY="${PREV_DAY:-1}"
  PREV_TOD="$(printf '%s' "$PROG_PRE" | cut -f2)"; PREV_TOD="${PREV_TOD:-morning}"
  PREV_LOC="$(printf '%s' "$PROG_PRE" | cut -f5)"

  # ARC BEAT MARKER: stamp the beat number into the combined DM stream BEFORE this beat's events are
  # appended, so the arc behavioral gate can attribute an offending tool call to its BEAT rather than
  # guessing from stream position (retries append extra `system/init` events, so init-counting lies).
  # An unknown event `type` is ignored by every existing consumer (distill.py dispatches on type and
  # drops the rest; assert_behavioral's _tally/_tool_events read message.content; model_provenance
  # regex-scans for a "model" field) — so this is invisible to the transcript, the play log and the
  # scorers.
  printf '{"type":"worldos_arc_beat","beat":%s}\n' "$b" >> "$COMBINED"

  PMSG="$(player_move 0 "The DM says:

$DMSG

Take your next action(s) toward the mission using your tools — say / do / request_check / attack / cast_spell / use_item (look or my_sheet first if useful). Push toward the NEXT objective. Tools only.")"
  echo "[adventure] beat $b player: ${PMSG:0:100}…"
  [ -z "$PMSG" ] && { echo "[adventure] player went silent at beat $b; stopping early"; break; }
  chatlog player "$PMSG"

  RUNBOOK="$(worldos_runbook_for_beat "$b" "$BEATS" "$PREV_LOC" "$STATE_DIR")"
  DIRECTOR="$(worldos_director_advisory "$ROOT" "$STATE_DIR")"
  EVENT_ADV="$(worldos_event_advisory "$ROOT" "$STATE_DIR")"
  DMSG="$(turn_retry dm "$DSID" 0 "The player does:

$PMSG

Resolve it through the engine (roll/attack/travel as needed), then PLAY the next beat as a full lived scene — any NPC or companion in the scene SPEAKS at least one quoted line; weave the open moment back to the player. Mark quest objectives with complete_objective as they land, and run real combat in the crypt/throne hall.

$RUNBOOK

$DIRECTOR

$EVENT_ADV")"
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  echo "[adventure] beat $b DM: ${DMSG:0:100}…"
  if [ -z "$DMSG" ]; then worldos_chatlog_dm_failed; echo "[adventure] DM went silent at beat $b; stopping early"; break; fi
  worldos_chatlog_dm "$DMSG"

  worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD"
  adv_write_checkpoint "$b"

  # Quest telemetry + COMPLETION SHORT-CIRCUIT: stamp this beat, then stop if the quest left "active".
  COMPLETED_STATUS="$(adv_quest_poll "$b")"
  echo "[adventure] beat $b quest status: ${COMPLETED_STATUS:-active}"
  case "${COMPLETED_STATUS:-active}" in
    active|""|None) : ;;
    *) echo "[adventure] quest '$QUEST_TITLE' resolved ($COMPLETED_STATUS) at beat $b — short-circuiting."; break ;;
  esac
done
fi

# ── wrap + score (same 3-lens + behavioral + latency tail as run_duo) ───────────────────────────
turn dm "$DSID" 0 "We are out of time. Bring this to a clean stop and call end_session with a one-line summary." >/dev/null
echo "[adventure] distilling + scoring…"
python3 qa/distill.py "$COMBINED" 2>/dev/null
PLAY="$T/$RUN.play.md"
jq -rs 'map((.role|ascii_upcase) + ": " + (.text // "")) | join("\n\n")' "$CHAT" > "$PLAY" 2>/dev/null
[ -s "$PLAY" ] || cp "$T/$RUN.md" "$PLAY" 2>/dev/null
SNAP="$(find "$STATE_DIR/campaigns" -mindepth 2 -maxdepth 2 -name snapshot.json -size +1c -exec ls -S {} + 2>/dev/null | head -1)"
if [ -n "$SNAP" ]; then cp "$SNAP" "$T/$RUN.state.json"; else echo '{"warning":"no state"}' > "$T/$RUN.state.json"; fi
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric.md qa/score_schema.json "$T/$RUN.score.json" 1.50 &
[ -s "$PLAY" ] && "$SCORE_SCRIPT" "$PLAY" "$T/$RUN.state.json" qa/rubric_tolkien.md qa/score_schema_tolkien.json "$T/$RUN.tolkien.json" 1.50 &
wait
[ -f "$T/$RUN.md" ] && "$SCORE_SCRIPT" "$T/$RUN.md" "$T/$RUN.state.json" qa/rubric_angry_dm.md qa/score_schema_angry_dm.json "$T/$RUN.angrydm.json" 1.50

# ── scorer-sentinel guard (item 10; mirrors run_duo's Fix F / #1404) ─────────────────────────────
# score.sh fails FAST on a 429 (writes {"quota_exhausted":true,…}) or an expired/invalid credential
# (writes {"error":"scorer_auth_expired",…}) instead of a real scorecard. A run scored on top of an
# INFRA fault is NOT a clean product measurement — flag it CONTAMINATED (a contaminated summary +
# nonzero EX_TEMPFAIL exit) so adventure_eval excludes it and never persists it as a scored run,
# rather than aggregating a quota/auth corpse as clean.
adv_write_contaminated_summary() {  # $1 = reason
  python3 - "$T/$RUN.adventure.json" "$RUN" "$LAST_COMPLETED_BEAT" "$1" <<'PY'
import json,sys
from pathlib import Path
out,run,last_beat,reason = sys.argv[1:5]
Path(out).write_text(json.dumps({"run":run,"contaminated":True,"contaminated_reason":reason,
  "behavioral":"CONTAMINATED","completed":False,
  "last_completed_beat":int(last_beat) if str(last_beat).lstrip("-").isdigit() else None},indent=2)+"\n")
PY
}
for _scf in "$T/$RUN.tolkien.json" "$T/$RUN.score.json" "$T/$RUN.angrydm.json"; do
  [ -f "$_scf" ] || continue
  if jq -e '.quota_exhausted == true' "$_scf" >/dev/null 2>&1; then
    echo "[adventure] SCORER QUOTA ABORT — $(basename "$_scf") is a 429 quota sentinel, not a scorecard. Marking this run infra-CONTAMINATED (skipping gate/scoring aggregation)." >&2
    adv_write_contaminated_summary "SCORER QUOTA ABORT (HTTP 429) on $(basename "$_scf")"
    rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
    exit "$EX_TEMPFAIL"
  fi
  if jq -e '.error == "scorer_auth_expired"' "$_scf" >/dev/null 2>&1; then
    echo "[adventure] SCORER AUTH ABORT — $(basename "$_scf") is an expired/invalid-credential sentinel, not a scorecard. Marking this run infra-CONTAMINATED." >&2
    adv_write_contaminated_summary "SCORER AUTH ABORT (scorer_auth_expired) on $(basename "$_scf")"
    rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
    exit "$EX_TEMPFAIL"
  fi
done

# WORLDOS_GATE_ARC turns on the seeded-arc lens (the addendum's five rules as hard FAIL rows);
# WORLDOS_ARC_SEED_SPECIES hands it the species this run was actually SEEDED with.
WORLDOS_GATE_ARC=1 WORLDOS_ARC_SEED_SPECIES="$SEED_SPECIES" \
  python3 "$ASSERT_BEHAVIORAL_SCRIPT" "$COMBINED" "$T/$RUN.state.json" "$T/$RUN.chat.jsonl" "$MOVES" | tee "$T/$RUN.gate.txt"; GATE=${PIPESTATUS[0]}
# ── honest scoring on a RED gate (item 11; mirrors run_duo) ──────────────────────────────────────
# A structurally broken (gate-RED) run must not persist high lens medians — CAP the three lens files
# to <= 2.5 / INVALID via the SHARED worldos_cap_score_red helper (qa/lib_beat_driver.sh), annotated
# with the failed checks, so a dead scene can't masquerade as prestige play in the aggregate.
if [ "${GATE:-0}" != "0" ]; then
  GATE_REASON="$(grep -E '^\s*\[(FAIL)\]' "$T/$RUN.gate.txt" 2>/dev/null | sed 's/^[[:space:]]*//' | paste -sd'; ' - 2>/dev/null)"
  GATE_REASON="${GATE_REASON:-behavioral gate RED}"
  worldos_cap_score_red "$T/$RUN.tolkien.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.score.json" "$GATE_REASON" story
  worldos_cap_score_red "$T/$RUN.angrydm.json" "$GATE_REASON"
fi
LATENCY_JSON="$T/$RUN.latency.json"
python3 qa/latency_rollup.py --dir "$T" --run "$RUN" --tooltiming "$TOOLTIMING_PATH" --out "$LATENCY_JSON" >/dev/null 2>&1 || true

# ── the per-run adventure summary (self-describing; adventure_eval also falls back to raw files) ─
# Carries the requested DM/actor MODELS (item 6 provenance), the CONCRETE model ids resolved from the
# run's own transcripts (dm_model_resolved / player_model_resolved — the alias `opus`/`sonnet` drifts,
# and only the resolved id says which model produced this row), + session_beats (item 4 engagement) so
# the aggregator reads provenance/beats from the summary rather than guessing. Completion + behavioral use
# the SAME honest semantics as adventure_eval (items 5 + 17): only a "completed" terminal status is a
# completion; GREEN requires the assert_behavioral success marker, not the mere absence of [FAIL].
python3 - "$T/$RUN.adventure.json" "$TRACE" "$T/$RUN.gate.txt" "$RUN" "$LAST_COMPLETED_BEAT" "$LATENCY_JSON" "$WORLDOS_DM_MODEL" "$WORLDOS_ACTOR_MODEL" "$ROOT/qa" "$T/$RUN" <<'PY'
import json,sys
from pathlib import Path
out,trace,gate,run,last_beat,lat,dm_model,actor_model,qa_dir,prefix = sys.argv[1:11]
sys.path.insert(0, qa_dir)
try:
    from model_provenance import resolve_models   # concrete ids from this run's own transcripts
    resolved = resolve_models(prefix)
except Exception:
    resolved = {"dm_model_resolved": None, "player_model_resolved": None}
def rj(p):
    try: return json.loads(Path(p).read_text())
    except Exception: return {}
tr=rj(trace); stamps=tr.get("stamps") or []
completed_stamp=next((s for s in stamps if s.get("stage")=="quest_completed"),None)
# Terminal status: prefer the completed stamp's recorded status:<x> signal, else the live status.
status=""
if completed_stamp:
    sig=str(completed_stamp.get("signal") or "")
    if sig.startswith("status:"): status=sig.split(":",1)[1].strip().lower()
if not status: status=str(tr.get("quest_status") or "active").strip().lower()
completed=(status=="completed")   # completion HONESTY — a terminal "failed"/other is NOT a completion
btc=completed_stamp.get("beat") if (completed and completed_stamp) else None
gate_txt=""
try: gate_txt=Path(gate).read_text()
except Exception: pass
def _green_marker(txt):
    return any(l.strip()=="GREEN" or l.strip().startswith("GREEN (") for l in txt.splitlines())
if "[FAIL]" in gate_txt:
    behavioral="RED"
elif "=== behavioral assertions ===" in gate_txt and _green_marker(gate_txt):
    behavioral="GREEN"
else:
    behavioral=None   # empty/truncated/malformed gate — never an assumed GREEN
dead=rj(lat).get("failed_beats")
lb=int(last_beat) if str(last_beat).lstrip("-").isdigit() else None
session_beats=lb if (lb is not None and lb>=0) else None
Path(out).write_text(json.dumps({"run":run,"campaign_id":tr.get("campaign_id"),"quest_status":tr.get("quest_status"),
  "completed":bool(completed),"beats_to_complete":btc,"last_completed_beat":lb,"session_beats":session_beats,
  "dm_model":dm_model or None,"actor_model":actor_model or None,
  "dm_model_resolved":resolved.get("dm_model_resolved"),"player_model_resolved":resolved.get("player_model_resolved"),
  "dead_beats":dead,"behavioral":behavioral,"stages_reached":[s.get("stage") for s in stamps]},indent=2)+"\n")
print(f"[adventure] summary: completed={completed} beats_to_complete={btc} behavioral={behavioral} "
      f"dm={dm_model}({resolved.get('dm_model_resolved') or 'unresolved'}) "
      f"actor={actor_model}({resolved.get('player_model_resolved') or 'unresolved'})")
PY

# Item 16: surface a run-level telemetry-health summary if any quest poll failed its contract.
ADV_TELEMETRY_FAILS="$(cat "$ADV_TELEMETRY_FAIL_FILE" 2>/dev/null || echo 0)"; ADV_TELEMETRY_FAILS="${ADV_TELEMETRY_FAILS:-0}"
if [ "$ADV_TELEMETRY_FAILS" -gt 0 ]; then
  echo "[adventure] WARN: quest telemetry failed its contract on $ADV_TELEMETRY_FAILS poll(s) this run — stage stamps / the completion signal may be incomplete (see $T/$RUN.quest.err)." >&2
fi

echo "[adventure] done. run=$RUN behavioral=$([ "${GATE:-0}" = 0 ] && echo GREEN || echo RED) quest=${COMPLETED_STATUS:-active} trace=$TRACE"
rm -f "$CHECKPOINT" "$CHECKPOINT.tmp" 2>/dev/null || true
exit "${GATE:-0}"
