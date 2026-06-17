#!/usr/bin/env bash
# DRY-RUN PROOF (no model call): shows that scripts/play_party.sh's DM turn now honors
# CLAWDND_LEAN_BEATS AND the DM effort-tier — both via the SHARED helpers in
# qa/lib_beat_driver.sh (worldos_dm_lean_args + worldos_dm_effort_arg). This matters because the
# BUILT dist/WorldOS.app shells scripts/play_party.sh for its DM (see
# macos/.../ProviderAdapters.swift ClaudeProvider), so the .app's DM must run the fast
# lean+effort config or the G1-G5 gate would run the slow non-lean path and likely fail G3 on
# latency. This is the play_party sibling of qa/dryrun_lean_proof.sh (which proves run_duo).
#
# It sources the REAL qa/lib_beat_driver.sh and reproduces play_party.sh's turn() DM branch AND
# companion (facade) branch argv assembly VERBATIM, with a stub `claude` that just prints the argv
# it would have run. We assert that:
#   (1) CLAWDND_LEAN_BEATS now DEFAULTS to 1 (lean is standard — no env set → lean fires);
#   (2) the COLD-OPEN DM argv includes --effort max (no lean: normal --session-id);
#   (3) a ROUTINE/continuing DM argv includes --effort medium + fresh --session-id + LEAN RE-GROUND;
#   (4) the COMPANION turn argv has NO --effort and NO lean re-ground (player/companion untouched);
#   (5) the flag override still works: CLAWDND_LEAN_BEATS=0 forces the legacy --resume path.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

# play_party's DM session id, a companion session id, and the campaign id play_party resolves
# UP FRONT from the pre-seed (so lean re-grounds against the real campaign on continuing beats).
DSID="DSID-fixed-0000"; CSID="CSID-fixed-0000"
CAMPAIGN_ID="camp-abc123"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
DM_CFG="/tmp/dm.mcp.json"; COMP_CFG="/tmp/companion_0.mcp.json"; COMBINED="/tmp/combined.jsonl"
DM_LOG="/tmp/dm"; STATE_DIR="/tmp/state"; BUDGET="1.50"
CLAWDND_DM_MODEL="sonnet"; CLAWDND_ACTOR_MODEL="sonnet"

# Stub `claude`: print the exact argv (each arg on its own line in «»), then return. We also stub
# the I/O sinks (cat/jq/date) so the VERBATIM turn() body runs without touching real files/models.
claude() {
  printf 'CLAUDE-ARGV-BEGIN\n'
  local a; for a in "$@"; do printf '  «%s»\n' "$a"; done
  printf 'CLAUDE-ARGV-END\n'
}

# VERBATIM copy of scripts/play_party.sh turn()'s body (the code under test), including the shared
# lean + effort-tier splice. Sinks (> "$out", cat >> COMBINED, the jq result-extract) are dropped
# so this prints ONLY the argv. Signature mirrors play_party: $1=kind $2=sid $3=first $4=msg $5=cfg.
turn_argv() {
  local kind="$1" sid="$2" first="$3" msg="$4" cfg="${5:-}" out resume=() extra=()
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$kind" = "dm" ]; then
    worldos_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
    if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${CLAWDND_DM_LEAN_SESSION[@]}")
      extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
    fi
    worldos_dm_effort_arg "$first"
    claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose
  else
    claude -p "$msg" "${resume[@]}" --mcp-config "$cfg" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose
  fi
}

hr() { printf '\n========== %s ==========\n' "$1"; }

hr "SCENARIO 1 — DEFAULT (no CLAWDND_LEAN_BEATS set), continuing DM beat (first=0) -> lean fires + effort medium"
out1="$(unset CLAWDND_LEAN_BEATS; turn_argv dm "$DSID" 0 'This beat, the party acts: opens the door.')"
printf '%s\n' "$out1"

hr "SCENARIO 2 — DEFAULT (no CLAWDND_LEAN_BEATS set), COLD OPEN DM beat (first=1) -> no lean + effort max"
out2="$(unset CLAWDND_LEAN_BEATS; turn_argv dm "$DSID" 1 'You are the Dungeon Master. Begin the session.')"
printf '%s\n' "$out2"

hr "SCENARIO 3 — OVERRIDE CLAWDND_LEAN_BEATS=0, continuing DM beat (first=0) -> legacy --resume (lean off), effort still medium"
out3="$(CLAWDND_LEAN_BEATS=0 turn_argv dm "$DSID" 0 'This beat, the party acts: opens the door.')"
printf '%s\n' "$out3"

hr "SCENARIO 4 — COMPANION turn (facade, continuing beat) -> NO --effort, NO lean re-ground"
out4="$(unset CLAWDND_LEAN_BEATS; turn_argv actor "$CSID" 0 'Take your next action through your tools.' "$COMP_CFG")"
printf '%s\n' "$out4"

# ---- Assertions -------------------------------------------------------------------
hr "ASSERTIONS"
fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# (1)+(3) DEFAULT lean=1: S1 (no env) on a continuing DM beat must FIRE lean -> fresh UUID session,
#         LEAN RE-GROUND directive, NO --resume, AND --effort medium. Proves the default is 1.
chk "S1 DEFAULT fires lean: has --session-id"        'printf "%s" "$out1" | grep -q -- "--session-id"'
chk "S1 DEFAULT lean: fresh UUID (not \$DSID)"        'printf "%s" "$out1" | grep -Eq "«[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}»"'
chk "S1 DEFAULT lean: NOT \$DSID"                     '! printf "%s" "$out1" | grep -q -- "«DSID-fixed-0000»"'
chk "S1 DEFAULT lean: has --append-system-prompt"    'printf "%s" "$out1" | grep -q -- "--append-system-prompt"'
chk "S1 DEFAULT lean: directive says LEAN RE-GROUND" 'printf "%s" "$out1" | grep -q "LEAN RE-GROUND"'
chk "S1 DEFAULT lean: re-grounds THIS campaign id"   'printf "%s" "$out1" | grep -q "camp-abc123"'
chk "S1 DEFAULT lean: NO --resume"                   '! printf "%s" "$out1" | grep -q -- "--resume"'
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
chk "S3 routine DM argv still --effort medium"        'printf "%s" "$out3" | grep -A1 -- "«--effort»" | grep -q -- "«medium»"'

# (4) the COMPANION facade turn never gets --effort or a lean re-ground (effort/lean = DM only).
chk "S4 companion turn has NO --effort"               '! printf "%s" "$out4" | grep -q -- "--effort"'
chk "S4 companion turn has NO --append-system-prompt" '! printf "%s" "$out4" | grep -q -- "--append-system-prompt"'
chk "S4 companion turn has NO LEAN RE-GROUND"         '! printf "%s" "$out4" | grep -q "LEAN RE-GROUND"'
chk "S4 companion turn uses --resume \$CSID"          'printf "%s" "$out4" | grep -q -- "--resume" && printf "%s" "$out4" | grep -q -- "«CSID-fixed-0000»"'

hr "RESULT"
[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
