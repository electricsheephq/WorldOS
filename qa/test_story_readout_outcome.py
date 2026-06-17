"""Self-test for story_readout._outcome()'s garble guard.

Found on the gs-ledger-deep authored-spine run (BG golden spine, story-craft 4.8): on a beat with
several tool results joined into one blob, the permissive `_OUT_KEYS` value capture grabbed adjacent
narration/lore prose as a boolean "value", rendering an outcome line like

    → dc=14 success="The Book of Grace dc=13 success="Two finds. First dc=13 success="You read Tobias Q

A boolean engine key (success/failed/crit/hit/defeated/dead) whose captured value isn't true/false is
such a mis-capture — _outcome now drops it, so the readout (a shipped artifact) never renders garble.
The COVERAGE stamp is computed elsewhere (analyze) and is unaffected.

Stdlib + pytest only; self-contained. Run single-process:
    uv run --directory servers/engine python -m pytest qa/test_story_readout_outcome.py -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_readout as sr  # noqa: E402


def test_narration_captured_as_success_value_is_dropped():
    # The real-world garble shape: a `"success"` key whose value is narration prose, not a boolean.
    garble = '"dc": 14, "success": "The Book of Grace is open, success= laid bare on the lectern'
    out = sr._outcome(garble)
    assert "Book of Grace" not in out, out
    assert "success=The" not in out and 'success="' not in out, out
    # the legitimate numeric key still renders
    assert "dc=14" in out, out


def test_real_boolean_outcomes_survive():
    clean = '"roll": 8, "dc": 13, "success": false'
    out = sr._outcome(clean)
    assert "roll=8" in out and "dc=13" in out and "success=false" in out, out


def test_true_survives_and_quotes_are_stripped():
    # attitude is a free-text key; its surrounding quotes should be stripped, not rendered.
    txt = '"success": true, "attitude": "anxious"'
    out = sr._outcome(txt)
    assert "success=true" in out, out
    assert "attitude=anxious" in out and 'attitude="' not in out, out
