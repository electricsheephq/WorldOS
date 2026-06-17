#!/usr/bin/env bash
# BEHAVIORAL TEST (no model call): proves scripts/play_party.sh's COMPANION facade turn is now
# bounded by a per-beat deadline (F12-2 audit finding F12-12). Before this, the actor branch of
# turn() ran a BARE `claude -p` with no `timeout`, so a wedged companion blocked companion_moves —
# which runs BEFORE the DM turn each beat — indefinitely (the human move was acknowledged, the
# cursor advanced, but nothing resolved). The DM branch was already wrapped; the companion was not.
#
# We source the REAL qa/lib_beat_driver.sh (for worldos_timeout) and reproduce the actor branch of
# play_party.sh turn() VERBATIM, with stub `claude`/`worldos_timeout` that print the argv. We assert:
#   (1) the COMPANION argv IS wrapped: `worldos_timeout 120 claude …` (the default actor deadline);
#   (2) WORLDOS_ACTOR_TIMEOUT overrides the deadline;
#   (3) a HUNG companion (sleep-forever stub) is killed within the deadline AND yields EMPTY output
#       (so companion_moves' `[ -n "$cm" ] &&` guard skips it — graceful degradation, beat survives);
#   (4) the deadline is SHORT enough that the kill is observable (well under a wall-clock budget).
# Self-contained under mktemp; safe on macOS dev box AND ubuntu CI (worldos_timeout falls back to a
# python3 subprocess when timeout(1) is absent, with the same rc=124 deadline semantics).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/qa/lib_beat_driver.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STATE_DIR="$TMP/state"; mkdir -p "$STATE_DIR"
COMBINED="$STATE_DIR/dm.combined.jsonl"; : > "$COMBINED"
WORLDOS_ACTOR_MODEL="sonnet"; BUDGET="1.50"
CSID="CSID-fixed-0000"; COMP_CFG="$TMP/companion_0.mcp.json"; : > "$COMP_CFG"

fail=0
chk() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi; }

# ---- VERBATIM: scripts/play_party.sh turn() ACTOR branch (F12-12 timeout-wrapped) -------------
# Mirrors the else-branch of turn(): out path -> worldos_timeout-wrapped claude -p (stdout -> $out) ->
# cost append -> the jq result extraction. $1=msg $2=sid $3=cfg. Echoes $out so the argv-capture
# probe (which writes TIMEOUT-WRAP/CLAUDE-ARGV into $out via the same redirect the real branch uses)
# can be inspected. The real branch's stdout-to-file redirect is exactly why we read the FILE, not the
# function's own stdout, to see the wrapped argv.
ACTOR_OUT=""
party_actor_turn() {
  local msg="$1" sid="$2" cfg="$3" out resume
  resume=(--resume "$sid")
  out="$STATE_DIR/companion.$(date +%s%N).jsonl"
  worldos_timeout "${WORLDOS_ACTOR_TIMEOUT:-120}" \
    claude -p "$msg" "${resume[@]}" --mcp-config "$cfg" --strict-mcp-config \
      --model "$WORLDOS_ACTOR_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
      --output-format stream-json --verbose > "$out" 2>> "$STATE_DIR/companion.err" || true
  ACTOR_OUT="$out"
  cat "$out" >> "$COMBINED"
  jq -rs 'map(select(.type=="result"))[-1].result // ""' "$out" 2>/dev/null
}

# (1)+(2): argv capture. Stub claude prints its argv in «»; stub worldos_timeout prints the deadline
# then exec-through (overrides the lib's for this argv-only probe). The real actor branch redirects
# stdout to $out, so the captured argv lands in the file ACTOR_OUT, not the function's stdout.
claude() { printf 'CLAUDE-ARGV-BEGIN\n'; local a; for a in "$@"; do printf '  «%s»\n' "$a"; done; printf 'CLAUDE-ARGV-END\n'; }
worldos_timeout() { printf 'TIMEOUT-WRAP «%s»\n' "$1"; shift; "$@"; }

party_actor_turn 'Take your action.' "$CSID" "$COMP_CFG" >/dev/null; argv_default="$(cat "$ACTOR_OUT")"
chk "companion turn IS wrapped by worldos_timeout"   'printf "%s" "$argv_default" | grep -q -- "TIMEOUT-WRAP"'
chk "default actor deadline is 120s"                 'printf "%s" "$argv_default" | grep -q -- "TIMEOUT-WRAP «120»"'
chk "companion turn still runs claude"               'printf "%s" "$argv_default" | grep -q -- "CLAUDE-ARGV-BEGIN"'
WORLDOS_ACTOR_TIMEOUT=45 party_actor_turn 'Take your action.' "$CSID" "$COMP_CFG" >/dev/null; argv_env="$(cat "$ACTOR_OUT")"
chk "WORLDOS_ACTOR_TIMEOUT=45 overrides the deadline" 'printf "%s" "$argv_env" | grep -q -- "TIMEOUT-WRAP «45»"'

# (3)+(4): a HUNG companion is actually killed within the deadline and yields empty. Use the REAL
# worldos_timeout (re-source to drop the argv-stub override) with a sleep-forever `claude` stub and a
# 2s deadline. worldos_timeout execs `claude` as an EXTERNAL command (a bash function is invisible to
# timeout(1)/the python3 subprocess), so the wedged stub is a real EXECUTABLE on PATH. Assert: returns
# empty, returns FAST (the no-timeout branch would hang ~forever), result file holds no result event.
unset -f worldos_timeout
. "$ROOT/qa/lib_beat_driver.sh"     # restore the real shim
BIN="$TMP/bin"; mkdir -p "$BIN"; PATH="$BIN:$PATH"
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
sleep 600
STUB
chmod +x "$BIN/claude"
start=$SECONDS
hung="$(WORLDOS_ACTOR_TIMEOUT=2 party_actor_turn 'Take your action.' "$CSID" "$COMP_CFG")"
elapsed=$((SECONDS - start))
chk "hung companion turn yields EMPTY (skip-safe)"   '[ -z "$hung" ]'
chk "hung companion turn is KILLED at the deadline (<=10s, not 600s)" '[ "$elapsed" -le 10 ]'

[ "$fail" = 0 ] && echo "ALL ASSERTIONS PASSED" || echo "SOME ASSERTIONS FAILED"
exit "$fail"
