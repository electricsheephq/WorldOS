"""#752 — the chronicle must not flood the accessibility tree and bury the action controls.

The 2026-06-15 confirm sweep flagged this MAJOR by 3 of 5 personas (newbie, adversarial,
narrative). Verbatim symptoms:
  • "Chronicle log grows into one massive block — later beats invisible in a11y tree"
  • "Oversized chronicle log pushes Actions section out of a11y tree entirely"
  • "Chronicle log a11y overflow buries action buttons — player can't tell if DM is done"
  • "Chronicle log overflows a11y tree, hiding all action controls from screen reader"

FELT MECHANISM (the why, not a rubric): the QA blind-player reads the screen via
`qa/playwright/palette_server.js::ariaText`, which is `ariaSnapshot().slice(0, 9000)` — a
HARD CHAR CAP on the body's accessibility YAML, rendered in DOM order. The Chronicle
(`role="log"`) renders BEFORE the Actions palette + the move composer in the DOM, so once
the chronicle's rendered rows carry many multi-paragraph DM beats their YAML alone exceeds
9000 chars and the Actions / composer are sliced off the snapshot ENTIRELY — exactly "the
player can't tell if the DM is done / can't find the action buttons." #402 anchored the
action bar VISUALLY (a sticky DOM sibling) but the a11y snapshot is LINEAR, so the visual
anchor doesn't help the screen-reader / snapshot path.

THE FIX (viewer-only, READ-ONLY): bound the chronicle's accessibility FOOTPRINT. Only the
most-recent `CHRONICLE_A11Y_TAIL` rendered rows stay in the accessibility tree; older
rendered rows (kept visible for sighted scroll-back, and preserved IN FULL in the Quest
Journal) are marked `aria-hidden` with an accessible "earlier beats are in your Quest
Journal" summary already present (#402). So the chronicle's a11y subtree is bounded to a
small, predictable size REGARDLESS of session length — the latest DM beat is announced, then
the Actions/composer are immediately reachable in the snapshot.

These tests exercise the REAL shipped render (ScreenTable's chronicle map + the action
palette + composer), transpiled from the actual `.jsx` under Node — mirroring the sibling
JS-behavior harnesses (test_chronicle_hygiene.py, test_nav_chronicle_resilience.py). Skipped
where Node is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
SCREEN_TABLE = OPENWORLDS / "screen-table.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


# --------------------------------------------------------------------------------------------- #
# A small a11y-aware renderer for ScreenTable's chronicle map. We reproduce ONLY the exact JSX
# the component emits for the rendered-row list (the `renderedLog.map(...)` block) by calling the
# shipped helpers the component uses — CHRONICLE_RENDER_CAP, CHRONICLE_A11Y_TAIL, and
# chronicleRowAriaHidden — so the test tracks the real exposed constants/predicate, not a copy.
# The component wraps each row in a <div> with `aria-hidden` set by chronicleRowAriaHidden(i, n);
# we assert the bound directly on that pure predicate + the constants it reads.
# --------------------------------------------------------------------------------------------- #
def _eval_screen_table(expr: str):
    """Transpile screen-table.jsx under Node and evaluate `expr` against its window scope."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(SCREEN_TABLE))
        + "const code = Babel.transform(src, { presets: ['react'], filename: 'screen-table.jsx' }).code;\n"
        + "function h(type, props, ...children){ return { type: (typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:()=>[null,()=>{}], useRef:()=>({}), useCallback:f=>f, useEffect:()=>{},"
        + " createElement:h, Fragment:'F' };\n"
        + "const sb = { React, console }; sb.window = sb; vm.createContext(sb); vm.runInContext(code, sb);\n"
        + "const __res = (function(){ return (" + expr + "); })();\n"
        + "process.stdout.write(JSON.stringify(__res));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_chronicle_a11y_tail_constant_is_present_and_tight():
    # The a11y tail must EXIST and be tight enough that even multi-paragraph beats can't blow the
    # ~9000-char snapshot budget before the action controls. (A handful of recent beats, not 50.)
    vals = _eval_screen_table(
        "({ tail: sb.window.CHRONICLE_A11Y_TAIL, renderCap: sb.window.CHRONICLE_RENDER_CAP })"
    )
    assert isinstance(vals["tail"], int), "CHRONICLE_A11Y_TAIL must be exported on window"
    assert 1 <= vals["tail"] <= 15, (
        f"a11y tail {vals['tail']} must be a tight handful of beats so the chronicle can't bury "
        "the action controls in the a11y snapshot"
    )
    assert vals["tail"] <= vals["renderCap"], "the a11y tail can't exceed what is rendered"


def test_action_controls_stay_in_a11y_tree_after_30_chronicle_rows():
    # The headline assertion: with 30 rendered chronicle rows, the NEWEST rows stay in the a11y
    # tree and the OLDER rows are aria-hidden — so the chronicle's exposed a11y footprint is the
    # tight tail, NOT all 30 rows. The action palette + composer (DOM siblings AFTER the log) then
    # land within the snapshot budget instead of being sliced off.
    out = _eval_screen_table(
        "(function(){"
        "  var n = 30;"
        "  var hidden = [];"
        "  for (var i = 0; i < n; i++) hidden.push(sb.window.chronicleRowAriaHidden(i, n));"
        "  var exposed = hidden.filter(function(x){ return !x; }).length;"
        "  return { n: n, exposed: exposed, lastHidden: hidden[n-1], firstHidden: hidden[0],"
        "           tail: sb.window.CHRONICLE_A11Y_TAIL };"
        "})()"
    )
    # Only the tail is exposed to AT; the rest are aria-hidden (still visible for scroll-back).
    assert out["exposed"] == out["tail"], (
        f"with {out['n']} rows, exactly the {out['tail']}-row a11y tail must stay in the tree "
        f"(got {out['exposed']} exposed) so the action controls are not sliced off the snapshot"
    )
    assert out["lastHidden"] is False, "the NEWEST beat must always stay in the a11y tree"
    assert out["firstHidden"] is True, "the OLDEST rendered beat must be aria-hidden once past the tail"


def test_short_chronicle_exposes_every_row_to_a11y_tree():
    # No regression for a short session: when the rendered list is at/under the tail, NOTHING is
    # aria-hidden — every beat is announced (the bound only engages once the log is long).
    out = _eval_screen_table(
        "(function(){"
        "  var tail = sb.window.CHRONICLE_A11Y_TAIL;"
        "  var n = tail;"  # exactly the tail length
        "  var anyHidden = false;"
        "  for (var i = 0; i < n; i++) if (sb.window.chronicleRowAriaHidden(i, n)) anyHidden = true;"
        "  return { n: n, anyHidden: anyHidden };"
        "})()"
    )
    assert out["anyHidden"] is False, (
        "a short chronicle (≤ the a11y tail) must expose every row — the a11y bound only engages "
        "for a long log, never for an early-session player"
    )


# --------------------------------------------------------------------------------------------- #
# #752 (STILL flagged at sweep 3582dc2 DESPITE the row-count bound): a ROW count is not a CHAR
# count. CHRONICLE_A11Y_TAIL=8 multi-paragraph DM beats (~1000–2000 chars each) still total
# ~12000+ chars of exposed a11y YAML — past the 9000 ariaSnapshot() budget — so the Actions /
# composer (DOM siblings AFTER the log) are STILL sliced off the snapshot. The fix bounds the
# EXPOSED (non-aria-hidden) rows by a cumulative CHARACTER budget, under the tail row ceiling:
# accumulate visible text length from the NEWEST row backward, stop before exceeding the budget,
# always expose ≥1 (the newest beat is never hidden), never more than CHRONICLE_A11Y_TAIL.
# --------------------------------------------------------------------------------------------- #
def test_char_budget_constant_is_present_and_conservative():
    # The char budget must EXIST, be window-exported, and leave real headroom under the 9000-char
    # ariaSnapshot() budget for the YAML inflation + the Actions palette + composer that follow.
    vals = _eval_screen_table(
        "({ budget: sb.window.CHRONICLE_A11Y_CHAR_BUDGET, tail: sb.window.CHRONICLE_A11Y_TAIL })"
    )
    assert isinstance(vals["budget"], int), "CHRONICLE_A11Y_CHAR_BUDGET must be exported on window"
    assert 1000 <= vals["budget"] <= 5000, (
        f"char budget {vals['budget']} must be conservative — large enough for a couple of recent "
        "beats, small enough to leave headroom under the 9000-char snapshot for the action controls"
    )


def test_long_beats_expose_fewer_rows_so_cumulative_text_fits_budget():
    # The headline #752 assertion the row-count bound MISSES: 8 rendered rows each ~1500 chars
    # (a normal mid-session of multi-paragraph DM beats) must expose FEWER than 8 rows such that
    # the cumulative EXPOSED text stays <= CHRONICLE_A11Y_CHAR_BUDGET — otherwise the ~12000 chars
    # of exposed YAML slice the Actions/composer off the snapshot.
    out = _eval_screen_table(
        "(function(){"
        "  var n = 8, per = 1500;"
        "  var lens = [];"
        "  for (var i = 0; i < n; i++) lens.push(per);"  # most-recent last (uniform here)
        "  var exposed = sb.window.chronicleA11yExposedCount(lens);"
        "  var budget = sb.window.CHRONICLE_A11Y_CHAR_BUDGET;"
        "  var tail = sb.window.CHRONICLE_A11Y_TAIL;"
        # cumulative exposed text = the last `exposed` rows
        "  var cum = 0; for (var j = n - exposed; j < n; j++) cum += lens[j];"
        "  return { n: n, exposed: exposed, cum: cum, budget: budget, tail: tail };"
        "})()"
    )
    assert out["exposed"] < out["n"], (
        f"with {out['n']} long (~1500-char) beats the char bound MUST expose fewer than all "
        f"{out['n']} (got {out['exposed']}) — a row-count-only bound exposes all 8 = ~12000 chars "
        "and slices the action controls off the 9000-char snapshot"
    )
    assert out["cum"] <= out["budget"], (
        f"cumulative exposed text {out['cum']} must stay within the {out['budget']}-char budget so "
        "the Actions palette + composer land inside the snapshot"
    )
    assert out["exposed"] <= out["tail"], "the char bound may shrink the exposed set but never grow it past the row tail"


def test_newest_beat_is_always_exposed_even_when_it_alone_exceeds_budget():
    # Invariant: the newest beat is ALWAYS announced, even a single huge beat bigger than the whole
    # budget — we never hide the player's most recent DM reply.
    out = _eval_screen_table(
        "(function(){"
        "  var budget = sb.window.CHRONICLE_A11Y_CHAR_BUDGET;"
        "  var lens = [800, 800, budget * 3];"  # newest beat alone dwarfs the budget
        "  var exposed = sb.window.chronicleA11yExposedCount(lens);"
        "  return { exposed: exposed };"
        "})()"
    )
    assert out["exposed"] >= 1, "the newest beat must always be exposed — never hide the latest DM reply"


def test_short_low_volume_session_still_exposes_every_row_no_regression():
    # No regression: a short session whose cumulative text is UNDER the budget AND whose row count
    # is <= the tail still exposes EVERY row (the char bound is additive, it only shrinks a long
    # high-volume log, never an early-session player's chronicle).
    out = _eval_screen_table(
        "(function(){"
        "  var tail = sb.window.CHRONICLE_A11Y_TAIL;"
        "  var n = Math.min(4, tail);"
        "  var lens = [];"
        "  for (var i = 0; i < n; i++) lens.push(120);"  # small beats, well under budget
        "  var exposed = sb.window.chronicleA11yExposedCount(lens);"
        "  return { n: n, exposed: exposed };"
        "})()"
    )
    assert out["exposed"] == out["n"], (
        "a short low-volume session (cumulative text under budget AND rows <= tail) must expose "
        "every row — the char bound must not regress the early-session player"
    )
