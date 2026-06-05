#!/usr/bin/env bash
# WorldOS FAST-GATE — Tier 0 (deterministic, $0, ~30-60s).
#
# The cheap inner-loop substitute for the ~90-min / ~$10 five-persona milestone sweep. It catches the
# STRUCTURAL + SEAT-PATH + ENGINE-TRANSITION regression classes (G1 combat/rest/travel, G2 seat-path
# data correctness) in CI for FREE — the classes that do NOT need an LLM to detect. Most regressions
# we actually shipped (the skill-case +3-not-+6 crit, frozen-clock, combat-unresolved, version-skew)
# are in this tier; catching them here means we never burn a 90-minute sweep to discover a $0 bug.
#
# PASS here  => the core engine loops + seat path are intact; safe to keep iterating, and the change
#               is WORTH a heavier LLM probe / milestone sweep.
# ITERATE    => fix this now; do NOT spend the sweep.
#
# This is an ITERATION signal, NOT a release verdict. G3 (cross-persona satisfaction) and G5 quality
# scores are irreducibly LLM-judged + noisy and stay in the LLM probe / milestone sweep. The honest
# 3-tier design + the ~75-80% signal accounting (and why naive mid-arc snapshot seeding is a
# false-confidence trap) is in docs/qa/FAST_GATE.md.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 2
LOG="${TMPDIR:-/tmp}/wos_fastgate_t0.log"

echo "── FAST-GATE Tier 0 — deterministic engine + seat-path + rest/travel + combat ($0) ──"
# Per the QA cost/signal map, these gate signals are FREE + INSTANT (no LLM, no cold-open world build):
#   - test_canon_abilities  : SEAT-PATH skill correctness (the optimizer skill-case crit) + ability derivation (G2 data)
#   - test_character_skill_normalization : the model-boundary skill-name normalizer (G2)
#   - test_rests            : rest-that-restores HP/slots + advances the clock (G1 rest limb)
#   - test_travel           : travel-that-moves location + clock (G1 travel limb)
#   - test_combat           : combat resolved THROUGH the engine — start_combat/attack/rounds (G1 combat limb)
TESTS="tests/test_canon_abilities.py tests/test_character_skill_normalization.py tests/test_rests.py tests/test_travel.py tests/test_combat.py"

if uv run --directory servers/engine python -m pytest -q -p no:xdist $TESTS >"$LOG" 2>&1; then
  echo "  ✓ deterministic engine tier: $(grep -oE '[0-9]+ passed' "$LOG" | tail -1)"
else
  echo "❌ FAST-GATE T0: ITERATE — deterministic engine tier failed (G1 combat/rest/travel · G2 seat-path):"
  grep -E "FAILED|Error|assert|[0-9]+ failed" "$LOG" | head -15
  echo "   full log: $LOG"
  exit 1
fi

echo "✅ FAST-GATE T0 PASS — core engine loops + seat path intact (\$0, deterministic)."
echo "   When iterating on DM-craft / UX, run the LLM probe next (rotated persona + ≥6-beat duo, ~13 min / ~\$2.5)."
echo "   Before merge/release, run the milestone 5-persona sweep + RRI. See docs/qa/FAST_GATE.md."
