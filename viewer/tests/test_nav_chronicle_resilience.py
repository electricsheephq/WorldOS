"""Behavior tests for the two v1.0.4 viewer orphans #826 + #825 (engine-audit Section-4 coverage gaps).

These exercise the REAL shipped code (no reimplementation) — the chronicle bridge projection
(viewer/server.py), the scene-image component (viewer/openworlds/chrome.jsx), and the app-level
in-flight turn state (viewer/openworlds/app.jsx `useLiveSession`) — mirroring the sibling
JS-behavior harnesses (test_recovery_timing.py, test_chronicle_hygiene.py).

#826 — Navigating away mid-narration corrupts session state + permanently freezes the scene image
   (rc2 adversarial; engine-audit Section-4 orphan #5). A user-initiated nav DURING an in-flight
   beat (distinct from the #745/#648 STALLED-beat trigger) must not (a) drop the one-move gate (so a
   nav-away/nav-back can't double-submit into the one-move-at-a-time lane — state corruption), nor
   (b) latch a DEAD scene-image handle that stays frozen after the #399 fire-and-forget image
   finally becomes servable. Two legs:
     • the optimistic in-flight gate: `useLiveSession` must arm the pending turn the instant the
       player commits (BEFORE the network round-trip resolves) so the gate SURVIVES a remount; and a
       genuine POST rejection must be able to cleanly ABANDON that optimistic arm (the new
       `abandonPending`) WITHOUT being swallowed by the #648 arm-grace.
     • the scene-image handle: `Img` (chrome.jsx) must NOT permanently latch `failed` — a #399
       pending scene that 404s then becomes servable must be recoverable, not frozen forever.

#825 — Chronicle truncates DM narration mid-word at a fixed ceiling (3 personas, rc1+rc2;
   engine-audit Section-4 orphan #1). The chronicle's leading history band (`_session_recent_events`
   in viewer/server.py) hard-cut every row to `text[:1000]`, slicing a long DM beat mid-word with no
   ellipsis / no expand — the remainder unreadable. The full narration must round-trip into the
   chronicle (the render region is already a scrollable `role="log"`, and the #752 a11y bound is the
   ROW cap CHRONICLE_RENDER_CAP/MAX_LIVE_BEATS — NOT a per-row char ceiling), so removing the
   per-row char cut does not reintroduce the #752 a11y flood.

Skipped where Node is not on PATH (the JS-behavior legs only).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
VIEWER_ROOT = HERE.parent
APP_JSX = OPENWORLDS / "app.jsx"
SCREEN_TABLE = OPENWORLDS / "screen-table.jsx"
CHROME = OPENWORLDS / "chrome.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


# ===========================================================================================
# #825 — the chronicle bridge must NOT cut a long DM beat mid-word at a fixed char ceiling.
# ===========================================================================================
@pytest.fixture(scope="module")
def viewer_server():
    sys.path.insert(0, str(VIEWER_ROOT))
    import server  # noqa: WPS433  (viewer/server.py)

    return server


def _long_dm_beat(n_chars: int = 4000) -> str:
    # A realistic multi-paragraph DM narration well past the old 1000-char ceiling. We deterministically
    # place an unbroken 80-char run straddling index 1000 so a fixed [:1000] slice would visibly cut
    # mid-word — the exact #825 complaint. Then pad with prose to n_chars.
    body = (
        "The torchlight gutters as you descend the winding stair into the undercroft, "
        "each step slick with centuries of damp. "
    )
    head = "A" * 970          # exactly 970 chars, no spaces
    midword = "x" * 80        # straddles index 1000 (970..1050), no spaces
    text = head + midword + " " + (body * 60)
    text = text[:n_chars]
    assert text[999] != " " and text[1000] != " ", "fixture must straddle a word at the old ceiling"
    return text


def test_825_chronicle_history_band_does_not_truncate_long_narration_midword(viewer_server):
    long_text = _long_dm_beat(4000)
    rows = viewer_server._session_recent_events(
        [{"kind": "narration", "text": long_text, "seq": 0, "sid": "s1"}]
    )
    assert len(rows) == 1
    got = rows[0]["text"]
    # The FULL beat must survive — no fixed-ceiling mid-word cut.
    assert got == long_text, (
        f"chronicle history band truncated DM narration: kept {len(got)} of {len(long_text)} chars"
    )


def test_825_short_narration_is_unchanged(viewer_server):
    text = "A short, clean beat the DM narrated."
    rows = viewer_server._session_recent_events([{"kind": "narration", "text": text}])
    assert rows and rows[0]["text"] == text


def test_825_no_fixed_1000_char_ceiling_on_narration_rows(viewer_server):
    # Pin the regression directly: a beat that is exactly 1500 chars must come back at 1500, not 1000.
    text = "x" * 1500
    rows = viewer_server._session_recent_events([{"kind": "narration", "text": text}])
    assert rows and len(rows[0]["text"]) == 1500, "a fixed 1000-char ceiling is back"


# ===========================================================================================
# #826 (image leg) — `Img` must not permanently latch a DEAD handle on a recoverable error.
# ===========================================================================================
def _img_recovers_after_error() -> dict:
    """Mount the REAL `Img` (chrome.jsx) with a stateful React stub + a controllable clock, fire
    onError (a #399 pending scene 404), then advance the retry timer (the pending art has landed and
    the same scope is now servable) and report whether the component RECOVERED to an <img> or stayed
    frozen on the Placeholder (the #826 dead handle). Also reports the retry URL is cache-busted so a
    cached 404 is actually re-requested."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(CHROME))
        + r"""
// A controllable clock so the timer-driven retry (the #826 fix mirrors the browser: a pending image
// lands after a delay and the component re-probes) is deterministic.
let NOW = 1000;
const timers = [];
let nextId = 1;
function setTimeoutStub(fn, ms) { const id = nextId++; timers.push({ id, at: NOW + (ms || 0), fn, cleared: false }); return id; }
function clearTimeoutStub(id) { const t = timers.find((t) => t.id === id); if (t) t.cleared = true; }
function advance(ms) {
  const target = NOW + ms;
  while (true) {
    const due = timers.filter((t) => !t.cleared && t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break; NOW = due.at; due.cleared = true; due.fn();
  }
  NOW = target;
}
// A minimal stateful React: useState cells persist across renders; useEffect bodies RUN and their
// deps are honored (so an effect only re-runs when its deps change — exactly the browser's model).
function makeReact() {
  let stateCells = [], effectCells = [], sIdx = 0, eIdx = 0, renderFn = null, result = null;
  const pendingEffects = [];
  function useState(init) {
    const i = sIdx++;
    if (stateCells[i] === undefined) stateCells[i] = { v: (typeof init === 'function' ? init() : init) };
    const cell = stateCells[i];
    const set = (n) => { cell.v = (typeof n === 'function' ? n(cell.v) : n); render(); };
    return [cell.v, set];
  }
  function useEffect(fn, deps) {
    const i = eIdx++;
    const prev = effectCells[i];
    const changed = !prev || !deps || deps.some((d, k) => d !== prev.deps[k]);
    if (changed) { if (prev && typeof prev.cleanup === 'function') { try { prev.cleanup(); } catch (e) {} }
      effectCells[i] = { deps }; pendingEffects.push({ i, fn }); }
  }
  function useCallback(fn) { return fn; }
  function useRef(init) { const i = 'r' + (sIdx++); if (stateCells[i] === undefined) stateCells[i] = { current: init }; return stateCells[i]; }
  function createElement(type, props, ...children) {
    return { type: (typeof type === 'function' ? (type.name || 'C') : type), fn: (typeof type === 'function' ? type : null),
             props: props || {}, children: children.flat(Infinity).filter((c) => c != null) }; }
  function flush() { while (pendingEffects.length) { const { i, fn } = pendingEffects.shift(); const cl = fn(); if (effectCells[i]) effectCells[i].cleanup = (typeof cl === 'function') ? cl : undefined; } }
  function render() { sIdx = 0; eIdx = 0; result = renderFn(); flush(); return result; }
  function mount(fn) { renderFn = fn; return render(); }
  return { React: { useState, useEffect, useCallback, useRef, createElement, Fragment: 'F' }, render, mount, get result() { return result; } };
}
const host = makeReact();
const sb = { React: host.React, console, setTimeout: setTimeoutStub, clearTimeout: clearTimeoutStub };
sb.window = sb;
vm.createContext(sb);
const code = Babel.transform(src, { presets: ['react'], filename: 'chrome.jsx' }).code;
vm.runInContext(code, sb);
const Img = sb.window.Img;
if (typeof Img !== 'function') throw new Error('Img not exported on window');

// Walk the createElement tree to find the first <img> (and its props) if present.
function findImg(node) {
  if (!node || typeof node !== 'object') return null;
  if (node.type === 'img') return node;
  for (const c of (node.children || [])) { const r = findImg(c); if (r) return r; }
  return null;
}
const scope = 'scene:undercroft';
host.mount(() => Img({ scope, label: 'scene', h: 260 }));
const firstImg = findImg(host.result);
const hadImgInitially = !!firstImg;
// Simulate the /image 404 (a #399 fire-and-forget scene that is not yet servable).
if (firstImg && firstImg.props.onError) firstImg.props.onError();
const afterErrorImg = findImg(host.result);
const frozenAfterError = !afterErrorImg;   // dropped to Placeholder
// The async worker finishes — the SAME scope is now servable. Advancing the retry clock must let the
// component RECOVER (re-mount the <img>) rather than stay frozen on a dead handle.
advance(5000);
const recoveredImg = findImg(host.result);
const recovered = !!recoveredImg;
const retryUrlCacheBusted = !!(recoveredImg && /[?&]v=\d+/.test(String(recoveredImg.props.src || "")));
process.stdout.write(JSON.stringify({ hadImgInitially, frozenAfterError, recovered, retryUrlCacheBusted }));
"""
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_826_scene_image_does_not_latch_dead_handle_after_recoverable_error():
    out = _img_recovers_after_error()
    assert out["hadImgInitially"], "Img should render an <img> for a real scope"
    assert out["frozenAfterError"], "an onError should fall back to the Placeholder (expected)"
    # The #826 fix: once the same-scope image becomes servable, the component must be able to recover
    # — NOT stay permanently frozen on a dead handle.
    assert out["recovered"], "scene image stayed FROZEN on a dead handle after a recoverable error (#826)"
    assert out["retryUrlCacheBusted"], "the retry must cache-bust the URL so a cached 404 is re-requested"


# ===========================================================================================
# #826 (state leg) — the in-flight turn gate must be armed OPTIMISTICALLY (survives nav) and a
# genuine POST rejection must cleanly ABANDON it (bypassing the #648 arm-grace).
# ===========================================================================================
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
  const stateCells = []; const refCells = []; const cbCells = []; const effects = [];
  let sIdx = 0, rIdx = 0, cIdx = 0, eIdx = 0;
  let renderFn = null; let result = null;
  const pendingEffects = []; let flushing = false;
  function useState(init) {
    const i = sIdx++;
    if (stateCells[i] === undefined) stateCells[i] = { v: typeof init === 'function' ? init() : init };
    const cell = stateCells[i];
    const set = (next) => { cell.v = (typeof next === 'function') ? next(cell.v) : next; render(); };
    return [cell.v, set];
  }
  function useRef(init) { const i = rIdx++; if (refCells[i] === undefined) refCells[i] = { current: init }; return refCells[i]; }
  function useCallback(fn, deps) {
    const i = cIdx++; const prev = cbCells[i];
    if (!prev || !deps || deps.some((d, k) => d !== prev.deps[k])) cbCells[i] = { fn, deps };
    return cbCells[i].fn;
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
  function render() { sIdx = 0; rIdx = 0; cIdx = 0; eIdx = 0; result = renderFn(); if (!flushing) flushEffects(); return result; }
  function mount(fn) { renderFn = fn; render(); }
  function commit() { render(); }
  function api() { return result; }
  return { React: { useState, useRef, useCallback, useEffect, createElement: (t, p, ...c) => ({ t, p, c }), Fragment: 'F' }, mount, commit, api };
}

const reactHost = makeReact();
const sandbox = {
  React: reactHost.React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: setTimeoutStub, clearTimeout: clearTimeoutStub, setInterval: setIntervalStub, clearInterval: clearIntervalStub,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [], next: 0 }) }),
  URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, console,
};
sandbox.window = sandbox; sandbox.window.window = sandbox;
sandbox.Date = { now: () => NOW };
vm.createContext(sandbox);

function load(p, stripBootstrap) {
  let src = fs.readFileSync(p, 'utf8');
  if (stripBootstrap) { const i = src.indexOf('ReactDOM.createRoot'); if (i !== -1) src = src.slice(0, i); }
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(screen_table)s);
load(%(app)s, true);

const useLiveSession = sandbox.window.useLiveSession;
if (typeof useLiveSession !== 'function') throw new Error('useLiveSession not exported');
const state = { activeCampaign: 'camp1', campaigns: [{ id: 'camp1', campaign_id: 'camp1' }] };
reactHost.mount(() => useLiveSession(state));
const win = sandbox.window;
const h = {
  now: () => NOW,
  advance,
  pending: () => reactHost.api().pending,
  arm: (text) => reactHost.api().armPending(text || 'do something'),
  clear: () => reactHost.api().clearPending(),
  abandon: (text) => (typeof reactHost.api().abandonPending === 'function'
      ? reactHost.api().abandonPending(text)
      : (() => { throw new Error('abandonPending not exported'); })()),
  hasAbandon: () => typeof reactHost.api().abandonPending === 'function',
};
const script = %(script)s;
(async () => {
  const out = await script(h, win);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


def _run_hook(script_js: str) -> dict:
    program = _HARNESS % {
        "babel": json.dumps(str(BABEL)),
        "screen_table": json.dumps(str(SCREEN_TABLE)),
        "app": json.dumps(str(APP_JSX)),
        # `script_js` is a raw JS arrow-function body — injected verbatim (NOT JSON-quoted), exactly
        # like test_recovery_timing.py's `%(script)s`. The harness assigns `const script = <it>;`.
        "script": script_js,
    }
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_826_in_flight_gate_survives_immediately_after_arm():
    # The optimistic arm: the instant the player commits (armPending called BEFORE the network
    # resolves), the one-move gate is live — so a nav-away/nav-back that remounts ScreenTable (its
    # local submittingRef resets) still sees `pending` truthy and blocks a second submit. We assert
    # the gate is up RIGHT after arm (t=0), with no advance — the in-flight window.
    out = _run_hook(
        "async (h) => { h.arm('I open the door'); "
        "return { pendingRightAfterArm: !!h.pending(), text: h.pending() && h.pending().text }; }"
    )
    assert out["pendingRightAfterArm"], "the gate must be armed in the in-flight window (before the POST resolves)"
    assert out["text"] == "I open the door"


def test_826_abandon_pending_clears_optimistic_arm_within_grace():
    # A genuine POST rejection must be able to ROLL BACK the optimistic arm immediately — even inside
    # the #648 arm-grace (which deliberately ignores a SPURIOUS clearPending). abandonPending is the
    # authoritative rollback (the move was rejected by the server), so it must clear NOW, not wait for
    # the recovery window. Distinct from clearPending, which #648 grace correctly swallows here.
    # advance(500) stays well inside the 10s arm-grace; clear() is the spurious clear #648 ignores;
    # abandon() is the authoritative rollback that must clear NOW.
    out = _run_hook(
        "async (h) => { "
        "h.arm('rejected move'); "
        "const armed = !!h.pending(); "
        "h.advance(500); "
        "h.clear(); const stillThereAfterClear = !!h.pending(); "
        "h.abandon('rejected move'); const goneAfterAbandon = !h.pending(); "
        "return { hasAbandon: h.hasAbandon(), armed, stillThereAfterClear, goneAfterAbandon }; }"
    )
    assert out["hasAbandon"], "useLiveSession must export abandonPending (the optimistic-arm rollback)"
    assert out["armed"], "armPending must arm a turn"
    assert out["stillThereAfterClear"], "#648 arm-grace must still ignore a spurious clearPending"
    assert out["goneAfterAbandon"], "abandonPending must authoritatively roll back the optimistic arm"


def test_826_abandon_only_rolls_back_the_matching_in_flight_move():
    # abandonPending must NOT wipe a DIFFERENT in-flight turn — if (somehow) a newer turn is pending,
    # abandoning the OLD move's text is a no-op (it can't clobber the live turn). Keeps the rollback
    # surgical: only the move WE optimistically armed and the server rejected gets cleared.
    # advance(15000) clears the grace so a real clear WOULD work; the mismatched abandon text is a no-op.
    out = _run_hook(
        "async (h) => { "
        "h.arm('newer live move'); "
        "h.advance(15000); "
        "h.abandon('an old, different move'); "
        "return { stillPending: !!h.pending(), text: h.pending() && h.pending().text }; }"
    )
    assert out["stillPending"], "abandoning a non-matching move must not clear the live turn"
    assert out["text"] == "newer live move"
