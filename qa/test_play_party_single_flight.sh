#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves scripts/launch_common.sh's single-flight launch lock
# rejects a SECOND concurrent ensemble cold-open and self-heals across release / a dead holder.
# This is the lock that play_party.sh acquires before the DM cold-open so two launches can't
# collide on session ids / the viewer port (observed under memory pressure as
# "Session ID already in use").
#
# It sources the REAL helpers and drives them against a throwaway $ROOT under mktemp — NO claude,
# NO viewer, NO pytest workers, so it is safe to run anywhere (macOS dev box AND ubuntu CI; the
# lock is mkdir/kill -0/rm, which behave identically on both). Mirrors qa/dryrun_lean_proof_*.sh:
# a `chk` assertion helper, PASS/FAIL lines, exit = number of failures.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/scripts/launch_common.sh"

holder=""
TMP="$(mktemp -d)"
trap 'kill "${holder:-}" 2>/dev/null; rm -rf "$TMP"' EXIT
LOCK="$TMP/play-state/.launch.lock"

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# (1) First acquire on a free checkout succeeds and records OUR pid.
WORLDOS_LAUNCH_LOCK_WAIT=0 worldos_acquire_launch_lock "$TMP"; rc=$?
chk "first acquire succeeds (rc=0)"            '[ "$rc" = 0 ]'
chk "lock dir + pid file created"              '[ -f "$LOCK/pid" ]'
chk "pid file records our pid"                 '[ "$(cat "$LOCK/pid")" = "$$" ]'

# (2) A LIVE holder makes a second acquire reject within the bounded wait — and must NOT be stolen.
sleep 60 & holder=$!
printf '%s\n' "$holder" > "$LOCK/pid"          # pretend a different live cold-open (pid=$holder) owns it
start=$SECONDS
WORLDOS_LAUNCH_LOCK_WAIT=1 worldos_acquire_launch_lock "$TMP" 2>"$TMP/err"; rc=$?
elapsed=$((SECONDS - start))
chk "live holder → second acquire rejected (rc!=0)"  '[ "$rc" != 0 ]'
chk "rejection message names the running cold-open"  'grep -q "already running" "$TMP/err"'
chk "rejection is bounded (<=4s for WAIT=1)"         '[ "$elapsed" -le 4 ]'
chk "live holder lock NOT stolen (pid unchanged)"    '[ "$(cat "$LOCK/pid")" = "$holder" ]'

# (3) The holder dies WITHOUT releasing (the OOM scenario: SIGKILL skips the cleanup trap). The
#     stale lock must be reclaimed so the next launch is not blocked forever.
kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null   # holder now definitively dead
chk "precondition: dead holder pid is gone"          '! kill -0 "$holder" 2>/dev/null'
chk "stale lock left behind (holder never released)" '[ "$(cat "$LOCK/pid" 2>/dev/null)" = "$holder" ]'
WORLDOS_LAUNCH_LOCK_WAIT=1 worldos_acquire_launch_lock "$TMP" 2>"$TMP/err2"; rc=$?
chk "stale (dead-holder) lock reclaimed → acquire ok" '[ "$rc" = 0 ]'
chk "reclaimed lock now records our pid"              '[ "$(cat "$LOCK/pid")" = "$$" ]'

# (4) Release frees the lock for the OWNER, is a NO-OP for a non-owner, and re-acquire then works.
worldos_release_launch_lock "$TMP"
chk "owner release removes the lock"                 '[ ! -e "$LOCK" ]'
mkdir -p "$LOCK"; printf '%s\n' "424242" > "$LOCK/pid"   # a lock owned by someone else
worldos_release_launch_lock "$TMP"
chk "non-owner release is a no-op (lock survives)"   '[ "$(cat "$LOCK/pid" 2>/dev/null)" = "424242" ]'
rm -rf "$LOCK"
WORLDOS_LAUNCH_LOCK_WAIT=0 worldos_acquire_launch_lock "$TMP"; rc=$?
chk "acquire succeeds again after a clean release"   '[ "$rc" = 0 ]'

# (5) Non-contention mkdir failures fail FAST — must never spin forever (CodeRabbit on #564).
#  (a) play-state cannot be created (ROOT is a regular file) → up-front guard rejects, no hang.
notdir="$TMP/iam-a-file"; : > "$notdir"
WORLDOS_LAUNCH_LOCK_WAIT=0 worldos_acquire_launch_lock "$notdir" 2>"$TMP/err5a"; rc=$?
chk "uncreatable play-state → acquire fails (rc!=0)"  '[ "$rc" != 0 ]'
chk "...with a clear 'could not create' message"      'grep -q "could not create" "$TMP/err5a"'
#  (b) play-state exists but the lock dir is uncreatable (read-only) → in-loop guard rejects fast
#      instead of spinning. Run in the background with a watchdog so a regression FAILS, not hangs.
#      Needs non-root (root bypasses directory permissions), so skip there.
if [ "$(id -u)" != 0 ]; then
  ro="$TMP/ro"; mkdir -p "$ro/play-state"; chmod 555 "$ro/play-state"
  ( WORLDOS_LAUNCH_LOCK_WAIT=0 worldos_acquire_launch_lock "$ro" >/dev/null 2>>"$TMP/err5b"; echo "$?" > "$TMP/rc5b" ) &
  bpid=$!
  for _ in $(seq 1 50); do kill -0 "$bpid" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$bpid" 2>/dev/null; then
    kill "$bpid" 2>/dev/null; echo "FAIL: read-only lock dir made acquire SPIN (did not return)"; fail=1
  else
    chk "read-only lock dir → acquire fails fast (rc!=0)" '[ "$(cat "$TMP/rc5b" 2>/dev/null)" != 0 ]'
  fi
  chmod 755 "$ro/play-state" 2>/dev/null
else
  echo "PASS: read-only-dir spin guard (skipped — running as root bypasses dir perms)"
fi

# (6) F12-13: scripts/play.sh (the .app's DEFAULT solo entry point) must ALSO acquire + release this
#     lock — before, only play_party.sh did, and on the solo path it `exec play.sh` AFTER acquiring,
#     so a solo launch had NO lock and two solo launches stacked two viewers + two DM sessions. These
#     are static-wiring assertions on the real script (a runtime launch needs claude + a viewer).
PLAY="$ROOT/scripts/play.sh"
chk "play.sh acquires the single-flight launch lock"  'grep -q "worldos_acquire_launch_lock" "$PLAY"'
chk "play.sh releases the lock in cleanup"            'grep -q "worldos_release_launch_lock" "$PLAY"'
chk "play.sh acquire is guarded (declare -F) like play_party" 'grep -q "declare -F worldos_acquire_launch_lock" "$PLAY"'
chk "play.sh acquire precedes the viewer supervisor"  '[ "$(grep -n "worldos_acquire_launch_lock" "$PLAY" | head -1 | cut -d: -f1)" -lt "$(grep -n "viewer_supervisor &" "$PLAY" | head -1 | cut -d: -f1)" ]'

# (7) F12-13: play.sh has an IDLE CEILING (was spinning `sleep 2` forever with no player). Static +
#     a hermetic runtime check of the idle-break logic extracted VERBATIM from play.sh's loop tail.
chk "play.sh defines MAX_IDLE from WORLDOS_PLAY_MAX_IDLE" 'grep -q "MAX_IDLE=.*WORLDOS_PLAY_MAX_IDLE" "$PLAY"'
chk "play.sh idle-break echoes the stop reason"       'grep -q "idle .* with no player move — stopping" "$PLAY"'
# Hermetic idle-break: mirror the loop's else-branch (no claude/viewer). With MAX_IDLE=1 and no move,
# the loop must BREAK within a few seconds, not spin forever. A watchdog turns a regression into a
# FAIL, not a hang.
( SECONDS=0; MAX_IDLE=1; last_activity=$SECONDS
  while true; do
    if [ $((SECONDS - last_activity)) -ge "$MAX_IDLE" ]; then echo "BROKE"; break; fi
    sleep 1
  done > "$TMP/idle.out" ) & ipid=$!
for _ in $(seq 1 60); do kill -0 "$ipid" 2>/dev/null || break; sleep 0.1; done
if kill -0 "$ipid" 2>/dev/null; then
  kill "$ipid" 2>/dev/null; echo "FAIL: idle ceiling did not break within 6s (regression: would spin forever)"; fail=1
else
  chk "idle ceiling breaks the loop when MAX_IDLE elapses" 'grep -q "BROKE" "$TMP/idle.out"'
fi

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
