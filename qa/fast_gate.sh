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

# ── OPT-IN green-cache (WORLDOS_FASTGATE_CACHE=1; default OFF == today's behavior) ──────────────
# Re-running the gate on an UNCHANGED tree is pure waste: the deterministic tier is a pure function
# of the working tree. When the cache is enabled we key a GREEN verdict on (git HEAD + persona +
# gate args) and short-circuit an identical re-run to the cached verdict instead of re-running the
# inner gate. This is an INNER-LOOP convenience only — strictly additive (off by default), never
# wired into CI, and a RED result is NEVER cached (so a failure is always reproducible).
#   WORLDOS_FASTGATE_CACHE=1        — turn the cache ON (default empty/0 = off = run every time).
#   WORLDOS_FASTGATE_CACHE_DIR=...  — override the cache root (default qa/.cache/fastgate; gitignored).
#   WORLDOS_FASTGATE_PERSONA=...    — persona discriminator folded into the key (default "default").
#   WORLDOS_FASTGATE_INNER_CMD=...  — test seam: run this instead of the real pytest tier (mockable).
_fastgate_cache_enabled() { [ "${WORLDOS_FASTGATE_CACHE:-0}" = "1" ]; }

_fastgate_cache_key() {
  # Key the cached verdict on the exact tree + persona + invocation. HEAD is the cheap proxy for the
  # tree; a dirty tree still re-uses the key, so the cache is for clean SHA-pinned re-runs (the
  # documented use). The persona + gate-args make the key persona/arg specific.
  local head persona args
  head="$(git rev-parse HEAD 2>/dev/null || echo nohead)"
  persona="${WORLDOS_FASTGATE_PERSONA:-default}"
  args="${FASTGATE_ARGS:-$*}"
  printf '%s' "$head|$persona|$args" \
    | { command -v shasum >/dev/null 2>&1 && shasum -a 256 || md5; } \
    | awk '{print $1}'
}

# Inner deterministic tier — the real work. Returns the inner gate exit code. The test seam lets a
# bash test substitute a mock that does not need uv/pytest (and counts its own invocations).
_fastgate_run_inner() {
  if [ -n "${WORLDOS_FASTGATE_INNER_CMD:-}" ]; then
    "$WORLDOS_FASTGATE_INNER_CMD"
    return $?
  fi
  echo "── FAST-GATE Tier 0 — deterministic engine + seat-path + rest/travel + combat ($0) ──"
  # Per the QA cost/signal map, these gate signals are FREE + INSTANT (no LLM, no cold-open world build):
  #   - test_canon_abilities  : SEAT-PATH skill correctness (the optimizer skill-case crit) + ability derivation (G2 data)
  #   - test_character_skill_normalization : the model-boundary skill-name normalizer (G2)
  #   - test_rests            : rest-that-restores HP/slots + advances the clock (G1 rest limb)
  #   - test_travel           : travel-that-moves location + clock (G1 travel limb)
  #   - test_combat           : combat resolved THROUGH the engine — start_combat/attack/rounds (G1 combat limb)
  #   - test_combat_smoke     : engine-only combat smoke (Track 2d) — random-vs-random ALL-MECHANICS-fire
  #                             (hit/miss/crit/save/condition/concentration/resource/XP/death-save) + a
  #                             spell-resolution sweep (every category). A TRUSTWORTHY mechanical signal
  #                             independent of the LLM scorer; deterministic, ~1-2s. (Path is relative to
  #                             servers/engine, like the qa-release-gate-tests CI job.)
  local TESTS="tests/test_canon_abilities.py tests/test_character_skill_normalization.py tests/test_rests.py tests/test_travel.py tests/test_combat.py ../../qa/test_combat_smoke.py"
  if uv run --directory servers/engine python -m pytest -q -p no:xdist $TESTS >"$LOG" 2>&1; then
    echo "  ✓ deterministic engine tier: $(grep -oE '[0-9]+ passed' "$LOG" | tail -1)"
    return 0
  fi
  echo "❌ FAST-GATE T0: ITERATE — deterministic engine tier failed (G1 combat/rest/travel · G2 seat-path):"
  grep -E "FAILED|Error|assert|[0-9]+ failed" "$LOG" | head -15
  echo "   full log: $LOG"
  return 1
}

CACHE_FILE=""
if _fastgate_cache_enabled; then
  CACHE_DIR="${WORLDOS_FASTGATE_CACHE_DIR:-$ROOT/qa/.cache/fastgate}"
  CACHE_FILE="$CACHE_DIR/$(_fastgate_cache_key "$@").pass"
  if [ -f "$CACHE_FILE" ]; then
    echo "── FAST-GATE cache HIT (WORLDOS_FASTGATE_CACHE=1) — short-circuiting to the cached GREEN verdict ──"
    echo "  key persona=${WORLDOS_FASTGATE_PERSONA:-default} sha=$(git rev-parse --short HEAD 2>/dev/null || echo nohead)"
    echo "  cached: $(cat "$CACHE_FILE" 2>/dev/null)"
    echo "  (inner gate NOT re-run; delete $CACHE_FILE or unset WORLDOS_FASTGATE_CACHE to force.)"
    exit 0
  fi
fi

if _fastgate_run_inner "$@"; then
  if [ -n "$CACHE_FILE" ]; then
    mkdir -p "$(dirname "$CACHE_FILE")"
    printf 'PASS %s sha=%s persona=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "$(git rev-parse --short HEAD 2>/dev/null || echo nohead)" \
      "${WORLDOS_FASTGATE_PERSONA:-default}" > "$CACHE_FILE"
    echo "  (cached GREEN verdict at $CACHE_FILE — re-run short-circuits while the tree is unchanged.)"
  fi
else
  rc=$?
  # NEVER cache a RED result: leave the file absent so the failure stays reproducible on re-run.
  [ -n "$CACHE_FILE" ] && rm -f "$CACHE_FILE" 2>/dev/null || true
  exit "$rc"
fi

echo "✅ FAST-GATE T0 PASS — core engine loops + seat path intact (\$0, deterministic)."
echo "   When iterating on DM-craft / UX, run the LLM probe next (rotated persona + ≥6-beat duo, ~13 min / ~\$2.5)."
echo "   Before merge/release, run the milestone 5-persona sweep + RRI. See docs/qa/FAST_GATE.md."
