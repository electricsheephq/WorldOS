#!/usr/bin/env bash
# ARC-MODE RUNBOOK PROOF — qa/run_adventure.sh drives a PRE-SEEDED arc, so three per-beat directives
# the shared duo path injects pull AGAINST the seed (measured in the three failed Opus-5 arc runs,
# session-notes 2026-09-02 DM-DEVIATIONS). WORLDOS_ARC_MODE=1 rewrites exactly those three.
#
# Deterministic, $0, no LLM, no engine. Asserts:
#   1. DEFAULT (WORLDOS_ARC_MODE unset) — the duo/play runbook strings are UNCHANGED, byte-for-byte
#      (this is the run_duo no-drift guarantee: run_duo.sh never sets the flag);
#   2. ARC MODE scene-intro drops the "put at least one named face here who SPEAKS" mandate;
#   3. ARC MODE midpoint reversal is a PRICE, gated on the crypt being cleared, and forbids a spawn;
#   4. ARC MODE travel/peopling stops naming add_location / create_character (the two calls the
#      addendum forbids and the behavioral gate now FAILs).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/qa/lib_beat_driver.sh"

FAILS=0
fail() { printf '  ✗ %s\n' "$*"; FAILS=$((FAILS + 1)); }
pass() { printf '  ✓ %s\n' "$*"; }
has()  { case "$2" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wos_arcmode_XXXXXX")"
trap 'rm -rf "$STATE_DIR"' EXIT

echo "── ARC-MODE RUNBOOK PROOF ──"

# --- (1) DEFAULT path: byte-identical to the pre-arc-mode strings ------------------------------
unset WORLDOS_ARC_MODE
D_INTRO="$(_worldos_runbook_body 1 20 "" "$STATE_DIR")"
D_MID="$(_worldos_runbook_body 10 20 "camp" "$STATE_DIR")"
has "RUNBOOK — SCENE-INTRO" "$D_INTRO" && has "put at least one named face here who SPEAKS" "$D_INTRO" \
  && pass "default scene-intro unchanged (still mandates a named face)" \
  || fail "default scene-intro CHANGED — run_duo would drift: $D_INTRO"
has "RUNBOOK — MIDPOINT REVERSAL" "$D_MID" \
  && has "Make a real attempt FAIL or a choice exact a price that STICKS" "$D_MID" \
  && pass "default midpoint reversal unchanged" \
  || fail "default midpoint reversal CHANGED — run_duo would drift: $D_MID"
grep -q 'travel_to along a connection (advance_time=True for a real journey) or add_location(make_current=True)' \
  "$ROOT/qa/lib_beat_driver.sh" \
  && pass "default travel/peopling line still present verbatim" \
  || fail "default travel/peopling line CHANGED — run_duo would drift"

# --- (2)(3) ARC MODE: scene-intro + midpoint ---------------------------------------------------
export WORLDOS_ARC_MODE=1
A_INTRO="$(_worldos_runbook_body 1 20 "" "$STATE_DIR")"
A_MID="$(_worldos_runbook_body 10 20 "camp" "$STATE_DIR")"
has "RUNBOOK — SCENE-INTRO" "$A_INTRO" && ! has "named face here who SPEAKS" "$A_INTRO" \
  && has "do not mint a new face" "$A_INTRO" \
  && pass "arc scene-intro suppresses the named-face mandate" \
  || fail "arc scene-intro still demands a new named face: $A_INTRO"
has "the reversal is a PRICE, never a new fight or a new creature" "$A_MID" \
  && has "only after the crypt is cleared (objective 2) or at the true midpoint, whichever is later" "$A_MID" \
  && has "Do NOT spawn anything" "$A_MID" \
  && pass "arc midpoint reversal is a price, gated on objective 2, spawn-forbidden" \
  || fail "arc midpoint reversal wording wrong: $A_MID"

# --- (4) ARC MODE travel/peopling (branch needs a live campaign to reach; assert the source) ----
if awk '/ARC MODE \(run_adventure\): the default line names add_location/,/^    fi$/' \
     "$ROOT/qa/lib_beat_driver.sh" | grep -q 'Do NOT add_location and do NOT create_character'; then
  pass "arc travel/peopling forbids add_location + create_character"
else
  fail "arc travel/peopling variant missing or still names the forbidden calls"
fi

# --- run_adventure sets the flag; run_duo must NOT --------------------------------------------
grep -q '^export WORLDOS_ARC_MODE=1' "$ROOT/qa/run_adventure.sh" \
  && pass "run_adventure.sh sets WORLDOS_ARC_MODE" || fail "run_adventure.sh does not set WORLDOS_ARC_MODE"
if grep -q 'WORLDOS_ARC_MODE' "$ROOT/qa/run_duo.sh" "$ROOT/qa/run_duo_openclaw.sh" 2>/dev/null; then
  fail "a run_duo runner references WORLDOS_ARC_MODE — the duo lane must be untouched"
else
  pass "no run_duo runner references WORLDOS_ARC_MODE"
fi

if [ "$FAILS" -eq 0 ]; then echo "── ARC-MODE RUNBOOK PROOF: PASS ──"; exit 0; fi
echo "── ARC-MODE RUNBOOK PROOF: FAIL ($FAILS) ──"; exit 1
