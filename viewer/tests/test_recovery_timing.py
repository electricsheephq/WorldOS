"""Behavior tests for the #348 adaptive 'stuck' recovery window in `useLiveSession`.

`useLiveSession` (viewer/openworlds/app.jsx) owns the "DM is narrating…" pending
state and the recovery/backstop timers. Because the DM beat lands ALL-AT-ONCE (the
/chat tail carries no streaming/partial/heartbeat signal — the duo + human runners
append ONE {"role":"dm",...} line only after the whole turn's `result` is in), the
recovery is a wall-clock from submit. #342 fixed it at 90s, which PRE-EMPTED the
legit multi-minute Act-opening → false 'stuck', narration lost at a cliffhanger
(#348). #348 makes the window turn-position-aware:

  • FIRST beat of a session (the cold-open the engine spends minutes building):
    a generous PENDING_RECOVERY_FIRST_MS (~4 min) — no longer falsely stuck.
  • LATER beats (the ~35–60s norm): the snappy PENDING_RECOVERY_MS (~90s).
  • The 12-min hard backstop is unchanged.

These tests exercise the REAL code by transpiling the actual `.jsx` with the SAME
bundled Babel-standalone the browser uses and running it under Node with a
deterministic React + fake-timer stub, so the test tracks the shipped behavior
rather than a reimplementation (mirrors test_sanitize_narration.py). They cover:

  • the timing CONTRACT (`recoveryWindowMs` for both branches + the constants);
  • the hook's observable FIRST-beat behavior (no false stuck at 91s; recovers
    after the long window; the 12-min backstop still force-clears) — the #348 fix;
  • the LATER-beat branch (snappy 90s) — preserved for a genuine mid-session stall;
  • the #344 contract (`armPending`/`clearPending` shape the retry path relies on).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_APP = _OPENWORLDS / "app.jsx"
_SCREEN_TABLE = _OPENWORLDS / "screen-table.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A self-contained Node harness: a minimal-but-real React stub (storing state, refs,
# callbacks per render so setState re-renders the hook) plus a controllable clock + timer
# queue. It transpiles screen-table.jsx (defines sanitizeNarration onto window, used by
# app.jsx) then app.jsx with the bundled Babel, mounts useLiveSession over a live campaign,
# and exposes a tiny scripting surface (`h`) so each test can arm/clear pending, advance the
# fake clock, and read pending state — plus the pure `recoveryWindowMs` + constants.
#
# `script` is a JS expression evaluated with `h`/`win` in scope; its value → JSON on stdout.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

// ---- deterministic clock + one-shot timer queue ----------------------------
let NOW = 1000000;
const timers = [];
let nextId = 1;
function setTimeoutStub(fn, ms) {
  const id = nextId++;
  timers.push({ id, at: NOW + (ms || 0), fn, cleared: false });
  return id;
}
function clearTimeoutStub(id) {
  const t = timers.find((t) => t.id === id);
  if (t) t.cleared = true;
}
// Advance the clock by `ms`, firing each un-cleared timer whose deadline we cross, in
// deadline order — exactly the browser's behavior.
function advance(ms) {
  const target = NOW + ms;
  while (true) {
    const due = timers.filter((t) => !t.cleared && t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    NOW = due.at;
    due.cleared = true;   // setTimeout is one-shot
    due.fn();
  }
  NOW = target;
}

// ---- a minimal real React: hook cells persist across re-renders ------------
function makeReact() {
  const cells = [];
  let idx = 0;
  let renderFn = null;
  let result = null;
  function useState(init) {
    const i = idx++;
    if (cells[i] === undefined) cells[i] = { v: typeof init === 'function' ? init() : init };
    const cell = cells[i];
    const set = (next) => { cell.v = (typeof next === 'function') ? next(cell.v) : next; rerender(); };
    return [cell.v, set];
  }
  function useRef(init) {
    const i = idx++;
    if (cells[i] === undefined) cells[i] = { current: init };
    return cells[i];
  }
  // The /chat poll + clearTimers effects are NOT exercised here (we drive arm/clear + the
  // clock directly), so useEffect/useCallback can be lightweight: callbacks pass through,
  // effects are skipped (their cleanup-only/interval bodies aren't under test).
  function useCallback(fn) { return fn; }
  function useEffect() {}
  function rerender() { idx = 0; result = renderFn(); }
  const React = { useState, useRef, useCallback, useEffect, createElement: () => null, Fragment: 'F' };
  function mount(fn) { renderFn = fn; rerender(); }
  return { React, mount };
}

const reactHost = makeReact();
const sandbox = {
  React: reactHost.React,
  // app.jsx ends with ReactDOM.createRoot(...).render(<App/>); stub it to a no-op so module load
  // doesn't try to mount the whole app — we drive useLiveSession ourselves (defined above that line).
  ReactDOM: { createRoot: () => ({ render() {} }) },
  // getElementById returns truthy so screen-table.jsx's ensureDmNarrateStyle() IIFE early-returns
  // (it injects a <style> on first load; irrelevant to timing and needs no real DOM here).
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}) },
  setTimeout: setTimeoutStub,
  clearTimeout: clearTimeoutStub,
  setInterval: () => 0,
  clearInterval: () => {},
  fetch: () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }),
  console,
};
sandbox.window = sandbox;
sandbox.Date = { now: () => NOW };
vm.createContext(sandbox);

function load(p, stripBootstrap) {
  let src = fs.readFileSync(p, 'utf8');
  // app.jsx ends with `ReactDOM.createRoot(...).render(<ToastProvider>…)` — a browser bootstrap
  // that references components defined in OTHER bundle files (ToastProvider, …) not loaded here.
  // Strip that trailing statement so module load defines our targets (useLiveSession, the timing
  // helper + constants — all ABOVE it) without needing the whole app's component graph stubbed.
  if (stripBootstrap) {
    const i = src.indexOf('ReactDOM.createRoot');
    if (i !== -1) src = src.slice(0, i);
  }
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(screen_table)s);
load(%(app)s, true);

const useLiveSession = sandbox.window.useLiveSession;
if (typeof useLiveSession !== 'function') throw new Error('useLiveSession not exported');

// Mount the hook over a LIVE campaign so its body runs (campaignId must be truthy).
const state = { activeCampaign: 'camp1', campaigns: [{ id: 'camp1', campaign_id: 'camp1' }] };
let api = null;
reactHost.mount(() => { api = useLiveSession(state); return null; });

const win = sandbox.window;
const h = {
  now: () => NOW,
  advance,
  pending: () => api.pending,
  arm: (text) => api.armPending(text || 'do something'),
  clear: () => api.clearPending(),
  recoveryWindowMs: (firstBeat) => win.recoveryWindowMs(firstBeat),
  constants: () => win.__PENDING_TIMING__,
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX hook")
class RecoveryTimingTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_APP, _SCREEN_TABLE, _BABEL):
            cls.assertTrue(p.exists(), f"missing {p}")

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "screen_table": json.dumps(str(_SCREEN_TABLE)),
            "app": json.dumps(str(_APP)),
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

    # --- the timing CONTRACT: constants exported, correctly ordered -----------
    def test_constants_exported_and_ordered(self):
        c = self._run("h.constants()")
        self.assertIn("recoveryMs", c)
        self.assertIn("recoveryFirstMs", c)
        self.assertIn("backstopMs", c)
        # The first-beat window is generous (minutes), strictly larger than the later-beat one,
        # and still strictly inside the hard backstop. This is the whole shape of the #348 fix.
        self.assertLess(c["recoveryMs"], c["recoveryFirstMs"])
        self.assertLess(c["recoveryFirstMs"], c["backstopMs"])
        # Concretely: later = 90s, first = 4 min, backstop = 12 min.
        self.assertEqual(c["recoveryMs"], 90 * 1000)
        self.assertEqual(c["recoveryFirstMs"], 4 * 60 * 1000)
        self.assertEqual(c["backstopMs"], 12 * 60 * 1000)

    # --- the pure selector: both branches (this is exactly what armPending calls) ---
    def test_recovery_window_selector_both_branches(self):
        out = self._run("({ first: h.recoveryWindowMs(true), later: h.recoveryWindowMs(false) })")
        self.assertEqual(out["first"], 4 * 60 * 1000)
        self.assertEqual(out["later"], 90 * 1000)

    # --- #348 CORE: the FIRST beat is NOT falsely declared stuck at 90s -------
    def test_first_beat_survives_past_the_old_90s_threshold(self):
        out = self._run(
            "h.arm('open the scene');"
            # 91s in — the OLD fixed window would already be 'stuck'. The first beat must NOT be.
            "h.advance(91 * 1000);"
            "var p1 = h.pending();"
            "({ stuck_at_91s: !!(p1 && p1.stuck), firstBeat: !!(p1 && p1.firstBeat), active: !!(p1 && !p1.stuck) })"
        )
        self.assertFalse(out["stuck_at_91s"], "first beat falsely went stuck at 91s (the #348 bug)")
        self.assertTrue(out["active"], "first beat should still be narrating (pending, not stuck) at 91s")
        self.assertTrue(out["firstBeat"], "first-beat pending should carry firstBeat:true so the copy is honest")

    # --- the slow-but-valid opening DOES eventually recover (real stall safety net) ---
    def test_first_beat_does_recover_after_the_long_window(self):
        out = self._run(
            "h.arm('open the scene');"
            "h.advance(91 * 1000);"
            "var early = h.pending();"
            # cross the 4-min first-beat deadline (total ~251s)
            "h.advance(160 * 1000);"
            "var late = h.pending();"
            "({ early_stuck: !!(early && early.stuck), late_stuck: !!(late && late.stuck) })"
        )
        self.assertFalse(out["early_stuck"])
        self.assertTrue(out["late_stuck"], "a genuinely-stalled opening must STILL recover after its long window")

    # --- the 12-min hard backstop is preserved (clears even a wedged turn) ----
    def test_backstop_clears_pending_entirely(self):
        out = self._run(
            "h.arm('open the scene');"
            # Past the 12-min backstop — pending should be force-cleared to null.
            "h.advance((12 * 60 + 5) * 1000);"
            "({ pending_is_null: h.pending() === null })"
        )
        self.assertTrue(out["pending_is_null"], "the 12-min backstop must still force-clear a wedged turn")

    # --- #344 contract: clearPending wipes pending + disarms the timers -------
    def test_clear_pending_resets_state_and_disarms_timers(self):
        out = self._run(
            "h.arm('do thing');"
            "var armed = h.pending();"
            "h.clear();"
            "var cleared = h.pending();"
            # advancing past every window after a manual clear must NOT resurrect a stuck flag —
            # this is what lets the #344 'Try again' re-arm cleanly via a fresh armPending.
            "h.advance((13 * 60) * 1000);"
            "var afterAdvance = h.pending();"
            "({ armed_present: !!armed, cleared_null: cleared === null, no_resurrect: afterAdvance === null })"
        )
        self.assertTrue(out["armed_present"])
        self.assertTrue(out["cleared_null"])
        self.assertTrue(out["no_resurrect"], "cleared timers must not fire after clearPending (clean #344 retry re-arm)")
