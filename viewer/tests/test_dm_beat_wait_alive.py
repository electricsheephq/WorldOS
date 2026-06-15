"""Behaviour tests for the dogfood-#1 fix: make the DM-beat wait FEEL ALIVE.

The #1 felt issue from a live newbie dogfood (the player scored 7/10 and loved the
writing): "after declaring, everything locks for 30+ seconds with only static text
saying it'll take 'a minute or two' — NO spinner, NO streaming — leaving a new player
genuinely unsure whether the app froze." The DM beat is generation-bound (~100-150s and
can't be made meaningfully faster), so the fix is NOT speed — it is making the WAIT read
as obviously-alive so the player never thinks it's frozen.

ROOT CAUSE of "NO spinner, NO motion" on the LATER-beat path (the path whose copy is the
verbatim "a minute or two"): #385 made the COLD-OPEN obviously-alive on the surfaces that
matter — a rotating headline + a NON-aria-hidden ticking elapsed, so a static screenshot /
an ariaSnapshot / a frame-grabbed snapshot DIFFERS render-to-render. But the LATER beat was
left with the original #336 treatment: its only motion is CSS-keyframe animation (the
opacity-pulsing dots + the label shimmer) plus an `aria-hidden` ticking elapsed. CSS
animation is invisible to a still frame AND to an ariaSnapshot, and the elapsed counter is
aria-hidden — so to a static screenshot / a snapshot / a screen reader the LATER-beat
affordance collapsed to TWO UNCHANGING STRINGS ("The Dungeon Master is narrating" +
"Weaving the next beat — this can take a minute or two"). That is exactly the frozen-app
illusion the newbie hit. This file pins the LATER-beat affordance to the SAME obviously-
alive contract the cold-open already meets: its accessible text must visibly CHANGE as the
wait elapses (a ticking elapsed in the a11y tree + a rotating "still working" phrase).

ROOT CAUSE of "NO streaming": `streaming` only flips when the /events poll ingests an early
progress beat (a model-authored progress line, or the wrapper heartbeat). When that flip
DOES happen, the streamed prose lands "above" in the chronicle — but a newbie staring at the
wait may not connect the spinner to the paragraph that appeared higher up (or may have
scrolled). So when the turn is `streaming`, the affordance now surfaces the LATEST streamed
line INLINE (a "scene building above" confirmation + the actual newest prose), so the player
sees real scene text wired directly to the spinner. (The streaming_finding in the PR reports
whether the run's emission gap is engine/wrapper-side — that is fixed there, not the viewer.)

These tests render the REAL `DmNarratingBeat` by transpiling the actual screen-table.jsx with
the SAME vendored Babel-standalone the browser uses and walking its element tree at controllable
elapsed times — so they track the shipped JSX, not a reimplementation (mirrors
test_cold_open_progress.py).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_TABLE = _OPENWORLDS / "screen-table.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A self-contained Node harness. React.createElement is captured into a plain node tree;
# hooks are stubbed (useState seeds its initial value; useEffect is a no-op — elapsed is driven
# via the injected Date.now, not the 1s interval). We render DmNarratingBeat at a chosen elapsed
# time + flags and walk the tree to collect every visible text string, every text string NOT under
# an aria-hidden subtree (the "accessible" text a screen reader / ariaSnapshot sees), the count of
# role="status" regions, and whether a given data-worldos-testid is present.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

let NOW = 1000000;

function makeReact() {
  function useState(init) {
    const v = (typeof init === 'function') ? init() : init;
    return [v, function () {}];
  }
  function useEffect() {}
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

function renderBeat(props) { return DmNarratingBeat(props); }

// Walk a node tree, collecting strings. accessibleOnly skips any subtree whose props carry
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

// Find a node carrying data-worldos-testid === id; return the joined text inside it (or null).
function testidText(node, id) {
  if (node == null || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const c of node) { const r = testidText(c, id); if (r !== null) return r; }
    return null;
  }
  const props = node.props || {};
  if (props['data-worldos-testid'] === id) return collectText(node, false, false).join(' ');
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) { const r = testidText(c, id); if (r !== null) return r; }
  return null;
}

function report(props) {
  const tree = renderBeat(props);
  return {
    allText: collectText(tree, false, false).join(' ␟ '),
    accessibleText: collectText(tree, true, false).join(' ␟ '),
    statusRegions: countStatusRegions(tree),
    hasNavAffordance: testidText(tree, 'narrating-nav-affordance') !== null,
    latestLine: testidText(tree, 'narrating-latest-line'),
  };
}

const h = {
  // render the LATER beat (firstBeat=false) at `elapsedSec` seconds in, with the given streaming
  // flag + optional latest streamed line.
  render: (elapsedSec, opts) => report({
    since: NOW - elapsedSec * 1000,
    firstBeat: false,
    streaming: !!(opts && opts.streaming),
    latestStreamed: opts && opts.latestStreamed,
    onNavigate: function () {},
  }),
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class DmBeatWaitAliveTests(unittest.TestCase):
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

    # === PART 1: a LATER-beat in-flight turn must read as ALIVE in a STILL frame ============
    # The verbatim newbie finding ("static text... NO spinner, NO motion") is the later-beat path
    # (its copy is "a minute or two"). The only motion it had was CSS-animated (the opacity dots +
    # the label shimmer) + an aria-hidden ticking elapsed — all invisible to a static screenshot /
    # an ariaSnapshot. So the affordance read as TWO UNCHANGING STRINGS = frozen. This asserts the
    # later beat now changes its ACCESSIBLE text over elapsed time (a snapshot/screen-reader can see
    # the motion), exactly as #385 already does for the cold-open.

    def test_later_beat_accessible_text_changes_over_time(self):
        out = self._run(
            "({ early: h.render(2, {}), mid: h.render(30, {}), late: h.render(120, {}) })"
        )
        a_early = out["early"]["accessibleText"]
        a_mid = out["mid"]["accessibleText"]
        a_late = out["late"]["accessibleText"]
        self.assertNotEqual(
            a_early, a_mid,
            "the later-beat wait must read as ALIVE in a still frame: its accessible text must "
            "CHANGE as the wait elapses (not collapse to two unchanging strings) — dogfood #1",
        )
        self.assertNotEqual(
            a_mid, a_late,
            "the later-beat accessible text must keep changing deeper into the wait (it can't go static)",
        )

    def test_later_beat_alive_signal_does_not_spam_a_screen_reader(self):
        # the alive-in-a-still-frame change must be COARSE (a rotating phrase on a ~few-second
        # cadence), NOT a per-second tick — so a polite region isn't re-announced every second.
        # This pins the same contract test_cold_open_progress.test_later_beat_treatment_is_unchanged
        # asserts: the ACCESSIBLE text is stable across a 1-second step (t=7 vs t=8).
        out = self._run("({ a: h.render(7, {}), b: h.render(8, {}) })")
        self.assertEqual(
            out["a"]["accessibleText"], out["b"]["accessibleText"],
            "the later-beat alive signal must be stable second-to-second (no per-tick a11y spam) — "
            "it changes on a coarse rotation cadence, not every second",
        )

    def test_later_beat_visible_elapsed_clock_still_ticks(self):
        # the sighted-user proof-of-life — a visible m:ss elapsed clock — stays in the FULL (all) text
        # and ticks every second (the #336 cue is intact). It is aria-hidden, so it does not spam a
        # screen reader; the coarse rotating phrase is the accessible-tree alive signal instead.
        out = self._run("({ early: h.render(2, {}), mid: h.render(30, {}) })")
        self.assertIn("0:02", out["early"]["allText"],
                      "the visible ticking elapsed clock must still be present (a sighted still-alive cue)")
        self.assertIn("0:30", out["mid"]["allText"])

    def test_later_beat_still_announces_via_a_status_region(self):
        # the wait must be announced to a screen reader (the a11y consumer the §8.2 harness reads).
        out = self._run("h.render(3, {})")
        self.assertGreaterEqual(out["statusRegions"], 1,
                                "the later-beat wait must still announce itself via a role=status region")

    def test_later_beat_keeps_the_minute_or_two_expectation(self):
        # the honest expectation copy stays (a content-rich beat runs ~90-120s); we are adding MOTION,
        # not removing the reassurance.
        out = self._run("h.render(5, {})")
        self.assertIn("minute", out["allText"].lower(),
                      "the later-beat wait must still set the honest ~minute expectation")

    def test_later_beat_keeps_the_navigation_affordance(self):
        # regression guard for #G3-UX FIX 2: the "while you wait, review your sheet/map/journal"
        # invitation must survive (read-only surfaces stay open during compose).
        out = self._run("h.render(5, {})")
        self.assertTrue(out["hasNavAffordance"],
                        "the later-beat wait must keep the read-only navigation affordance (#G3-UX FIX 2)")

    # === PART 2: when STREAMING, the affordance surfaces the live scene text INLINE ==========
    # When the turn is `streaming` (live /events prose arrived for this in-flight turn), the affordance
    # confirms "the scene is building above" AND surfaces the LATEST streamed line right at the spinner,
    # so the player sees real scene text wired to the wait (not just a paragraph that landed higher up).

    def test_streaming_beat_shows_scene_building_above_state(self):
        out = self._run("h.render(20, { streaming: true })")
        self.assertIn("above", out["allText"].lower(),
                      "a streaming later-beat must confirm the scene is being written ABOVE (not a generic wait)")

    def test_streaming_beat_surfaces_the_latest_streamed_line(self):
        out = self._run(
            "h.render(20, { streaming: true, latestStreamed: 'The hinges shriek as the door yawns wide.' })"
        )
        latest = out["latestLine"]
        self.assertIsNotNone(
            latest,
            "a streaming later-beat must surface the latest streamed prose INLINE in the affordance "
            "(data-worldos-testid=narrating-latest-line) so the player sees real scene text at the spinner",
        )
        self.assertIn("The hinges shriek as the door yawns wide.", latest)

    def test_streaming_latest_line_is_capped_for_a_long_paragraph(self):
        # a long streamed paragraph must not blow out the affordance — the inline preview is capped
        # with an ellipsis so the wait stays a compact spinner, not a wall of text.
        out = self._run(
            "h.render(20, { streaming: true, latestStreamed: ('Lorem ipsum dolor sit amet. ').repeat(40) })"
        )
        latest = out["latestLine"]
        self.assertIsNotNone(latest, "a long streamed line must still render an inline preview")
        self.assertLessEqual(len(latest), 290, "the inline preview must be length-capped (~280 chars)")
        self.assertTrue(latest.endswith("…"), "a truncated preview must end with an ellipsis")

    def test_non_streaming_beat_does_not_invent_a_latest_line(self):
        # before any prose streams there is nothing to preview — the inline latest-line must be absent
        # (the player shouldn't see a stale/empty preview box).
        out = self._run("h.render(20, { streaming: false, latestStreamed: 'should be ignored' })")
        self.assertIsNone(out["latestLine"],
                          "a non-streaming wait must NOT render an inline streamed-line preview")

    def test_streaming_without_a_line_does_not_render_an_empty_preview(self):
        # streaming flipped (e.g. by the wrapper heartbeat, which renders no prose) but no real
        # paragraph yet → the inline preview must be absent, not an empty box.
        out = self._run("h.render(20, { streaming: true })")
        self.assertIsNone(out["latestLine"],
                          "streaming with no streamed line yet must NOT render an empty inline preview")

    # === PART 3: the COLD-OPEN path is unchanged (regression guard) =========================
    # The fix is later-beat-scoped. The cold-open already meets the alive contract (#385) and must
    # keep its rotating-flavor + composing voice — assert we didn't disturb it.
    def test_cold_open_path_unchanged(self):
        out = self._run(
            "report({ since: NOW - 2000, firstBeat: true, streaming: false, onNavigate: function(){} })"
        )
        self.assertIn("composing", out["allText"].lower(),
                      "the cold-open must still read as the DM actively composing (#385, unchanged)")


if __name__ == "__main__":
    unittest.main()
