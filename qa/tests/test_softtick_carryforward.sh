#!/usr/bin/env bash
# F04-2 PROOF — the harness soft-tick must SURFACE the living-world content the engine
# processes between beats (world beats / backlog developments / effect expiries) into the
# NEXT beat's runbook, instead of discarding it to the run log.
#
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F04-2, issue #823).
#
# Deterministic, $0, no LLM. Drives the REAL engine (advance_time) + the REAL shell helpers
# (worldos_soft_tick / worldos_runbook_for_beat / worldos_take_carryforward). Asserts:
#   1. a due thread-beat fired by the soft-tick lands in the carry-forward file as a
#      "While time passed:" block (BEFORE: discarded);
#   2. the next beat's runbook PREPENDS that block (the DM is told);
#   3. the carry is read-and-CLEARED (surfaced exactly once — the following runbook is clean);
#   4. a QUIET tick (nothing fired) writes NO carry file (no token cost on a still world).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/qa/lib_beat_driver.sh"

FAILS=0
note() { printf '  %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*"; FAILS=$((FAILS + 1)); }
pass() { printf '  ✓ %s\n' "$*"; }

STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wos_f04_2_XXXXXX")"
trap 'rm -rf "$STATE_DIR"' EXIT

# --- Seed a campaign with a thread-beat due TODAY (fires on the next clock advance) ----------
CID="$(WORLDOS_STATE_DIR="$STATE_DIR" CLAWDND_STATE_DIR="$STATE_DIR" \
  uv run --directory "$ROOT/servers/engine" python "$ROOT/qa/tests/_seed_softtick_fixture.py" 2>&1)"
if [ -z "$CID" ] || printf '%s' "$CID" | grep -qi "error\|traceback"; then
  fail "fixture seed failed: $CID"
  echo "── F04-2 PROOF: FAIL ($FAILS) ──"; exit 1
fi
note "seeded campaign $CID (state=$STATE_DIR)"

SNAP="$(worldos_snapshot_path "$STATE_DIR")"
[ -n "$SNAP" ] && pass "snapshot present for the harness" || fail "no snapshot found"

CARRY="$(worldos_carryforward_path "$STATE_DIR")"
[ -f "$CARRY" ] && fail "carry file exists BEFORE any tick (should be absent)" || pass "no carry file pre-tick"

# --- (1) Run the soft-tick. The clock is FROZEN (prev == cur), so it advances one phase, fires
#         the due thread-beat, and must persist it to the carry file. ---------------------------
PROG="$(worldos_read_progress "$STATE_DIR")"
PREV_DAY="$(printf '%s' "$PROG" | cut -f1)"
PREV_TOD="$(printf '%s' "$PROG" | cut -f2)"
worldos_soft_tick "$ROOT" "$STATE_DIR" "$PREV_DAY" "$PREV_TOD" 2>/dev/null

if [ -f "$CARRY" ]; then
  pass "soft-tick WROTE the carry-forward file"
  if grep -q "While time passed" "$CARRY"; then
    pass "carry file carries the 'While time passed' block"
  else
    fail "carry file missing the 'While time passed' header: $(cat "$CARRY")"
  fi
  if grep -qi "marketplace fixers\|the world moved\|FIXTURE-BEAT" "$CARRY"; then
    pass "carry file carries the fired thread-beat text"
  else
    fail "carry file missing the fired beat text. Contents:"; sed 's/^/      /' "$CARRY"
  fi
  # --- EXPIRY CHANNEL (the dict-repr leak). The expired clock effect (Bless) must surface as
  #     its clean NAME on the "effects that ran out" line, NEVER as the raw {character_id, name}
  #     dict repr. The engine returns expired_effects as list[{character_id, name}]; before the
  #     fix str(x) rendered the whole dict, leaking "character_id" + braces to the player. -----
  if grep -q "effects that ran out overnight: .*Bless" "$CARRY"; then
    pass "carry file carries the expired effect's clean NAME (Bless)"
  else
    fail "carry file missing the clean expired-effect name. Contents:"; sed 's/^/      /' "$CARRY"
  fi
  if grep -q "character_id" "$CARRY"; then
    fail "carry file leaked the raw dict repr (found 'character_id'). Contents:"; sed 's/^/      /' "$CARRY"
  else
    pass "carry file does NOT leak the raw dict repr (no 'character_id')"
  fi
else
  fail "soft-tick did NOT write a carry file (the F04-2 leak)"
fi

# --- (2) The next beat's runbook must PREPEND the carry block (the DM is told). --------------
RB="$(worldos_runbook_for_beat 3 8 "loc-nowhere" "$STATE_DIR")"
if printf '%s' "$RB" | grep -q "While time passed"; then
  pass "next runbook surfaces the carry block to the DM"
else
  fail "next runbook did NOT surface the carry block"
fi
if printf '%s' "$RB" | grep -q "RUNBOOK —"; then
  pass "runbook still carries its moment-specific body (carry is ADDITIVE)"
else
  fail "runbook body was clobbered by the carry prepend"
fi

# --- (3) Read-and-CLEAR: the carry surfaces exactly once. ------------------------------------
[ -f "$CARRY" ] && fail "carry file NOT cleared after the runbook read it" || pass "carry file cleared (surfaced once)"
RB2="$(worldos_runbook_for_beat 4 8 "loc-nowhere" "$STATE_DIR")"
if printf '%s' "$RB2" | grep -q "While time passed"; then
  fail "the SECOND runbook re-fed the already-surfaced carry block"
else
  pass "the second runbook is clean (no stale re-feed)"
fi
if printf '%s' "$RB2" | grep -q "RUNBOOK —"; then
  pass "the second runbook still has its body"
else
  fail "the second runbook is empty"
fi

# --- (4) A QUIET tick (clock already moved by a 'DM' beat) writes NO carry. ------------------
# Bump the clock so the soft-tick sees prev != cur and no-ops (no advance, nothing fired).
QPROG="$(worldos_read_progress "$STATE_DIR")"
QDAY="$(printf '%s' "$QPROG" | cut -f1)"; QTOD="$(printf '%s' "$QPROG" | cut -f2)"
worldos_soft_tick "$ROOT" "$STATE_DIR" "$((QDAY - 1))" "yesteryear" 2>/dev/null  # prev != cur -> no-op
[ -f "$CARRY" ] && fail "a no-op soft-tick wrote a carry file (should be silent)" || pass "no-op tick wrote no carry (no token cost on a still world)"

echo "── F04-2 PROOF: $([ "$FAILS" -eq 0 ] && echo PASS || echo "FAIL ($FAILS)") ──"
[ "$FAILS" -eq 0 ]
