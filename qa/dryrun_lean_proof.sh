#!/usr/bin/env bash
# DRY-RUN PROOF (no model call): shows that qa/run_duo.sh's DM turn now honors
# CLAWDND_LEAN_BEATS via the SHARED clawdnd_dm_lean_args helper. It sources the REAL
# qa/lib_beat_driver.sh and reproduces run_duo.sh's DM-branch argv assembly VERBATIM,
# with a stub `claude` that just prints the argv it would have run. We assert that:
#   (1) lean ON + continuing beat + known campaign  -> fresh --session-id + LEAN RE-GROUND
#       --append-system-prompt, and NO --resume;
#   (2) lean ON + cold open (first=1)               -> normal --session-id $DSID, no lean;
#   (3) lean OFF + continuing beat                  -> --resume $DSID, no lean (unchanged).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

DSID="DSID-fixed-0000"
CAMPAIGN_ID="camp-abc123"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
DM_CFG="/tmp/dm.mcp.json"; BUDGET="0.80"; CLAWDND_DM_MODEL="sonnet"

# Stub `claude`: print the exact argv (NUL-joined, then shown one-per-line) and return.
claude() {
  printf 'CLAUDE-ARGV-BEGIN\n'
  local a; for a in "$@"; do printf '  «%s»\n' "$a"; done
  printf 'CLAUDE-ARGV-END\n'
}

# VERBATIM copy of run_duo.sh turn()'s DM branch argv assembly (the code under test).
dm_turn_argv() {            # $1=first(1/0)  $2=msg
  local first="$1" msg="$2" sid="$DSID" resume=() extra=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  clawdnd_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
  if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
    resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
    extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
  fi
  claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model "$CLAWDND_DM_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
    --output-format stream-json --verbose
}

hr() { printf '\n========== %s ==========\n' "$1"; }

hr "SCENARIO 1 — CLAWDND_LEAN_BEATS=1, continuing beat (first=0), campaign known"
out1="$(CLAWDND_LEAN_BEATS=1 dm_turn_argv 0 'The player does: opens the door.')"
printf '%s\n' "$out1"

hr "SCENARIO 2 — CLAWDND_LEAN_BEATS=1, COLD OPEN (first=1) -> lean must NOT fire"
out2="$(CLAWDND_LEAN_BEATS=1 dm_turn_argv 1 'Begin the session.')"
printf '%s\n' "$out2"

hr "SCENARIO 3 — CLAWDND_LEAN_BEATS=0, continuing beat (first=0) -> UNCHANGED"
out3="$(CLAWDND_LEAN_BEATS=0 dm_turn_argv 0 'The player does: opens the door.')"
printf '%s\n' "$out3"

# ---- Assertions -------------------------------------------------------------------
hr "ASSERTIONS"
fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# S1: lean ON, continuing -> fresh --session-id (a UUID, NOT $DSID), LEAN RE-GROUND present, NO --resume.
chk "S1 has --session-id"                       'printf "%s" "$out1" | grep -q -- "--session-id"'
chk "S1 fresh session id is a UUID (not \$DSID)" 'printf "%s" "$out1" | grep -Eq "«[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}»"'
chk "S1 NOT $DSID"                               '! printf "%s" "$out1" | grep -q -- "«DSID-fixed-0000»"'
chk "S1 has --append-system-prompt"             'printf "%s" "$out1" | grep -q -- "--append-system-prompt"'
chk "S1 directive says LEAN RE-GROUND"          'printf "%s" "$out1" | grep -q "LEAN RE-GROUND"'
chk "S1 directive names scene_context+campaign" 'printf "%s" "$out1" | grep -q "scene_context(campaign_id=\\\"camp-abc123\\\""'
chk "S1 directive honors recent_narration tail" 'printf "%s" "$out1" | grep -q "recent_narration=8"'
chk "S1 has NO --resume"                         '! printf "%s" "$out1" | grep -q -- "--resume"'

# S2: lean ON but cold open -> normal --session-id $DSID, no lean.
chk "S2 uses --session-id \$DSID (cold open)"   'printf "%s" "$out2" | grep -q -- "«DSID-fixed-0000»"'
chk "S2 has NO --append-system-prompt"           '! printf "%s" "$out2" | grep -q -- "--append-system-prompt"'
chk "S2 has NO --resume"                          '! printf "%s" "$out2" | grep -q -- "--resume"'

# S3: lean OFF, continuing -> --resume $DSID, no lean (byte-identical to today).
chk "S3 uses --resume \$DSID"                    'printf "%s" "$out3" | grep -q -- "--resume" && printf "%s" "$out3" | grep -q -- "«DSID-fixed-0000»"'
chk "S3 has NO --append-system-prompt"           '! printf "%s" "$out3" | grep -q -- "--append-system-prompt"'
chk "S3 has NO fresh --session-id"               '! printf "%s" "$out3" | grep -q -- "--session-id"'

hr "RESULT"
[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
