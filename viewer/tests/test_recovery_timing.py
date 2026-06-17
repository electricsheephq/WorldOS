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

#745 added a HARD stuck ceiling from submit (progress does NOT reset it) so a
mid-stream trickle-then-freeze still recovers to the `stuck` 'Try again' affordance.
#746 made that ceiling BUDGET-AWARE by turn position: the original flat 5-min value
sat BELOW the system's own healthy turn budgets (cold open ~300s measured with a
400–500s deadline; a continuing beat can run ~400s via play.sh's 200s timeout + ONE
retry), so it false-fired `stuck` on healthy slow turns — re-opening the action bar,
toasting "DM seems stuck", and letting a retry re-POST resolve the same intent TWICE.

These tests exercise the REAL code by transpiling the actual `.jsx` with the SAME
bundled Babel-standalone the browser uses and running it under Node with a
deterministic React + fake-timer stub, so the test tracks the shipped behavior
rather than a reimplementation (mirrors test_sanitize_narration.py). They cover:

  • the timing CONTRACT (`recoveryWindowMs` + `stuckBackstopMs` for both branches
    + the constants and their strict ordering);
  • the hook's observable FIRST-beat behavior (no false stuck at 91s; recovers
    after the long window; the 12-min backstop still force-clears) — the #348 fix;
  • the LATER-beat branch — the harness's /chat poll is LIVE, so a test can resolve
    a turn (flipping firstBeat) and exercise the later-beat ceiling for real;
  • the #745 mid-stream-stall ceiling + the #746 budget-aware false-fire fix;
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


# A self-contained Node harness: a real-enough React stub (state/refs/callbacks/effects persist
# across renders; useEffect bodies RUN, so the /chat + /events polls are live) plus a controllable
# clock + one-shot timer queue AND a scripted fetch (a per-URL queue of JSON responses) with a
# manual interval pump. The live /chat poll matters here (#746): resolving a turn — a {"role":"dm"}
# line landing on /chat — is the ONLY thing that flips the hook's firstBeat off, and the later-beat
# stuck-backstop branch can't be exercised without it. It transpiles screen-table.jsx (defines
# sanitizeNarration onto window, used by app.jsx) then app.jsx with the bundled Babel, mounts
# useLiveSession over a live campaign, and exposes a tiny scripting surface (`h`) so each test can
# arm/clear pending, stream progress, RESOLVE turns, advance the fake clock, and read pending state
# — plus the pure `recoveryWindowMs`/`stuckBackstopMs` + constants.
#
# `script` is an async JS body evaluated with `h`/`win` in scope; its `return` value → JSON on stdout.
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

// ---- a real-enough React (ported from test_live_narration_stream.py) -------
// Hook cells + effects persist across renders; useEffect bodies actually RUN so the /chat poll is
// live and a test can RESOLVE a turn — flipping firstBeat — before arming the next one (#746 needs
// the later-beat branch of the hook, which an effect-less stub cannot reach). KEY contract
// (mirrors React's actual model): rendering and EFFECT-FLUSHING are decoupled — a setState only
// recomputes the render output; effects run in a separate re-entrancy-guarded committed pass, so a
// setState fired from inside an effect schedules (not recursively runs) the next flush.
function makeReact() {
  const stateCells = [];
  const refCells = [];
  const cbCells = [];          // memoized { fn, deps } per useCallback slot
  const effects = [];          // { deps, cleanup } per useEffect slot, in mount order
  let sIdx = 0, rIdx = 0, cIdx = 0, eIdx = 0;
  let renderFn = null;
  let result = null;
  const pendingEffects = [];   // effect bodies queued this render, drained by flushEffects()
  let flushing = false;        // re-entrancy guard: a setState during a flush re-renders only

  function useState(init) {
    const i = sIdx++;
    if (stateCells[i] === undefined) stateCells[i] = { v: typeof init === 'function' ? init() : init };
    const cell = stateCells[i];
    const set = (next) => { cell.v = (typeof next === 'function') ? next(cell.v) : next; render(); };
    return [cell.v, set];
  }
  function useRef(init) {
    const i = rIdx++;
    if (refCells[i] === undefined) refCells[i] = { current: init };
    return refCells[i];
  }
  function depsEqual(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  // useCallback MUST memoize by deps (same identity when deps are unchanged) — a naive passthrough
  // returns a fresh fn every render → every effect whose deps include a callback re-queues every
  // render → an effect's setState re-renders → infinite loop at mount.
  function useCallback(fn, deps) {
    const i = cIdx++;
    const prev = cbCells[i];
    if (prev === undefined || !depsEqual(prev.deps, deps)) {
      cbCells[i] = { fn, deps };
      return fn;
    }
    return prev.fn;
  }
  function useEffect(fn, deps) {
    const i = eIdx++;
    const prev = effects[i];
    const changed = !prev || !depsEqual(prev.deps, deps);
    if (changed) {
      pendingEffects.push(() => {
        if (prev && typeof prev.cleanup === 'function') prev.cleanup();
        const cleanup = fn();
        effects[i] = { deps, cleanup: typeof cleanup === 'function' ? cleanup : null };
      });
      if (!prev) effects[i] = { deps, cleanup: null };  // seed so the slot exists
      else effects[i].deps = deps;
    }
  }
  function render() {
    sIdx = 0; rIdx = 0; cIdx = 0; eIdx = 0;
    result = renderFn();
  }
  function flushEffects() {
    if (flushing) return;
    flushing = true;
    try { while (pendingEffects.length) pendingEffects.shift()(); }
    finally { flushing = false; }
  }
  const React = { useState, useRef, useCallback, useEffect, createElement: () => null, Fragment: 'F' };
  function mount(fn) { renderFn = fn; render(); flushEffects(); }
  function commit() { flushEffects(); }
  function api() { return result; }
  return { React, mount, commit, api };
}

// ---- a SCRIPTED fetch + manual interval pump (ported from test_live_narration_stream.py) ----
const responses = { '/events': [], '/chat': [] };
function enqueue(path, payload) { responses[path].push(payload); }
function pathOf(url) { return String(url).split('?')[0]; }
function fetchStub(url) {
  const p = pathOf(url);
  const q = responses[p];
  const payload = (q && q.length) ? q.shift() : {};   // empty when nothing scripted
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
}
const intervals = [];
function setIntervalStub(fn) { intervals.push(fn); return intervals.length; }
function clearIntervalStub() {}
// Fire every registered poll once AND await the async chain each returns (the polls do
// `await fetch().json()` then setState), so a test reads fully-settled state afterward.
async function tickAll() {
  const ps = intervals.slice().map((fn) => { try { return fn(); } catch (_e) { return undefined; } });
  await Promise.all(ps.map((p) => Promise.resolve(p)));
  await new Promise((r) => setImmediate(r));
}

const reactHost = makeReact();
const sandbox = {
  React: reactHost.React,
  // app.jsx ends with ReactDOM.createRoot(...).render(<App/>); stub it to a no-op so module load
  // doesn't try to mount the whole app — we drive useLiveSession ourselves (defined above that line).
  ReactDOM: { createRoot: () => ({ render() {} }) },
  // getElementById returns truthy so screen-table.jsx's ensureDmNarrateStyle() IIFE early-returns
  // (it injects a <style> on first load; irrelevant to timing and needs no real DOM here).
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: setTimeoutStub,
  clearTimeout: clearTimeoutStub,
  setInterval: setIntervalStub,
  clearInterval: clearIntervalStub,
  fetch: fetchStub,
  // The JSX poll code runs INSIDE this vm context, so the web/JS globals it uses must be present
  // here (a vm context does NOT inherit the host's globals).
  URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number,
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
reactHost.mount(() => useLiveSession(state));

const win = sandbox.window;
let chatNext = 0;
const h = {
  now: () => NOW,
  advance,
  pending: () => reactHost.api().pending,
  arm: (text) => reactHost.api().armPending(text || 'do something'),
  clear: () => reactHost.api().clearPending(),
  // #745: drive the live-progress signal exactly as the /events poll does (a streamed paragraph
  // landed for the in-flight turn), so the mid-stream stall ceiling is exercised against the real code.
  note: () => reactHost.api().notePendingProgress(),
  // #746: RESOLVE the in-flight turn exactly as a real one resolves — a {"role":"dm"} line lands on
  // /chat (the polled turn-END signal). That is the ONLY writer of resolvedTurnsRef, so the NEXT
  // armPending is a LATER beat (firstBeat=false) — the branch the budget-aware ceiling keys on.
  // NOTE: clearPending runs inside this (the #648 arm-grace applies — resolve > armGraceMs after arm).
  resolveTurn: async (text) => {
    chatNext += 1;
    enqueue('/chat', { items: [{ role: 'dm', text: text || 'The beat resolves.' }], next: chatNext });
    await tickAll();
    reactHost.commit();
  },
  recoveryWindowMs: (firstBeat) => win.recoveryWindowMs(firstBeat),
  stuckBackstopMs: (firstBeat) => win.stuckBackstopMs(firstBeat),
  constants: () => win.__PENDING_TIMING__,
};

// The script body may await (resolveTurn drains the async pollers); run it inside an async arrow —
// its `return` is the resolved value (mirrors test_live_narration_stream.py).
const script = %(script)s;
eval('(async () => { ' + script + ' })()')
  .then((result) => { process.stdout.write(JSON.stringify(result)); })
  .catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


class _BabelHarness(unittest.TestCase):
    """Shared Node+Babel harness: transpiles the real .jsx and runs a JS `script` against `h`/`win`.
    A NON-test base (no `test_*` methods) so subclasses don't re-run each other's cases."""

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


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX hook")
class RecoveryTimingTests(_BabelHarness):
    pass

    # --- the timing CONTRACT: constants exported, correctly ordered -----------
    def test_constants_exported_and_ordered(self):
        c = self._run("return h.constants();")
        self.assertIn("recoveryMs", c)
        self.assertIn("recoveryFirstMs", c)
        self.assertIn("backstopMs", c)
        # The first-beat window is generous (minutes), strictly larger than the later-beat one,
        # and still strictly inside the hard backstop. This is the whole shape of the #348 fix.
        self.assertLess(c["recoveryMs"], c["recoveryFirstMs"])
        self.assertLess(c["recoveryFirstMs"], c["backstopMs"])
        # Concretely: later = 180s (#399, was 90s), first = 4 min, backstop = 12 min.
        self.assertEqual(c["recoveryMs"], 180 * 1000)
        self.assertEqual(c["recoveryFirstMs"], 4 * 60 * 1000)
        self.assertEqual(c["backstopMs"], 12 * 60 * 1000)

    # --- the pure selector: both branches (this is exactly what armPending calls) ---
    def test_recovery_window_selector_both_branches(self):
        out = self._run("return ({ first: h.recoveryWindowMs(true), later: h.recoveryWindowMs(false) });")
        self.assertEqual(out["first"], 4 * 60 * 1000)
        self.assertEqual(out["later"], 180 * 1000)  # #399: was 90s

    # --- #399 CORE: the FIRST-beat window covers a 120s turn without going stuck --------------
    # The first beat already gets the generous 4-min window, so a 120s turn is comfortably inside it.
    def test_no_false_stuck_at_120s(self):
        out = self._run(
            "h.arm('open the scene');"
            # 120s in — the OLD later-beat window (90s) would already be 'stuck'. The first-beat
            # window (and the new 180s later window) must NOT be.
            "h.advance(120 * 1000);"
            "var p1 = h.pending();"
            "return ({ stuck_at_120s: !!(p1 && p1.stuck), active_at_120s: !!(p1 && !p1.stuck) });"
        )
        self.assertFalse(out["stuck_at_120s"], "a 120s turn must not be falsely declared stuck (#399)")
        self.assertTrue(out["active_at_120s"], "a 120s turn should still be narrating (pending, not stuck)")

    # --- #348 CORE: the FIRST beat is NOT falsely declared stuck at 90s -------
    def test_first_beat_survives_past_the_old_90s_threshold(self):
        out = self._run(
            "h.arm('open the scene');"
            # 91s in — the OLD fixed window would already be 'stuck'. The first beat must NOT be.
            "h.advance(91 * 1000);"
            "var p1 = h.pending();"
            "return ({ stuck_at_91s: !!(p1 && p1.stuck), firstBeat: !!(p1 && p1.firstBeat), active: !!(p1 && !p1.stuck) });"
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
            "return ({ early_stuck: !!(early && early.stuck), late_stuck: !!(late && late.stuck) });"
        )
        self.assertFalse(out["early_stuck"])
        self.assertTrue(out["late_stuck"], "a genuinely-stalled opening must STILL recover after its long window")

    # --- the 12-min hard backstop is preserved (clears even a wedged turn) ----
    def test_backstop_clears_pending_entirely(self):
        out = self._run(
            "h.arm('open the scene');"
            # Past the 12-min backstop — pending should be force-cleared to null.
            "h.advance((12 * 60 + 5) * 1000);"
            "return ({ pending_is_null: h.pending() === null });"
        )
        self.assertTrue(out["pending_is_null"], "the 12-min backstop must still force-clear a wedged turn")

    # --- #344 contract: clearPending wipes pending + disarms the timers -------
    def test_clear_pending_resets_state_and_disarms_timers(self):
        out = self._run(
            "h.arm('do thing');"
            "var armed = h.pending();"
            # advance past the #648 arm-grace so this exercises a GENUINE clear (a real resolution
            # lands long after submit); the same-tick protection is covered by the #648 test below.
            "h.advance(h.constants().armGraceMs + 1000);"
            "h.clear();"
            "var cleared = h.pending();"
            # advancing past every window after a manual clear must NOT resurrect a stuck flag —
            # this is what lets the #344 'Try again' re-arm cleanly via a fresh armPending.
            "h.advance((13 * 60) * 1000);"
            "var afterAdvance = h.pending();"
            "return ({ armed_present: !!armed, cleared_null: cleared === null, no_resurrect: afterAdvance === null });"
        )
        self.assertTrue(out["armed_present"])
        self.assertTrue(out["cleared_null"])
        self.assertTrue(out["no_resurrect"], "cleared timers must not fire after clearPending (clean #344 retry re-arm)")

    # --- #648: a freshly-armed narrating turn survives a SPURIOUS same-tick clear -------------
    def test_armpending_survives_a_same_tick_clear_then_resolves(self):
        """#648 (the move-sink → pending-arm contract): after an Enter-submitted Do, a clearPending
        can fire milliseconds later — the immediate post-armPending surface poll, a /chat cursor-reset
        re-reading the prior resolved turn's line as a fresh resolution, or a transient campaignId
        flip tripping the per-run reset. That MUST NOT wipe the just-armed narrating spinner (the
        adversarial's '[MAJOR] no spinner, buttons enabled, no DM for 3+ min until I clicked
        Continue'). The guard is bounded: a REAL resolution (~100–150s later, past the grace) still
        clears the turn, so the spinner is never stuck on."""
        out = self._run(
            "h.arm('Stand down. That child is with me.');"
            "var armed = !!h.pending();"
            "h.clear();"                                     # the spurious same-tick clear (must be a no-op)
            "var p = h.pending();"
            "var survived = !!p;"
            "var narrating = !!(p && !p.stuck);"
            "h.advance(h.constants().armGraceMs + 1000);"    # the real DM beat lands well past the grace
            "h.clear();"                                     # genuine resolution → clears
            "return ({ armed: armed, survives: survived, narrating: narrating, resolves_later: h.pending() === null });"
        )
        self.assertTrue(out["armed"], "armPending should arm a narrating turn")
        self.assertTrue(out["survives"], "#648: a same-tick clear must NOT wipe the just-armed spinner")
        self.assertTrue(out["narrating"], "the protected turn stays in the narrating (not stuck) state")
        self.assertTrue(out["resolves_later"], "the protected turn still resolves on the real (post-grace) clear")


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX hook")
class MidStreamStallTests(_BabelHarness):
    """#745 mid-stream-stall ceiling + #746 budget-aware false-fire fix.

    #745 (the newbie mid-stream-stall give-up, the lone v1.0.4-rc2 RRI holdout @c92a393): a DM beat
    that STREAMS partial prose via /events and then FREEZES mid-generation must STILL recover to the
    recoverable `stuck` 'Try again' affordance within a BOUNDED time — independent of how many partial
    paragraphs landed. The fix is a HARD stuck-backstop armed once in armPending that progress does
    NOT reset; a trickle can no longer defer recovery to the silent 12-min null-backstop.

    #746: the #745 ceiling was a FLAT 5 min — BELOW the system's own healthy turn budgets. The cold
    open measures ~300s with a 400–500s deadline (qa/lib_beat_driver.sh worldos_dm_timeout; 500s for
    Opus), and a healthy CONTINUING beat can run ~400s (scripts/play.sh CLAWDND_BEAT_TIMEOUT=200s +
    ONE retry). So the ceiling false-fired `stuck` on healthy slow turns: the action bar re-opened
    mid-flight, the 'DM seems stuck' toast fired, and retryStuck re-POSTed the move — the SAME intent
    resolving TWICE once the in-flight beat landed. The fix makes the ceiling budget-aware by turn
    position (`stuckBackstopMs`, mirroring `recoveryWindowMs`): firstBeat ⇒ 9 min (≥ the 500s
    cold-open budget); later beats ⇒ 7 min (≥ the ~400s timeout+retry budget). Both stay strictly
    under the 12-min null-backstop, so the #745 ordering (resettable position window < hard stuck
    ceiling < null clear) — and its trickle-then-freeze protection — is retained.

    These tests drive the REAL hook (armPending + notePendingProgress + the live /chat resolution
    path + a fake clock) so they track the shipped behavior.
    """

    def test_stuck_backstop_constants_exported_and_strictly_ordered(self):
        c = self._run("return h.constants();")
        self.assertIn("stuckBackstopMs", c)
        self.assertIn("stuckBackstopFirstMs", c)  # #746: the first-beat (cold-open) ceiling
        # The hard stuck ceilings sit STRICTLY between their (resettable) position windows and the
        # 12-min null-backstop: position recovery < stuck-backstop < null-backstop, per branch. So a
        # frozen turn always surfaces the recoverable `stuck` affordance BEFORE the last-resort null
        # clear, and a healthy in-budget turn resolves before either.
        self.assertLess(c["recoveryMs"], c["stuckBackstopMs"])
        self.assertLess(c["recoveryFirstMs"], c["stuckBackstopFirstMs"])
        self.assertLess(c["stuckBackstopMs"], c["stuckBackstopFirstMs"])
        self.assertLess(c["stuckBackstopFirstMs"], c["backstopMs"])
        # #746 budget-awareness: each ceiling clears its branch's HEALTHY budget with margin —
        # cold open: 400–500s deadline (500s Opus, qa/lib_beat_driver.sh) ⇒ first ceiling ≥ 500s;
        # later beats: 200s timeout + ONE retry ≈ 400s (scripts/play.sh) ⇒ later ceiling ≥ 400s.
        self.assertGreaterEqual(c["stuckBackstopFirstMs"], 500 * 1000,
                                "#746: the first-beat ceiling must clear the 500s cold-open budget")
        self.assertGreaterEqual(c["stuckBackstopMs"], 400 * 1000,
                                "#746: the later-beat ceiling must clear the ~400s timeout+retry budget")
        # Concretely: later = 7 min, first = 9 min.
        self.assertEqual(c["stuckBackstopMs"], 7 * 60 * 1000)
        self.assertEqual(c["stuckBackstopFirstMs"], 9 * 60 * 1000)

    # --- #746: the pure ceiling selector, both branches (exactly what armPending arms) --------
    def test_stuck_backstop_selector_both_branches(self):
        out = self._run("return ({ first: h.stuckBackstopMs(true), later: h.stuckBackstopMs(false) });")
        self.assertEqual(out["first"], 9 * 60 * 1000)
        self.assertEqual(out["later"], 7 * 60 * 1000)

    # --- #746 CORE (THE MISSING BAND): a healthy first beat resolving at ~350–450s ------------
    def test_healthy_first_beat_in_the_cold_open_budget_band_never_shows_stuck(self):
        """The false-fire band the flat 5-min ceiling created: the cold open measures ~300s healthy
        with a 400–500s deadline, so a first beat that streams progress and resolves at ~430s is
        HEALTHY — yet the old ceiling flagged it `stuck` at 300s mid-flight (action bar re-opened,
        'DM seems stuck' toast, retryStuck double-resolution). Sample `stuck` right after each clock
        step BEFORE the next paragraph's progress note — a streamed paragraph CLEARS stuck, so
        sampling after note() would mask the mid-window false fire."""
        out = self._run(
            "h.arm('open the scene');"
            "var sawStuck = false; var stuckAtS = [];"
            # a paragraph streams every 30s through 420s — a healthy cold open inside its budget.
            "for (var i = 0; i < 14; i++) { h.advance(30 * 1000);"
            "  var p = h.pending(); if (p && p.stuck) { sawStuck = true; stuckAtS.push((i + 1) * 30); }"
            "  h.note(); }"
            # …and RESOLVES on /chat at ~430s, squarely inside the measured 400–500s cold-open band.
            "h.advance(10 * 1000);"
            "var q = h.pending(); if (q && q.stuck) sawStuck = true;"
            "await h.resolveTurn('The gates of Embergloom swing open.');"
            "return ({ saw_stuck: sawStuck, stuck_at_s: stuckAtS, resolved_null: h.pending() === null });"
        )
        self.assertFalse(out["saw_stuck"],
                         f"#746: a HEALTHY first beat resolving at ~430s must NEVER show stuck "
                         f"(false-fired at {out['stuck_at_s']}s — the stuck toast + bar re-open + "
                         f"retryStuck double-resolution on a working turn)")
        self.assertTrue(out["resolved_null"], "the healthy turn resolves normally on /chat")

    # --- #746 CORE: a healthy LATER beat resolving at ~380s (timeout + one retry) -------------
    def test_healthy_later_beat_resolving_within_the_retry_budget_never_shows_stuck(self):
        """play.sh gives a continuing beat 200s + ONE retry, so a healthy later beat can resolve at
        ~380s. The old flat 5-min ceiling fired at 300s — mid-retry — on a turn the system itself
        still considered in-budget. Resolving turn 1 first (a real /chat dm line) flips firstBeat
        off, so this exercises the LATER-beat ceiling branch of the real hook."""
        out = self._run(
            # Turn 1 resolves → the next armPending is a LATER beat (firstBeat=false).
            "await h.resolveTurn('You arrive at the tavern.');"
            "h.arm('strike the bandit');"
            "var armed = h.pending();"
            "var sawStuck = false;"
            # the first attempt times out at ~200s and the retry streams; prose lands every 30s.
            # Sample stuck right after each step BEFORE the note (a note clears stuck).
            "for (var i = 0; i < 12; i++) { h.advance(30 * 1000);"
            "  var p = h.pending(); if (p && p.stuck) sawStuck = true;"
            "  h.note(); }"
            "h.advance(20 * 1000);"   # ~380s from submit — the retry lands
            "var q = h.pending(); if (q && q.stuck) sawStuck = true;"
            "await h.resolveTurn('The bandit crumples.');"
            "return ({ later_beat: !!(armed && !armed.firstBeat), saw_stuck: sawStuck,"
            "          resolved_null: h.pending() === null });"
        )
        self.assertTrue(out["later_beat"],
                        "precondition: after a resolved turn the next arm must be a LATER beat (firstBeat=false)")
        self.assertFalse(out["saw_stuck"],
                         "#746: a healthy later beat resolving at ~380s (200s timeout + one retry) "
                         "must NEVER show stuck mid-flight")
        self.assertTrue(out["resolved_null"], "the healthy retry-path turn resolves normally on /chat")

    def test_multi_partial_trickle_then_freeze_recovers_to_stuck_not_null(self):
        """The literal #745 newbie scenario, RETAINED under #746: SEVERAL paragraphs stream ('You give
        the sergeant your own name… The charcoal touches the paper… That's the arithmetic o—') then it
        freezes mid-word. Each partial used to reset the full window AND clear `stuck`, deferring
        recovery to the 12-min null-backstop. Recovery must surface the recoverable `stuck` 'Try
        again' affordance — bounded (≤ the first-beat hard ceiling), and as `stuck` (pending
        non-null), not a silent clear."""
        out = self._run(
            "h.arm('give my own name');"
            # eight partials, 30s apart (a plausibly-alive trickle) → 240s of streaming.
            "for (var i = 0; i < 8; i++) { h.advance(30 * 1000); h.note(); }"
            "var duringTrickle = h.pending();"
            # …then the final paragraph FREEZES. Walk forward; capture when `stuck` first fires and the
            # state at that moment (must be the recoverable stuck, NOT a null clear).
            "var t = 240 * 1000; var stuckAt = null; var nulledFirst = false;"
            "while (t < 11 * 60 * 1000) { h.advance(5 * 1000); t += 5 * 1000;"
            "  var q = h.pending(); if (q === null) { nulledFirst = (stuckAt === null); break; }"
            "  if (q && q.stuck) { stuckAt = t; break; } }"
            "return ({ alive_during_trickle: !!(duringTrickle && !duringTrickle.stuck && duringTrickle.streaming),"
            "   stuck_fired: stuckAt !== null, stuck_at_ms_from_submit: stuckAt,"
            "   nulled_before_stuck: nulledFirst, still_pending_at_stuck: !!(h.pending()) });"
        )
        self.assertTrue(out["alive_during_trickle"],
                        "a flowing trickle stays narrating (not stuck) — the live-progress feel is preserved")
        self.assertTrue(out["stuck_fired"],
                        "#745: a trickle-then-freeze MUST recover to `stuck`, not vanish via the 12-min null-backstop")
        self.assertFalse(out["nulled_before_stuck"],
                         "recovery must surface the RECOVERABLE `stuck` affordance, not a silent null clear")
        # The give-up was a ~12–15-min lockout. Recovery stays bounded well under that: the (resettable)
        # position window re-armed at the LAST partial fires first, and the budget-aware hard ceiling
        # (#746: 9 min for the first beat) caps it regardless of how many partials trickled in.
        self.assertIsNotNone(out["stuck_at_ms_from_submit"])
        self.assertLessEqual(out["stuck_at_ms_from_submit"], 9 * 60 * 1000 + 5 * 1000,
                             "a trickle-then-freeze must go stuck by the (first-beat) hard ceiling, "
                             "not the 12-min null-backstop")
        self.assertTrue(out["still_pending_at_stuck"],
                        "recovery surfaces the `stuck` 'Try again' affordance (pending stays non-null)")

    def test_stuck_backstop_is_not_reset_by_progress(self):
        """The #745 crux, RETAINED at the #746 ceiling: streamed progress re-arms the per-progress
        recovery timer but must NOT push the hard stuck-backstop forward. A trickle that streams right
        up near the (now 9-min first-beat) ceiling and then freezes goes stuck AT the submit-anchored
        ceiling — progress cannot defer it."""
        out = self._run(
            "h.arm('do');"
            # frequent partials up to 510s — inside the 540s first-beat ceiling; each resets the
            # position timer (the turn looks 'alive' to the per-progress path) but must NOT move the
            # hard ceiling.
            "for (var i = 0; i < 17; i++) { h.advance(30 * 1000); h.note(); }"   # 510s of streaming
            "var at510 = h.pending();"
            # cross the 9-min submit ceiling with NO further progress → the hard backstop fires `stuck`.
            "h.advance(35 * 1000);"   # 545s from submit, past the 540s ceiling
            "var at545 = h.pending();"
            "return ({ alive_at_510s: !!(at510 && !at510.stuck), stuck_at_545s: !!(at545 && at545.stuck) });"
        )
        self.assertTrue(out["alive_at_510s"],
                        "frequent progress keeps the turn narrating up to the ceiling (per-progress timer reset)")
        self.assertTrue(out["stuck_at_545s"],
                        "#745/#746: the hard stuck-backstop is anchored to submit — progress must NOT defer it")

    def test_healthy_streaming_turn_that_resolves_never_trips_the_stuck_backstop(self):
        """A long-but-HEALTHY streaming turn RESOLVES on /chat (clearPending) before the hard ceiling, which
        cancels every timer — so the stuck-backstop never false-positives on a slow-but-alive beat (the
        #348/#399/#623 live-progress contract is preserved)."""
        out = self._run(
            "h.arm('open the scene');"
            # 4 minutes of healthy streaming (past the 240s first-beat window, under the ceiling),
            # then the turn resolves on /chat (clearPending) — exactly what a real completed beat does.
            "for (var i = 0; i < 8; i++) { h.advance(30 * 1000); h.note(); }"   # 240s
            "h.advance(h.constants().armGraceMs + 1000);"
            "h.clear();"                                   # the turn RESOLVED on /chat
            "var resolved = h.pending();"
            # advance far past the stuck-backstop AND the 12-min null-backstop — a resolved turn must
            # not resurrect any stuck/pending state (the timers were cancelled by clearPending).
            "h.advance(13 * 60 * 1000);"
            "var afterAll = h.pending();"
            "return ({ resolved_null: resolved === null, no_resurrect: afterAll === null });"
        )
        self.assertTrue(out["resolved_null"], "a resolved healthy turn clears pending")
        self.assertTrue(out["no_resurrect"],
                        "a resolved turn must NOT later trip the stuck-backstop (clearPending cancelled it)")

    def test_pre_stream_slow_open_still_uses_the_full_window(self):
        """A slow cold-open that streams NOTHING yet keeps the generous first-beat window — the #348/#399
        false-stuck guard. The stuck-backstop is a HARD additional ceiling, not a replacement: it does not
        clip the legit pre-stream think to anything shorter, and a genuinely-SILENT beat is still flagged
        by the adaptive recovery window (180s/240s) — the ceiling's job is only the mid-stream stall."""
        out = self._run(
            "h.arm('open the scene');"
            # 120s with NO streamed paragraph — neither the 240s first-beat window nor the ceiling
            # has elapsed, so the turn is still narrating (no false stuck).
            "h.advance(120 * 1000);"
            "var p = h.pending();"
            "return ({ stuck_at_120s_no_stream: !!(p && p.stuck), narrating: !!(p && !p.stuck) });"
        )
        self.assertFalse(out["stuck_at_120s_no_stream"],
                         "the pre-stream slow open must keep the full window — no false stuck at 120s")
        self.assertTrue(out["narrating"], "still narrating at 120s when nothing has streamed yet")


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX gate")
class PlayGateLockoutTests(_BabelHarness):
    """LOCKOUT P0 (detach-locks-the-action-bar) — the CLIENT play-gate contract.

    `computePlayGate` (screen-table.jsx, module scope) is the single source of truth for every
    derived play-gate flag + the player-facing block message. These tests transpile the REAL .jsx
    and exercise that pure function (mirrors the `recoveryWindowMs` tests above), pinning the three
    contracts the sweep blocked on:
      B) the raw "live provider move sink is not ready" dev string NEVER reaches the player;
      C) a STUCK turn re-opens the bar for a real retry through an app-status latch
         (`stuckRetryUnblocked`) — but NOT through a genuine surface outage; and
      -) the no-block happy path stays unblocked.
    """

    _NO_PROVIDER_STATUS = (
        "{ readiness: { ready_for_play: false, failure_bucket: 'no_provider',"
        " failure_detail: 'live provider move sink is not ready' } }"
    )

    def _gate(self, surface_status: str, app_status_js: str, pending_stuck: str):
        return self._run(
            f"return (win.computePlayGate({{ surfaceStatus: {json.dumps(surface_status)},"
            f" appStatus: {app_status_js}, pendingStuck: {pending_stuck} }}));"
        )

    # B) the raw dev string never reaches the player ---------------------------
    def test_raw_move_sink_string_never_leaks_to_block_reason(self):
        gate = self._gate("ready", self._NO_PROVIDER_STATUS, "false")
        self.assertTrue(gate["appStatusBlocksPlay"])
        self.assertTrue(gate["livePlayBlocked"])
        # The humane copy is shown; the raw "move sink"/"provider-backed" jargon is gone.
        self.assertNotIn("move sink", gate["blockReason"].lower())
        self.assertNotIn("provider-backed", gate["blockReason"].lower())
        self.assertIn("Chronicles", gate["blockReason"])
        self.assertIn("Dungeon Master", gate["blockReason"])

    def test_block_reason_maps_each_bucket_to_human_copy(self):
        for bucket in ("no_provider", "no_launcher", "move_rejected"):
            app = (
                "{ readiness: { ready_for_play: false, failure_bucket: '" + bucket + "',"
                " failure_detail: 'live provider move sink is not ready' } }"
            )
            gate = self._gate("ready", app, "false")
            self.assertNotIn("move sink", gate["blockReason"].lower(), bucket)
            self.assertIn("Chronicles", gate["blockReason"], bucket)

    # C) a stuck turn re-opens the bar for a real retry through the app-status latch ----
    def test_stuck_turn_unblocks_retry_through_app_status_latch(self):
        # The lockout: app-status latched no_provider, the surface is fine, the turn is stuck.
        gate = self._gate("ready", self._NO_PROVIDER_STATUS, "true")
        self.assertTrue(gate["livePlayBlocked"], "the app-status latch still blocks normal play")
        self.assertTrue(gate["stuckRetryUnblocked"],
                        "a stuck turn MUST re-open the bar for a retry despite the app-status latch")

    def test_stuck_turn_does_not_unblock_on_surface_outage(self):
        # A genuine surface outage (the fetch failed/loading) → nothing to act on; stay frozen.
        gate = self._gate("unavailable", "null", "true")
        self.assertTrue(gate["surfaceStatusBlocksPlay"])
        self.assertFalse(gate["stuckRetryUnblocked"],
                         "a genuine surface outage must NOT be bypassed even on a stuck turn")

    def test_not_stuck_turn_stays_blocked_under_latch(self):
        # Not stuck + app-status latch → the bar stays blocked (no spurious unblock).
        gate = self._gate("ready", self._NO_PROVIDER_STATUS, "false")
        self.assertFalse(gate["stuckRetryUnblocked"])

    # happy path ---------------------------------------------------------------
    def test_ready_surface_and_status_is_unblocked(self):
        ready = "{ readiness: { ready_for_play: true, failure_bucket: 'none', failure_detail: '' } }"
        gate = self._gate("ready", ready, "false")
        self.assertFalse(gate["surfaceStatusBlocksPlay"])
        self.assertFalse(gate["appStatusBlocksPlay"])
        self.assertFalse(gate["livePlayBlocked"])


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX selector")
class ColdOpenAwaitingTests(_BabelHarness):
    """The cold-open wait affordance gate (RRI 2026-06-09 input-lock give-up): computeColdOpenAwaiting
    fires the watchable 'DM is opening your world' first-beat affordance ONLY in the cold-open frame —
    a LIVE session with nothing landed yet (no chronicle, no seated party) and no pending/stuck beat
    already spinning. Pure selector; the render block in ScreenTable just gates on it."""

    def _awaiting(self, **over):
        base = dict(surfaceStatus="ready", live=True, isLiveView=False, pendingActive=False,
                    pendingStuck=False, visibleLogLength=0, partyEmpty=True)
        base.update(over)
        return self._run("return (win.computeColdOpenAwaiting(%s));" % json.dumps(base))

    def test_fires_at_the_real_coldopen_frame(self):
        # The exact bug frame: live=true, is_live_view=false (a desync the heal hasn't caught), the
        # bar locked, empty chronicle + empty party, no pending beat. RED today (no affordance).
        self.assertTrue(self._awaiting())

    def test_isliveview_only_also_fires(self):
        self.assertTrue(self._awaiting(live=False, isLiveView=True))

    def test_not_live_does_not_fire(self):
        self.assertFalse(self._awaiting(live=False, isLiveView=False),
                         "a read-only / dead session must not show a cold-open spinner")

    def test_seated_party_does_not_fire(self):
        self.assertFalse(self._awaiting(partyEmpty=False),
                         "once the PC is seated the cold-open affordance must clear")

    def test_existing_log_does_not_fire(self):
        self.assertFalse(self._awaiting(visibleLogLength=1),
                         "once a beat has landed the chronicle carries it; no cold-open spinner")

    def test_pending_beat_takes_precedence(self):
        self.assertFalse(self._awaiting(pendingActive=True),
                         "a player-move pending beat shows its own spinner")
        self.assertFalse(self._awaiting(pendingStuck=True),
                         "a stuck beat shows its own affordance")

    def test_surface_not_ready_does_not_fire(self):
        self.assertFalse(self._awaiting(surfaceStatus="loading"),
                         "don't mask a real surface outage with a cold-open spinner")


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX selector")
class SurfaceFreshnessTests(_BabelHarness):
    """shouldApplySurface monotonic guard (RRI 2026-06-09 wall-of-text rollback): a strictly-OLDER
    same-campaign surface is REJECTED so a mid-beat re-fetch can't regress the header backward in
    time (day/HP/location reverting while the live chronicle held). Everything else applies."""

    def _apply(self, prev, nxt):
        return self._run("return (win.shouldApplySurface(%s, %s));" % (json.dumps(prev), json.dumps(nxt)))

    def test_strictly_older_same_campaign_is_rejected(self):
        self.assertFalse(self._apply({"campaign_id": "c", "updated_at": 200},
                                     {"campaign_id": "c", "updated_at": 100}),
                         "an older same-campaign snapshot must not regress the header")

    def test_newer_same_campaign_applies(self):
        self.assertTrue(self._apply({"campaign_id": "c", "updated_at": 100},
                                    {"campaign_id": "c", "updated_at": 200}))

    def test_equal_same_campaign_applies(self):
        self.assertTrue(self._apply({"campaign_id": "c", "updated_at": 100},
                                    {"campaign_id": "c", "updated_at": 100}))

    def test_different_campaign_always_applies(self):
        # A real campaign switch is never a 'regression' — apply even if the new one is older.
        self.assertTrue(self._apply({"campaign_id": "live", "updated_at": 200},
                                    {"campaign_id": "other", "updated_at": 100}))

    def test_first_surface_applies(self):
        self.assertTrue(self._apply(None, {"campaign_id": "c", "updated_at": 100}))

    def test_missing_clock_no_ops(self):
        # No monotonic signal (an older save without updated_at) -> apply, exactly as today.
        self.assertTrue(self._apply({"campaign_id": "c"}, {"campaign_id": "c", "updated_at": 100}))
        self.assertTrue(self._apply({"campaign_id": "c", "updated_at": 100}, {"campaign_id": "c"}))
