"""Behavior test for the Beta dead-wait on map click-to-travel (the #913/#623 narrating-indicator
gap, on the MAP screen this time).

Native-app players (the Beta target) can click-to-travel from the World Atlas. The map's
`postTravel` (viewer/openworlds/screen-map.jsx) POSTs the travel intent to /move and navigates to
the table — but it historically NEVER armed the app-level `pending` (narrating) indicator. That
indicator is owned by `useLiveSession` (viewer/openworlds/app.jsx, exported as `armPending`) and was
armed ONLY by the table's own `postMove` path; the /chat poll merely CLEARS pending on a resolved
beat, it never ARMS it for an externally-posted move. So a mid-campaign map travel resolved a ~100s
DM beat with NO on-screen "Narrating…" — the frozen-feel the #826/#648/#913 work eliminated
everywhere else.

This mirrors test_nav_chronicle_resilience.py's #826/#648 arm tests + the Node babel-vm harness: it
loads the REAL screen-map.jsx + app.jsx, threads the REAL `useLiveSession` as `liveSession`, captures
the REAL `postTravel` (via the `onTravel` prop screen-map hands AtlasSidebar), and asserts:
  • postTravel ARMS pending OPTIMISTICALLY — the narrating indicator is live in the in-flight window
    BEFORE the /move POST resolves (RED before the fix: pending stays null on map travel); and
  • a POST rejection cleanly ROLLS BACK that optimistic arm (abandonPending), so a dead /move sink
    doesn't strand a phantom "Narrating…" on the table.

Pure viewer presentation: read-only on engine state, no /move-contract change, reuses the existing
armPending/abandonPending state machine.

Skipped where Node is not on PATH (the JS-behavior legs only).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
APP_JSX = OPENWORLDS / "app.jsx"
SCREEN_MAP = OPENWORLDS / "screen-map.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


# The harness loads the REAL useLiveSession (app.jsx) + the REAL ScreenMap (screen-map.jsx). It stubs
# only the leaf UI globals ScreenMap references (Panel/BrassButton/AtlasMap/Img/icons/toast) — every
# stub is a capture-passthrough so ScreenMap's own `postTravel` closure is the code under test. The
# travel handler is the `onTravel` prop ScreenMap hands `AtlasSidebar`; we grab it from the captured
# props and drive it, exactly as a "Travel here" click would. `fetch` is DEFERRED (a never-resolving
# promise by default) so we can observe the OPTIMISTIC arm in the in-flight window, then resolve/reject
# it deterministically to assert clear/rollback.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

let NOW = 1000000;
const timers = [];
let nextId = 1;
function setTimeoutStub(fn, ms) { const id = nextId++; timers.push({ id, at: NOW + (ms || 0), fn, cleared: false }); return id; }
function clearTimeoutStub(id) { const t = timers.find((t) => t.id === id); if (t) t.cleared = true; }
function setIntervalStub() { return nextId++; }
function clearIntervalStub() {}
function advance(ms) {
  const target = NOW + ms;
  while (true) {
    const due = timers.filter((t) => !t.cleared && t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    NOW = due.at; due.cleared = true; due.fn();
  }
  NOW = target;
}

function makeReact() {
  const stateCells = []; const refCells = []; const cbCells = []; const memoCells = []; const effects = [];
  let sIdx = 0, rIdx = 0, cIdx = 0, mIdx = 0, eIdx = 0;
  let renderFn = null; let result = null;
  const pendingEffects = []; let flushing = false;
  function useState(init) {
    const i = sIdx++;
    if (stateCells[i] === undefined) stateCells[i] = { v: typeof init === 'function' ? init() : init };
    const cell = stateCells[i];
    // Mirror React's bail-out: a set to the SAME value (Object.is) does NOT re-render. ScreenMap's
    // setSelectedId('') effect re-fires every render against a fresh-`[]` `locations`; without the
    // bail-out the stub would spin forever where the browser quietly no-ops.
    const set = (next) => {
      const v = (typeof next === 'function') ? next(cell.v) : next;
      if (Object.is(v, cell.v)) return;
      cell.v = v; render();
    };
    return [cell.v, set];
  }
  function useRef(init) { const i = rIdx++; if (refCells[i] === undefined) refCells[i] = { current: init }; return refCells[i]; }
  function useCallback(fn, deps) {
    const i = cIdx++; const prev = cbCells[i];
    if (!prev || !deps || deps.some((d, k) => d !== prev.deps[k])) cbCells[i] = { fn, deps };
    return cbCells[i].fn;
  }
  function useMemo(fn, deps) {
    const i = mIdx++; const prev = memoCells[i];
    if (!prev || !deps || deps.some((d, k) => d !== prev.deps[k])) memoCells[i] = { v: fn(), deps };
    return memoCells[i].v;
  }
  function useEffect(fn, deps) {
    const i = eIdx++; const prev = effects[i];
    const changed = !prev || !deps || deps.some((d, k) => d !== prev.deps[k]);
    if (changed) { effects[i] = { deps, cleanup: prev && prev.cleanup }; pendingEffects.push({ i, fn }); }
  }
  function flushEffects() {
    if (flushing) return; flushing = true;
    while (pendingEffects.length) {
      const { i, fn } = pendingEffects.shift();
      if (effects[i] && typeof effects[i].cleanup === 'function') { try { effects[i].cleanup(); } catch (e) {} }
      const cl = fn(); if (effects[i]) effects[i].cleanup = (typeof cl === 'function') ? cl : undefined;
    }
    flushing = false;
  }
  function render() { sIdx = 0; rIdx = 0; cIdx = 0; mIdx = 0; eIdx = 0; result = renderFn(); if (!flushing) flushEffects(); return result; }
  function mount(fn) { renderFn = fn; render(); }
  function api() { return result; }
  return { React: { useState, useRef, useCallback, useMemo, useEffect, createElement: (t, p, ...c) => ({ t, p, c }), Fragment: 'F' }, mount, api };
}

const reactHost = makeReact();

// The surface the map reads. The travel option carries an engine-backed move (so postTravel's guards
// pass) and a name (the human label that should become the arm text + chronicle echo).
const surface = {
  campaign_id: 'camp1',
  can_act: true,
  known_locations: [{ id: 'here', name: 'Here', current: true }, { id: 'dock', name: 'The Drowned Dock' }],
  current_location: { id: 'here', name: 'Here' },
  travel_options: [{ to: 'dock', name: 'The Drowned Dock', available: true, move: { kind: 'travel', to: 'dock', text: 'travel to the Drowned Dock' } }],
  edges: [], quest_markers: [], strategic_clocks: [], downtime_projects: [], region_control: [],
};

// Routed fetch: /atlas-surface resolves IMMEDIATELY with the surface (the map's poll), so the
// component has live travel options + can_act. /move is DEFERRED — the call under test (postTravel)
// awaits it, and we hold the promise so the OPTIMISTIC arm is observable in the in-flight window.
let moveResolve = null, moveCalls = 0;
function routedFetch(url) {
  const u = String(url || '');
  if (u.indexOf('/move') !== -1) {
    moveCalls++;
    return new Promise((res) => { moveResolve = res; });
  }
  // /atlas-surface (or anything else) — resolve immediately with the surface.
  return Promise.resolve({ ok: true, json: () => Promise.resolve(surface) });
}

const sandbox = {
  React: reactHost.React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: setTimeoutStub, clearTimeout: clearTimeoutStub, setInterval: setIntervalStub, clearInterval: clearIntervalStub,
  fetch: routedFetch,
  URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math, Date: { now: () => NOW }, console,
};
sandbox.window = sandbox; sandbox.window.window = sandbox;
vm.createContext(sandbox);

function load(p, stripBootstrap) {
  let src = fs.readFileSync(p, 'utf8');
  if (stripBootstrap) { const i = src.indexOf('ReactDOM.createRoot'); if (i !== -1) src = src.slice(0, i); }
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
// app.jsx first (defines useLiveSession + neutralizeMarkup etc.), then screen-map.jsx.
load(%(app)s, true);
load(%(screen_map)s);

// Leaf UI globals ScreenMap references as BARE identifiers that are NOT defined in screen-map.jsx
// (Panel/BrassButton/Img/icons/toast live in chrome.jsx). AtlasMap + AtlasSidebar ARE defined inside
// screen-map.jsx, so they render for real — and we reach `postTravel` by WALKING the rendered tree to
// the element that carries the `onTravel` prop (= the AtlasSidebar element). Pass-through stubs keep
// the render cheap and side-effect-free.
function passthrough(name) { return (props) => ({ t: name, p: props || {}, c: [] }); }
Object.assign(sandbox.window, {
  Panel: (p) => ({ t: 'Panel', p, c: (p && p.children) || [] }),
  BrassButton: passthrough('BrassButton'),
  Pill: passthrough('Pill'),
  Divider: passthrough('Divider'),
  Img: passthrough('Img'),
  SectionTitle: passthrough('SectionTitle'),
  CampSidebar: passthrough('CampSidebar'),
  useToast: () => (() => {}),
  OpenWorldsIcon: Object.assign(() => null, { has: () => false }),
});

const useLiveSession = sandbox.window.useLiveSession;
const ScreenMap = sandbox.window.ScreenMap;
if (typeof useLiveSession !== 'function') throw new Error('useLiveSession not exported');
if (typeof ScreenMap !== 'function') throw new Error('ScreenMap not exported');

const state = { activeCampaign: 'camp1', campaigns: [{ id: 'camp1', campaign_id: 'camp1' }] };

// Mount ONE root on the single React host — exactly like the real App: the root calls the REAL
// useLiveSession AND renders the REAL ScreenMap with that live session as a prop. Both share the same
// host, so an arm from postTravel re-renders the whole tree coherently (no cross-host hook wiring —
// the VM exposes a single `React`, so hooks must run on one host). We capture the per-render live
// session + the rendered ScreenMap tree off the root result.
const navCalls = [];
let liveSessionSnapshot = null;
let mapTree = null;
function Root() {
  const liveSession = useLiveSession(state);
  liveSessionSnapshot = liveSession;
  mapTree = ScreenMap({
    onNavigate: (s) => navCalls.push(s),
    state, campMode: false, setCampMode: () => {}, liveSession,
  });
  return mapTree;
}
reactHost.mount(Root);
function liveNow() { return liveSessionSnapshot; }

// Find the element carrying the `onTravel` prop (the AtlasSidebar element ScreenMap renders). This is
// the real `postTravel` closure — the code under test.
function findOnTravel(node) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) { for (const n of node) { const r = findOnTravel(n); if (r) return r; } return null; }
  const p = node.p || {};
  if (typeof p.onTravel === 'function') return p;
  const kids = (node.c || []).concat(p.children ? [p.children] : []);
  for (const c of kids) { const r = findOnTravel(c); if (r) return r; }
  return null;
}
function sidebarProps() { return findOnTravel(mapTree); }

const h = {
  advance,
  pending: () => liveNow().pending,
  log: () => liveNow().log,
  navCalls: () => navCalls,
  travelOption: () => { const sp = sidebarProps(); return (sp && sp.travel) || surface.travel_options[0]; },
  // Drive the REAL postTravel. Returns the in-flight promise (the /move POST is deferred).
  travel: (opt) => sidebarProps().onTravel(opt || surface.travel_options[0]),
  resolveMove: (payload) => { if (moveResolve) { const r = moveResolve; moveResolve = null; r({ ok: true, json: () => Promise.resolve(payload || { ok: true }) }); } },
  rejectMove: (payload) => { if (moveResolve) { const r = moveResolve; moveResolve = null; r({ ok: false, status: 503, json: () => Promise.resolve(payload || { ok: false, reason: 'sink down' }) }); } },
  fetchCalls: () => moveCalls,
  hasOnTravel: () => !!sidebarProps(),
};

// Let the synchronous /atlas-surface fetch settle (its awaited continuation runs on the microtask
// queue) BEFORE any test body runs, so the surface is loaded + the sidebar has real travel options.
async function settleSurface() { for (let i = 0; i < 5; i++) await Promise.resolve(); }

const script = %(script)s;
(async () => {
  await settleSurface();
  const out = await script(h, sandbox.window);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


def _run(script_js: str) -> dict:
    program = _HARNESS % {
        "babel": json.dumps(str(BABEL)),
        "app": json.dumps(str(APP_JSX)),
        "screen_map": json.dumps(str(SCREEN_MAP)),
        "script": script_js,
    }
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_map_travel_arms_pending_in_flight_window():
    # The fix: map click-to-travel arms the narrating indicator OPTIMISTICALLY — pending is truthy in
    # the in-flight window, BEFORE the /move POST resolves — so the ~100s DM beat that resolves the
    # travel never shows the Beta dead-wait. RED before the fix (postTravel never called armPending).
    out = _run(
        "async (h) => {"
        "  const armedBefore = !!h.pending();"
        "  const p = h.travel();"               # fire postTravel (fetch is deferred, unresolved)
        "  const pendingInFlight = !!h.pending();"
        "  const text = h.pending() && h.pending().text;"
        "  h.resolveMove({ ok: true });"        # let /move succeed
        "  await p;"
        "  return { hasOnTravel: h.hasOnTravel(), armedBefore, pendingInFlight, text, fetchCalls: h.fetchCalls() };"
        "}"
    )
    assert out["hasOnTravel"], "ScreenMap must hand AtlasSidebar an onTravel handler (postTravel)"
    assert not out["armedBefore"], "no turn should be pending before the travel is initiated"
    assert out["pendingInFlight"], (
        "map click-to-travel must ARM the narrating indicator in the in-flight window — "
        "the Beta dead-wait bug (postTravel never armed pending)"
    )
    assert out["text"] == "The Drowned Dock", "the arm text should be the human travel label"
    # The move still posts exactly once (no contract change, no double-fire).
    assert out["fetchCalls"] == 1, "exactly one /move POST"


def test_map_travel_records_player_echo():
    # Mirror postMove's optimistic arm: the chronicle echo is recorded BEFORE the await, so the move
    # the player committed shows in the chronicle the instant they travel (survives the nav to table).
    out = _run(
        "async (h) => {"
        "  const before = h.log().length;"
        "  const p = h.travel();"
        "  const after = h.log().length;"
        "  const lastText = h.log()[h.log().length - 1] && h.log()[h.log().length - 1].text;"
        "  h.resolveMove({ ok: true });"
        "  await p;"
        "  return { before, after, lastText };"
        "}"
    )
    assert out["after"] == out["before"] + 1, "postTravel must record a player echo in the chronicle"
    assert out["lastText"] == "The Drowned Dock", "the echo carries the travel label"


def test_map_travel_navigates_to_table_after_arming():
    # The arm happens BEFORE/AS the nav to the table — so the table opens already showing "Narrating…".
    out = _run(
        "async (h) => {"
        "  const p = h.travel();"
        "  const pendingInFlight = !!h.pending();"
        "  h.resolveMove({ ok: true });"
        "  await p;"
        "  return { pendingInFlight, navCalls: h.navCalls() };"
        "}"
    )
    assert out["pendingInFlight"], "pending must be armed during the in-flight window"
    assert "table" in out["navCalls"], "a successful travel still navigates to the table"


def test_map_travel_rejection_rolls_back_the_arm():
    # A dead /move sink: the POST is rejected, so the optimistic arm must be ROLLED BACK
    # (abandonPending) — no phantom "Narrating…" stranded on the table. Mirrors postMove's #826
    # rollback. RED before the fix (nothing was armed, so nothing to assert) AND a naive fix that armed
    # but never rolled back would FAIL here.
    out = _run(
        "async (h) => {"
        "  const p = h.travel();"
        "  const pendingInFlight = !!h.pending();"
        "  h.rejectMove({ ok: false, reason: 'sink down' });"
        "  await p;"
        "  const pendingAfterReject = !!h.pending();"
        "  return { pendingInFlight, pendingAfterReject };"
        "}"
    )
    assert out["pendingInFlight"], "the arm must be live in the in-flight window"
    assert not out["pendingAfterReject"], (
        "a /move rejection must roll back the optimistic arm (abandonPending) — "
        "no phantom narrating indicator on a dead sink"
    )
