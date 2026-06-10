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
  // #745: drive the live-progress signal exactly as the /events poll does (a streamed paragraph
  // landed for the in-flight turn), so the mid-stream stall ceiling is exercised against the real code.
  note: () => api.notePendingProgress(),
  recoveryWindowMs: (firstBeat) => win.recoveryWindowMs(firstBeat),
  constants: () => win.__PENDING_TIMING__,
};

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
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
        c = self._run("h.constants()")
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
        out = self._run("({ first: h.recoveryWindowMs(true), later: h.recoveryWindowMs(false) })")
        self.assertEqual(out["first"], 4 * 60 * 1000)
        self.assertEqual(out["later"], 180 * 1000)  # #399: was 90s

    # --- #399 CORE: the FIRST-beat window covers a 120s turn without going stuck --------------
    # The first beat already gets the generous 4-min window, so a 120s turn is comfortably inside it.
    # (The LATER-beat 180s window can't be exercised in THIS harness — flipping firstBeat=false needs
    # a real DM beat to arrive via the /chat poll, which is stubbed here; that path is covered in
    # test_live_narration_stream.py::test_resolved_beat_makes_next_turn_a_later_beat. Here we lock
    # that NEITHER window trips at 120s, the worst-case content-rich turn the playtester gave up on.)
    def test_no_false_stuck_at_120s(self):
        out = self._run(
            "h.arm('open the scene');"
            # 120s in — the OLD later-beat window (90s) would already be 'stuck'. The first-beat
            # window (and the new 180s later window) must NOT be.
            "h.advance(120 * 1000);"
            "var p1 = h.pending();"
            "({ stuck_at_120s: !!(p1 && p1.stuck), active_at_120s: !!(p1 && !p1.stuck) })"
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
            # advance past the #648 arm-grace so this exercises a GENUINE clear (a real resolution
            # lands long after submit); the same-tick protection is covered by the #648 test below.
            "h.advance(h.constants().armGraceMs + 1000);"
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
            "({ armed: armed, survives: survived, narrating: narrating, resolves_later: h.pending() === null })"
        )
        self.assertTrue(out["armed"], "armPending should arm a narrating turn")
        self.assertTrue(out["survives"], "#648: a same-tick clear must NOT wipe the just-armed spinner")
        self.assertTrue(out["narrating"], "the protected turn stays in the narrating (not stuck) state")
        self.assertTrue(out["resolves_later"], "the protected turn still resolves on the real (post-grace) clear")


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX hook")
class MidStreamStallTests(_BabelHarness):
    """#745 — the newbie mid-stream-stall give-up (the lone v1.0.4-rc2 RRI holdout @c92a393).

    A DM beat that STREAMS partial prose via /events and then FREEZES mid-generation must STILL recover
    to the recoverable `stuck` 'Try again' affordance within a BOUNDED time — independent of how many
    partial paragraphs landed. Before #745, `notePendingProgress` re-armed the FULL position window
    (180s/240s) AND cleared `stuck` on every streamed paragraph, so a multi-paragraph trickle that then
    froze kept pushing the deadline forward; recovery was deferred to the silent 12-min null-backstop
    (clears pending to null → a plain re-enabled bar, no 'Try again', the partial narration stranded) —
    the ~12–15-min lockout the newbie gave up on.

    The fix is a HARD stuck-backstop armed once in armPending that progress does NOT reset
    (PENDING_STUCK_BACKSTOP_MS, ~5 min from submit). A trickle can no longer defer recovery past it, and
    it resolves to the SAME recoverable `stuck` state (NOT a null clear). It is deliberately generous so
    a long-but-HEALTHY streaming turn (which RESOLVES on /chat → clearPending cancels every timer) never
    trips it — preserving the #348/#399/#623 live-progress behavior. These tests drive the REAL hook
    (armPending + notePendingProgress + a fake clock) so they track the shipped behavior.
    """

    def test_stuck_backstop_constant_exported_and_strictly_ordered(self):
        c = self._run("h.constants()")
        self.assertIn("stuckBackstopMs", c)
        # The hard stuck ceiling sits STRICTLY between the (resettable) position windows and the 12-min
        # null-backstop: position recovery < stuck-backstop < null-backstop. So a frozen turn always
        # surfaces the recoverable `stuck` affordance BEFORE the last-resort null clear.
        self.assertLess(c["recoveryMs"], c["stuckBackstopMs"])
        self.assertLess(c["recoveryFirstMs"], c["stuckBackstopMs"])
        self.assertLess(c["stuckBackstopMs"], c["backstopMs"])
        self.assertEqual(c["stuckBackstopMs"], 5 * 60 * 1000)

    def test_multi_partial_trickle_then_freeze_recovers_to_stuck_not_null(self):
        """The literal newbie scenario: SEVERAL paragraphs stream ('You give the sergeant your own
        name… The charcoal touches the paper… That's the arithmetic o—') then it freezes mid-word. Each
        partial used to reset the full window AND clear `stuck`, deferring recovery to the 12-min
        null-backstop. Now the hard stuck-backstop (progress does NOT reset it) surfaces the recoverable
        `stuck` 'Try again' affordance — bounded, and as `stuck` (pending non-null), not a silent clear."""
        out = self._run(
            "h.arm('give my own name');"
            # eight partials, 30s apart (a plausibly-alive trickle) → 240s of streaming. Under the OLD code
            # `stuck` never fired during this (every partial reset the full window AND cleared stuck), and
            # recovery waited for the 12-min null-backstop. Capture that the trickle stays alive…
            "for (var i = 0; i < 8; i++) { h.advance(30 * 1000); h.note(); }"
            "var duringTrickle = h.pending();"
            # …then the final paragraph FREEZES. Walk forward; capture when `stuck` first fires and the
            # state at that moment (must be the recoverable stuck, NOT a null clear).
            "var t = 240 * 1000; var stuckAt = null; var nulledFirst = false;"
            "while (t < 11 * 60 * 1000) { h.advance(5 * 1000); t += 5 * 1000;"
            "  var q = h.pending(); if (q === null) { nulledFirst = (stuckAt === null); break; }"
            "  if (q && q.stuck) { stuckAt = t; break; } }"
            "({ alive_during_trickle: !!(duringTrickle && !duringTrickle.stuck && duringTrickle.streaming),"
            "   stuck_fired: stuckAt !== null, stuck_at_ms_from_submit: stuckAt,"
            "   nulled_before_stuck: nulledFirst, still_pending_at_stuck: !!(h.pending()) })"
        )
        self.assertTrue(out["alive_during_trickle"],
                        "a flowing trickle stays narrating (not stuck) — the live-progress feel is preserved")
        self.assertTrue(out["stuck_fired"],
                        "#745: a trickle-then-freeze MUST recover to `stuck`, not vanish via the 12-min null-backstop")
        self.assertFalse(out["nulled_before_stuck"],
                         "recovery must surface the RECOVERABLE `stuck` affordance, not a silent null clear")
        # The give-up was a ~12–15-min lockout. The hard ceiling bounds total stall from submit well under
        # that — it fires by PENDING_STUCK_BACKSTOP_MS regardless of how many partials trickled in.
        self.assertIsNotNone(out["stuck_at_ms_from_submit"])
        self.assertLessEqual(out["stuck_at_ms_from_submit"], 5 * 60 * 1000 + 5 * 1000,
                             "the hard stuck-backstop must fire by ~5 min from submit, not the 12-min null-backstop")
        self.assertTrue(out["still_pending_at_stuck"],
                        "recovery surfaces the `stuck` 'Try again' affordance (pending stays non-null)")

    def test_stuck_backstop_is_not_reset_by_progress(self):
        """The crux: streamed progress re-arms the per-progress recovery timer but must NOT push the hard
        stuck-backstop forward. So even a long, frequent trickle is bounded by the submit-anchored ceiling."""
        out = self._run(
            "h.arm('do');"
            # frequent partials right up to just before the 5-min ceiling — each resets the position timer
            # (proving the turn looks 'alive' to the per-progress path) but must NOT move the hard ceiling.
            "for (var i = 0; i < 9; i++) { h.advance(30 * 1000); h.note(); }"   # 270s of streaming
            "var at270 = h.pending();"
            # cross the 5-min submit ceiling with NO further progress → the hard backstop fires `stuck`.
            "h.advance(35 * 1000);"   # 305s from submit, past the 300s ceiling
            "var at305 = h.pending();"
            "({ alive_at_270s: !!(at270 && !at270.stuck), stuck_at_305s: !!(at305 && at305.stuck) })"
        )
        self.assertTrue(out["alive_at_270s"],
                        "frequent progress keeps the turn narrating up to the ceiling (per-progress timer reset)")
        self.assertTrue(out["stuck_at_305s"],
                        "#745: the hard stuck-backstop is anchored to submit — progress must NOT defer it")

    def test_healthy_streaming_turn_that_resolves_never_trips_the_stuck_backstop(self):
        """A long-but-HEALTHY streaming turn RESOLVES on /chat (clearPending) before the hard ceiling, which
        cancels every timer — so the stuck-backstop never false-positives on a slow-but-alive beat (the
        #348/#399/#623 live-progress contract is preserved)."""
        out = self._run(
            "h.arm('open the scene');"
            # 4 minutes of healthy streaming (well past the 240s first-beat window, UNDER the 5-min ceiling),
            # then the turn resolves on /chat (clearPending) — exactly what a real completed beat does.
            "for (var i = 0; i < 8; i++) { h.advance(30 * 1000); h.note(); }"   # 240s
            "h.advance(h.constants().armGraceMs + 1000);"
            "h.clear();"                                   # the turn RESOLVED on /chat
            "var resolved = h.pending();"
            # advance far past the 5-min stuck-backstop AND the 12-min null-backstop — a resolved turn must
            # not resurrect any stuck/pending state (the timers were cancelled by clearPending).
            "h.advance(13 * 60 * 1000);"
            "var afterAll = h.pending();"
            "({ resolved_null: resolved === null, no_resurrect: afterAll === null })"
        )
        self.assertTrue(out["resolved_null"], "a resolved healthy turn clears pending")
        self.assertTrue(out["no_resurrect"],
                        "a resolved turn must NOT later trip the stuck-backstop (clearPending cancelled it)")

    def test_pre_stream_slow_open_still_uses_the_full_window(self):
        """A slow cold-open that streams NOTHING yet keeps the generous first-beat window — the #348/#399
        false-stuck guard. The stuck-backstop is a HARD additional ceiling, not a replacement: it does not
        clip the legit pre-stream think (which is well under 5 min) to anything shorter."""
        out = self._run(
            "h.arm('open the scene');"
            # 120s with NO streamed paragraph — neither the 240s first-beat window nor the 5-min ceiling
            # has elapsed, so the turn is still narrating (no false stuck).
            "h.advance(120 * 1000);"
            "var p = h.pending();"
            "({ stuck_at_120s_no_stream: !!(p && p.stuck), narrating: !!(p && !p.stuck) })"
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
            f"win.computePlayGate({{ surfaceStatus: {json.dumps(surface_status)},"
            f" appStatus: {app_status_js}, pendingStuck: {pending_stuck} }})"
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
        return self._run("win.computeColdOpenAwaiting(%s)" % json.dumps(base))

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
        return self._run("win.shouldApplySurface(%s, %s)" % (json.dumps(prev), json.dumps(nxt)))

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
