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

// Find the overlay ROOT (the .building-universe element) and report its a11y-relevant attrs, so a
// test can assert the #406 fix: NOT role="dialog" aria-modal (a false modal with no focus trap).
function rootAttrs(node) {
  if (node == null || typeof node !== 'object') return null;
  if (Array.isArray(node)) { for (const c of node) { const r = rootAttrs(c); if (r) return r; } return null; }
  const props = node.props || {};
  if (typeof props.className === 'string' && props.className.indexOf('building-universe') !== -1) {
    return { role: props.role || null, ariaModal: props['aria-modal'] || null, ariaBusy: props['aria-busy'], ariaLabel: props['aria-label'] || null };
  }
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) { const r = rootAttrs(c); if (r) return r; }
  return null;
}
// Locate the "Enter anyway" escape button (className bu-escape) and confirm it's a real, wired button.
function escapeButton(node) {
  if (node == null || typeof node !== 'object') return null;
  if (Array.isArray(node)) { for (const c of node) { const r = escapeButton(c); if (r) return r; } return null; }
  const props = node.props || {};
  if (props.className === 'bu-escape') {
    const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
    return { tag: node.type, type: props.type || null, wired: typeof props.onClick === 'function',
             text: collectText(kids, false, false).join(' ').trim() };
  }
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) { const r = escapeButton(c); if (r) return r; }
  return null;
}

function report(props) {
  const tree = BuildingUniverse(props);
  return {
    allText: collectText(tree, false, false).join(' ␟ '),
    accessibleText: collectText(tree, true, false).join(' ␟ '),
    statusRegions: countStatus(tree),
    statusText: statusText(tree, false).join(' '),
    root: rootAttrs(tree),
    escape: escapeButton(tree),
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

    # --- #406 (1): the overlay root is NOT a false aria-modal dialog (no focus trap exists) -------
    def test_root_is_not_a_false_aria_modal_dialog(self):
        out = self._run("h.render(20, {})")
        root = out["root"]
        self.assertIsNotNone(root, "the overlay root (.building-universe) must be found")
        self.assertNotEqual(root["role"], "dialog",
                            "the overlay must NOT claim role=dialog — it has no focus trap (#406)")
        self.assertIsNone(root["ariaModal"],
                          "the overlay must NOT claim aria-modal=true (a false modal leaves the covered app in the tab order)")
        self.assertTrue(root["ariaBusy"], "as a loading state the root should be aria-busy while building")
        self.assertTrue(root["ariaLabel"], "the overlay keeps an aria-label")
        # the dedicated role=status announcement region is still present (the correct a11y model).
        self.assertGreaterEqual(out["statusRegions"], 1, "a dedicated role=status region still announces the wait")

    # --- #405: the manual 'Enter anyway' escape renders as a real wired button when escapable ----
    def test_enter_anyway_escape_button_renders_and_is_wired(self):
        # onEnterAnyway is a function prop (App passes building.dismiss) — supply one so the harness
        # can confirm the button is actually wired to it.
        with_escape = self._run("h.render(20, { escapable: true, onEnterAnyway: function(){} })")
        no_escape = self._run("h.render(5, { escapable: false, onEnterAnyway: function(){} })")
        self.assertIsNone(no_escape["escape"], "the escape button must NOT show before it's escapable")
        esc = with_escape["escape"]
        self.assertIsNotNone(esc, "when escapable, the 'Enter anyway' button must render")
        self.assertEqual(esc["tag"], "button", "it must be a real <button> (focusable for keyboard/screen-reader)")
        self.assertEqual(esc["type"], "button")
        self.assertTrue(esc["wired"], "the escape button must be wired to an onClick (onEnterAnyway → dismiss)")
        self.assertIn("anyway", esc["text"].lower(), "the escape reads as 'Enter anyway'")
        # and during the handoff flourish the escape is suppressed (we're already entering the table).
        handoff = self._run("h.render(20, { escapable: true, handoff: true, onEnterAnyway: function(){} })")
        self.assertIsNone(handoff["escape"], "the escape is hidden during the handoff flourish")


# ---------------------------------------------------------------------------------------------
# #405/#406: the OVERLAY LIFECYCLE — handoff / dismiss-on-error / dismiss-on-ceiling / manual
# "Enter anyway". The suite above stubs useEffect to a no-op, so the lifecycle (the whole reason
# the overlay can wedge) was UNTESTED — that is exactly why the dead-end shipped green. This harness
# runs a minimal but REAL React: useState with a working setter that re-renders, useEffect that runs
# after commit + re-runs on dep change + cleans up, useRef/useCallback with stable identity, plus a
# controllable Date.now()/setTimeout so we can advance time and fire the ceiling/escape timers. We
# drive the actual useBuildingUniverse(liveSession, sessionError) hook from building-universe.jsx
# (not a reimplementation) and assert the real exits.
_LIFECYCLE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

// ---- controllable clock + timers ----
let NOW = 1000000;
let _timerId = 1;
let _timers = [];  // { id, at, fn }
function setTimeoutImpl(fn, ms) { const id = _timerId++; _timers.push({ id, at: NOW + (ms || 0), fn }); return id; }
function clearTimeoutImpl(id) { _timers = _timers.filter((t) => t.id !== id); }
function advance(ms) {
  const target = NOW + ms;
  // fire timers in due order, allowing newly-scheduled ones within the window to also fire.
  for (;;) {
    const due = _timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    _timers = _timers.filter((t) => t !== due);
    NOW = due.at;
    due.fn();
  }
  NOW = target;
}

// ---- a tiny single-component React with WORKING hooks ----
// State + ref + callback-memo slots are positional (hooks rules: same order every render). After a
// render we run effects whose deps changed (and their cleanups), then re-render if any setState ran,
// to a fixed point — mirroring React's commit→effect→re-render loop closely enough for this hook.
function makeRuntime(renderFn) {
  let hooks = [];          // positional hook cells
  let cursor = 0;
  let dirty = false;       // a setState ran → another render pass is needed
  let lastTree = null;

  function useState(init) {
    const i = cursor++;
    if (!hooks[i]) hooks[i] = { type: 'state', value: (typeof init === 'function') ? init() : init };
    const cell = hooks[i];
    const set = (next) => {
      const v = (typeof next === 'function') ? next(cell.value) : next;
      if (!Object.is(v, cell.value)) { cell.value = v; dirty = true; }
    };
    return [cell.value, set];
  }
  function useRef(init) {
    const i = cursor++;
    if (!hooks[i]) hooks[i] = { type: 'ref', ref: { current: init } };
    return hooks[i].ref;
  }
  function depsEqual(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let k = 0; k < a.length; k++) if (!Object.is(a[k], b[k])) return false;
    return true;
  }
  function useCallback(fn, deps) {
    const i = cursor++;
    if (!hooks[i] || !depsEqual(hooks[i].deps, deps)) hooks[i] = { type: 'cb', fn, deps };
    return hooks[i].fn;
  }
  // effects are collected during render and run after commit (post-render).
  let pendingEffects = [];
  function useEffect(fn, deps) {
    const i = cursor++;
    const prev = hooks[i];
    if (!prev) { hooks[i] = { type: 'effect', deps, cleanup: null }; pendingEffects.push(i); }
    else if (!depsEqual(prev.deps, deps)) { prev.deps = deps; pendingEffects.push(i); }
    // store the fn so the post-commit pass can run exactly this render's closure.
    hooks[i].fn = fn;
  }

  const React = { useState, useRef, useCallback, useEffect,
    createElement: (type, props) => ({ type, props: props || {} }), Fragment: 'F' };

  function commitEffects() {
    const toRun = pendingEffects.slice();
    pendingEffects = [];
    for (const i of toRun) {
      const cell = hooks[i];
      if (typeof cell.cleanup === 'function') { try { cell.cleanup(); } catch (_e) {} }
      const ret = cell.fn();
      cell.cleanup = (typeof ret === 'function') ? ret : null;
    }
  }
  function renderOnce() { cursor = 0; dirty = false; lastTree = renderFn(React); commitEffects(); }
  function renderToFixedPoint(maxPasses) {
    let n = 0;
    do { renderOnce(); n++; } while (dirty && n < (maxPasses || 50));
    return lastTree;
  }
  function unmount() { for (const c of hooks) if (c && typeof c.cleanup === 'function') { try { c.cleanup(); } catch (_e) {} } }
  return { React, render: renderToFixedPoint };
}

const _store = {};
const sessionStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: (k) => { delete _store[k]; },
};
const _events = {};
const sandbox = {
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  sessionStorage,
  CustomEvent: function (type, opts) { this.type = type; this.detail = (opts || {}).detail; },
  setInterval: () => 0, clearInterval: () => {},
  setTimeout: setTimeoutImpl, clearTimeout: clearTimeoutImpl,
  console,
};
sandbox.window = sandbox;
sandbox.window.addEventListener = (t, fn) => { (_events[t] = _events[t] || []).push(fn); };
sandbox.window.removeEventListener = () => {};
sandbox.window.dispatchEvent = (e) => { (_events[e.type] || []).forEach((fn) => fn(e)); return true; };
sandbox.Date = { now: () => NOW };
// React is needed at module-eval time (the file calls React.useState etc only inside fns, but the
// component bodies reference React); inject a placeholder that the per-test runtime overwrites.
sandbox.React = { createElement: (type, props) => ({ type, props: props || {} }), Fragment: 'F' };
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(building)s);

const useBuildingUniverse = sandbox.window.useBuildingUniverse;
if (typeof useBuildingUniverse !== 'function') throw new Error('useBuildingUniverse not exported');
const B = sandbox.window.OpenWorldsBuilding;

// A driver: holds mutable inputs (liveSession.chatBeats, sessionError), renders the hook to a fixed
// point against the CURRENT inputs, and exposes the latest hook result. `h.*` mutators change inputs
// then re-render so a test reads the post-effect state.
function makeDriver() {
  const inputs = { chatBeats: [], sessionError: '' };
  let result = null;
  const rt = makeRuntime((React) => {
    sandbox.React = React;  // the component body closes over `React` from the sandbox global
    result = useBuildingUniverse({ chatBeats: inputs.chatBeats.slice() }, inputs.sessionError);
    return null;
  });
  return {
    render: () => { rt.render(); return snap(); },
    addNarration: () => { inputs.chatBeats = inputs.chatBeats.concat([{ kind: 'narration', text: 'A door creaks open.' }]); rt.render(); return snap(); },
    setError: (msg) => { inputs.sessionError = msg; rt.render(); return snap(); },
    advance: (ms) => { advance(ms); rt.render(); return snap(); },
    callDismiss: () => { result.dismiss(); rt.render(); return snap(); },
    snap,
  };
  function snap() {
    return {
      active: !!(result && result.active),
      handoff: !!(result && result.handoff),
      escapable: !!(result && result.escapable),
      hasKey: ('openworlds.building' in _store),
      now: NOW,
      ceilingMs: B.ceilingMs,
      escapeMs: B.escapeMs,
      errorGraceMs: B.errorGraceMs,
    };
  }
}

const h = {
  begin: (meta) => B.begin(meta || { world: 'baldurs-gate', kind: 'play' }),
  driver: () => makeDriver(),
  setNow: (n) => { NOW = n; },
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class BuildingUniverseLifecycleTests(unittest.TestCase):
    """The REAL effect-driven lifecycle (the suite above stubs useEffect, so these paths — the
    handoff, the #405 escape hatches — were entirely uncovered)."""

    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_BUILDING, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _LIFECYCLE_HARNESS % {
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

    # --- sanity: the harness actually runs effects (the begin() flag → active overlay) ----------
    def test_harness_runs_effects_and_overlay_is_active_while_building(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); var s = d.render();"
            " return { active: s.active, handoff: s.handoff }; })()"
        )
        self.assertTrue(out["active"], "with a building flag set, the overlay must be active (effects run)")
        self.assertFalse(out["handoff"])

    # --- HANDOFF: the first narration beat flips active→false (after the flourish) --------------
    def test_first_narration_beat_hands_off_and_clears(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); d.render();"
            " var afterBeat = d.addNarration();"            # narration lands → handoff flourish begins
            " var afterFlourish = d.advance(1500);"          # past the 1400ms flourish → clear
            " return { handoffWhileFlourishing: afterBeat.handoff, activeAfter: afterFlourish.active,"
            "          keyAfter: afterFlourish.hasKey }; })()"
        )
        self.assertTrue(out["handoffWhileFlourishing"], "the first narration beat should enter the handoff flourish")
        self.assertFalse(out["activeAfter"], "after the flourish the overlay must clear (hand off to the table)")
        self.assertFalse(out["keyAfter"], "the persisted building flag must be cleared on handoff")

    # --- #405 (1) DISMISS ON ERROR: a cold-open/session error past the grace clears the overlay ----
    def test_session_error_dismisses_after_grace(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); var s0 = d.render();"
            " var pastGrace = d.advance(s0.errorGraceMs + 500);"   # build is now seconds in (the real cold-open phase)
            " var after = d.setError('cold-open failed: provider error');"
            " return { errorGraceMs: s0.errorGraceMs, activeBefore: pastGrace.active,"
            "          activeAfter: after.active, keyAfter: after.hasKey }; })()"
        )
        self.assertLess(out["errorGraceMs"], 10 * 1000, "the error grace must be short (a few seconds)")
        self.assertTrue(out["activeBefore"], "overlay is up while building (no error yet)")
        self.assertFalse(out["activeAfter"], "a cold-open/session error must dismiss the overlay (yield to the table)")
        self.assertFalse(out["keyAfter"], "the building flag must be cleared on a hard error")

    # --- #405 (1) the grace guard: a STALE error at the click instant must NOT nuke a fresh build ---
    # The bridge lastError can carry a leftover error from a prior failed attempt at the instant a new
    # build begins (pre-reload). It must be ignored within the grace; if a fresh build clears it, the
    # overlay survives — but if it persists past the grace it IS a real failure and dismisses.
    def test_stale_error_within_grace_does_not_dismiss_but_persisting_error_does(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver();"
            " var withStaleErr = d.setError('stale leftover from a prior attempt');"   # error present from t=0
            " var withinGrace = d.snap();"                  # still within grace → must remain active
            " var cleared = d.setError('');"                # a fresh build clears the leftover error
            " var afterClear = d.advance(5000);"            # past the grace, now error-free
            " return { activeWithinGrace: withinGrace.active, activeAfterClear: afterClear.active }; })()"
        )
        self.assertTrue(out["activeWithinGrace"],
                        "a stale error within the grace must NOT immediately dismiss a just-begun build")
        self.assertTrue(out["activeAfterClear"],
                        "if a fresh build clears the leftover error, the overlay survives (no false dismiss)")

    def test_error_persisting_through_grace_dismisses(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver();"
            " d.setError('cold-open failed at t=0');"       # error present from the start
            " var early = d.snap();"                         # within grace → still up
            " var late = d.advance(d.snap().errorGraceMs + 1000);"  # error never cleared → dismiss at grace
            " return { activeEarly: early.active, activeLate: late.active }; })()"
        )
        self.assertTrue(out["activeEarly"], "within the grace the overlay holds (the error may be stale)")
        self.assertFalse(out["activeLate"], "an error that persists through the grace IS real → dismiss")

    # --- #405 (2) DISMISS ON CEILING: ~120s with no narration auto-dismisses (NOT 12 min) --------
    def test_stall_ceiling_dismisses_well_before_twelve_minutes(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); var s0 = d.render();"
            " var justBefore = d.advance(s0.ceilingMs - 2000);"   # 2s shy of the ceiling — still up
            " var atCeiling = d.advance(3000);"                    # cross the ceiling → dismiss
            " return { ceilingMs: s0.ceilingMs, activeJustBefore: justBefore.active,"
            "          activeAtCeiling: atCeiling.active }; })()"
        )
        self.assertLessEqual(out["ceilingMs"], 180 * 1000,
                             "the stall ceiling must be well under the old 12-min dead-end (~120s)")
        self.assertTrue(out["activeJustBefore"], "the overlay should still cover just before the ceiling")
        self.assertFalse(out["activeAtCeiling"], "at the stall ceiling the overlay must dismiss (table takes over)")

    def test_ceiling_does_not_rearm_on_streamed_progress(self):
        # #406 (2): a build that keeps re-rendering (e.g. streamed beats / a ticking clock) must NOT
        # push the ceiling forward — it is a FIXED deadline from start. Render repeatedly across the
        # window, then cross the ceiling and confirm it still fires.
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); var s0 = d.render();"
            " for (var i=0;i<5;i++){ d.advance(20000); }"   # 100s of re-renders within the window
            " var nearCeiling = d.snap();"
            " var past = d.advance(25000);"                  # now well past 120s
            " return { activeNear: nearCeiling.active, activePast: past.active }; })()"
        )
        self.assertTrue(out["activeNear"], "still covering within the fixed window")
        self.assertFalse(out["activePast"], "the ceiling is a fixed deadline — re-renders must not defer it")

    # --- #405 (3) MANUAL ESCAPE: 'escapable' arms after ~15s; dismiss() clears + table reachable --
    def test_manual_escape_arms_after_threshold_and_dismiss_reveals_table(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); var s0 = d.render();"
            " var early = d.advance(s0.escapeMs - 2000);"   # before the escape threshold
            " var armed = d.advance(3000);"                  # past ~15s → 'Enter anyway' affordance
            " var afterClick = d.callDismiss();"             # the player clicks Enter anyway
            " return { escapeMs: s0.escapeMs, escapableEarly: early.escapable, escapableArmed: armed.escapable,"
            "          activeAfterClick: afterClick.active, keyAfterClick: afterClick.hasKey }; })()"
        )
        self.assertLess(out["escapeMs"], 60 * 1000, "the manual escape must appear early (~15s), not minutes in")
        self.assertFalse(out["escapableEarly"], "the manual escape must NOT show before its threshold")
        self.assertTrue(out["escapableArmed"], "after ~15s the manual 'Enter anyway' affordance must appear")
        self.assertFalse(out["activeAfterClick"], "clicking 'Enter anyway' (dismiss) must clear the overlay")
        self.assertFalse(out["keyAfterClick"], "the building flag must be cleared so the overlay can't re-enter")

    # --- NEGATIVE DISCLOSURE: no narration + within the ceiling STAYS active (documents the cover)
    def test_no_narration_within_ceiling_stays_active(self):
        out = self._run(
            "(function(){ h.begin(); var d = h.driver(); d.render();"
            " var mid = d.advance(40000);"   # 40s in, no beat, no error — still legitimately covering
            " return { active: mid.active, handoff: mid.handoff }; })()"
        )
        self.assertTrue(out["active"],
                        "with no beat, no error, and within the ceiling the cover legitimately stays up")
        self.assertFalse(out["handoff"])


if __name__ == "__main__":
    unittest.main()
