#!/usr/bin/env bash
# DRY-RUN PROOF (no model call): shows that qa/run_duo.sh's DM turn now honors
# CLAWDND_LEAN_BEATS AND the DM effort-tier — both via the SHARED helpers in
# qa/lib_beat_driver.sh (clawdnd_dm_lean_args + clawdnd_dm_effort_arg). It sources the REAL
# qa/lib_beat_driver.sh and reproduces run_duo.sh's DM-branch AND player-branch argv assembly
# VERBATIM, with a stub `claude` that just prints the argv it would have run. We assert that:
#   (1) CLAWDND_LEAN_BEATS now DEFAULTS to 1 (lean is standard — no env set → lean fires);
#   (2) the COLD-OPEN DM argv includes --effort max;
#   (3) a ROUTINE/continuing DM argv includes --effort medium;
#   (4) the PLAYER turn argv has NO --effort;
#   (5) the flag override still works: CLAWDND_LEAN_BEATS=0 forces the legacy --resume path;
# plus the original lean-fires/no-fires behavior (fresh --session-id + LEAN RE-GROUND on a
# continuing beat; normal --session-id on the cold open).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

DSID="DSID-fixed-0000"; PSID="PSID-fixed-0000"
CAMPAIGN_ID="camp-abc123"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
DM_CFG="/tmp/dm.mcp.json"; PLAYER_CFG="/tmp/player.mcp.json"; BUDGET="0.80"
CLAWDND_DM_MODEL="sonnet"; CLAWDND_ACTOR_MODEL="sonnet"

# Stub `claude`: print the exact argv (each arg on its own line in «»), then return.
claude() {
  printf 'CLAUDE-ARGV-BEGIN\n'
  local a; for a in "$@"; do printf '  «%s»\n' "$a"; done
  printf 'CLAUDE-ARGV-END\n'
}

# VERBATIM copy of run_duo.sh turn()'s DM branch argv assembly (the code under test) —
# including the shared effort-tier splice. $1=first(1/0)  $2=msg
dm_turn_argv() {
  local first="$1" msg="$2" sid="$DSID" resume=() extra=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  clawdnd_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
  if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
    resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
    extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
  fi
  clawdnd_dm_effort_arg "$first"
  claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
    --output-format stream-json --verbose
}

# VERBATIM copy of run_duo.sh turn()'s PLAYER branch argv assembly — proves the player facade
# gets NO --effort (and no lean/effort helper is ever called for it). $1=first(1/0)  $2=msg
player_turn_argv() {
  local first="$1" msg="$2" sid="$PSID" resume=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  claude -p "$msg" "${resume[@]}" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
    --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
    --output-format json
}

hr() { printf '\n========== %s ==========\n' "$1"; }

hr "SCENARIO 1 — DEFAULT (no CLAWDND_LEAN_BEATS set), continuing beat (first=0) -> lean fires + effort medium"
out1="$(unset CLAWDND_LEAN_BEATS; dm_turn_argv 0 'The player does: opens the door.')"
printf '%s\n' "$out1"

hr "SCENARIO 2 — DEFAULT (no CLAWDND_LEAN_BEATS set), COLD OPEN (first=1) -> no lean + effort max"
out2="$(unset CLAWDND_LEAN_BEATS; dm_turn_argv 1 'Begin the session.')"
printf '%s\n' "$out2"

hr "SCENARIO 3 — OVERRIDE CLAWDND_LEAN_BEATS=0, continuing beat (first=0) -> legacy --resume (lean off), effort still medium"
out3="$(CLAWDND_LEAN_BEATS=0 dm_turn_argv 0 'The player does: opens the door.')"
printf '%s\n' "$out3"

hr "SCENARIO 4 — PLAYER turn (continuing beat) -> NO --effort, NO lean"
out4="$(unset CLAWDND_LEAN_BEATS; player_turn_argv 0 'I draw my sword and advance.')"
printf '%s\n' "$out4"

# ---- Assertions -------------------------------------------------------------------
hr "ASSERTIONS"
fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# (1) DEFAULT lean=1: S1 (no env) on a continuing beat must FIRE lean -> fresh UUID session,
#     LEAN RE-GROUND directive, NO --resume. This proves the default flipped to 1.
chk "S1 DEFAULT fires lean: has --session-id"        'printf "%s" "$out1" | grep -q -- "--session-id"'
chk "S1 DEFAULT lean: fresh UUID (not \$DSID)"        'printf "%s" "$out1" | grep -Eq "«[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}»"'
chk "S1 DEFAULT lean: NOT \$DSID"                     '! printf "%s" "$out1" | grep -q -- "«DSID-fixed-0000»"'
chk "S1 DEFAULT lean: has --append-system-prompt"    'printf "%s" "$out1" | grep -q -- "--append-system-prompt"'
chk "S1 DEFAULT lean: directive says LEAN RE-GROUND" 'printf "%s" "$out1" | grep -q "LEAN RE-GROUND"'
chk "S1 DEFAULT lean: NO --resume"                   '! printf "%s" "$out1" | grep -q -- "--resume"'
# (3) routine effort = medium (continuing beat).
chk "S1 routine DM argv has --effort medium"         'printf "%s" "$out1" | grep -A1 -- "«--effort»" | grep -q -- "«medium»"'

# (2) cold-open effort = max; cold open does NOT fire lean (normal --session-id \$DSID, no lean).
chk "S2 cold-open DM argv has --effort max"          'printf "%s" "$out2" | grep -A1 -- "«--effort»" | grep -q -- "«max»"'
chk "S2 cold open uses --session-id \$DSID"          'printf "%s" "$out2" | grep -q -- "«DSID-fixed-0000»"'
chk "S2 cold open has NO --append-system-prompt"     '! printf "%s" "$out2" | grep -q -- "--append-system-prompt"'
chk "S2 cold open has NO --resume"                   '! printf "%s" "$out2" | grep -q -- "--resume"'
chk "S2 cold open does NOT use --effort medium"      '! { printf "%s" "$out2" | grep -A1 -- "«--effort»" | grep -q -- "«medium»"; }'

# (5) override LEAN=0 forces the legacy --resume path (no lean) — still fully reversible per-run.
chk "S3 override LEAN=0 uses --resume \$DSID"         'printf "%s" "$out3" | grep -q -- "--resume" && printf "%s" "$out3" | grep -q -- "«DSID-fixed-0000»"'
chk "S3 override LEAN=0 has NO --append-system-prompt" '! printf "%s" "$out3" | grep -q -- "--append-system-prompt"'
chk "S3 override LEAN=0 has NO fresh --session-id"    '! printf "%s" "$out3" | grep -q -- "--session-id"'
# effort tier is independent of lean: a continuing beat is still medium even with lean forced off.
chk "S3 routine DM argv still --effort medium"        'printf "%s" "$out3" | grep -A1 -- "«--effort»" | grep -q -- "«medium»"'

# (4) the PLAYER turn never gets --effort (effort applies ONLY to the DM turn).
chk "S4 player turn has NO --effort"                  '! printf "%s" "$out4" | grep -q -- "--effort"'
chk "S4 player turn has NO --append-system-prompt"    '! printf "%s" "$out4" | grep -q -- "--append-system-prompt"'

hr "RESULT"
[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
