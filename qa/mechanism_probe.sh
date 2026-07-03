#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────────────────────
# WorldOS TIER 1.5 — the MECHANISM PROBE.  ⚠ ITERATION SIGNAL ONLY — never cite as release
# evidence (the seeded state SKIPS the cold-open / seat-path / free-play surfaces where our real
# bugs live; see docs/qa/FAST_GATE.md "the trap"). This answers ONE cheap question — "does the
# DM ACT on obligation cue X, or only narrate it?" — from a SEEDED trigger state + a few live DM
# beats (~$1, ~10 min), instead of the 24-beat Opus duo (~$5–8, ~70 min) it used to take to reach
# that state naturally. It sits BETWEEN Tier 0 (qa/fast_gate.sh, deterministic, $0) and the full
# duos (qa/run_duo.sh): cheaper than a duo, richer than a pure-engine test (it drives a REAL DM).
# ─────────────────────────────────────────────────────────────────────────────────────────────
#
# Usage: qa/mechanism_probe.sh <probe-name> <fixture> [beats=3] [budget=1.00]
#   <probe-name>  a run label (goes into the transcript filenames + the scores_db row_id)
#   <fixture>     a builder in qa/probe_fixtures/<fixture>.py (deterministic; engine=sole writer)
#   [beats]       how many REAL DM beats to drive against the seeded state (default 3)
#   [budget]      per-DM-turn --max-budget-usd cap (default 1.00; a CAP, not a spend)
# Example: qa/mechanism_probe.sh wrap1 wrap_window_active_quest 3 1.00
#
# The DM model is WORLDOS_DM_MODEL (default opus; GLM works via qa/glm_profile.sh, exactly like
# run_duo.sh). The verdict (qa/probe_verdict.py) is DETERMINISTIC — no LLM lens: it reads the DM
# tool tally out of the transcript + the engine snapshot before/after, and reports
# {cue_present_each_beat, dm_engagement_tools_called, quest_resolved_or_progressed, verdict}.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 2
# Source (NOT fork) the SAME shared beat-driver + GLM profile qa/run_duo.sh uses, so the probe's DM
# beat can't drift from the duo harness's (worldos_env / worldos_isolate_claude_auth / the DM
# timeout tier / worldos_dm_final_text / worldos_resolve_dm_reply all come from here).
# shellcheck source=lib_beat_driver.sh
. "$ROOT/qa/lib_beat_driver.sh"

PROBE="${1:-probe-$(date +%H%M%S)}"
FIXTURE="${2:-}"
BEATS="${3:-3}"
BUDGET="${4:-1.00}"
if [ -z "$FIXTURE" ]; then
  echo "usage: qa/mechanism_probe.sh <probe-name> <fixture> [beats=3] [budget=1.00]" >&2
  exit 2
fi
FIXTURE_PY="$ROOT/qa/probe_fixtures/$FIXTURE.py"
if [ ! -f "$FIXTURE_PY" ]; then
  echo "[probe] FATAL: no fixture builder at $FIXTURE_PY" >&2
  exit 2
fi

# ── Root + IS_SANDBOX preflight (same real beat-0 blocker run_duo.sh guards) ───────────────────
if [ "$(id -u)" = "0" ] && [ -z "${IS_SANDBOX:-}" ]; then
  echo "[probe] FATAL: running as root without IS_SANDBOX=1 — claude refuses --dangerously-skip-permissions as root." >&2
  echo "[probe]        re-run as: IS_SANDBOX=1 bash qa/mechanism_probe.sh $*" >&2
  exit 2
fi

WORLDOS_DM_MODEL="$(worldos_env DM_MODEL opus)"
# Floor the per-turn cap for an Opus DM so a heavier beat is never budget-tripped (mirrors run_duo.sh's
# opus floor, scaled to the probe's lighter default — the probe never does a cold-open world-build).
case "$WORLDOS_DM_MODEL" in
  *opus*) if awk "BEGIN{exit !($BUDGET < 1.5)}"; then echo "[probe] opus: flooring per-turn budget \$$BUDGET -> \$1.50 (headroom)"; BUDGET=1.50; fi ;;
esac
# GLM-only settings profile — a TOTAL no-op on a Claude run (byte-identical), raises timeouts/retries
# on a GLM run. Sourced + applied exactly as run_duo.sh does.
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile

T="qa/transcripts"; STATE_DIR="$ROOT/qa/state/$PROBE"
mkdir -p "$T" "$STATE_DIR"; rm -rf "$STATE_DIR/campaigns" 2>/dev/null
# Keep the DM `claude -p` off the keychain / off any /Volumes TCC prompt (gated no-op without a
# file/env credential — the same isolation run_duo.sh applies).
worldos_isolate_claude_auth

# ── 1. Seed the deterministic fixture (engine = sole writer) + run its FREE pre-check ──────────
echo "[probe] seeding fixture '$FIXTURE' into $STATE_DIR …"
MANIFEST="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory servers/engine python "$FIXTURE_PY" "$STATE_DIR" 2>"$T/$PROBE.seed.err" | tail -n1)"
if [ -z "$MANIFEST" ] || ! printf '%s' "$MANIFEST" | jq -e . >/dev/null 2>&1; then
  echo "[probe] FATAL: fixture builder produced no JSON manifest (its pre-check may have failed):" >&2
  tail -n 20 "$T/$PROBE.seed.err" >&2 || true
  exit 1
fi
CAMPAIGN_ID="$(printf '%s' "$MANIFEST" | jq -r '.campaign_id')"
CUE="$(printf '%s' "$MANIFEST" | jq -r '.cue')"
echo "[probe] fixture pre-check OK — campaign=$CAMPAIGN_ID cue=$CUE next_action=$(printf '%s' "$MANIFEST" | jq -r '.next_action')"

# Snapshot the seeded engine state BEFORE any DM beat (the verdict's ground-truth baseline).
SNAP_SRC="$STATE_DIR/campaigns/$CAMPAIGN_ID/snapshot.json"
STATE_BEFORE="$T/$PROBE.state_before.json"
if [ -f "$SNAP_SRC" ]; then cp "$SNAP_SRC" "$STATE_BEFORE"; else echo '{"warning":"no state"}' > "$STATE_BEFORE"; fi

# ── 2. Build the DM MCP config (engine/rules/voice), state dir patched in + servers re-rooted ──
# IDENTICAL re-rooting to qa/run_duo.sh: point every server's --directory at THIS repo so the DM
# engine runs the SAME code as the fixture writer (a version-skew else fails every DM tool call).
DM_CFG="$STATE_DIR/dm.mcp.json"
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" "$ROOT" <<'PY'
import json, os, sys
cfg_path, state, out, root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.load(open(cfg_path))
for name, srv in cfg.get("mcpServers", {}).items():
    args = srv.get("args", [])
    if "--directory" in args:
        i = args.index("--directory")
        raw = args[i + 1].rstrip("/")
        if raw.startswith("./"):
            raw = raw[2:]
        if "/servers/" in raw:
            pkg = raw.rsplit("/servers/", 1)[1]
        elif raw.startswith("servers/"):
            pkg = raw[len("servers/"):]
        else:
            pkg = raw
        args[i + 1] = f"{root}/servers/{pkg}"
    if name == "worldos-engine":
        srv.setdefault("env", {})["WORLDOS_STATE_DIR"] = state
        # Pin the engine tools (un-defer) so the DM doesn't burn ToolSearch round-trips re-finding them.
        if os.environ.get("WORLDOS_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
PY

DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
COMBINED="$T/$PROBE.jsonl"; : > "$COMBINED"
DM_BRIEF="$(cat qa/play_dm_duo.txt)"

# ── The DM beat: a MINIMAL turn that composes the SHARED lib helpers (never forks run_duo's turn).
# $1=session-id $2=first?(1/0) $3=message ; echoes the DM's resolved reply text.
probe_dm_turn() {
  local sid="$1" first="$2" msg="$3" out rc=0 resume=() beat_timeout
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  worldos_dm_effort_arg "$first"                 # --effort tier (shared): medium on continuing beats
  beat_timeout="$(worldos_dm_timeout "$first")"  # model-aware DM deadline (shared)
  out="$T/$PROBE.dm.$(date +%s%N).jsonl"
  worldos_timeout "$beat_timeout" \
    claude -p "$msg" ${resume[@]+"${resume[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} --permission-mode bypassPermissions \
      --max-budget-usd "$BUDGET" --output-format stream-json --verbose > "$out" 2>> "$T/$PROBE.dm.err"
  rc=$?
  cat "$out" >> "$COMBINED"
  # Shared final-text resolver (persists .dm_last_result/.dm_last_rc; empty on an error-class result).
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# ── 3. Drive BEATS real DM beats. Re-check the seeded cue at the START of each beat (engine's own
#       _compute_beat_obligations / _next_action, via qa/probe_verdict.py cue-check) so a vanished
#       cue is caught (→ CUE_ABSENT). The DM prompt names the seeded cue's imperative so the probe
#       actually EXERCISES the mechanism — then the verdict measures whether the DM engined it.
PER_BEAT_CUE=""   # CSV of 1/0 — was the seeded cue the next_action at the start of each beat?
DMSG=""
for b in $(seq 1 "$BEATS"); do
  CUR_CUE="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory servers/engine python "$ROOT/qa/probe_verdict.py" cue-check "$STATE_DIR" "$CAMPAIGN_ID" 2>/dev/null | tail -n1)"
  if [ "$CUR_CUE" = "$CUE" ]; then PER_BEAT_CUE="${PER_BEAT_CUE}${PER_BEAT_CUE:+,}1"; else PER_BEAT_CUE="${PER_BEAT_CUE}${PER_BEAT_CUE:+,}0"; fi
  echo "[probe] beat $b: next_action=$CUR_CUE (seeded cue=$CUE $([ "$CUR_CUE" = "$CUE" ] && echo present || echo ABSENT))"

  if [ "$b" = "1" ]; then
    # Beat 1 grounds the DM in the seeded mid-arc state + hands it the moment. The DM re-grounds from
    # the engine's persisted truth (scene_context / get_state) — NOT a cold open (there is no
    # world-build here; the fixture already seated the party, quest, and arc).
    PROMPT="$DM_BRIEF

You are resuming an IN-PROGRESS session (campaign already seeded: party, an active quest, an arc mid-story). Do NOT start_world or re-seat anyone. FIRST call scene_context (or get_state) to re-ground on the current party, the OPEN quest and its objectives, and where the arc stands. Then play THIS beat as a full lived scene: the party is at the Harper safehouse and the session is heading for a close. Resolve what the moment calls for THROUGH THE ENGINE (the quest is still open — if the thread closes in fiction, complete_objective / complete_quest it; carry any hand-off with add_consequence). End by handing the moment to the party. OUTPUT DISCIPLINE — your reply IS the scene: 2nd-person in-fiction prose + quoted dialogue only, never a status line."
  else
    PROMPT="The party presses on toward closing this out. Play the next beat: keep driving the OPEN thread to a real resolution and record it THROUGH THE ENGINE (complete_objective as a step clears; complete_quest with evolves_to when it resolves; add_consequence to carry a hand-off). Any NPC or companion in the scene speaks at least one quoted line. End by handing the moment back to the party — never a bare 'What do you do?'."
  fi

  DMSG="$(probe_dm_turn "$DSID" "$([ "$b" = "1" ] && echo 1 || echo 0)" "$PROMPT")"
  # #357 recovery: if the turn ended on a tool call but logged real prose, recover it (shared helper).
  worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
  echo "[probe] beat $b DM: ${DMSG:0:100}…"
  [ -z "$DMSG" ] && echo "[probe] beat $b: DM produced no reply (see $COMBINED) — continuing; the verdict reads engine state, not prose." >&2
done

# ── 4. Snapshot the engine state AFTER the beats + compute the DETERMINISTIC verdict ───────────
STATE_AFTER="$T/$PROBE.state_after.json"
if [ -f "$SNAP_SRC" ]; then cp "$SNAP_SRC" "$STATE_AFTER"; else echo '{"warning":"no state"}' > "$STATE_AFTER"; fi

REPORT="$(python3 "$ROOT/qa/probe_verdict.py" report "$CUE" "$COMBINED" "$STATE_BEFORE" "$STATE_AFTER" "$PER_BEAT_CUE")"
VERDICT="$(printf '%s' "$REPORT" | jq -r '.verdict')"

# ── 5. Bounded report + a scores_db row (surface=engine-duo, methodology=mechanism-probe, notes
#       stamped ITERATION-ONLY so it can never be mistaken for release evidence).
echo "──────────────────────────────────────────────────────────────────────────"
echo "[probe] TIER-1.5 MECHANISM PROBE — ITERATION SIGNAL ONLY (never a release verdict)"
echo "  probe=$PROBE  fixture=$FIXTURE  cue=$CUE  dm=$WORLDOS_DM_MODEL  beats=$BEATS"
printf '%s\n' "$REPORT" | jq .
echo "  VERDICT: $VERDICT"
echo "──────────────────────────────────────────────────────────────────────────"

BUILD_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
ENG_TALLY="$(printf '%s' "$REPORT" | jq -c '.dm_engagement_tools_called')"
NOTES="ITERATION-ONLY — Tier-1.5 mechanism probe (seeded ${FIXTURE} fixture, ${BEATS} live beats). NOT release evidence: the seeded mid-arc state skips cold-open/seat-path/free-play surfaces (see docs/qa/FAST_GATE.md). cue=${CUE} verdict=${VERDICT} engagement=${ENG_TALLY} cue_each_beat=$(printf '%s' "$REPORT" | jq -r '.cue_present_each_beat') state_moved=$(printf '%s' "$REPORT" | jq -r '.quest_resolved_or_progressed')."
python3 - "$ROOT" "$PROBE-$(date +%s)" "$WORLDOS_DM_MODEL" "$BUILD_SHA" "$COMBINED" "$NOTES" <<'PY' || echo "[probe] WARN: scores_db row not written (non-fatal)" >&2
import sys
root, run_id, dm_model, sha, source_path, notes = sys.argv[1:7]
sys.path.insert(0, f"{root}/qa")
import scores_db  # noqa: E402
scores_db.add_run(
    run_id=run_id,
    surface="engine-duo",
    methodology="mechanism-probe",
    dm_model=dm_model,
    actor_model="scripted",   # no AI player — the probe drives the DM against a seeded cue
    scorer_model="derived",   # deterministic verdict, no LLM lens
    build_sha=sha,
    source_path=source_path,
    notes=notes,
    # No pass/fail verdict written for a fast tier (contract: never write pass=1 from an iteration run).
)
print("[probe] scores_db row appended (surface=engine-duo, methodology=mechanism-probe).")
PY

# Exit code: 0 for ACTED, 3 for IGNORED, 4 for CUE_ABSENT — a machine-readable outcome for a
# harness, WITHOUT ever implying a release pass (this is an iteration tripwire, not a gate).
case "$VERDICT" in
  ACTED) exit 0 ;;
  IGNORED) exit 3 ;;
  *) exit 4 ;;
esac
