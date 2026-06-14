"""Behavior tests for #754 — the browsable preparable spell pool in the OpenWorlds character screen.

From a real optimizer-persona complaint in the 2026-06-15 confirm sweep: a prepared caster
(Paladin) could NOT plan because the Spellbook + Rest & Prepare only showed the FEW currently
prepared/known spells — never the full class spell list to prepare FROM.

Two surfaces are fixed in screen-character.jsx, both fed by the read-model's additive
``hero.preparableSpells`` (the engine's srd524-derived class spell list):

  (1) RestPrepareModal prep step: iterates the FULL preparable pool (grouped by level), so the
      caster can SELECT a spell they have NOT prepared. The chosen set rides the relayed `do`
      /move which names prepare_spells (the viewer stays a move-sink; the engine prepares).

  (2) SpellbookBrowser: renders an "Available to Prepare" section listing the whole class list,
      each tagged Prepared vs Available — alongside the existing prepared/known groups.

These exercise the REAL components by transpiling the shipped screen-character.jsx with the SAME
bundled Babel-standalone the browser uses, rendering under a createElement-capturing React stub
(state persists, effects run, setState re-renders), with a scripted fetch — so the test tracks the
shipped JSX, not a reimplementation (mirrors test_rest_camp_levelup_wiring.py).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_CHARACTER = _OPENWORLDS / "screen-character.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

function makeReact() {
  const stateCells = [], refCells = [], cbCells = [], effects = [];
  let sIdx = 0, rIdx = 0, cIdx = 0, eIdx = 0;
  let renderFn = null, result = null;
  const pendingEffects = [];
  let flushing = false;
  function useState(init) {
    const i = sIdx++;
    if (stateCells[i] === undefined) stateCells[i] = { v: typeof init === 'function' ? init() : init };
    const cell = stateCells[i];
    const set = (next) => { cell.v = (typeof next === 'function') ? next(cell.v) : next; render(); flushEffects(); };
    return [cell.v, set];
  }
  function useRef(init) { const i = rIdx++; if (refCells[i] === undefined) refCells[i] = { current: init }; return refCells[i]; }
  function depsEqual(a, b) { if (!a || !b || a.length !== b.length) return false; for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false; return true; }
  function useCallback(fn, deps) { const i = cIdx++; const prev = cbCells[i]; if (prev === undefined || !depsEqual(prev.deps, deps)) { cbCells[i] = { fn, deps }; return fn; } return prev.fn; }
  function useMemo(fn, deps) { const i = cIdx++; const prev = cbCells[i]; if (prev === undefined || !depsEqual(prev.deps, deps)) { const v = fn(); cbCells[i] = { v, deps }; return v; } return prev.v; }
  function useEffect(fn, deps) {
    const i = eIdx++;
    const prev = effects[i];
    const changed = !prev || !depsEqual(prev.deps, deps);
    if (changed) {
      pendingEffects.push(() => { if (prev && typeof prev.cleanup === 'function') prev.cleanup(); const cleanup = fn(); effects[i] = { deps, cleanup: typeof cleanup === 'function' ? cleanup : null }; });
      if (!prev) effects[i] = { deps, cleanup: null }; else effects[i].deps = deps;
    }
  }
  function createElement(type, props) {
    const children = Array.prototype.slice.call(arguments, 2);
    return { type, props: props || {}, children };
  }
  function render() { sIdx = 0; rIdx = 0; cIdx = 0; eIdx = 0; result = renderFn(); }
  function flushEffects() { if (flushing) return; flushing = true; try { while (pendingEffects.length) pendingEffects.shift()(); } finally { flushing = false; } }
  const React = { useState, useRef, useCallback, useMemo, useEffect, createElement, Fragment: 'F' };
  function mount(fn) { renderFn = fn; render(); flushEffects(); }
  function commit() { flushEffects(); }
  function api() { return result; }
  return { React, mount, commit, api };
}

const posts = [];
function fetchStub(url, opts) {
  const u = String(url).split('?')[0];
  if (u === '/move') {
    let body = {}; try { body = JSON.parse((opts && opts.body) || '{}'); } catch (_e) {}
    posts.push({ url: u, body });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
}

const reactHost = makeReact();
function passthrough(name) { return function (props) { const p = props || {}; const kids = (p.children !== undefined) ? [].concat(p.children) : []; return reactHost.React.createElement(name, p, ...kids); }; }

const sandbox = {
  React: reactHost.React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: (fn) => { return 0; }, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  fetch: fetchStub,
  addEventListener() {}, removeEventListener() {},
  encodeURIComponent, URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math,
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

function load(p) {
  let src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
for (const name of ['Panel','BrassButton','Divider','SectionTitle','Pill','Placeholder','Img','Glyph','CornerOrnament']) {
  sandbox[name] = passthrough(name);
}
const toastCalls = [];
sandbox.useToast = () => (t) => { toastCalls.push(t); };
sandbox.slug = (n) => (n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
sandbox.combatSurfaceFromCampaign = () => '';

load(%(screen_character)s);

function findByTestId(node, id, hits) {
  hits = hits || [];
  if (node == null || typeof node !== 'object') return hits;
  if (Array.isArray(node)) { for (const c of node) findByTestId(c, id, hits); return hits; }
  const props = node.props || {};
  if (props['data-worldos-testid'] === id || props.testId === id) hits.push(node);
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) findByTestId(c, id, hits);
  return hits;
}
function findByPrefix(node, prefix, hits) {
  hits = hits || [];
  if (node == null || typeof node !== 'object') return hits;
  if (Array.isArray(node)) { for (const c of node) findByPrefix(c, prefix, hits); return hits; }
  const props = node.props || {};
  const tid = props['data-worldos-testid'];
  if (typeof tid === 'string' && tid.indexOf(prefix) === 0) hits.push(tid);
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) findByPrefix(c, prefix, hits);
  return hits;
}
function collectText(node, out) {
  out = out || [];
  if (node == null || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') { out.push(String(node)); return out; }
  if (Array.isArray(node)) { for (const c of node) collectText(c, out); return out; }
  const props = node.props || {};
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) collectText(c, out);
  return out;
}
function firstProps(node, id) { const hits = findByTestId(node, id); return hits.length ? (hits[0].props || {}) : null; }
async function settle() { await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r)); reactHost.commit(); }

const h = {
  mountRest: (props) => { reactHost.mount(() => sandbox.window.RestPrepareModal(props)); },
  mountBrowser: (props) => { reactHost.mount(() => sandbox.window.SpellbookBrowser(props)); },
  mountSpellsTab: (props) => { reactHost.mount(() => sandbox.window.SpellsTab(props)); },
  tree: () => reactHost.api(),
  settle,
  props: (id) => firstProps(reactHost.api(), id),
  click: async (id) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onClick; if (typeof oc !== 'function') throw new Error('no onClick on ' + id); oc(); await settle(); },
  text: () => collectText(reactHost.api()).join(' ␟ '),
  posts: () => posts,
  exists: (id) => findByTestId(reactHost.api(), id).length,
  byPrefix: (p) => findByPrefix(reactHost.api(), p),
};

const script = %(script)s;
eval('(async () => { ' + script + ' })()')
  .then((result) => { process.stdout.write(JSON.stringify(result)); })
  .catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class _Harness(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_SCREEN_CHARACTER, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "screen_character": json.dumps(str(_SCREEN_CHARACTER)),
            "script": json.dumps(script),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)


# A L10 Paladin: prepares 3 spells, but the browsable preparable pool is the full L1–3 list.
_PALADIN = (
    "{ id: 'wyll', name: 'Wyll', class: 'paladin', level: 10, "
    "xp: 64000, xpMax: 85000, "
    "spells: [{ level: 'Prepared', list: ["
    "  { name: 'Bless', glyph: 'spell', levelLabel: 'Level 1' },"
    "  { name: 'Cure Wounds', glyph: 'spell', levelLabel: 'Level 1' } ] }], "
    "spellSlots: [{ level: 1, max: 4, used: 0 }, { level: 2, max: 3, used: 0 }, { level: 3, max: 2, used: 0 }], "
    "preparableSpells: ["
    "  { name: 'Bless', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Cure Wounds', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Divine Smite', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Command', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Aid', level: 2, levelLabel: 'Level 2', glyph: 'spell' },"
    "  { name: 'Lesser Restoration', level: 2, levelLabel: 'Level 2', glyph: 'spell' },"
    "  { name: 'Daylight', level: 3, levelLabel: 'Level 3', glyph: 'spell' } ] }"
)

# A non-caster Fighter: no preparableSpells, no caster surfaces.
_FIGHTER = (
    "{ id: 'lae', name: 'Laezel', class: 'fighter', level: 5, "
    "xp: 6500, xpMax: 14000, spells: [], spellSlots: [], preparableSpells: [] }"
)

# The browsable preparable pool (the full Paladin L1-3 list), shared by the browser tests.
_POOL = (
    "[ { name: 'Bless', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Cure Wounds', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Divine Smite', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Command', level: 1, levelLabel: 'Level 1', glyph: 'spell' },"
    "  { name: 'Aid', level: 2, levelLabel: 'Level 2', glyph: 'spell' },"
    "  { name: 'Lesser Restoration', level: 2, levelLabel: 'Level 2', glyph: 'spell' },"
    "  { name: 'Daylight', level: 3, levelLabel: 'Level 3', glyph: 'spell' } ]"
)


class RestPrepareBrowsablePoolTests(_Harness):
    def _mount(self):
        return (
            "h.mountRest({ hero: " + _PALADIN + ", party: [{ id:'wyll', name:'Wyll' }],"
            " campaignId: 'camp1', canAct: true, dmBusy: false,"
            " onClose: function(){}, onDone: function(){}, toast: function(){}, setState: function(){} });"
        )

    def test_prep_step_lists_the_full_preparable_pool_not_just_prepared(self):
        out = self._run(
            self._mount() +
            "await h.click('rest-make-camp');"          # advance to the prep step
            "var ids = h.byPrefix('prep-spell-');"
            "return ({ spell_ids: ids, has_smite: h.exists('prep-spell-divine-smite'),"
            "  level3: h.exists('prep-level-3'), text: h.text() });"
        )
        # the FULL class list is browsable (7 pool spells across L1-3), not just the 2 prepared.
        self.assertGreaterEqual(len(out["spell_ids"]), 7,
                                "the prep step must list the whole preparable pool, not just prepared spells")
        self.assertGreaterEqual(out["has_smite"], 1,
                                "Divine Smite (NOT currently prepared) must be selectable — the optimizer's blocker")
        self.assertGreaterEqual(out["level3"], 1, "L3 spells are browsable (a L10 Paladin can slot them)")

    def test_selecting_a_new_spell_rides_the_prepare_move(self):
        out = self._run(
            self._mount() +
            "await h.click('rest-make-camp');"
            "await h.click('prep-spell-divine-smite');"   # select a NEW spell to prepare
            "await h.click('rest-prepare-spells');"        # seal -> relay prepare_spells
            "return ({ posts: h.posts() });"
        )
        moves = [p for p in out["posts"] if p["url"] == "/move"]
        # Make-camp + Seal each relay one /move.
        self.assertEqual(len(moves), 2)
        prep = moves[-1]["body"]
        self.assertEqual(prep["kind"], "do", "the prepare relay is a structured `do` move-intent (move-sink)")
        text = prep["text"].lower()
        self.assertIn("prepare_spells", text, "the intent must name the engine's prepare_spells tool")
        self.assertIn("divine smite", text, "the newly-selected spell must ride the relayed preparation")

    def test_currently_prepared_spells_are_preselected(self):
        # Opening the prep step pre-seeds the picker with the caster's CURRENT preparation, so
        # "Seal" without changes keeps Bless + Cure Wounds (it edits, never silently wipes).
        out = self._run(
            self._mount() +
            "await h.click('rest-make-camp');"
            "await h.click('rest-prepare-spells');"        # seal WITHOUT changing anything
            "return ({ posts: h.posts() });"
        )
        moves = [p for p in out["posts"] if p["url"] == "/move"]
        text = moves[-1]["body"]["text"].lower()
        self.assertIn("bless", text)
        self.assertIn("cure wounds", text)


class SpellbookAvailableSectionTests(_Harness):
    def _mount(self, hero=_PALADIN):
        return (
            "h.mountBrowser({ hero: " + hero + ","
            " groups: [{ level: 'Prepared', list: ["
            "  { name: 'Bless', glyph: 'spell' }, { name: 'Cure Wounds', glyph: 'spell' } ] }],"
            " preparable: " + _POOL + ","
            " onClose: function(){} });"
        )

    def test_spellbook_shows_available_to_prepare_section_with_full_list(self):
        out = self._run(
            self._mount() +
            "return ({ section: h.exists('spellbook-available'),"
            "  smite: h.exists('spellbook-available-spell-divine-smite'),"
            "  l3: h.exists('spellbook-available-level-3'),"
            "  ids: h.byPrefix('spellbook-available-spell-'), text: h.text() });"
        )
        self.assertGreaterEqual(out["section"], 1, "the Spellbook must show an 'Available to Prepare' section")
        self.assertGreaterEqual(out["smite"], 1,
                                "Divine Smite (browsable, not prepared) must appear in the available list")
        self.assertGreaterEqual(len(out["ids"]), 7, "the WHOLE class list shows, not just the prepared few")
        self.assertIn("Available to Prepare", out["text"])

    def test_available_list_marks_prepared_vs_available(self):
        out = self._run(
            self._mount() +
            "return ({ text: h.text() });"
        )
        # Bless is prepared -> tagged Prepared; Divine Smite is not -> tagged Available.
        self.assertIn("Prepared", out["text"])
        self.assertIn("Available", out["text"])


class NonCasterTests(_Harness):
    def test_non_caster_spellbook_has_no_available_section(self):
        out = self._run(
            "h.mountBrowser({ hero: " + _FIGHTER + ", groups: [], preparable: [], onClose: function(){} });"
            "return ({ section: h.exists('spellbook-available') });"
        )
        self.assertEqual(out["section"], 0, "a non-caster shows no preparable pool (never fabricated)")


if __name__ == "__main__":
    unittest.main()
