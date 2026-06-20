#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves the #835 Increment-2 FIX-B stream-tailer lifecycle
# helpers in qa/lib_beat_driver.sh:
#   (1) worldos_stream_tailer_start is a NO-OP when streaming is OFF (default WORLDOS_STREAM_BEATS=0)
#       — no pidfile, no PID — so the feature stays dark by default;
#   (2) when ON, start persists the bg PID to $STATE_DIR/stream/tailer.pid (the subshell-survivable
#       handle the signal trap reads) and worldos_stream_tailer_stop removes it;
#   (3) worldos_stream_tailer_kill_pidfile (the trap reaper) kills the persisted PID and removes the
#       pidfile — even when WORLDOS_STREAM_TAILER_PID is empty in the calling shell (the orphan-on-
#       signal case where the tailer was launched inside dm_turn's $(...) subshell);
#   (4) the reaper is a benign no-op with no pidfile.
#
# It sources the REAL qa/lib_beat_driver.sh and uses a stub long-lived bg process as the "tailer"
# via WORLDOS_STREAM_TAILER pointing at a sleeper script. Self-contained under mktemp; macOS-safe.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
OUT="$TMP/dm.jsonl"; : > "$OUT"

# A stub "tailer": a long-lived PYTHON sleeper (the helper launches it as `python3 "$script"`, so the
# stub must be valid python, not bash). Stands in for scripts/stream_tailer.py so the test never
# depends on real DM output or python decode timing — only on the launch/persist/kill lifecycle.
STUB="$TMP/fake_tailer.py"
cat > "$STUB" <<'PY'
import sys, time
time.sleep(300)
PY
export WORLDOS_STREAM_TAILER="$STUB"

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }
alive() { kill -0 "$1" 2>/dev/null; }

PIDFILE="$STATE_DIR/stream/tailer.pid"

# (1) OFF by default: start is a no-op (no pidfile, empty PID).
unset WORLDOS_STREAM_BEATS
worldos_stream_tailer_start "$OUT" "$STATE_DIR"
chk "OFF default: no PID set"        '[ -z "${WORLDOS_STREAM_TAILER_PID:-}" ]'
chk "OFF default: no pidfile"        '[ ! -f "$PIDFILE" ]'

# (2) ON: start launches the stub, sets the PID, and persists the pidfile.
export WORLDOS_STREAM_BEATS=1
worldos_stream_tailer_start "$OUT" "$STATE_DIR"
sleep 0.2  # let python3 actually start so the liveness probe isn't racing the fork
START_PID="${WORLDOS_STREAM_TAILER_PID:-}"
chk "ON: PID is set"                 '[ -n "$START_PID" ]'
chk "ON: process is alive"           'alive "$START_PID"'
chk "ON: pidfile written"            '[ -f "$PIDFILE" ]'
chk "ON: pidfile matches PID"        '[ "$(cat "$PIDFILE")" = "$START_PID" ]'

# worldos_stream_tailer_stop kills the proc and removes the pidfile.
worldos_stream_tailer_stop
sleep 0.2
chk "stop: process killed"           '! alive "$START_PID"'
chk "stop: PID cleared"              '[ -z "${WORLDOS_STREAM_TAILER_PID:-}" ]'
chk "stop: pidfile removed"          '[ ! -f "$PIDFILE" ]'

# (3) Orphan-on-signal: start again, then simulate the subshell boundary by CLEARING the global
# PID (as it would be in the parent shell) and reaping via the pidfile only.
worldos_stream_tailer_start "$OUT" "$STATE_DIR"
sleep 0.2
ORPHAN_PID="${WORLDOS_STREAM_TAILER_PID:-}"
chk "orphan: tailer alive pre-reap"  'alive "$ORPHAN_PID"'
WORLDOS_STREAM_TAILER_PID=""          # the parent shell can't see the subshell's global
worldos_stream_tailer_kill_pidfile "$STATE_DIR"
sleep 0.2
chk "orphan: reaper killed by pidfile" '! alive "$ORPHAN_PID"'
chk "orphan: pidfile removed"          '[ ! -f "$PIDFILE" ]'

# (4) Reaper is a benign no-op with no pidfile.
worldos_stream_tailer_kill_pidfile "$STATE_DIR"
chk "reaper no-op when no pidfile"   'true'

if [ "$fail" -eq 0 ]; then echo "ALL PASS"; else echo "FAILURES"; fi
exit "$fail"
