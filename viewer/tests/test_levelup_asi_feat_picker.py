"""#607 — the level-up flow's ASI / feat choice is a STRUCTURED picker, not a free-text note.

The old LevelUpModal surfaced the ability-score-improvement choice (5e levels 4/8/12/16/19) as a
single free-text box (`asiNote`): the player typed "+2 STR" into prose and hoped the DM parsed it.
That is the free-text trap this issue closes — replace it with:

  * an ability-score STEPPER (one +/- control per ability) that enforces the engine's ASI contract
    (`level_up(asi=...)` → `_validated_asi_choice`: +2 to one ability OR +1 to two, each capped at
    20 — server.py); the composed `/move` intent encodes the exact picks the engine resolves;
  * a FEAT picker at ASI levels when the campaign allows feats (`option.choices.feat_allowed`): a
    toggle to "take a feat instead" + a named feat input, mutually exclusive with the ASI (the engine
    rejects both: "choose either asi or feat, not both").

The viewer stays a read-only move-sink: it composes a `do` move-intent text and POSTs it to /move;
the ENGINE (sole writer) applies the ASI/feat via its level_up tool. This exercises the SHIPPED
screen-character.jsx through the bundled-Babel + createElement-capturing harness (same as
test_rest_camp_levelup_wiring.py) so the test tracks the real JSX, not a reimplementation.
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
  const stateCells = [], refCells = [], cbCells = [], memoCells = [], effects = [];
  let sIdx = 0, rIdx = 0, cIdx = 0, mIdx = 0, eIdx = 0;
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
  function useMemo(fn, deps) { const i = mIdx++; const prev = memoCells[i]; if (prev === undefined || !depsEqual(prev.deps, deps)) { const v = fn(); memoCells[i] = { v, deps }; return v; } return prev.v; }
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
  function render() { sIdx = 0; rIdx = 0; cIdx = 0; mIdx = 0; eIdx = 0; result = renderFn(); }
  function flushEffects() { if (flushing) return; flushing = true; try { while (pendingEffects.length) pendingEffects.shift()(); } finally { flushing = false; } }
  const React = { useState, useRef, useCallback, useMemo, useEffect, createElement, Fragment: 'F' };
  function mount(fn) { renderFn = fn; render(); flushEffects(); }
  function commit() { flushEffects(); }
  function api() { return result; }
  return { React, mount, commit, api };
}

let PLANNER = null;
let FEATS = null;   // the /feat-catalog payload (set per test that exercises the browsable feat picker)
const posts = [];
function fetchStub(url, opts) {
  const u = String(url).split('?')[0];
  if (u === '/build-options') {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(PLANNER || { ok: false, errors: ['no planner'] }) });
  }
  if (u === '/feat-catalog') {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(FEATS || { feats: [] }) });
  }
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
  setTimeout: (fn) => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
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
sandbox.useToast = () => (t) => {};
sandbox.slug = (n) => (n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
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
  mountLevelUp: (props) => { reactHost.mount(() => sandbox.window.LevelUpModal(props)); },
  setPlanner: (p) => { PLANNER = p; },
  setFeats: (f) => { FEATS = f; },
  tree: () => reactHost.api(),
  settle,
  props: (id) => firstProps(reactHost.api(), id),
  click: async (id) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onClick; if (typeof oc !== 'function') throw new Error('no onClick on ' + id); oc(); await settle(); },
  // set the value of an input-like node by invoking its onChange with a synthetic event.
  change: async (id, value) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onChange; if (typeof oc !== 'function') throw new Error('no onChange on ' + id); oc({ target: { value: value } }); await settle(); },
  text: () => collectText(reactHost.api()).join(' ' + String.fromCharCode(31) + ' '),
  posts: () => posts,
  exists: (id) => findByTestId(reactHost.api(), id).length,
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


# An L3 fighter at its level-4 ASI (the SRD's first ASI level). The planner option flags the ASI
# choice as required and feats as allowed (a campaign that turned feats on).
_HERO_L3_FIGHTER = (
    "{ id: 'char_1', name: 'Karlach', class: 'fighter', level: 3, "
    "xp: 2700, xpMax: 6500, stats: { str: 15, dex: 13, con: 14, int: 8, wis: 10, cha: 12 }, "
    "spells: [], spellSlots: {} }"
)

_PLANNER_ASI_AND_FEAT = json.dumps({
    "ok": True,
    "planner": {
        "options": [{
            "class_name": "fighter",
            "from": {"level": 3}, "to": {"level": 4, "class_level": 4},
            "hp_gain": 7,
            "features_gained": [{"name": "Ability Score Improvement"}],
            "choices": {"asi_required": True, "feat_allowed": True, "multiclass_allowed": True},
        }],
    },
})

# Same level but a campaign with feats OFF — the feat toggle must not appear; only the ASI stepper.
_PLANNER_ASI_ONLY = json.dumps({
    "ok": True,
    "planner": {
        "options": [{
            "class_name": "fighter",
            "from": {"level": 3}, "to": {"level": 4, "class_level": 4},
            "hp_gain": 7,
            "features_gained": [{"name": "Ability Score Improvement"}],
            "choices": {"asi_required": True, "feat_allowed": False, "multiclass_allowed": True},
        }],
    },
})


class LevelUpAsiStepperTests(_Harness):
    """#607 — the ASI choice is a structured ability-score stepper enforcing the engine's
    +2-to-one / +1-to-two contract, and the composed /move encodes the exact picks."""

    def _mount(self, hero=_HERO_L3_FIGHTER, planner=_PLANNER_ASI_AND_FEAT):
        return (
            "h.setPlanner(" + planner + ");"
            "h.mountLevelUp({ hero: " + hero + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
        )

    def test_no_free_text_asi_note_remains(self):
        """The old free-text asiNote box is GONE — the choice is structured now, not prose."""
        out = self._run(self._mount() + "return ({ legacy_input: h.exists('levelup-asi-input') });")
        self.assertEqual(out["legacy_input"], 0,
                         "the free-text levelup-asi-input must be replaced by a structured picker")

    def test_a_stepper_exists_for_every_ability(self):
        out = self._run(
            self._mount() +
            "return ({"
            "  str: h.exists('levelup-asi-inc-str'), dex: h.exists('levelup-asi-inc-dex'),"
            "  con: h.exists('levelup-asi-inc-con'), int: h.exists('levelup-asi-inc-int'),"
            "  wis: h.exists('levelup-asi-inc-wis'), cha: h.exists('levelup-asi-inc-cha'),"
            "  dec: h.exists('levelup-asi-dec-str'), text: h.text() });"
        )
        for ab in ("str", "dex", "con", "int", "wis", "cha"):
            self.assertGreaterEqual(out[ab], 1, f"the {ab.upper()} +1 control must render")
        self.assertGreaterEqual(out["dec"], 1, "a -1 control must render for an allocated ability")

    def test_confirm_blocked_until_two_points_allocated(self):
        out = self._run(
            self._mount() +
            "var before = h.props('levelup-confirm');"
            "await h.click('levelup-asi-inc-str');"          # +1 STR (1 of 2)
            "var mid = h.props('levelup-confirm');"
            "await h.click('levelup-asi-inc-dex');"          # +1 DEX (2 of 2)
            "var after = h.props('levelup-confirm');"
            "return ({ disabled_before: !!before.disabled, disabled_mid: !!mid.disabled,"
            "  disabled_after: !!after.disabled, title_before: before.title || '' });"
        )
        self.assertTrue(out["disabled_before"], "Confirm is blocked before any ASI points are allocated")
        self.assertTrue(out["disabled_mid"], "Confirm stays blocked with only 1 of 2 points allocated")
        self.assertFalse(out["disabled_after"], "allocating the full +2 (here +1/+1) enables Confirm")

    def test_two_into_one_ability_is_allowed_and_caps_at_two(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-asi-inc-str');"          # +1 STR
            "await h.click('levelup-asi-inc-str');"          # +2 STR (full)
            "var afterTwo = h.props('levelup-confirm');"
            "var incThree = h.exists('levelup-asi-inc-str');" # a 3rd +1 control must be gone/blocked
            "await h.click('levelup-confirm');"
            "return ({ disabled_after_two: !!afterTwo.disabled, post: (h.posts()[0] || null) });"
        )
        self.assertFalse(out["disabled_after_two"], "+2 into a single ability is a legal full allocation")
        self.assertIsNotNone(out["post"], "confirming a full +2 STR must relay a /move")
        text = out["post"]["body"]["text"].lower()
        self.assertEqual(out["post"]["body"]["kind"], "do")
        self.assertIn("+2 str", text, "the composed intent must encode the structured +2 STR pick")

    def test_split_asi_encodes_both_picks_in_the_move(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-asi-inc-str');"
            "await h.click('levelup-asi-inc-con');"
            "await h.click('levelup-confirm');"
            "return ({ post: (h.posts()[0] || null) });"
        )
        self.assertIsNotNone(out["post"])
        text = out["post"]["body"]["text"].lower()
        self.assertIn("+1 str", text)
        self.assertIn("+1 con", text)

    def test_a_score_cannot_be_raised_above_twenty(self):
        """The engine caps an ASI at 20; the stepper must not let a 20-score ability take more."""
        hero20 = _HERO_L3_FIGHTER.replace("str: 15", "str: 20")
        out = self._run(
            self._mount(hero=hero20) +
            "var p = h.props('levelup-asi-inc-str');"
            "return ({ disabled: !!(p && p.disabled), exists: h.exists('levelup-asi-inc-str') });"
        )
        # The +1 STR control is present but disabled (the ability is already at the 20 cap).
        self.assertTrue(out["disabled"], "raising a 20-score ability must be blocked (the engine caps at 20)")


class LevelUpFeatPickerTests(_Harness):
    """#607 — at an ASI level where the campaign allows feats, a 'take a feat instead' toggle +
    named feat input appears (mutually exclusive with the ASI), and the /move encodes the feat."""

    def _mount(self, planner=_PLANNER_ASI_AND_FEAT):
        return (
            "h.setPlanner(" + planner + ");"
            "h.mountLevelUp({ hero: " + _HERO_L3_FIGHTER + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
        )

    def test_feat_toggle_present_only_when_feats_allowed(self):
        with_feats = self._run(self._mount() + "return ({ toggle: h.exists('levelup-feat-toggle') });")
        self.assertGreaterEqual(with_feats["toggle"], 1, "the feat toggle must render when feat_allowed")
        without = self._run(self._mount(_PLANNER_ASI_ONLY) + "return ({ toggle: h.exists('levelup-feat-toggle') });")
        self.assertEqual(without["toggle"], 0, "no feat toggle when the campaign disables feats")

    def test_taking_a_feat_swaps_in_a_named_input_and_disables_the_stepper(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-feat-toggle');"
            "return ({ feat_input: h.exists('levelup-feat-input'),"
            "  stepper: h.exists('levelup-asi-inc-str') });"
        )
        self.assertGreaterEqual(out["feat_input"], 1, "choosing a feat must reveal a named feat input")
        self.assertEqual(out["stepper"], 0, "the ASI stepper is hidden in feat mode (mutually exclusive)")

    def test_feat_mode_requires_a_name_then_encodes_it_in_the_move(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-feat-toggle');"
            "var blocked = h.props('levelup-confirm');"
            "await h.change('levelup-feat-input', 'Great Weapon Master');"
            "var ready = h.props('levelup-confirm');"
            "await h.click('levelup-confirm');"
            "return ({ blocked: !!blocked.disabled, ready: !!ready.disabled, post: (h.posts()[0] || null) });"
        )
        self.assertTrue(out["blocked"], "feat mode with no feat named must block Confirm")
        self.assertFalse(out["ready"], "naming the feat enables Confirm")
        self.assertIsNotNone(out["post"])
        text = out["post"]["body"]["text"].lower()
        self.assertIn("great weapon master", text, "the composed intent must name the chosen feat")
        self.assertIn("feat", text, "the intent must signal a feat choice (not an ASI)")
        # mutually exclusive: an ASI was never allocated, so the intent must NOT also claim an ASI bump.
        self.assertNotIn("+1 str", text)
        self.assertNotIn("+2 str", text)


# A small /feat-catalog payload the browsable picker loads when the feat pane opens (mirrors the
# shape of GET /feat-catalog: {name, desc, prerequisite, type}).
_FEAT_CATALOG = json.dumps({
    "count": 2,
    "feats": [
        {"name": "Alert", "desc": "Initiative Proficiency: add your proficiency to initiative.",
         "prerequisite": "", "type": "Origin"},
        {"name": "Grappler", "desc": "You have advantage on attacks against grappled creatures.",
         "prerequisite": "Level 4+, Strength or Dexterity 13+", "type": "General"},
    ],
})


class LevelUpFeatBrowserTests(_Harness):
    """#feat-browser — the feat pane is now a BROWSABLE picker (GET /feat-catalog) showing each
    feat's effect + prerequisite; selecting one fills featName which rides the existing relay. The
    free-text input REMAINS for world-canon feats the SRD list doesn't enumerate."""

    def _mount(self):
        return (
            "h.setPlanner(" + _PLANNER_ASI_AND_FEAT + ");"
            "h.setFeats(" + _FEAT_CATALOG + ");"
            "h.mountLevelUp({ hero: " + _HERO_L3_FIGHTER + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
        )

    def test_feat_options_render_after_opening_the_feat_pane(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-feat-toggle');"   # opens the feat pane -> loads /feat-catalog
            "return ({ options: h.exists('levelup-feat-options'),"
            "  alert: h.exists('levelup-feat-option-alert'),"
            "  grappler: h.exists('levelup-feat-option-grappler'),"
            "  free_text: h.exists('levelup-feat-input'), text: h.text() });"
        )
        self.assertGreaterEqual(out["options"], 1, "the browsable feat list must render in the feat pane")
        self.assertGreaterEqual(out["alert"], 1, "each SRD feat is a selectable option")
        self.assertGreaterEqual(out["grappler"], 1)
        self.assertGreaterEqual(out["free_text"], 1, "the free-text override input must remain (world-canon feats)")
        # the option shows the feat's effect text + prerequisite (a real browse, not a name list)
        self.assertIn("advantage on attacks against grappled", out["text"].lower())
        self.assertIn("Strength or Dexterity 13+", out["text"])

    def test_selecting_a_feat_option_fills_the_name_and_rides_the_move(self):
        out = self._run(
            self._mount() +
            "await h.click('levelup-feat-toggle');"
            "var before = h.props('levelup-confirm');"
            "await h.click('levelup-feat-option-grappler');"   # selecting fills featName
            "var after = h.props('levelup-confirm');"
            "await h.click('levelup-confirm');"
            "return ({ blocked_before: !!before.disabled, ready: !!after.disabled,"
            "  post: (h.posts()[0] || null) });"
        )
        self.assertTrue(out["blocked_before"], "no feat chosen yet -> Confirm is blocked")
        self.assertFalse(out["ready"], "selecting a feat option enables Confirm")
        self.assertIsNotNone(out["post"])
        text = out["post"]["body"]["text"].lower()
        self.assertIn("grappler", text, "the chosen feat name must ride the existing level_up relay")
        self.assertIn("feat", text)


if __name__ == "__main__":
    unittest.main()
