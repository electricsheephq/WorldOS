"""Behavior tests for the #385 cold-open "obviously-alive" waiting state.

The first DM beat of a session (the cold-open / Act-opening) legitimately takes minutes
(the engine builds the world + sets the scene; the /chat tail carries no streaming). The
shipped `DmNarratingBeat` (viewer/openworlds/screen-table.jsx) DID animate — pulsing dots,
a shimmer label, a live elapsed counter — but every one of those aliveness cues was either
decorative-and-`aria-hidden` (the dots, the shimmer) or `aria-hidden` (the elapsed counter)
AND the visible label never changed. So to the accessibility tree (what the §8.2 newbie
harness reads via `ariaSnapshot`) AND to a single static screenshot, the whole affordance
collapsed to two unchanging strings — it read as a FROZEN app, and a fresh player gave up
(satisfaction 4/10, gave_up:true). #385.

The fix makes the FIRST-BEAT branch obviously alive on the surfaces that matter:
  • a ROTATING flavor headline ("The Dungeon Master is composing your opening scene…",
    "Lighting the candles…", …) that changes every ~4s — so consecutive renders / aria
    snapshots DIFFER;
  • the elapsed counter is NO LONGER aria-hidden for the cold-open (it's in the a11y tree
    and visibly ticks) — the strongest "still alive" cue a screen reader / snapshot can see;
  • the per-second-changing text lives OUTSIDE the aria-live region (a separate
    visually-hidden role="status" announces a STABLE reassurance ONCE) so a screen reader
    isn't spammed every tick.
Later beats (the ~35–60s norm) keep the original #336 treatment unchanged.

These tests exercise the REAL component by transpiling the actual `screen-table.jsx` with the
SAME bundled Babel-standalone the browser uses and rendering `DmNarratingBeat` under a tiny
`createElement`-capturing React stub at controllable elapsed times — so the test tracks the
shipped JSX, not a reimplementation (mirrors test_recovery_timing.py / test_sanitize_narration.py).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_TABLE = _OPENWORLDS / "screen-table.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A self-contained Node harness. React.createElement is captured into a plain node tree
# ({type, props, children}); hooks are stubbed (useState seeds its initial value, useEffect
# is a no-op — we drive elapsed via the injected `Date.now`, not the 1s interval). We render
# DmNarratingBeat at a chosen elapsed time and walk the tree to collect: every visible text
# string, every text string that is NOT under an aria-hidden subtree (the "accessible" text a
# screen reader / ariaSnapshot would see), and whether a role="status" live region exists.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

let NOW = 1000000;

// ---- a createElement-capturing React: enough to render a pure component to a node tree ----
function makeReact() {
  function useState(init) {
    const v = (typeof init === 'function') ? init() : init;
    return [v, function () {}];               // first-render value; setState is a no-op here
  }
  function useEffect() {}                       // the 1s interval is irrelevant — we set NOW directly
  function useRef(init) { return { current: init }; }
  function useCallback(fn) { return fn; }
  function createElement(type, props) {
    const children = Array.prototype.slice.call(arguments, 2);
    return { type, props: props || {}, children };
  }
  return { useState, useEffect, useRef, useCallback, createElement, Fragment: 'F' };
}

const React = makeReact();
const sandbox = {
  React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
  fetch: () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }),
  console,
};
sandbox.window = sandbox;
sandbox.Date = { now: () => NOW };
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(screen_table)s);

const DmNarratingBeat = sandbox.window.DmNarratingBeat;
if (typeof DmNarratingBeat !== 'function') throw new Error('DmNarratingBeat not exported');

// Render the component (a function component) by invoking it and getting its element tree.
function renderBeat(props) { return DmNarratingBeat(props); }

// Walk a node tree, collecting strings. `accessibleOnly` skips any subtree whose props carry
// aria-hidden truthy (mirrors how ariaSnapshot / a screen reader drops aria-hidden content).
function collectText(node, accessibleOnly, hiddenAncestor) {
  let out = [];
  if (node == null || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    if (!(accessibleOnly && hiddenAncestor)) out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) {
    for (const c of node) out = out.concat(collectText(c, accessibleOnly, hiddenAncestor));
    return out;
  }
  const props = node.props || {};
  const hidden = hiddenAncestor || props['aria-hidden'] === 'true' || props['aria-hidden'] === true;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) out = out.concat(collectText(c, accessibleOnly, hidden));
  return out;
}

// Does the tree contain an element with role="status" (a live region)? Return how many.
function countStatusRegions(node) {
  let n = 0;
  if (node == null || typeof node !== 'object') return 0;
  if (Array.isArray(node)) { for (const c of node) n += countStatusRegions(c); return n; }
  const props = node.props || {};
  if (props.role === 'status') n += 1;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) n += countStatusRegions(c);
  return n;
}

// Collect the text inside every role="status" subtree (what a polite region would announce).
function statusText(node, inStatus) {
  let out = [];
  if (node == null || typeof node !== 'object') {
    return out;
  }
  if (Array.isArray(node)) { for (const c of node) out = out.concat(statusText(c, inStatus)); return out; }
  const props = node.props || {};
  const here = inStatus || props.role === 'status';
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) {
    if (typeof c === 'string' || typeof c === 'number') { if (here) out.push(String(c)); }
    else out = out.concat(statusText(c, here));
  }
  return out;
}

function report(props) {
  const tree = renderBeat(props);
  return {
    allText: collectText(tree, false, false).join(' ␟ '),
    accessibleText: collectText(tree, true, false).join(' ␟ '),
    statusRegions: countStatusRegions(tree),
    statusText: statusText(tree, false).join(' '),
  };
}

const h = {
  setNow: (n) => { NOW = n; },
  // render at `elapsedSec` seconds in (since = NOW - elapsedSec*1000) for the given firstBeat flag.
  render: (elapsedSec, firstBeat) => report({ since: NOW - elapsedSec * 1000, firstBeat }),
  flavor: () => sandbox.window.DM_COLD_OPEN_FLAVOR,
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class ColdOpenProgressTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_SCREEN_TABLE, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "screen_table": json.dumps(str(_SCREEN_TABLE)),
            "script": json.dumps(script),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)

    # --- the flavor pool exists and is non-trivial ---------------------------
    def test_flavor_pool_is_exported_and_rotatable(self):
        flavor = self._run("h.flavor()")
        self.assertIsInstance(flavor, list)
        self.assertGreaterEqual(len(flavor), 4, "need several lines so the headline visibly rotates")
        self.assertTrue(all(isinstance(s, str) and s.strip() for s in flavor))

    # --- #385 CORE: the cold-open ACCESSIBLE text CHANGES over time ----------
    # This is the exact failure: the §8.2 harness reads ariaSnapshot (accessible text only) and a
    # static screenshot. If the accessible text is identical at t=2s and t=30s, it reads as frozen.
    def test_cold_open_accessible_text_changes_over_time(self):
        out = self._run(
            "({ early: h.render(2, true), mid: h.render(30, true), late: h.render(120, true) })"
        )
        a_early = out["early"]["accessibleText"]
        a_mid = out["mid"]["accessibleText"]
        a_late = out["late"]["accessibleText"]
        self.assertNotEqual(a_early, a_mid, "cold-open accessible text must change (not frozen) as time passes")
        self.assertNotEqual(a_mid, a_late, "cold-open accessible text must keep changing deeper into the wait")
        # the live elapsed readout is part of the ACCESSIBLE text (not aria-hidden) for the cold-open
        self.assertIn("0:02", a_early)
        self.assertIn("0:30", a_mid)
        self.assertIn("2:00", a_late)

    # --- the headline rotates through the flavor pool ------------------------
    def test_cold_open_headline_rotates_through_flavor(self):
        flavor = self._run("h.flavor()")
        # sample a few 4s buckets; collect the headline (accessible) text seen
        out = self._run(
            "[0,4,8,12,20].map(function(s){ return h.render(s, true).accessibleText; })"
        )
        seen = set()
        for txt in out:
            for line in flavor:
                if line in txt:
                    seen.add(line)
        self.assertGreaterEqual(len(seen), 3, "the cold-open headline should rotate through several flavor lines")

    # --- the cold-open headline is composing-voiced, NOT the static old copy --
    def test_cold_open_drops_the_static_setting_the_scene_label(self):
        text = self._run("h.render(1, true).allText")
        self.assertIn("composing", text.lower(), "cold-open should read as the DM actively composing")
        # The old dead-end phrasing must be gone from the cold-open.
        self.assertNotIn("Setting the opening scene — the first beat", text)

    # --- the per-second-changing text is NOT inside the announced live region -
    # A polite role="status" re-announces its whole text on every change; the ticking elapsed must
    # live OUTSIDE it so a screen reader isn't spammed every second. The live region announces a
    # STABLE reassurance that does NOT contain the elapsed clock.
    def test_cold_open_live_region_is_stable_not_per_second(self):
        out = self._run("({ a: h.render(7, true), b: h.render(8, true) })")
        # there IS a status region (so the wait is announced at all)
        self.assertGreaterEqual(out["a"]["statusRegions"], 1, "cold-open must still announce the wait via a status region")
        # the announced (status) text must NOT carry the per-second clock → identical across a 1s step
        self.assertEqual(
            out["a"]["statusText"], out["b"]["statusText"],
            "the announced live-region text must be stable second-to-second (no per-tick screen-reader spam)",
        )
        # …and concretely it must not contain an m:ss clock token (a digit-colon-digit pattern)
        import re as _re
        self.assertIsNone(
            _re.search(r"\d:\d\d", out["a"]["statusText"]),
            "the announced live-region text must not contain a ticking m:ss clock",
        )

    # --- LATER beats keep the original #336 treatment (regression guard) ------
    # The fix is first-beat-scoped: a later beat keeps the steady "narrating" label and its elapsed
    # stays aria-hidden (short waits → no per-second announcement). So its ACCESSIBLE text does NOT
    # carry the ticking clock and is stable across a 1s step.
    def test_later_beat_treatment_is_unchanged(self):
        out = self._run("({ a: h.render(7, false), b: h.render(8, false) })")
        self.assertIn("The Dungeon Master is narrating", out["a"]["allText"])
        self.assertNotIn("composing", out["a"]["allText"].lower())
        # later-beat elapsed is aria-hidden → not in accessible text → accessible text stable 1s apart
        self.assertEqual(
            out["a"]["accessibleText"], out["b"]["accessibleText"],
            "later-beat accessible text should be unchanged second-to-second (elapsed stays aria-hidden)",
        )
        # and the visible elapsed still ticks in the full text (the #336 visual cue is intact)
        self.assertIn("0:07", out["a"]["allText"])
        self.assertIn("0:08", out["b"]["allText"])


if __name__ == "__main__":
    unittest.main()
