#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves #1285's run-invalidation guard — qa/lib_beat_driver.sh's
# worldos_chatlog_dm_failed now tracks a CONSECUTIVE-failure streak (reset by any genuine beat via
# worldos_chatlog_dm / record_dm_reply) and stamps $STATE_DIR/.run_infra_invalid.json via
# worldos_mark_run_infra_invalid once the streak crosses WORLDOS_INFRA_INVALID_STREAK — the
# rri-a1-duo/duo2 defect class (a quota window / host-session death that fails several beats in a
# row mid-run, previously silently scored to the end as if it were a product measurement).
#
# Sources the REAL qa/lib_beat_driver.sh; exercises worldos_chatlog_dm_failed / worldos_chatlog_dm /
# worldos_mark_run_infra_invalid directly (no `claude` stub needed — these are pure bash+python
# bookkeeping helpers). Self-contained under mktemp; macOS + ubuntu CI safe.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
CHAT="$TMP/chat.jsonl"; : > "$CHAT"

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# ---- (1) below-threshold: 2 consecutive failures never stamps the sentinel -------------------
worldos_chatlog_dm_failed
worldos_chatlog_dm_failed
chk "2 consecutive failures: streak counter is 2"        '[ "${WORLDOS_DM_BEATS_FAILED_STREAK:-0}" = "2" ]'
chk "2 consecutive failures: NO sentinel stamped yet"     '[ ! -s "$STATE_DIR/.run_infra_invalid.json" ]'

# ---- (2) a genuine beat resets the streak ------------------------------------------------------
worldos_chatlog_dm "The road bends onward."
chk "a genuine beat resets the streak to 0"               '[ "${WORLDOS_DM_BEATS_FAILED_STREAK:-0}" = "0" ]'

# ---- (3) at-threshold: 3 CONSECUTIVE failures stamps the sentinel ------------------------------
worldos_chatlog_dm_failed
worldos_chatlog_dm_failed
worldos_chatlog_dm_failed
chk "3 consecutive failures: streak counter is 3"         '[ "${WORLDOS_DM_BEATS_FAILED_STREAK:-0}" = "3" ]'
chk "3 consecutive failures: sentinel IS stamped"          '[ -s "$STATE_DIR/.run_infra_invalid.json" ]'
chk "sentinel carries infra_invalid:true"                 'python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get(\"infra_invalid\") is True else 1)" "$STATE_DIR/.run_infra_invalid.json"'
chk "sentinel carries consecutive_failed_beats=3"          'python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get(\"consecutive_failed_beats\")==3 else 1)" "$STATE_DIR/.run_infra_invalid.json"'
chk "sentinel reason mentions consecutive DM beat failures" 'grep -q "consecutive DM beat failures" "$STATE_DIR/.run_infra_invalid.json"'

# ---- (4) first trip wins: a later mark call does NOT overwrite the original reason -------------
_orig="$(cat "$STATE_DIR/.run_infra_invalid.json")"
worldos_mark_run_infra_invalid "$STATE_DIR" "a different, later reason that must NOT overwrite"
chk "first-trip-wins: sentinel unchanged after a second mark call" '[ "$(cat "$STATE_DIR/.run_infra_invalid.json")" = "$_orig" ]'

# ---- (5) a fresh STATE_DIR (new run) starts clean — no cross-run leakage ------------------------
STATE_DIR2="$TMP/state2"; mkdir -p "$STATE_DIR2"
STATE_DIR="$STATE_DIR2" WORLDOS_DM_BEATS_FAILED_STREAK=0 bash -c '
  . "'"$ROOT"'/qa/lib_beat_driver.sh"
  CHAT="'"$TMP"'/chat2.jsonl"; : > "$CHAT"
  worldos_chatlog_dm_failed
'
chk "a fresh run with only 1 failure: no sentinel"        '[ ! -s "$STATE_DIR2/.run_infra_invalid.json" ]'

echo "----"
if [ "$fail" -eq 0 ]; then
  echo "ALL PASS: test_run_invalidation_guard.sh"
else
  echo "SOME FAILED: test_run_invalidation_guard.sh"
fi
exit "$fail"
