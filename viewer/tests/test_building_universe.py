"""Behavior tests for the "Building your universe" full-screen loading experience.

Pressing Start / Resume → Play (screen-launcher.jsx) or Bind (screen-create.jsx) mints a DM
provider session and then generates the cold-open — two long waits with a full page RELOAD
(window.location.assign) wedged between them. Before this feature the player saw "nothing
happens" then an abrupt read-only flash. building-universe.jsx replaces both waits with one
intentional, on-brand loading state that:
  • appears the instant play is pressed (window.OpenWorldsBuilding.begin),
  • SURVIVES the reload (the intent is stamped in sessionStorage, not React state), and
  • hands off to the live table when the FIRST DM narration beat lands in
    liveSession.chatBeats (the same real milestone the in-table cold-open clears on).

The bar of honesty: rotating lore flavor + a live elapsed readout + an indeterminate sweep —
NO faked percentage. These tests assert exactly those contracts against the REAL component by
transpiling the actual building-universe.jsx with the SAME bundled Babel-standalone the browser
uses and rendering BuildingUniverse / driving OpenWorldsBuilding under a tiny stub — so the test
tracks the shipped JSX, not a reimplementation (mirrors test_cold_open_progress.py).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_BUILDING = _OPENWORLDS / "building-universe.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A self-contained Node harness. React.createElement is captured into a plain node tree; hooks are
# stubbed (useState seeds its initial value, useEffect/useRef/useCallback are inert) so we render
# BuildingUniverse at a chosen elapsed time. A tiny in-memory sessionStorage + dispatchEvent lets us
# exercise the OpenWorldsBuilding persistence facade (begin / read / clear) the way the page does.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

let NOW = 1000000;

function makeReact() {
  function useState(init) { const v = (typeof init === 'function') ? init() : init; return [v, function () {}]; }
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
// In-memory sessionStorage so OpenWorldsBuilding.{begin,read,clear} are exercised for real.
const _store = {};
const sessionStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: (k) => { delete _store[k]; },
};
const _events = {};
const sandbox = {
  React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  sessionStorage,
  CustomEvent: function (type, opts) { this.type = type; this.detail = (opts || {}).detail; },
  setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
  console,
};
sandbox.window = sandbox;
sandbox.window.addEventListener = (t, fn) => { (_events[t] = _events[t] || []).push(fn); };
sandbox.window.removeEventListener = () => {};
const _dispatched = [];
sandbox.window.dispatchEvent = (e) => { _dispatched.push(e.type); (_events[e.type] || []).forEach((fn) => fn(e)); return true; };
sandbox.Date = { now: () => NOW };
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(building)s);

const BuildingUniverse = sandbox.window.BuildingUniverse;
if (typeof BuildingUniverse !== 'function') throw new Error('BuildingUniverse not exported');

function collectText(node, accessibleOnly, hiddenAncestor) {
  let out = [];
  if (node == null || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    if (!(accessibleOnly && hiddenAncestor)) out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) { for (const c of node) out = out.concat(collectText(c, accessibleOnly, hiddenAncestor)); return out; }
  const props = node.props || {};
  const hidden = hiddenAncestor || props['aria-hidden'] === 'true' || props['aria-hidden'] === true;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) out = out.concat(collectText(c, accessibleOnly, hidden));
  return out;
}
function statusText(node, inStatus) {
  let out = [];
  if (node == null || typeof node !== 'object') return out;
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
function countStatus(node) {
  let n = 0;
  if (node == null || typeof node !== 'object') return 0;
  if (Array.isArray(node)) { for (const c of node) n += countStatus(c); return n; }
  const props = node.props || {};
  if (props.role === 'status') n += 1;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) n += countStatus(c);
  return n;
}

function report(props) {
  const tree = BuildingUniverse(props);
  return {
    allText: collectText(tree, false, false).join(' ␟ '),
    accessibleText: collectText(tree, true, false).join(' ␟ '),
    statusRegions: countStatus(tree),
    statusText: statusText(tree, false).join(' '),
  };
}

const B = sandbox.window.OpenWorldsBuilding;
const h = {
  setNow: (n) => { NOW = n; },
  render: (elapsedSec, opts) => report(Object.assign({ record: { startedAt: NOW - elapsedSec * 1000, kind: (opts && opts.kind) || 'play' } }, opts || {})),
  flavor: () => sandbox.window.BUILDING_FLAVOR,
  // persistence facade
  begin: (meta) => B.begin(meta),
  read: () => B.read(),
  clear: () => B.clear(),
  rawStore: () => Object.assign({}, _store),
  backstopMs: () => B.backstopMs,
  beginEventsFired: () => _dispatched.filter((t) => t === 'clawdnd:building-begin').length,
  // write a raw record directly (the script string runs in the OUTER node scope where `window`
  // is not global — go through the sandbox's storage via this helper instead).
  seedRaw: (k, v) => { sessionStorage.setItem(k, v); },
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class BuildingUniverseTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_BUILDING, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "building": json.dumps(str(_BUILDING)),
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

    # --- persistence facade: begin() stamps sessionStorage AND announces -----------------------
    def test_begin_persists_to_session_storage_and_is_readable(self):
        out = self._run(
            "(function(){ h.begin({world:'baldurs-gate', kind:'play'});"
            " var rec = h.read();"
            " return { hasKey: ('openworlds.building' in h.rawStore()), startedAt: typeof rec.startedAt,"
            "          world: rec.world, kind: rec.kind, announced: h.beginEventsFired() }; })()"
        )
        self.assertTrue(out["hasKey"], "begin() must stamp the intent into sessionStorage (survives the reload)")
        self.assertEqual(out["startedAt"], "number")
        self.assertEqual(out["world"], "baldurs-gate")
        self.assertEqual(out["kind"], "play")
        self.assertGreaterEqual(out["announced"], 1, "begin() must dispatch clawdnd:building-begin so a mounted App shows it pre-reload")

    def test_clear_removes_the_flag(self):
        out = self._run(
            "(function(){ h.begin({world:'x'}); var before = ('openworlds.building' in h.rawStore());"
            " h.clear(); var after = ('openworlds.building' in h.rawStore());"
            " return { before: before, after: after, readNull: (h.read()===null) }; })()"
        )
        self.assertTrue(out["before"])
        self.assertFalse(out["after"], "clear() must remove the persisted flag")
        self.assertTrue(out["readNull"], "read() must return null after clear()")

    def test_read_self_heals_a_stale_record_past_the_backstop(self):
        # A record older than the 12-min backstop is stale → read() drops it so a fresh load
        # doesn't re-enter the overlay forever.
        out = self._run(
            "(function(){ var ms = h.backstopMs();"
            " h.setNow(50000000);"
            " h.seedRaw('openworlds.building', JSON.stringify({startedAt: 50000000 - ms - 1000}));"
            " var r = h.read(); return { readNull: (r===null), cleared: !('openworlds.building' in h.rawStore()) }; })()"
        )
        self.assertTrue(out["readNull"], "a record older than the backstop must read as null")
        self.assertTrue(out["cleared"], "the stale record must be cleared from storage")

    # --- the overlay is honest: NO faked percentage --------------------------------------------
    def test_overlay_has_no_fake_percentage(self):
        out = self._run("h.render(20, {})")
        self.assertNotIn("%", out["allText"], "the loading copy must not show a fake percentage — honesty bar")

    # --- #385 lesson: the accessible text CHANGES over time (not a frozen app) ------------------
    def test_overlay_accessible_text_changes_over_time(self):
        out = self._run(
            "({ early: h.render(1, {}), mid: h.render(30, {}), late: h.render(120, {}) })"
        )
        self.assertNotEqual(out["early"]["accessibleText"], out["mid"]["accessibleText"],
                            "the loading text must change as time passes (not frozen)")
        self.assertNotEqual(out["mid"]["accessibleText"], out["late"]["accessibleText"],
                            "the loading text must keep changing deeper into the wait")
        # the live elapsed readout is part of the ACCESSIBLE text (proof-of-life on a screenshot AND aria)
        self.assertIn("0:01", out["early"]["allText"])
        self.assertIn("0:30", out["mid"]["allText"])
        self.assertIn("2:00", out["late"]["allText"])

    # --- the headline rotates through the lore flavor pool -------------------------------------
    def test_headline_rotates_through_flavor(self):
        flavor = self._run("h.flavor()")
        self.assertGreaterEqual(len(flavor), 4)
        out = self._run("[0,4,8,14,22,30].map(function(s){ return h.render(s, {}).allText; })")
        seen = set()
        for txt in out:
            for line in flavor:
                if line in txt:
                    seen.add(line)
        self.assertGreaterEqual(len(seen), 3, "the headline should rotate through several lore lines")

    # --- the announced live region is STABLE (no per-second screen-reader spam) -----------------
    def test_live_region_is_stable_not_per_second(self):
        out = self._run("({ a: h.render(7, {}), b: h.render(8, {}) })")
        self.assertGreaterEqual(out["a"]["statusRegions"], 1, "the wait must be announced via a status region")
        self.assertEqual(out["a"]["statusText"], out["b"]["statusText"],
                         "the announced text must be stable second-to-second (no per-tick spam)")
        import re as _re
        self.assertIsNone(_re.search(r"\d:\d\d", out["a"]["statusText"]),
                          "the announced live-region text must not carry a ticking clock")

    # --- the handoff flourish reads as 'your story begins' --------------------------------------
    def test_handoff_phase_shows_story_begins(self):
        out = self._run("h.render(40, { handoff: true })")
        self.assertIn("begins", out["allText"].lower(), "the handoff flourish should read as the story beginning")

    # --- the forge entry tunes the eyebrow ------------------------------------------------------
    def test_forge_kind_changes_eyebrow(self):
        play = self._run("h.render(5, { record: { startedAt: 1000000 - 5000, kind: 'play' } })")
        forge = self._run("h.render(5, { record: { startedAt: 1000000 - 5000, kind: 'forge' } })")
        self.assertIn("universe", play["allText"].lower())
        self.assertIn("hero", forge["allText"].lower(), "the forge flow should name the hero binding")


if __name__ == "__main__":
    unittest.main()
