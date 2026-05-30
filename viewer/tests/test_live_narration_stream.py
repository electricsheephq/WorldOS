"""Behavior tests for the #393 LIVE narration stream in `useLiveSession`.

The #1 playtest satisfaction-killer (build e6384e8): a DM turn takes ~60-90s and the
player saw a waiting indicator but NO content building — the whole beat landed at
turn-END (the duo/human runner appends ONE {"role":"dm",...} line to /chat only after
the turn's `result` is in). Impatient personas read the blank wait as "broken" and
quit (adversarial gave up 1m23s on the cold-open; narrative ~90s on turn 2).

The fix is bounded — NOT a streaming rewrite. The DM logs each narration/dialogue beat
via the engine's `log_event` DURING its turn, and the engine appends it to the per-
session log IMMEDIATELY (store.append_log). The viewer's `/events` endpoint already
tails that log with a line cursor. So `useLiveSession` (viewer/openworlds/app.jsx) now
polls `/events` alongside `/chat`: each new narration paragraph becomes a live, time-
stamped chronicle beat AND clears the "narrating…" indicator on first arrival — a blank
90s wait becomes 90s of prose appearing. The turn-END /chat line carries the same prose;
a shared text-keyed dedup (`claimNarration`) shows each paragraph EXACTLY ONCE, from
whichever source reached the player first.

These tests exercise the REAL hook by transpiling the actual `.jsx` with the SAME bundled
Babel-standalone the browser uses and running it under Node with a deterministic React +
controllable fetch/interval stub (mirrors test_recovery_timing.py), so the test tracks the
shipped behavior rather than a reimplementation. They cover:

  • mid-turn /events narration is surfaced as live chatBeats (the scene builds), and the
    FIRST streamed paragraph clears the pending "narrating…" indicator (the give-up fix);
  • the turn-END /chat copy of an already-streamed paragraph is DEDUPED (shown once);
  • a /chat paragraph that did NOT stream still renders (no regression for non-streamed prose);
  • a fresh run resets the dedup set (no cross-run suppression).
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


# A self-contained Node harness: a minimal-but-real React stub (state/refs/effects persist
# across renders; useEffect bodies actually RUN so the /chat + /events polls are live) plus a
# SCRIPTED fetch (a per-URL queue of JSON responses) and a manual interval pump. It transpiles
# screen-table.jsx (defines sanitizeNarration) then app.jsx, mounts useLiveSession over a live
# campaign, and exposes a tiny async scripting surface (`h`) so each test can enqueue /events
# and /chat responses, pump the pollers, and read the resulting chatBeats + pending state.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

// ---- a real-enough React: hook cells + effects persist; effects RUN on each render ----------
// A real-enough React. KEY contract (mirrors React's actual model, which a naive stub gets
// wrong → infinite recursion): rendering and EFFECT-FLUSHING are decoupled. A setState only
// recomputes the render OUTPUT (cheap, synchronous, no effect side-effects); effects run in a
// SEPARATE committed pass that is re-entrancy-guarded, so a setState fired from inside an effect
// (e.g. clearPending → setPending(null) during the poll) schedules — not recursively runs — the
// next flush. This is exactly why React defers effects to after commit; the stub must too.
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
    // setState recomputes the render output (so api() is fresh) but does NOT flush effects here —
    // the committed flush owns that. This is what breaks the recursion.
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
  // useCallback MUST memoize by deps (return the SAME function identity when deps are unchanged).
  // A naive identity-passthrough returns a fresh fn every render → every useEffect whose deps
  // include a callback sees "deps changed" every render → effects re-queue → an effect's setState
  // re-renders → infinite loop at mount. Memoizing mirrors React and stabilizes effect deps.
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
  // Recompute the render output only (resets hook cursors, re-runs the component body, re-queues
  // any dep-changed effects into pendingEffects — but does NOT run them).
  function render() {
    sIdx = 0; rIdx = 0; cIdx = 0; eIdx = 0;
    result = renderFn();
  }
  // Commit: run queued effects. A setState inside an effect re-renders (re-queueing dep-changed
  // effects) but cannot recurse into flushEffects (the guard) — those run in the same drain loop.
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

// ---- a SCRIPTED fetch: queues of JSON payloads keyed by URL path -----------------------------
const responses = { '/events': [], '/chat': [] };
function enqueue(path, payload) { responses[path].push(payload); }
function pathOf(url) { return String(url).split('?')[0]; }
function fetchStub(url) {
  const p = pathOf(url);
  const q = responses[p];
  const payload = (q && q.length) ? q.shift() : {};   // empty when nothing scripted
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
}

// ---- a manual interval pump: collect the registered poll callbacks, fire on demand -----------
const intervals = [];
function setIntervalStub(fn) { intervals.push(fn); return intervals.length; }
function clearIntervalStub() {}
// onVisibility() calls pollOnce() immediately AND registers an interval; we expose a manual "tick"
// that fires every registered poll once AND awaits the async chain each returns (the polls do
// `await fetch().json()` then setState), so a test can read fully-settled state afterward. A couple
// of extra microtask drains cover any trailing .then() the poll schedules after its setState.
async function tickAll() {
  const ps = intervals.slice().map((fn) => { try { return fn(); } catch (_e) { return undefined; } });
  await Promise.all(ps.map((p) => Promise.resolve(p)));
  await new Promise((r) => setImmediate(r));
}

let NOW = 1000000;
const sandbox = {
  React: null,  // set below
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: () => 0, clearTimeout: () => {},
  setInterval: setIntervalStub, clearInterval: clearIntervalStub,
  fetch: fetchStub,
  // The JSX poll code runs INSIDE this vm context, so the web/JS globals it uses must be present
  // here (a vm context does NOT inherit the host's globals). URLSearchParams is built by both polls;
  // Promise/JSON are used by the async fetch chain + sanitize. Missing URLSearchParams was the bug:
  // `new URLSearchParams()` threw, the poll's try/catch swallowed it, and no fetch ever fired.
  URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number,
  console,
};
sandbox.window = sandbox;
sandbox.Date = { now: () => NOW };
const reactHost = makeReact();
sandbox.React = reactHost.React;
vm.createContext(sandbox);

function load(p, stripBootstrap) {
  let src = fs.readFileSync(p, 'utf8');
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

// Mount over a LIVE campaign so the polls run. The `state` ref is mutable so a test can switch
// the bound run (campaignId change) to assert the dedup reset.
const state = { activeCampaign: 'camp1', campaigns: [{ id: 'camp1', campaign_id: 'camp1' }] };
reactHost.mount(() => useLiveSession(state));

// A microtask drain: the pollers are async (await fetch().json()); after firing them we must let
// the promise chain settle so setChatBeats/clearPending have applied before we read state.
function drain() { return new Promise((r) => setImmediate(r)); }

const h = {
  enqueue,
  // fire every registered poll once + AWAIT its async chain (so setChatBeats/clearPending have
  // applied), then commit any effects a poll's setState re-queued. Await this in the script.
  tick: async () => { await tickAll(); reactHost.commit(); },
  // switch the bound run: a fresh mount re-renders the (same-cell) hook so the campaignId-change
  // effect fires (resetting the cursor + dedup set), exactly like navigating into a new live run.
  setCampaign: (id) => { state.activeCampaign = id; state.campaigns = [{ id, campaign_id: id }]; reactHost.mount(() => useLiveSession(state)); },
  beats: () => (reactHost.api().chatBeats || []).map((b) => ({ kind: b.kind, text: b.text })),
  narrationTexts: () => (reactHost.api().chatBeats || []).filter((b) => b.kind === 'narration').map((b) => b.text),
  pending: () => reactHost.api().pending,
  // arm the "DM is narrating…" indicator exactly as a posted player move does (armPending is on
  // the hook's returned api). Used to prove a streamed paragraph CLEARS it (the give-up fix).
  arm: (text) => reactHost.api().armPending(text || 'open the scene'),
  // #399: the hook's public player-echo append (the chronicle's optimistic "You: …" row). Idempotent
  // so a #344 'Try again' re-POST of the exact stalled move doesn't double the line.
  echo: (who, text) => reactHost.api().recordPlayerEcho(who, text),
  log: () => (reactHost.api().log || []).map((e) => ({ kind: e.kind, who: e.who, text: e.text })),
  // #399: the recovery-window selector by turn position (firstBeat ⇒ cold-open window, else later).
  recoveryWindowMs: (firstBeat) => sandbox.window.recoveryWindowMs(firstBeat),
  drain,
};

// Each test's `script` is a sequence of statements ending in `return (<resultExpr>)`. The scripts
// use `await` (to drain the async pollers), so we run the body inside an async arrow via a DIRECT
// eval — `eval` of a string does not by itself grant a top-level-await context, so the explicit
// `(async () => { ... })()` wrapper supplies it. The arrow's `return` makes the trailing expression
// the resolved value. `h` is in lexical scope here (a direct eval sees the enclosing consts).
const script = %(script)s;
eval('(async () => { ' + script + ' })()')
  .then((result) => { process.stdout.write(JSON.stringify(result)); })
  .catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX hook")
class LiveNarrationStreamTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_APP, _SCREEN_TABLE, _BABEL):
            assert p.exists(), f"missing {p}"

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

    # --- sanity: the hook mounts and starts empty (the polls ran with nothing scripted) -------
    def test_starts_empty(self):
        out = self._run("await h.drain(); return ({ beats: h.beats(), pending: h.pending() });")
        self.assertEqual(out["beats"], [])
        self.assertIsNone(out["pending"])

    # --- #393 CORE: mid-turn /events narration surfaces as a live beat -------------------------
    def test_events_narration_surfaces_live(self):
        out = self._run(
            # The mount fired the first poll (nothing scripted). Now script an /events response and
            # pump the interval so the live narration stream ingests it.
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'The tavern hushes as you enter.' }], next: 1 });"
            "await h.tick();"
            "return ({ texts: h.narrationTexts() });"
        )
        self.assertEqual(out["texts"], ["The tavern hushes as you enter."],
                         "a mid-turn /events narration row must surface as a live chronicle beat")

    # --- #393 CORE: a streamed beat shows prose but KEEPS the turn gated (Option B) ------------
    # The give-up fix is "I can watch my story arriving", NOT "let me act mid-turn". So a paragraph
    # streamed via /events while the turn is in flight must (a) appear in the chronicle, while (b)
    # the "narrating…" indicator STAYS up (pending present, not stuck) — the action bar remains
    # gated (one move at a time). The turn only RESOLVES (pending → null) when its final text lands
    # on /chat (see test_chat_resolves_a_streamed_turn). And the streamed prose clears any 'stuck'.
    def test_streamed_beat_shows_prose_but_keeps_turn_gated(self):
        out = self._run(
            # Simulate a turn in flight: arm the "narrating…" indicator (as a posted move does).
            "h.arm('I push open the door');"
            "var armed = h.pending();"
            # mid-turn: the DM logs a beat -> it streams via /events while the turn is still running.
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'The hinges shriek.' }], next: 1 });"
            "await h.tick();"
            "var p = h.pending();"
            "return ({ armed_present: !!armed, pending_present: !!p, stuck: !!(p && p.stuck), texts: h.narrationTexts() });"
        )
        self.assertTrue(out["armed_present"], "the indicator should arm when a move is posted")
        self.assertEqual(out["texts"], ["The hinges shriek."],
                         "the mid-turn beat must stream live (the scene visibly builds)")
        self.assertTrue(out["pending_present"],
                        "a streamed beat must KEEP the turn gated — the bar stays disabled until /chat resolves it")
        self.assertFalse(out["stuck"],
                         "fresh streamed prose proves the turn is alive — it must not read as stuck")

    # --- #393: the turn-END /chat line RESOLVES a turn whose prose already streamed --------------
    def test_chat_resolves_a_streamed_turn(self):
        out = self._run(
            "h.arm('I push open the door');"
            # prose streams mid-turn (turn stays gated)
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'The hinges shriek.' }], next: 1 });"
            "await h.tick();"
            "var midTurn = h.pending();"
            # turn-END: the same prose lands on /chat → the turn resolves (pending clears, deduped).
            "h.enqueue('/chat', { items: [{ role: 'dm', text: 'The hinges shriek.' }], next: 1 });"
            "await h.tick();"
            "return ({ mid_pending: !!midTurn, after_pending: h.pending(), texts: h.narrationTexts() });"
        )
        self.assertTrue(out["mid_pending"], "the turn is gated while streaming")
        self.assertIsNone(out["after_pending"],
                          "the turn-END /chat line must RESOLVE the turn (clear the indicator, re-open the bar)")
        self.assertEqual(out["texts"], ["The hinges shriek."],
                         "and the streamed paragraph is still shown exactly once (the /chat copy deduped)")

    # --- a mid-turn /events DIALOGUE row also streams live (not just narration) ----------------
    def test_events_dialogue_streams_live(self):
        out = self._run(
            "h.enqueue('/events', { entries: [{ kind: 'dialogue', text: '\"Well met, traveller.\"' }], next: 1 });"
            "await h.tick();"
            "return ({ texts: h.narrationTexts() });"
        )
        self.assertEqual(out["texts"], ['"Well met, traveller."'],
                         "a mid-turn /events dialogue row must also stream live as chronicle prose")

    # --- #393 CORE: the turn-END /chat copy of an already-streamed paragraph is DEDUPED --------
    def test_chat_copy_of_streamed_paragraph_is_deduped(self):
        out = self._run(
            # 1) mid-turn: the paragraph streams via /events.
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'Rain lashes the cobbles.' }], next: 1 });"
            "await h.tick();"
            "var afterStream = h.narrationTexts();"
            # 2) turn-END: the SAME prose lands on /chat (the runner's chatlog dm line).
            "h.enqueue('/chat', { items: [{ role: 'dm', text: 'Rain lashes the cobbles.' }], next: 1 });"
            "await h.tick();"
            "var afterChat = h.narrationTexts();"
            "return ({ afterStream: afterStream, afterChat: afterChat });"
        )
        self.assertEqual(out["afterStream"], ["Rain lashes the cobbles."])
        self.assertEqual(out["afterChat"], ["Rain lashes the cobbles."],
                         "the turn-END /chat copy of an already-streamed paragraph must be deduped (shown once)")

    # --- dedup is whitespace/case-insensitive (the two projections may differ cosmetically) ----
    def test_dedup_normalizes_whitespace_and_case(self):
        out = self._run(
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'The   gate  groans open.' }], next: 1 });"
            "await h.tick();"
            "h.enqueue('/chat', { items: [{ role: 'dm', text: 'The gate groans open.' }], next: 1 });"
            "await h.tick();"
            "return ({ texts: h.narrationTexts() });"
        )
        self.assertEqual(len(out["texts"]), 1,
                         "a cosmetic whitespace/case difference between the streamed + chat copy must still dedup")

    # --- no regression: a /chat paragraph that did NOT stream still renders --------------------
    def test_unstreamed_chat_paragraph_still_renders(self):
        out = self._run(
            # Nothing on /events; a /chat-only beat (e.g. a terse turn that ended on prose with no
            # interim log_event) must still appear — the dedup must not swallow genuinely-new prose.
            "h.enqueue('/chat', { items: [{ role: 'dm', text: 'You awaken to birdsong.' }], next: 1 });"
            "await h.tick();"
            "return ({ texts: h.narrationTexts() });"
        )
        self.assertEqual(out["texts"], ["You awaken to birdsong."])

    # --- a player echo on /chat is NEVER deduped against narration -----------------------------
    def test_player_chat_line_is_not_deduped(self):
        out = self._run(
            "h.enqueue('/chat', { items: [{ role: 'player', text: 'I draw my blade.' }, { role: 'dm', text: 'Steel rings free.' }], next: 2 });"
            "await h.tick();"
            "return ({ beats: h.beats() });"
        )
        kinds = [b["kind"] for b in out["beats"]]
        self.assertIn("dialog", kinds, "the player's own line must render (as a dialog beat), never deduped as narration")
        self.assertIn("narration", kinds)

    # --- a fresh run RESETS the dedup set (no cross-run suppression) ---------------------------
    def test_new_run_resets_dedup(self):
        out = self._run(
            # Run 1 streams a paragraph.
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'A familiar refrain.' }], next: 1 });"
            "await h.tick();"
            "var run1 = h.narrationTexts();"
            # Switch to a NEW campaign/run, then the SAME text arrives — it must NOT be suppressed by
            # run-1's dedup keys (the cursor + seen-set reset per run).
            "h.setCampaign('camp2'); await h.drain();"
            "h.enqueue('/events', { entries: [{ kind: 'narration', text: 'A familiar refrain.' }], next: 1 });"
            "await h.tick();"
            "var run2 = h.narrationTexts();"
            "return ({ run1: run1, run2: run2 });"
        )
        self.assertEqual(out["run1"], ["A familiar refrain."])
        self.assertEqual(out["run2"], ["A familiar refrain."],
                         "a new run must reset the dedup set so identical prose isn't wrongly suppressed across runs")

    # --- #399: a resolved DM beat makes the NEXT turn a LATER beat (firstBeat=false) -----------
    # The recovery window is turn-position-aware: the cold-open gets the generous 4-min window, but
    # turns 2+ get the (now 180s, #399) later window. This proves the firstBeat flip happens after a
    # real beat resolves on /chat — so the later-beat window genuinely governs beats 2–4 (the slow-
    # but-working turns the playtester gave up on), NOT the cold-open window.
    def test_resolved_beat_makes_next_turn_a_later_beat(self):
        out = self._run(
            # Turn 1: arm, then resolve it with a turn-END /chat DM line (bumps the internal beat count).
            "h.arm('open the scene');"
            "h.enqueue('/chat', { items: [{ role: 'dm', text: 'You stand at the gates of Baldur\\u2019s Gate.' }], next: 1 });"
            "await h.tick();"
            "var afterTurn1 = h.pending();"  # JS string; afterTurn1 should be null (turn resolved)
            # Turn 2: arm again — this pending must be a LATER beat (firstBeat:false).
            "h.arm('walk through the gate');"
            "var turn2 = h.pending();"
            "return ({ turn1_resolved: afterTurn1 === null, turn2_firstBeat: !!(turn2 && turn2.firstBeat), turn2_active: !!(turn2 && !turn2.stuck) });"
        )
        self.assertTrue(out["turn1_resolved"], "the turn-END /chat line should resolve turn 1")
        self.assertTrue(out["turn2_active"], "turn 2 should arm a fresh narrating indicator")
        self.assertFalse(out["turn2_firstBeat"],
                         "turn 2 must be a LATER beat (firstBeat=false) → it uses the 180s later-beat window, not the cold-open window")

    # --- #399: the later-beat recovery window is 180s (covers the worst-case ~120s turn) -------
    def test_later_beat_window_is_180s(self):
        out = self._run(
            "return ({ first: h.recoveryWindowMs(true), later: h.recoveryWindowMs(false) });"
        )
        self.assertEqual(out["later"], 180 * 1000,
                         "the later-beat window must be 180s so a content-rich 90–120s beat 2–4 isn't falsely declared stuck (#399)")
        self.assertEqual(out["first"], 4 * 60 * 1000, "the cold-open window is unchanged (4 min)")

    # --- #399: the player echo is IDEMPOTENT (the 'Try again' re-POST doesn't duplicate) -------
    # The #344 stuck-recovery re-POSTs the EXACT stalled move (postMove → recordPlayerEcho again),
    # which used to append a SECOND identical action row — the duplicated "Rolan—" the playtester
    # filed. A back-to-back identical (who, text) must NOT double the chronicle line.
    def test_player_echo_is_idempotent_on_retry(self):
        out = self._run(
            "await h.drain();"
            "h.echo('Rolan', 'Rolan\\u2014 hold the line');"  # original submit
            "var afterFirst = h.log();"
            "h.echo('Rolan', 'Rolan\\u2014 hold the line');"  # 'Try again' re-POST (exact same move)
            "var afterRetry = h.log();"
            "return ({ afterFirst: afterFirst, afterRetry: afterRetry });"
        )
        self.assertEqual(len(out["afterFirst"]), 1, "the first submit records one action row")
        self.assertEqual(len(out["afterRetry"]), 1,
                         "a 'Try again' re-POST of the exact same move must NOT duplicate the chronicle action (#399)")
        self.assertEqual(out["afterRetry"][0]["text"], "Rolan— hold the line")

    # --- #399: a DIFFERENT action (a rephrase, or a later turn) is NOT deduped -----------------
    def test_player_echo_keeps_distinct_actions(self):
        out = self._run(
            "await h.drain();"
            "h.echo('Rolan', 'hold the line');"
            "h.echo('Rolan', 'fall back to the bridge');"  # a genuinely different move
            "return ({ log: h.log() });"
        )
        self.assertEqual(len(out["log"]), 2,
                         "two distinct actions must both appear (idempotence only suppresses a back-to-back exact repeat)")


if __name__ == "__main__":
    unittest.main()
