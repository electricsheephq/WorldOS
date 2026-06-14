#!/usr/bin/env bash
# DRY-RUN PROOF (no model call): shows that the DM-turn `timeout` deadline is now TIERED off the
# cold-open `first` signal via the SHARED helper clawdnd_dm_timeout (qa/lib_beat_driver.sh), the
# sibling of clawdnd_dm_effort_arg. The cold open's --effort max world-build runs ~280–400s, so the
# routine 200s deadline was KILLING it; the cold open now gets WORLDOS_COLDOPEN_TIMEOUT (default
# 400s) while continuing/routine beats keep CLAWDND_BEAT_TIMEOUT (default 200s, unchanged).
#
# It sources the REAL qa/lib_beat_driver.sh and reproduces BOTH product cold-open paths' DM-turn
# `timeout` wrapper VERBATIM — scripts/play.sh dm_turn AND scripts/play_party.sh turn() — plus the
# player / companion facade turn (which is NEVER wrapped in a per-beat timeout), with a stub
# `timeout`+`claude` that just prints the argv they would have run. We assert:
#   (1) the COLD-OPEN DM argv is `timeout <co> claude …`  (model-aware cold-open deadline: opus 500,
#       non-opus 550 — F12-2);
#   (2) a ROUTINE/continuing DM argv is `timeout 360 claude …`  (routine deadline — F12-1 raised the
#       flat 200s to 360s; this proof is updated to match);
#   (3) the env override works:  WORLDOS_COLDOPEN_TIMEOUT bumps the cold open, CLAWDND_BEAT_TIMEOUT
#       bumps the routine tier, independently;
#   (4) the PLAYER / COMPANION turn argv has NO `timeout` wrapper at all (player turn unaffected);
#   (5) the SAME resolved deadline (not the stale global) is echoed in the retry log line;
#   (6) F12-2 model-aware cold-open margin: opus cold open = 500, sonnet (non-opus) cold open = 550
#       (the sonnet default cleared its documented 400s band top, the thin-margin bug).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

DSID="DSID-fixed-0000"; PSID="PSID-fixed-0000"; CSID="CSID-fixed-0000"
CAMPAIGN_ID="camp-abc123"
CLAWDND_LEAN_TAIL="${CLAWDND_LEAN_TAIL:-8}"
DM_CFG="/tmp/dm.mcp.json"; PLAYER_CFG="/tmp/player.mcp.json"; COMP_CFG="/tmp/companion_0.mcp.json"
CLAWDND_DM_MODEL="sonnet"; CLAWDND_ACTOR_MODEL="sonnet"; BUDGET="1.50"

# Stub `claude`: print the exact argv (each arg in «»). Stub `timeout`: print TIMEOUT-WRAP <secs>
# then exec-through to the stubbed claude with the REST of the argv — so the proof shows the
# deadline AND the wrapped command exactly as the real `timeout <secs> claude …` would run.
claude() { printf 'CLAUDE-ARGV-BEGIN\n'; local a; for a in "$@"; do printf '  «%s»\n' "$a"; done; printf 'CLAUDE-ARGV-END\n'; }
timeout() { printf 'TIMEOUT-WRAP «%s»\n' "$1"; shift; "$@"; }

# ---- VERBATIM: scripts/play.sh dm_turn() argv assembly (the timeout-wrapped invoke) -----------
# Mirrors dm_turn: lean args -> effort arg -> beat_timeout via the shared helper -> the
# `timeout "$beat_timeout" claude -p …` wrapper. Sinks (> "$out", retry, jq) dropped. $1=first $2=msg
play_dm_turn_argv() {
  local first="$1" msg="$2" campaign_id="${CAMPAIGN_ID:-}" resume=() extra=() beat_timeout
  clawdnd_dm_lean_args "$first" "$campaign_id" "$CLAWDND_LEAN_TAIL"
  if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
    resume=("${CLAWDND_DM_LEAN_SESSION[@]}"); extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
  elif [ "$first" = "0" ]; then resume=(--resume "$DSID"); else resume=(--session-id "$DSID"); fi
  clawdnd_dm_effort_arg "$first"
  beat_timeout="$(clawdnd_dm_timeout "$first")"
  timeout "$beat_timeout" \
    claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
      --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose
  # Prove the retry log line uses the resolved $beat_timeout, not the stale global.
  printf 'RETRY-LOG-LINE: [play] DM turn rc=124 (timeout=%ss) — retrying once with a fresh session\n' "$beat_timeout"
}

# ---- VERBATIM: scripts/play_party.sh turn() DM-branch argv assembly (newly timeout-wrapped) ----
# $1=first $2=msg ; companion branch ($kind=actor) is NEVER timeout-wrapped (player untouched).
party_turn_argv() {
  local kind="$1" first="$2" msg="$3" sid="$4" cfg="${5:-}" resume=() extra=() beat_timeout
  [ "$first" = "0" ] && resume=(--resume "$sid") || resume=(--session-id "$sid")
  if [ "$kind" = "dm" ]; then
    clawdnd_dm_lean_args "$first" "${CAMPAIGN_ID:-}" "$CLAWDND_LEAN_TAIL"
    if [ "${#CLAWDND_DM_LEAN_SESSION[@]}" -gt 0 ]; then
      resume=("${CLAWDND_DM_LEAN_SESSION[@]}"); extra=("${CLAWDND_DM_LEAN_EXTRA[@]}")
    fi
    clawdnd_dm_effort_arg "$first"
    beat_timeout="$(clawdnd_dm_timeout "$first")"
    timeout "$beat_timeout" \
      claude -p "$msg" ${resume[@]+"${resume[@]}"} ${extra[@]+"${extra[@]}"} --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
        --model "$CLAWDND_DM_MODEL" ${CLAWDND_DM_EFFORT[@]+"${CLAWDND_DM_EFFORT[@]}"} --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
        --output-format stream-json --verbose
  else
    # Companion facade: NO timeout wrapper — exactly as today.
    claude -p "$msg" "${resume[@]}" --mcp-config "$cfg" --strict-mcp-config \
      --model "$CLAWDND_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose
  fi
}

hr() { printf '\n========== %s ==========\n' "$1"; }

# Default CLAWDND_DM_MODEL here is "sonnet" (set above) -> the cold-open default is the non-opus 550s.
hr "S1 play.sh COLD OPEN (first=1, sonnet), no env -> timeout 550"
out1="$(play_dm_turn_argv 1 'Begin the session.')"; printf '%s\n' "$out1"
hr "S2 play.sh ROUTINE beat (first=0), no env -> timeout 360"
out2="$(play_dm_turn_argv 0 'The player does: opens the door.')"; printf '%s\n' "$out2"
hr "S3 play.sh COLD OPEN + WORLDOS_COLDOPEN_TIMEOUT=600 -> timeout 600"
out3="$(WORLDOS_COLDOPEN_TIMEOUT=600 play_dm_turn_argv 1 'Begin the session.')"; printf '%s\n' "$out3"
hr "S4 play.sh ROUTINE + CLAWDND_BEAT_TIMEOUT=150 -> timeout 150 (cold open unaffected)"
out4="$(CLAWDND_BEAT_TIMEOUT=150 play_dm_turn_argv 0 'The player does: opens the door.')"; printf '%s\n' "$out4"
hr "S5 play_party.sh COLD OPEN DM (first=1, sonnet) -> timeout 550"
out5="$(party_turn_argv dm 1 'You are the Dungeon Master. Begin.' "$DSID")"; printf '%s\n' "$out5"
hr "S6 play_party.sh ROUTINE DM beat (first=0) -> timeout 360"
out6="$(party_turn_argv dm 0 'This beat, the party acts.' "$DSID")"; printf '%s\n' "$out6"
hr "S7 play_party.sh COMPANION facade turn -> NO timeout wrapper (player turn unaffected)"
out7="$(party_turn_argv actor 0 'Take your action through your tools.' "$CSID" "$COMP_CFG")"; printf '%s\n' "$out7"
# F12-2: the cold-open deadline is model-aware. Opus (the DEFAULT DM model in every lane) -> 500;
# sonnet / any non-opus (the explicit A/B opt-in) -> 550 (cleared the 400s band-top thin-margin bug).
hr "S8 play.sh COLD OPEN with CLAWDND_DM_MODEL=opus -> timeout 500"
out8="$(CLAWDND_DM_MODEL=opus play_dm_turn_argv 1 'Begin the session.')"; printf '%s\n' "$out8"
hr "S9 play.sh COLD OPEN with CLAWDND_DM_MODEL=sonnet -> timeout 550 (NOT the old 400 band-top)"
out9="$(CLAWDND_DM_MODEL=sonnet play_dm_turn_argv 1 'Begin the session.')"; printf '%s\n' "$out9"

# ---- Assertions -------------------------------------------------------------------
hr "ASSERTIONS"
fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# (1) cold open (sonnet) -> 550; (2) routine -> 360; and the cold open is NOT the routine 360 (tiered).
chk "S1 play.sh cold open wraps timeout 550"     'printf "%s" "$out1" | grep -q -- "TIMEOUT-WRAP «550»"'
chk "S1 play.sh cold open is NOT routine 360"     '! printf "%s" "$out1" | grep -q -- "TIMEOUT-WRAP «360»"'
chk "S1 play.sh cold open still has --effort max" 'printf "%s" "$out1" | grep -A1 -- "«--effort»" | grep -q -- "«max»"'
chk "S2 play.sh routine wraps timeout 360"       'printf "%s" "$out2" | grep -q -- "TIMEOUT-WRAP «360»"'
chk "S2 play.sh routine still --effort medium"    'printf "%s" "$out2" | grep -A1 -- "«--effort»" | grep -q -- "«medium»"'
# (3) env overrides, independent per tier.
chk "S3 WORLDOS_COLDOPEN_TIMEOUT=600 -> timeout 600" 'printf "%s" "$out3" | grep -q -- "TIMEOUT-WRAP «600»"'
chk "S4 CLAWDND_BEAT_TIMEOUT=150 -> routine timeout 150" 'printf "%s" "$out4" | grep -q -- "TIMEOUT-WRAP «150»"'
# (5) retry log line uses the RESOLVED deadline (the cold open's, the routine's) — not stale.
chk "S1 retry log line says timeout=550s"        'printf "%s" "$out1" | grep -q -- "timeout=550s"'
chk "S2 retry log line says timeout=360s"        'printf "%s" "$out2" | grep -q -- "timeout=360s"'
# play_party parity.
chk "S5 play_party cold open wraps timeout 550"  'printf "%s" "$out5" | grep -q -- "TIMEOUT-WRAP «550»"'
chk "S6 play_party routine wraps timeout 360"    'printf "%s" "$out6" | grep -q -- "TIMEOUT-WRAP «360»"'
# (4) the player / companion facade turn is NEVER timeout-wrapped (player turn unaffected).
chk "S7 companion turn has NO timeout wrapper"   '! printf "%s" "$out7" | grep -q -- "TIMEOUT-WRAP"'
chk "S7 companion turn still runs claude"        'printf "%s" "$out7" | grep -q -- "CLAUDE-ARGV-BEGIN"'
# (6) F12-2 model-aware cold-open margin: opus -> 500 (unchanged, the shipped default); sonnet -> 550
#     (the bump). The sonnet cold open must NOT be the old 400s band-top — the thin-margin bug.
chk "S8 opus cold open wraps timeout 500"        'printf "%s" "$out8" | grep -q -- "TIMEOUT-WRAP «500»"'
chk "S9 sonnet cold open wraps timeout 550"      'printf "%s" "$out9" | grep -q -- "TIMEOUT-WRAP «550»"'
chk "S9 sonnet cold open is NOT the old 400 band-top" '! printf "%s" "$out9" | grep -q -- "TIMEOUT-WRAP «400»"'

hr "RESULT"
[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
