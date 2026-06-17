#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves the CLAUDE lanes now write the provider-status sidecar the
# OpenWorlds viewer reads (F12-2 audit finding F12-10). Before this, only scripts/play_codex_dm.sh
# wrote provider_status.json; scripts/play.sh + scripts/play_party.sh never did, so on a turn-cap /
# budget stop or a crash the viewer fell back to status "unknown" (NOT in the {stopped,failed,
# exhausted} set it buckets as `no_provider`) → a live-looking-but-dead dashboard.
#
# It sources the REAL qa/lib_beat_driver.sh and drives worldos_write_provider_status + the lanes'
# clean-stop / crash-trap LOGIC (extracted verbatim) against a throwaway $STATE_DIR. We assert:
#   (1) "running" at start is a valid worldos.provider-status.v1 row;
#   (2) a clean TURN-CAP stop writes status=stopped reason=turn_cap (the no_provider bucket) and sets
#       the clean-stop flag so the crash trap does NOT relabel it "failed";
#   (3) the crash trap writes status=failed when the run did NOT stop cleanly;
#   (4) the crash trap is a NO-OP after a clean stop (preserves "stopped");
#   (5) the row carries the fields the viewer's _provider_status_summary expects.
# It ALSO statically asserts both real lanes wire the start / stopped / failed writes. Self-contained
# under mktemp; safe on macOS dev box AND ubuntu CI.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
PROVIDER_STATUS="$STATE_DIR/provider_status.json"
PROVIDER=claude WORLDOS_DM_MODEL=opus MAX_TURNS=40
export PROVIDER WORLDOS_DM_MODEL MAX_TURNS

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }
field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]))' "$PROVIDER_STATUS" "$1" 2>/dev/null; }

# Mirror the lanes' thin wrapper + clean-stop flag + crash trap.
DM_TURNS=3; PROVIDER_STOPPED_CLEANLY=0
provider_status_set() { worldos_write_provider_status "$PROVIDER_STATUS" "$1" "$2" "$3" "$DM_TURNS" "scripts/play.sh"; }
crash_trap() {
  if [ "${PROVIDER_STOPPED_CLEANLY:-0}" != "1" ] && [ -n "${PROVIDER_STATUS:-}" ]; then
    worldos_write_provider_status "$PROVIDER_STATUS" failed crashed "DM exited unexpectedly." "${DM_TURNS:-0}" "scripts/play.sh"
  fi
}

# (1) start → running, valid v1.
provider_status_set running active "DM is running."
chk "start row exists"                          '[ -f "$PROVIDER_STATUS" ]'
chk "start schema is worldos.provider-status.v1" '[ "$(field schema)" = "worldos.provider-status.v1" ]'
chk "start status is running"                   '[ "$(field status)" = "running" ]'
chk "running is NOT a no_provider bucket"       'case "$(field status)" in stopped|failed|exhausted) false;; *) true;; esac'

# (2) clean turn-cap stop → stopped/turn_cap, flag set.
provider_status_set stopped turn_cap "Max turns reached."; PROVIDER_STOPPED_CLEANLY=1
chk "turn-cap stop → status=stopped"            '[ "$(field status)" = "stopped" ]'
chk "turn-cap stop → reason=turn_cap"           '[ "$(field reason)" = "turn_cap" ]'
chk "stopped IS a no_provider bucket"           'case "$(field status)" in stopped|failed|exhausted) true;; *) false;; esac'

# (4) crash trap after a CLEAN stop is a NO-OP (must not relabel "stopped" as "failed").
crash_trap
chk "crash trap preserves a clean 'stopped'"    '[ "$(field status)" = "stopped" ]'

# (3) crash WITHOUT a clean stop → failed.
PROVIDER_STOPPED_CLEANLY=0
provider_status_set running active "DM is running."   # back to running (a live session)
crash_trap
chk "crash trap (not clean) → status=failed"    '[ "$(field status)" = "failed" ]'
chk "failed IS a no_provider bucket"            'case "$(field status)" in stopped|failed|exhausted) true;; *) false;; esac'

# (5) the row carries the viewer's expected fields.
chk "row carries provider"                      '[ "$(field provider)" = "claude" ]'
chk "row carries model (from DM_MODEL)"         '[ "$(field model)" = "opus" ]'
chk "row carries dm_turns"                      '[ "$(field dm_turns)" = "3" ]'
chk "row carries max_turns"                     '[ "$(field max_turns)" = "40" ]'
chk "row carries wrapper"                       'printf "%s" "$(field wrapper)" | grep -q "play"'

# Static wiring: both real lanes write start / stopped / failed.
for lane in scripts/play.sh scripts/play_party.sh; do
  chk "$lane writes a 'running' provider-status"  'grep -q "provider_status_set running" "$ROOT/'"$lane"'"'
  chk "$lane writes 'stopped' on a clean stop"    'grep -q "provider_status_set stopped" "$ROOT/'"$lane"'"'
  chk "$lane writes 'failed' in its cleanup trap" 'grep -q "worldos_write_provider_status .* failed" "$ROOT/'"$lane"'"'
done
# The codex wrapper already wrote it (the lane this finding brings the claude lanes up to).
chk "play_codex_dm still writes provider_status"  'grep -q "provider_status" "$ROOT/scripts/play_codex_dm.sh"'

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
