"""#1636 — the play/session screen must bind its surface in a STANDALONE-SANDBOX context.

Found via an eyes-on preview session against the merged adventure fixture (#1632): running
`viewer/server.py adventure_demo_v1 <port>` against a fresh seeded state dir (no native player, no
DM process — the A-G walked-eval context) left the play/session screen permanently on "Loading
session surface." with empty party/quest/action panels, while a `curl /session-surface?campaign=<id>`
returned the complete surface. Root cause: ScreenTable's /session-surface poll was gated behind
`document.visibilityState === "visible"` — on mount it called `handleVisibility()`, which in a
headless / never-focused sandbox tab (visibilityState "hidden" for the tab's whole life) took the
`else` branch and NEVER issued the fetch. So the surface was requested zero times; only the
app-level campaigns.json poll (not visibility-gated) looped.

The fix (additive; the live/visible player path is unchanged) makes the initial /session-surface
load + poll UNCONDITIONAL on mount, so the surface binds even in a permanently-hidden sandbox tab,
and keeps polling while hidden when the tab was hidden AT MOUNT (a standalone surface). A tab that
started VISIBLE keeps the battery-friendly pause-on-background behavior.

The JS-behavior test exercises the REAL shipped code (viewer/openworlds/*.jsx) under a mocked
transport with a fetch spy and a hidden document, mirroring the sibling harnesses
(test_continue_chronicle_surface.py, test_nav_chronicle_resilience.py). Skipped where Node is absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


# A compact stateful React (deps-honoring useEffect, stable useCallback, lazy useState init) + a
# controllable document.visibilityState + a fetch SPY that records requested URLs (and never
# resolves, so we observe the mount synchronously as the sandbox surface sees it). `mountTable`
# returns the URLs the component requested on mount plus whether a repeating poll was armed.
_PRELUDE = r"""
const fs = require('fs'); const vm = require('vm'); const path = require('path');
const OW = %(ow)s;
const Babel = require(%(babel)s);
const __fetches = [];
let __intervals = 0;
let __visibility = 'hidden';
function makeReact() {
  const s = [], r = [], e = []; let si = 0, ri = 0, ei = 0, rf = null;
  const pe = []; let fl = false; let RC = 0; let res = null;
  function useState(init) {
    const k = si++;
    if (s[k] === undefined) s[k] = { v: (typeof init === 'function' ? init() : init) };
    const cell = s[k];
    return [cell.v, (n) => { cell.v = (typeof n === 'function' ? n(cell.v) : n); render(); }];
  }
  function useRef(init) { const k = ri++; if (r[k] === undefined) r[k] = { current: init }; return r[k]; }
  function useCallback(fn) { return fn; }
  function useEffect(fn, deps) {
    const k = ei++; const prev = e[k];
    const changed = !prev || !deps || deps.some((d, x) => d !== prev.deps[x]);
    if (changed) { e[k] = { deps }; pe.push(fn); }
  }
  function ce(t, p, ...ch) {
    return { type: (typeof t === 'function' ? (t.name || 'C') : t), fn: (typeof t === 'function' ? t : null),
             props: p || {}, children: ch.flat(Infinity).filter((x) => x != null) };
  }
  function flush() { if (fl) return; fl = true; while (pe.length) { try { pe.shift()(); } catch (err) {} } fl = false; }
  function render() { if (++RC > 100) throw new Error('runaway renders'); si = 0; ri = 0; ei = 0; res = rf(); flush(); return res; }
  function mount(fn) { rf = fn; return render(); }
  const React = { useState, useRef, useCallback, useEffect, createElement: ce, Fragment: 'F',
    createContext: (d) => ({ Provider: function Provider(p) { return p && p.children; }, Consumer: 'Consumer', _def: d }),
    useContext: (c) => c && c._def, useMemo: (fn) => fn(), useLayoutEffect: useEffect };
  return { React, mount };
}
const host = makeReact();
const sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const sb = {
  React: host.React, console, sessionStorage,
  setTimeout: () => 1, clearTimeout: () => {},
  setInterval: () => { __intervals++; return 2; }, clearInterval: () => {},
  fetch: (url) => { __fetches.push(String(url)); return new Promise(() => {}); },
  URLSearchParams, Promise, JSON, Set, Map, Array, Object, String, Boolean, Number, Math, isNaN, parseInt, parseFloat,
  document: { addEventListener() {}, removeEventListener() {}, get visibilityState() { return __visibility; },
              getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({ style: {} }) },
};
sb.Date = { now: () => 1000000 };
sb.window = sb; sb.window.window = sb;
vm.createContext(sb);
function load(f) {
  let src = fs.readFileSync(path.join(OW, f), 'utf8');
  const i = src.indexOf('ReactDOM.createRoot'); if (i !== -1) src = src.slice(0, i);
  const code = Babel.transform(src, { presets: ['react'], filename: f }).code;
  vm.runInContext(code, sb);
}
for (const f of fs.readdirSync(OW).filter((x) => x.endsWith('.jsx'))) load(f);
const ScreenTable = sb.window.ScreenTable;
function mountTable(visibility) {
  __visibility = visibility; __fetches.length = 0; __intervals = 0;
  const cid = 'adventure_demo_v1';
  const state = { activeCampaign: cid, campaigns: [{ id: cid, campaign_id: cid, source: 'play', runId: 'state', world: 'x' }] };
  const live = { chatBeats: [], log: [], pending: null, armPending() {}, clearPending() {}, recordPlayerEcho() {} };
  host.mount(() => ScreenTable({ state, setState() {}, onNavigate() {}, liveSession: live }));
  return { fetches: __fetches.slice(), intervals: __intervals };
}
"""


def _run(body: str) -> dict:
    program = (
        _PRELUDE % {"ow": json.dumps(str(OPENWORLDS)), "babel": json.dumps(str(BABEL))}
        + "\nconst out = (function () {\n" + body + "\n})();\nprocess.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


# ===========================================================================================
# The regression: a HIDDEN mount (the standalone-sandbox / headless play surface) must still request
# /session-surface and arm the poll — the exact context #1636 left stuck on "Loading session surface."
# ===========================================================================================
def test_hidden_standalone_mount_still_fetches_session_surface():
    out = _run("return mountTable('hidden');")
    surface_hits = [u for u in out["fetches"] if "/session-surface" in u]
    assert surface_hits, (
        "a hidden standalone-sandbox tab must still request /session-surface on mount (#1636) — "
        f"requested: {out['fetches']!r}"
    )
    assert any("campaign=adventure_demo_v1" in u for u in surface_hits), (
        "the surface fetch must carry the active campaign so the sandbox surface binds"
    )
    assert out["intervals"] >= 1, "a hidden standalone surface must arm the polling interval so it stays fresh"


def test_visible_mount_still_fetches_session_surface_unchanged():
    # The live/visible player path is unchanged: a visible mount fetches the surface + arms the poll.
    out = _run("return mountTable('visible');")
    assert any("/session-surface" in u for u in out["fetches"]), "the visible player path must still fetch the surface"
    assert out["intervals"] >= 1, "the visible player path must still arm the poll"


# ===========================================================================================
# Static-source guard (runs even where Node is absent): the surface-poll effect must NOT gate its
# initial fetch behind visibility, and must carry the standalone (hidden-at-mount) polling heuristic.
# ===========================================================================================
def test_table_source_does_unconditional_initial_surface_load():
    source = (OPENWORLDS / "screen-table.jsx").read_text(encoding="utf-8")
    # The initial load + poll are armed unconditionally on mount (not behind `handleVisibility()`).
    assert "const standalone = document.visibilityState !== \"visible\";" in source, (
        "the effect must capture whether the tab was hidden AT MOUNT (the standalone-sandbox case)"
    )
    # The old `handleVisibility();` mount call (which skipped the fetch when hidden) must be gone,
    # replaced by an explicit unconditional guardedLoad() + startPolling().
    assert "handleVisibility();\n" not in source, (
        "the mount must not gate the first surface load behind handleVisibility() (#1636)"
    )
    load_idx = source.index("document.addEventListener(\"visibilitychange\", handleVisibility);")
    tail = source[load_idx:]
    assert "guardedLoad();" in tail and "startPolling();" in tail, (
        "mount must call guardedLoad() + startPolling() unconditionally so a hidden sandbox binds"
    )
