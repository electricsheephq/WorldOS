#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves qa/run_duo.sh's turn_retry now survives a TRANSIENT
# server-side failure cluster instead of aborting the whole run on the first retry.
#
# The motivating incident (gs-ember-18b): a long overnight playtest died at beat 4 when a DM turn
# hit HTTP 500 AND the single retry ALSO hit 500 — one transient cluster killed a 2-3h run. The fix:
#   • clawdnd_dm_failure_is_transient (qa/lib_beat_driver.sh) classifies a failed attempt as
#     TRANSIENT (5xx / overloaded / 429 / rc=124 timeout) vs REAL/fail-fast (401/403 auth, a
#     deterministic bad turn);
#   • turn_retry retries a TRANSIENT failure up to CLAWDND_DM_MAX_ATTEMPTS (default 4) with a
#     short backoff, but a REAL failure gets only the ONE historical retry (never 4×), preserving
#     the 401/403 re-auth fail-fast.
#
# It sources the REAL qa/lib_beat_driver.sh and reproduces run_duo.sh's DM branch + the NEW
# turn_retry VERBATIM, with stub claude executables. Self-contained under mktemp; macOS + CI safe.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
T="$TMP"; RUN="r"; COMBINED="$TMP/combined.jsonl"; : > "$COMBINED"
DM_CFG="$TMP/dm.json"; : > "$DM_CFG"
CLAWDND_DM_MODEL="sonnet"; BUDGET="1.50"; CLAWDND_LEAN_TAIL=8
# Test seam: a backoff that records the seconds it was asked to wait instead of actually sleeping.
BACKOFF_LOG="$TMP/backoff.log"; : > "$BACKOFF_LOG"
fake_sleep() { printf '%s\n' "$1" >> "$BACKOFF_LOG"; }
export BACKOFF_LOG
CLAWDND_RETRY_SLEEP_CMD="fake_sleep"
# fake_sleep is a bash function — clawdnd_dm_retry_backoff calls it directly (NOT via timeout/PATH),
# so a function is fine here.
export -f fake_sleep 2>/dev/null || true

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# Stub-claude on PATH (worldos_timeout execs `claude` as an EXTERNAL command, which cannot see a
# bash function). A COUNTER file lets one stub return 500 twice then succeed.
BIN="$TMP/bin"; mkdir -p "$BIN"; PATH="$BIN:$PATH"
CALLS="$TMP/calls"; echo 0 > "$CALLS"; export CALLS

# ---- VERBATIM-in-intent: run_duo.sh turn() DM branch + the NEW turn_retry --------------------
turn() {
  local role="$1" sid="$2" first="$3" msg="$4" out resume=() extra=() rc=0 beat_timeout
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  clawdnd_dm_effort_arg "$first"
  out="$T/$RUN.dm.$(date +%s%N).jsonl"
  beat_timeout="$(clawdnd_dm_timeout "$first")"
  worldos_timeout "$beat_timeout" \
    claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
  rc=$?
  cat "$out" >> "$COMBINED"
  if [ "$rc" -ne 0 ] && ! clawdnd_dm_result_is_error "$out"; then
    clawdnd_report_attempt_failure "$out" "$rc"
  fi
  clawdnd_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# turn_retry copied VERBATIM from qa/run_duo.sh (the function under test).
turn_retry() {
  local r last_out last_rc transient attempt max
  max="${CLAWDND_DM_MAX_ATTEMPTS:-4}"
  clawdnd_dm_prebeat_mark "$STATE_DIR"
  r="$(turn "$@")"
  attempt=1
  while [ -z "$r" ] && [ "$attempt" -lt "$max" ]; do
    last_out="$(cat "$STATE_DIR/.dm_last_result" 2>/dev/null | tail -n1)"
    last_rc="$(cat "$STATE_DIR/.dm_last_rc" 2>/dev/null | tail -n1)"; last_rc="${last_rc:-0}"
    transient=0
    clawdnd_dm_failure_is_transient "$last_out" "$last_rc" && transient=1
    if [ "$transient" != "1" ] && [ "$attempt" -ge 2 ]; then
      echo "[duo] empty turn ($1) — failure looks REAL (not transient); not retrying further." >&2
      break
    fi
    if [ "$transient" = "1" ]; then
      echo "[duo] empty turn ($1) — TRANSIENT failure (rc=$last_rc); retry $((attempt + 1))/$max after backoff…" >&2
      clawdnd_dm_retry_backoff "$attempt"
    else
      echo "[duo] empty turn ($1) — retrying once…" >&2
    fi
    if [ "${3:-}" = "1" ]; then
      clawdnd_dm_remint_session_on_retry --session-id "$2"
      local _fresh="$2"
      [ "${#CLAWDND_DM_RETRY_SESSION[@]}" -ge 2 ] && _fresh="${CLAWDND_DM_RETRY_SESSION[1]}"
      r="$(turn "$1" "$_fresh" "$3" "${@:4}")"
    else
      r="$(turn "$@")"
    fi
    attempt=$((attempt + 1))
  done
  printf '%s' "$r"
}

# ── Classifier unit checks (no turn loop) ──────────────────────────────────────────────────────
mk_result() { printf '%s\n' "$1" > "$TMP/cls.jsonl"; printf '%s' "$TMP/cls.jsonl"; }
F500="$(mk_result '{"type":"result","is_error":true,"api_error_status":500,"result":"API Error: 500 Internal server error"}')"
chk "classify: HTTP 500 is TRANSIENT"        'clawdnd_dm_failure_is_transient "'"$F500"'" 0'
F529="$(mk_result '{"type":"result","is_error":true,"api_error_status":529,"result":"Overloaded"}')"
chk "classify: HTTP 529 overloaded is TRANSIENT" 'clawdnd_dm_failure_is_transient "'"$F529"'" 0'
F429="$(mk_result '{"type":"result","is_error":true,"api_error_status":429,"result":"rate_limit_error"}')"
chk "classify: HTTP 429 rate-limit is TRANSIENT" 'clawdnd_dm_failure_is_transient "'"$F429"'" 0'
F401="$(mk_result '{"type":"result","is_error":true,"api_error_status":401,"result":"API Error: 401 authentication_error"}')"
chk "classify: HTTP 401 auth is REAL (fail-fast)"  '! clawdnd_dm_failure_is_transient "'"$F401"'" 0'
F403="$(mk_result '{"type":"result","is_error":true,"api_error_status":403,"result":"permission_error"}')"
chk "classify: HTTP 403 is REAL (fail-fast)"       '! clawdnd_dm_failure_is_transient "'"$F403"'" 0'
FOVR="$(mk_result '{"type":"result","is_error":true,"result":"Overloaded: the service is temporarily unavailable, please retry"}')"
chk "classify: text-only 'overloaded' is TRANSIENT" 'clawdnd_dm_failure_is_transient "'"$FOVR"'" 0'
chk "classify: rc=124 timeout is TRANSIENT"        'clawdnd_dm_failure_is_transient "/nonexistent" 124'
FOK="$(mk_result '{"type":"result","is_error":false,"result":"a fine beat"}')"
chk "classify: a clean result is NOT transient"    '! clawdnd_dm_failure_is_transient "'"$FOK"'" 0'

# ── Backoff schedule ───────────────────────────────────────────────────────────────────────────
: > "$BACKOFF_LOG"
clawdnd_dm_retry_backoff 1; clawdnd_dm_retry_backoff 2; clawdnd_dm_retry_backoff 3
chk "backoff schedule is 3s, 8s, 20s" '[ "$(tr "\n" " " < "$BACKOFF_LOG")" = "3 8 20 " ]'

# ── (A) 500 twice then SUCCEED → the beat SUCCEEDS over 3 attempts (the gs-ember-18b fix) ──────
cat > "$BIN/claude" <<STUB
#!/usr/bin/env bash
n=\$(cat "$CALLS"); n=\$((n + 1)); echo "\$n" > "$CALLS"
if [ "\$n" -le 2 ]; then
  printf '%s\n' '{"type":"result","is_error":true,"api_error_status":500,"result":"API Error: 500 Internal server error"}'
  exit 0
fi
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"The torchlight gutters as you step into the vault."}'
exit 0
STUB
chmod +x "$BIN/claude"
echo 0 > "$CALLS"; : > "$BACKOFF_LOG"
reply="$(turn_retry dm "DSID" 0 'Resolve the move.' 2>"$TMP/errA")"
chk "500x2-then-OK → beat SUCCEEDS (non-empty reply)" '[ -n "$reply" ]'
chk "500x2-then-OK → reply is the 3rd attempt's prose" 'printf "%s" "$reply" | grep -q "torchlight gutters"'
chk "500x2-then-OK → claude called exactly 3 times" '[ "$(cat "$CALLS")" = "3" ]'
chk "500x2-then-OK → backed off twice (3s, 8s)" '[ "$(tr "\n" " " < "$BACKOFF_LOG")" = "3 8 " ]'

# ── (B) all-500 cluster → 4 attempts total, then give up (does NOT loop forever) ──────────────
cat > "$BIN/claude" <<STUB
#!/usr/bin/env bash
n=\$(cat "$CALLS"); n=\$((n + 1)); echo "\$n" > "$CALLS"
printf '%s\n' '{"type":"result","is_error":true,"api_error_status":500,"result":"API Error: 500 Internal server error"}'
exit 0
STUB
chmod +x "$BIN/claude"
echo 0 > "$CALLS"; : > "$BACKOFF_LOG"
replyB="$(turn_retry dm "DSID" 0 'Resolve.' 2>"$TMP/errB")"
chk "all-500 → reply empty (failed beat, surfaced honestly)" '[ -z "$replyB" ]'
chk "all-500 → exactly 4 attempts (the cap, not infinite)"   '[ "$(cat "$CALLS")" = "4" ]'

# ── (C) 401 auth → FAIL-FAST: only the ONE historical retry (NOT 4×), re-auth hint preserved ──
cat > "$BIN/claude" <<STUB
#!/usr/bin/env bash
n=\$(cat "$CALLS"); n=\$((n + 1)); echo "\$n" > "$CALLS"
printf '%s\n' '{"type":"result","is_error":true,"api_error_status":401,"result":"API Error: 401 authentication_error"}'
exit 0
STUB
chmod +x "$BIN/claude"
echo 0 > "$CALLS"; : > "$BACKOFF_LOG"
replyC="$(turn_retry dm "DSID" 0 'Resolve.' 2>"$TMP/errC")"
chk "401 → reply empty (failed beat)"                  '[ -z "$replyC" ]'
chk "401 → fail-fast: only 2 attempts (1 historical retry, NOT 4)" '[ "$(cat "$CALLS")" = "2" ]'
chk "401 → NO backoff sleeps (auth is not transient)"  '[ ! -s "$BACKOFF_LOG" ]'
chk "401 → re-auth hint preserved (NOT retryable)"     'grep -q "NOT retryable" "$TMP/errC"'

# ── (D) 500 then 401 → switches to fail-fast once the failure stops being transient ───────────
cat > "$BIN/claude" <<STUB
#!/usr/bin/env bash
n=\$(cat "$CALLS"); n=\$((n + 1)); echo "\$n" > "$CALLS"
if [ "\$n" -eq 1 ]; then
  printf '%s\n' '{"type":"result","is_error":true,"api_error_status":500,"result":"API Error: 500 Internal server error"}'
else
  printf '%s\n' '{"type":"result","is_error":true,"api_error_status":401,"result":"API Error: 401 authentication_error"}'
fi
exit 0
STUB
chmod +x "$BIN/claude"
echo 0 > "$CALLS"; : > "$BACKOFF_LOG"
replyD="$(turn_retry dm "DSID" 0 'Resolve.' 2>"$TMP/errD")"
chk "500-then-401 → reply empty (failed beat)" '[ -z "$replyD" ]'
# attempt1=500(transient,retry), attempt2=401(real). attempt2's empty is classified REAL and
# attempt>=2 -> break. So 3 attempts max? No: attempt1 empty(transient)->retry=attempt2;
# attempt2 empty, classified REAL, attempt(now 2)>=2 -> break. => 2 calls total.
chk "500-then-401 → stops at 2 (transient retry, then REAL fail-fast)" '[ "$(cat "$CALLS")" = "2" ]'

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
