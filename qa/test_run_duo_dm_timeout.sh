#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves qa/run_duo.sh's DM turn is now (F12-2 audit finding F12-11)
#   (1) BOUNDED by a per-beat deadline via worldos_timeout + the model-aware worldos_dm_timeout tier
#       (cold-open vs routine) — before, the DM branch was an UNBOUNDED `claude -p`, so a wedged beat
#       hung the whole sweep and the empty-output retry never fired (a hang never returns empty);
#   (2) SURFACES the real failure cause on a nonzero rc with no error-class result (a timeout) AND on
#       an error-class 401 result — the "[dm-attempt] …" reason + the 401/403 NOT-retryable re-auth
#       hint, instead of masking it as a phantom empty turn;
#   (3) re-mints a FRESH cold-open session id on retry via the SHARED helper
#       (worldos_dm_remint_session_on_retry), not an inline uuid — so the three harnesses can't drift.
#
# It sources the REAL qa/lib_beat_driver.sh and reproduces run_duo.sh's DM branch + turn_retry remint
# VERBATIM with stub claude/worldos_timeout. Self-contained under mktemp; macOS + ubuntu CI safe.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
T="$TMP"; RUN="r"; COMBINED="$TMP/combined.jsonl"; : > "$COMBINED"
DM_CFG="$TMP/dm.json"; : > "$DM_CFG"
WORLDOS_DM_MODEL="sonnet"; BUDGET="1.50"; WORLDOS_LEAN_TAIL=8

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# ---- VERBATIM: run_duo.sh turn() DM branch (F12-11 timeout-wrapped + failure-surfaced) ---------
# $1=first $2=msg ; sinks reproduced. Echoes $out path via DM_OUT so the argv probe can read it.
DM_OUT=""
duo_dm_turn() {
  local first="$1" msg="$2" resume=() extra=() rc=0 out beat_timeout
  [ "$first" = "0" ] && resume=(--resume "DSID") || resume=(--session-id "DSID")
  worldos_dm_effort_arg "$first"
  out="$T/$RUN.dm.$(date +%s%N).jsonl"
  beat_timeout="$(worldos_dm_timeout "$first")"
  worldos_timeout "$beat_timeout" \
    claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$WORLDOS_DM_MODEL" ${WORLDOS_DM_EFFORT[@]+"${WORLDOS_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$T/$RUN.dm.err"
  rc=$?
  DM_OUT="$out"
  cat "$out" >> "$COMBINED"
  if [ "$rc" -ne 0 ] && ! worldos_dm_result_is_error "$out"; then
    worldos_report_attempt_failure "$out" "$rc"
  fi
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# (1) argv capture: the DM cold open IS wrapped in worldos_timeout with the sonnet cold-open 550.
claude() { printf 'CLAUDE-ARGV-BEGIN\n'; local a; for a in "$@"; do printf '  «%s»\n' "$a"; done; printf 'CLAUDE-ARGV-END\n'; }
worldos_timeout() { printf 'TIMEOUT-WRAP «%s»\n' "$1"; shift; "$@"; }
duo_dm_turn 1 'Begin the session.' >/dev/null; argv_co="$(cat "$DM_OUT")"
chk "duo DM cold open IS worldos_timeout-wrapped"     'printf "%s" "$argv_co" | grep -q -- "TIMEOUT-WRAP"'
chk "duo DM cold open deadline is the 550 tier"       'printf "%s" "$argv_co" | grep -q -- "TIMEOUT-WRAP «550»"'
duo_dm_turn 0 'Resolve the move.' >/dev/null; argv_rt="$(cat "$DM_OUT")"
chk "duo DM routine beat deadline is the 360 tier"    'printf "%s" "$argv_rt" | grep -q -- "TIMEOUT-WRAP «360»"'
unset -f claude worldos_timeout
. "$ROOT/qa/lib_beat_driver.sh"   # restore the real worldos_timeout shim for the behavior probes below
# worldos_timeout execs `claude` as an EXTERNAL command (timeout(1) or a python3 subprocess), neither
# of which can see a bash FUNCTION — so the behavior probes use real stub EXECUTABLES on PATH.
BIN="$TMP/bin"; mkdir -p "$BIN"; PATH="$BIN:$PATH"

# (2a) a 401 error-class RESULT (rc=0) → worldos_dm_final_text echoes EMPTY and surfaces the 401 hint.
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' '{"type":"result","subtype":"error_during_execution","is_error":true,"api_error_status":401,"result":"API Error: 401 authentication_error"}'
exit 0
STUB
chmod +x "$BIN/claude"
reply401="$(duo_dm_turn 0 'Resolve.' 2>"$TMP/err401")"
chk "401 result → DM reply text is EMPTY (not chatted as prose)" '[ -z "$reply401" ]'
chk "401 result → real cause surfaced on stderr"      'grep -q "dm-attempt" "$TMP/err401"'
chk "401 result → flagged NOT retryable (re-auth hint)" 'grep -q "NOT retryable" "$TMP/err401"'
chk "401 result → names HTTP 401"                     'grep -q "401" "$TMP/err401"'

# (2b) a TIMEOUT (rc=124, no result event) → empty reply AND the timeout cause surfaced on stderr.
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
sleep 600
STUB
chmod +x "$BIN/claude"
start=$SECONDS
reply_to="$(WORLDOS_COLDOPEN_TIMEOUT=2 WORLDOS_BEAT_TIMEOUT=2 duo_dm_turn 0 'Resolve.' 2>"$TMP/errto")"
elapsed=$((SECONDS - start))
chk "timeout → DM reply text is EMPTY"                '[ -z "$reply_to" ]'
chk "timeout → killed at the deadline (<=10s, not 600s)" '[ "$elapsed" -le 10 ]'
chk "timeout → cause surfaced on stderr (dm-attempt)" 'grep -q "dm-attempt" "$TMP/errto"'

# (3) the shared cold-open remint yields a FRESH --session-id (not the consumed one).
unset -f claude
WORLDOS_DM_RETRY_SESSION=()
worldos_dm_remint_session_on_retry --session-id "DSID-consumed-0000"
chk "shared remint emits a 2-token --session-id array" '[ "${#WORLDOS_DM_RETRY_SESSION[@]}" -ge 2 ]'
chk "shared remint mode is --session-id"               '[ "${WORLDOS_DM_RETRY_SESSION[0]}" = "--session-id" ]'
chk "shared remint id is FRESH (not the consumed id)"  '[ "${WORLDOS_DM_RETRY_SESSION[1]}" != "DSID-consumed-0000" ]'
chk "shared remint id is non-empty"                    '[ -n "${WORLDOS_DM_RETRY_SESSION[1]}" ]'

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
