"""#1375 — Continue Chronicle must not lose party + inventory from view on resume.

Found by the first native .app gate (qa/ui_playtest_runs/app-gate-v105): a first-timer resumed via
"Continue Chronicle" and hit "Session surface unavailable: Failed to fetch" — the party panel read
"No party", inventory showed 0 items, and the chronicle truncated. Root cause: resuming does a FULL
reload (screen-launcher startPlay -> location.assign), so ScreenTable remounts with `surface = null`;
when the FIRST /session-surface fetch fails (the backend momentarily unreachable / not-yet-ready on
resume — the run's backend had exited after its play-budget cap, so every fetch got
ERR_CONNECTION_REFUSED), the catch path left `surface` null and the whole table blanked.

The fix persists the freshest surface per-campaign in sessionStorage (SessionSurfaceCache, mirroring
building-universe.jsx's cross-reload facade) and hydrates ScreenTable's initial `surface` from it, so
a resume/remount holds party + inventory + chronicle in view under the "unavailable" banner while
live play stays gated (surfaceStatus !== "ready" — the cache restores the VIEW, never enables acting).

These exercise the REAL shipped code (viewer/openworlds/*.jsx), mirroring the sibling JS-behavior
harnesses (test_nav_chronicle_resilience.py, test_building_universe.py). Skipped where Node is absent.
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


# A compact stateful React (deps-honoring useEffect, stable useCallback, lazy useState init) + an
# in-memory sessionStorage + a controllable clock, so the REAL SessionSurfaceCache + ScreenTable
# hydration run deterministically. A never-resolving fetch keeps loadSurface pending (no re-render
# storm) so we observe the mount purely as the resume remount sees it before any live surface lands.
_PRELUDE = r"""
const fs = require('fs'); const vm = require('vm'); const path = require('path');
const OW = %(ow)s;
const Babel = require(%(babel)s);
let NOW = 1000000;
function makeReact() {
  const s = [], r = [], e = []; let si = 0, ri = 0, ei = 0, rf = null, res = null;
  const pe = []; let fl = false; let RC = 0;
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
  return { React, mount, get result() { return res; } };
}
const host = makeReact();
const _store = {};
let _throwStorage = false;
const sessionStorage = {
  getItem: (k) => { if (_throwStorage) throw new Error('private mode'); return (k in _store) ? _store[k] : null; },
  setItem: (k, v) => { if (_throwStorage) throw new Error('private mode'); _store[k] = String(v); },
  removeItem: (k) => { if (_throwStorage) throw new Error('private mode'); delete _store[k]; },
};
const sb = {
  React: host.React, console, sessionStorage,
  setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 2, clearInterval: () => {},
  fetch: () => new Promise(() => {}),
  URLSearchParams, Promise, JSON, Set, Map, Array, Object, String, Boolean, Number, Math, isNaN, parseInt, parseFloat, console,
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible',
              getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({ style: {} }) },
};
sb.Date = { now: () => NOW };
sb.window = sb; sb.window.window = sb;
vm.createContext(sb);
function load(f) {
  let src = fs.readFileSync(path.join(OW, f), 'utf8');
  const i = src.indexOf('ReactDOM.createRoot'); if (i !== -1) src = src.slice(0, i);
  const code = Babel.transform(src, { presets: ['react'], filename: f }).code;
  vm.runInContext(code, sb);
}
// Load every openworlds component so ScreenTable's cross-file deps (Panel, Img, Toast, ...) resolve.
for (const f of fs.readdirSync(OW).filter((x) => x.endsWith('.jsx'))) load(f);
const Cache = sb.window.SessionSurfaceCache;
const ScreenTable = sb.window.ScreenTable;
function mountTable(campaignId) {
  const state = { activeCampaign: campaignId, campaigns: [{ id: campaignId, campaign_id: campaignId, world: 'baldurs-gate' }] };
  const live = { chatBeats: [], log: [], pending: null, armPending() {}, clearPending() {}, recordPlayerEcho() {} };
  const tree = host.mount(() => ScreenTable({ state, setState() {}, onNavigate() {}, liveSession: live }));
  const texts = []; (function w(n) { if (!n || typeof n !== 'object') return; for (const c of (n.children || [])) { if (typeof c === 'string') texts.push(c); w(c); } })(tree);
  const has = (n, name) => (!n || typeof n !== 'object') ? false : (n.type === name ? true : (n.children || []).some((c) => has(c, name)));
  return { hasPartyRow: has(tree, 'PartyRow'), hasNoParty: texts.includes('No party') };
}
const SAMPLE = { campaign_id: 'camp_a', updated_at: 5,
  party: [{ id: 'tav', name: 'Tav', kind: 'player' }, { id: 'sh', name: 'Shadowheart' }],
  quickInventory: [{ id: 'i1', name: 'Torch' }] };
function setNow(v) { NOW = v; }
function setThrowStorage(v) { _throwStorage = v; }
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
# The regression: a resume remount whose first surface fetch is pending/failing must NOT blank the
# table — it renders the party from the last-known-good cache instead of "No party".
# ===========================================================================================
def test_resume_mount_restores_party_from_cache_when_surface_fetch_unavailable():
    out = _run(
        "Cache.write('camp_a', SAMPLE);"           # a prior live surface was cached
        "return mountTable('camp_a');"             # resume remounts; fetch never resolves (unavailable)
    )
    assert out["hasPartyRow"], "resume mount must restore the party from the last-known-good cache (#1375)"
    assert not out["hasNoParty"], "the table must not blank to 'No party' on a resume with an unavailable surface"


def test_resume_mount_with_no_cache_still_shows_empty_state():
    # The test bites: with NO cached surface (and the fetch unavailable), the table honestly shows the
    # empty state — this is exactly the pre-fix behavior, proving the cache is what restores the party.
    out = _run("return mountTable('camp_a');")
    assert not out["hasPartyRow"], "no cache + unavailable surface has no party to render"
    assert out["hasNoParty"], "without a cached surface the empty state is the honest fallback"


# ===========================================================================================
# The persistence facade (SessionSurfaceCache) — the mechanism that carries the surface across the
# location.assign reload. Mirrors test_building_universe.py's OpenWorldsBuilding coverage.
# ===========================================================================================
def test_cache_round_trips_the_surface_per_campaign():
    out = _run(
        "Cache.write('camp_a', SAMPLE);"
        "const got = Cache.read('camp_a');"
        "return { party: got && got.party && got.party.length, inv: got && got.quickInventory && got.quickInventory.length,"
        "         otherCampaignIsIsolated: Cache.read('camp_b') === null };"
    )
    assert out["party"] == 2, "cache must round-trip the party roster across the reload"
    assert out["inv"] == 1, "cache must round-trip the quick inventory across the reload"
    assert out["otherCampaignIsIsolated"], "a different campaign must not read another campaign's surface"


def test_cache_drops_a_record_past_the_backstop():
    out = _run(
        "setNow(0); Cache.write('camp_a', SAMPLE);"
        "setNow(8 * 24 * 60 * 60 * 1000);"                 # 8 days later > 7-day backstop
        "const stale = Cache.read('camp_a');"
        "return { stale: stale === null, cleared: sb.sessionStorage.getItem('openworlds.surface:camp_a') === null };"
    )
    assert out["stale"], "a surface older than the backstop must not be resurrected"
    assert out["cleared"], "an expired record must be cleared from storage"


def test_cache_is_safe_when_storage_throws_and_on_empty_campaign():
    out = _run(
        "setThrowStorage(true);"
        "Cache.write('camp_a', SAMPLE);"                    # must swallow (private mode)
        "const r = Cache.read('camp_a');"                   # must swallow -> null
        "setThrowStorage(false);"
        "return { readWhenThrowing: r === null, emptyCampaign: Cache.read('') === null };"
    )
    assert out["readWhenThrowing"], "read must not throw when sessionStorage is unavailable (private mode)"
    assert out["emptyCampaign"], "read with no campaign id must return null (never a cross-campaign hit)"
