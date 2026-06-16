#!/usr/bin/env python3
"""Canonical per-lens SCORING NOISE FLOOR — the single source of truth.

The three LLM lenses (story-craft/Tolkien, mechanical, angry-dm/5e-fidelity) are stochastic: two
behaviorally-GREEN runs that share a comparability key still differ run-to-run. ``qa/test_lens_variance.py``
measured that spread on the committed corpus; this module is where the resulting floor lives so EVERY
consumer reads the same numbers:

  - ``qa/test_lens_variance.py``  — asserts the live corpus spread stays under this floor.
  - ``qa/detect_regression.py``   — a candidate-vs-baseline lens delta within the floor is **noise, not a
                                    regression** (single-duo for velocity, median-of-N for gating).
  - ``qa/SCORING.md`` § "Variance & noise floor" — the human-readable mirror; keep it in sync with this.

`max_range` (max within-cluster max-min) is the right threshold for a TWO-run DELTA: a candidate run that
differs from the baseline by no more than the range floor is indistinguishable from scorer noise.
`max_stdev` (population stdev) is the right threshold for the SPREAD of an N-run cluster.

If a future re-score blows past these, raise the floor HERE (one edit) and update SCORING.md — the
self-consistency test in test_lens_variance.py guards the structure.
"""

from __future__ import annotations

# lens db-column -> (max population stdev across a comparable GREEN cluster, max range of that cluster).
# Mirrors qa/SCORING.md § "Variance & noise floor" (measured: story 0.15/0.30, mech 0.25/0.50,
# angry 0.35/0.70; documented floor rounds up for headroom on a thin corpus).
NOISE_FLOOR: dict[str, dict] = {
    "story_overall": {"max_stdev": 0.20, "max_range": 0.40, "label": "story-craft (Tolkien)"},
    "mech_overall": {"max_stdev": 0.30, "max_range": 0.60, "label": "mechanical"},
    "angrydm_overall": {"max_stdev": 0.40, "max_range": 0.80, "label": "angry-dm (5e fidelity)"},
}

# The three lens columns, in canonical display order.
LENS_COLUMNS: tuple[str, ...] = tuple(NOISE_FLOOR.keys())


def delta_floor(lens_col: str) -> float:
    """The noise band (max_range) for a candidate-vs-baseline delta on ``lens_col``."""
    return float(NOISE_FLOOR[lens_col]["max_range"])


def classify_delta(lens_col: str, delta: float) -> str:
    """Classify a candidate-minus-baseline ``delta`` against the lens noise floor.

    Returns "IMPROVED" (delta beyond +floor), "REGRESSED" (beyond -floor), or "WITHIN_NOISE".
    """
    floor = delta_floor(lens_col)
    if delta > floor:
        return "IMPROVED"
    if delta < -floor:
        return "REGRESSED"
    return "WITHIN_NOISE"
