"""Behavior tests for the #610/#617 Rest-&-Prepare relay + the #607 level-up subclass browse.

From the RRI-5e98e6f optimizer sweep — two MAJOR viewer-side wiring gaps where the ENGINE already
works but the viewer did not use it:

  (1) Rest & Prepare (RestPrepareModal, screen-character.jsx): the "Make camp" + "Seal the choices"
      CTAs were `disabled` display-only stubs ("…not saved to the engine"), so spell re-preparation
      and slot recovery were non-functional. The engine's long_rest / short_rest / prepare_spells
      already work; the fix relays a composed `do` move-intent to /move (the SAME write path
      camp-sidebar.jsx + LevelUpModal use — the viewer stays a read-only move-sink). The CTA is
      ENABLED + functional when a session is attached (can_act) AND the DM isn't mid-turn (dmBusy),
      and honestly DISABLED + explained otherwise.

  (2) Level-up dialog (LevelUpModal): the subclass picker must BROWSE all engine-exposed options
      (each with its level-3 feature breakdown — a real comparison), not just one; and the disabled
      "Confirm advancement" must say WHY (a subclass needs naming, or no XP is earned yet) instead of
      being a silently-dead button.

These exercise the REAL components by transpiling the actual `screen-character.jsx` with the SAME
bundled Babel-standalone the browser uses and rendering the modal under a createElement-capturing
React stub (state persists, effects RUN so the /build-options fetch lands, setState re-renders) with
a scripted fetch — so the test tracks the shipped JSX, not a reimplementation (mirrors
test_cold_open_progress.py + test_recovery_timing.py). A test can: render with props, drain the async
fetch, find a node by testid, INVOKE its onClick, re-render, and read the resulting /move POST.
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

// ---- a real-enough React: hook cells + effects persist; setState re-renders; effects RUN -------
// (ported from test_recovery_timing.py's stub, plus a createElement that builds a walkable tree so
// we can find a node by testid and invoke its onClick — the tree-walk from test_cold_open_progress.py.)
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
  const React = { useState, useRef, useCallback, useEffect, createElement, Fragment: 'F' };
  function mount(fn) { renderFn = fn; render(); flushEffects(); }
  function commit() { flushEffects(); }
  function api() { return result; }
  return { React, mount, commit, api };
}

// ---- a SCRIPTED fetch capturing POST bodies; /build-options returns the scripted planner --------
let PLANNER = null;            // the /build-options planner payload (set per test)
const posts = [];              // every /move POST {url, body}
function fetchStub(url, opts) {
  const u = String(url).split('?')[0];
  if (u === '/build-options') {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(PLANNER || { ok: false, errors: ['no planner'] }) });
  }
  if (u === '/move') {
    let body = {}; try { body = JSON.parse((opts && opts.body) || '{}'); } catch (_e) {}
    posts.push({ url: u, body });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
}

const reactHost = makeReact();

// Chrome components the modal renders (Panel, BrassButton, …) live on window in the browser; stub
// each as a createElement passthrough so the node carries its testid/title/onClick/disabled props
// (BrassButton must render to a real <button>-like node we can find + click).
function passthrough(name) { return function (props) { const p = props || {}; const kids = (p.children !== undefined) ? [].concat(p.children) : []; return reactHost.React.createElement(name, p, ...kids); }; }

const sandbox = {
  React: reactHost.React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: (fn) => { return 0; }, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  fetch: fetchStub,
  // the modals attach a keydown Escape handler via window.addEventListener in a useEffect.
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
// Provide the chrome/toast stubs as globals BEFORE the screen module loads (Babel emits bare refs).
for (const name of ['Panel','BrassButton','Divider','SectionTitle','Pill','Placeholder','Img','Glyph','CornerOrnament']) {
  sandbox[name] = passthrough(name);
}
const toastCalls = [];
sandbox.useToast = () => (t) => { toastCalls.push(t); };
sandbox.slug = (n) => (n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
sandbox.combatSurfaceFromCampaign = () => '';

load(%(screen_character)s);

// ---- tree helpers ----------------------------------------------------------------------------
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
  mountRest: (props) => { reactHost.mount(() => sandbox.window.RestPrepareModal(props)); },
  mountLevelUp: (props) => { reactHost.mount(() => sandbox.window.LevelUpModal(props)); },
  setPlanner: (p) => { PLANNER = p; },
  tree: () => reactHost.api(),
  settle,
  // find a node by testid and read a prop (disabled/title/etc.)
  props: (id) => firstProps(reactHost.api(), id),
  // INVOKE the onClick of the node with this testid, then re-render + drain async.
  click: async (id) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onClick; if (typeof oc !== 'function') throw new Error('no onClick on ' + id); oc(); await settle(); },
  text: () => collectText(reactHost.api()).join(' ␟ '),
  posts: () => posts,
  toasts: () => toastCalls,
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


# A minimal hero + a few props the modals need.
_HERO = (
    "{ id: 'char_1', name: 'Dal Lightspark', class: 'wizard', level: 5, "
    "xp: 6500, xpMax: 14000, spells: [], spellSlots: {} }"
)


class RestPrepareWiringTests(_Harness):
    """#610/#617 — the Make-camp / Seal-the-choices CTAs relay a real `do` /move (not a dead stub)."""

    def _mount(self, can_act="true", dm_busy="false"):
        return (
            "h.mountRest({ hero: " + _HERO + ", party: [{ id:'char_1', name:'Dal Lightspark' }],"
            " campaignId: 'camp1', canAct: " + can_act + ", dmBusy: " + dm_busy + ","
            " onClose: function(){}, onDone: function(){}, toast: h.toasts ? (function(t){}) : (function(){}),"
            " setState: function(){} });"
        )

    def test_make_camp_is_enabled_and_dispatches_a_do_move_when_party_can_rest(self):
        out = self._run(
            self._mount("true", "false") +
            "var before = h.props('rest-make-camp');"
            "await h.click('rest-make-camp');"
            "var posts = h.posts();"
            "return ({ disabled_before: !!before.disabled, has_test_id: !!before,"
            "  posts: posts.length, post: posts[0] || null });"
        )
        self.assertFalse(out["disabled_before"], "Make camp must be ENABLED when the party can rest (can_act, not dmBusy)")
        self.assertEqual(out["posts"], 1, "clicking Make camp must POST exactly one /move (the camp relay)")
        self.assertEqual(out["post"]["url"], "/move")
        self.assertEqual(out["post"]["body"]["kind"], "do", "the camp relay is a structured `do` move-intent")
        self.assertEqual(out["post"]["body"]["campaign"], "camp1")
        # the composed intent names a long rest (restoring slots) — the engine resolves it.
        self.assertIn("long rest", out["post"]["body"]["text"].lower())
        self.assertIn("spell slot", out["post"]["body"]["text"].lower())

    def test_make_camp_disabled_with_reason_when_read_only(self):
        out = self._run(
            self._mount("false", "false") +
            "var p = h.props('rest-make-camp');"
            "var hasReason = (h.text().indexOf('read-only') !== -1);"
            "return ({ disabled: !!p.disabled, title: p.title || '', reason_shown: hasReason });"
        )
        self.assertTrue(out["disabled"], "Make camp must be disabled when the chronicle is read-only")
        self.assertIn("read-only", out["title"].lower(), "the disabled button must explain WHY via title")
        self.assertTrue(out["reason_shown"], "the read-only reason must also be shown inline (not only as a hover title)")

    def test_make_camp_disabled_with_reason_when_dm_is_narrating(self):
        out = self._run(
            self._mount("true", "true") +
            "var p = h.props('rest-make-camp');"
            "return ({ disabled: !!p.disabled, title: p.title || '' });"
        )
        self.assertTrue(out["disabled"], "Make camp must be disabled while the DM is mid-turn")
        self.assertIn("narrating", out["title"].lower(), "the dm-busy disabled reason must be in the tooltip")

    def test_read_only_modal_carries_camp_reason_and_posts_nothing(self):
        # Defense-in-depth: the read-only modal renders the inline reason AND never posts a /move
        # (the engine is sole writer; a read-only chronicle must not relay). The button being
        # disabled blocks the click; this pins that opening the modal alone writes nothing.
        out = self._run(
            self._mount("false", "false") +
            "return ({ posts_at_mount: h.posts().length, has_reason: (h.text().indexOf('read-only') !== -1) });"
        )
        self.assertEqual(out["posts_at_mount"], 0, "no /move should be posted just by opening the modal")
        self.assertTrue(out["has_reason"], "the read-only modal must show the inline why (a fix-introduced affordance)")

    def test_prepare_spells_button_is_wired_after_resting(self):
        # After a successful Make-camp relay the modal advances to the prep step; the Seal CTA there
        # must also relay a real /move (prepare_spells), not be a dead stub.
        out = self._run(
            self._mount("true", "false") +
            "await h.click('rest-make-camp');"          # relays the rest, advances to 'prep'
            "var sealExists = h.exists('rest-prepare-spells');"
            "var sealProps = h.props('rest-prepare-spells');"
            "await h.click('rest-prepare-spells');"      # relays the preparation
            "var posts = h.posts();"
            "return ({ seal_exists: sealExists, seal_disabled: !!(sealProps && sealProps.disabled),"
            "  total_posts: posts.length, last: posts[posts.length - 1] || null });"
        )
        self.assertTrue(out["seal_exists"], "after resting, the prep step's Seal CTA must render")
        self.assertFalse(out["seal_disabled"], "the Seal CTA must be enabled (live session, not busy)")
        self.assertEqual(out["total_posts"], 2, "Make-camp + Seal must each relay one /move")
        self.assertEqual(out["last"]["body"]["kind"], "do")
        self.assertIn("prepare", out["last"]["body"]["text"].lower())

    def test_no_display_only_stub_copy_remains(self):
        # The old "not saved to the engine" display-only language must be GONE from the modal.
        out = self._run(
            self._mount("true", "false") +
            "return ({ text: h.text() });"
        )
        self.assertNotIn("not saved to the engine", out["text"])
        self.assertNotIn("(preview)", out["text"], "the Make-camp CTA is real now, not a (preview) stub")


# A planner with the wizard subclass block carrying MULTIPLE options + per-option features, so the
# 'browse all subclasses' assertion is meaningful regardless of the (single-entry) SRD data file.
_PLANNER_WITH_SUBCLASSES = json.dumps({
    "ok": True,
    "planner": {
        "options": [{
            "class_name": "wizard",
            "from": {"level": 2}, "to": {"level": 3, "class_level": 3},
            "hp_gain": 6,
            "features_gained": [{"name": "Arcane Tradition Subclass"}],
            "choices": {"asi_required": False, "feat_allowed": False, "multiclass_allowed": True},
            "subclass": {
                "required": True,
                "group_label": "Arcane Tradition",
                "current": None,
                "options": [
                    {"name": "Evoker", "desc": "Sculpt spells.",
                     "features": [{"name": "Evocation Savant", "desc": "Free evocation spells."},
                                  {"name": "Sculpt Spells", "desc": "Carve safe pockets."}]},
                    {"name": "Abjurer", "desc": "Ward against harm.",
                     "features": [{"name": "Arcane Ward", "desc": "A shield of force."}]},
                    {"name": "Diviner", "desc": "Glimpse the future.",
                     "features": [{"name": "Portent", "desc": "Replace a roll."}]},
                ],
            },
        }],
    },
})


class LevelUpSubclassBrowseTests(_Harness):
    """#607 — the level-up dialog browses ALL engine-exposed subclasses (with feature previews), and
    the disabled Confirm says WHY."""

    def _mount(self, hero=_HERO, planner=_PLANNER_WITH_SUBCLASSES):
        return (
            "h.setPlanner(" + planner + ");"
            "h.mountLevelUp({ hero: " + hero + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
        )

    def test_all_subclass_options_are_listed_not_just_the_first(self):
        out = self._run(
            self._mount() +
            "return ({"
            "  evoker: h.exists('levelup-subclass-option-evoker'),"
            "  abjurer: h.exists('levelup-subclass-option-abjurer'),"
            "  diviner: h.exists('levelup-subclass-option-diviner'),"
            "  text: h.text() });"
        )
        self.assertGreaterEqual(out["evoker"], 1, "Evoker option must render")
        self.assertGreaterEqual(out["abjurer"], 1, "Abjurer option must render — not just the first subclass")
        self.assertGreaterEqual(out["diviner"], 1, "Diviner option must render — the dialog browses ALL options")
        # the names are visible for a browsable comparison
        for name in ("Evoker", "Abjurer", "Diviner"):
            self.assertIn(name, out["text"])

    def test_each_option_shows_its_feature_breakdown_for_comparison(self):
        out = self._run(
            self._mount() +
            "return ({"
            "  evoker_features: h.exists('levelup-subclass-features-evoker'),"
            "  abjurer_features: h.exists('levelup-subclass-features-abjurer'),"
            "  text: h.text() });"
        )
        self.assertGreaterEqual(out["evoker_features"], 1, "Evoker's feature list makes it a real comparison")
        self.assertGreaterEqual(out["abjurer_features"], 1, "Abjurer's feature list renders too")
        # the actual feature NAMES are present (so a player can compare what each grants)
        self.assertIn("Sculpt Spells", out["text"])
        self.assertIn("Arcane Ward", out["text"])
        self.assertIn("Portent", out["text"])

    def test_selecting_an_option_fills_the_named_subclass(self):
        out = self._run(
            self._mount() +
            "var before = h.props('levelup-confirm');"
            "await h.click('levelup-subclass-option-abjurer');"
            "var after = h.props('levelup-confirm');"
            "return ({ disabled_before: !!before.disabled, disabled_after: !!after.disabled });"
        )
        self.assertTrue(out["disabled_before"], "Confirm is disabled until a due subclass is named")
        self.assertFalse(out["disabled_after"], "picking a subclass option must enable Confirm")

    def test_disabled_confirm_explains_unnamed_subclass(self):
        out = self._run(
            self._mount() +
            "var p = h.props('levelup-confirm');"
            "var reasonShown = h.exists('levelup-confirm-reason');"
            "return ({ disabled: !!p.disabled, title: p.title || '', reason_shown: reasonShown, text: h.text() });"
        )
        self.assertTrue(out["disabled"], "with a subclass due + unnamed, Confirm is disabled")
        self.assertIn("Arcane Tradition", out["title"], "the tooltip must name what's required (the subclass group)")
        self.assertGreaterEqual(out["reason_shown"], 1, "the why must also be shown inline (touch/screen-reader)")


# A planner with NO legal level option (no XP earned) but a pending subclass on the hero — opening
# the dialog to name the missed subclass. Confirm must block with a 'no XP' tooltip.
_PLANNER_NO_XP = json.dumps({"ok": True, "planner": {"options": []}})
_HERO_PENDING_NO_XP = (
    "{ id: 'char_1', name: 'Dal Lightspark', class: 'wizard', level: 5, "
    "xp: 100, xpMax: 14000, pendingSubclass: true, spells: [], spellSlots: {} }"
)


class LevelUpNoXpTooltipTests(_Harness):
    """#607 — when the dialog is open on a pending subclass but no XP is earned for a new level, the
    disabled Confirm explains 'no XP earned' rather than being a silently-dead button."""

    def test_no_xp_confirm_is_disabled_with_an_explanatory_tooltip(self):
        out = self._run(
            "h.setPlanner(" + _PLANNER_NO_XP + ");"
            "h.mountLevelUp({ hero: " + _HERO_PENDING_NO_XP + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
            "var p = h.props('levelup-confirm');"
            "var reasonShown = h.exists('levelup-confirm-reason');"
            "return ({ disabled: !!p.disabled, title: (p.title || '').toLowerCase(),"
            "  reason_shown: reasonShown, text: h.text().toLowerCase() });"
        )
        self.assertTrue(out["disabled"], "with no XP-legal level, Confirm must be disabled")
        self.assertIn("xp", out["title"], "the tooltip must explain it's blocked on XP (no level to advance into)")
        self.assertGreaterEqual(out["reason_shown"], 1, "the no-XP reason must be shown inline too")
        self.assertIn("no xp", out["text"])


# RRI-25e55fa optimizer #1 (the L11-no-archetype case): a Fighter PAST its subclass-choice level
# (3) with NO archetype set, leveling into L11 — a level that grants NO fresh "subclass" feature in
# features_gained, and the hero carries no `pendingSubclass` flag. The ENGINE still flags the missed
# choice as OVERDUE on the build option (`option.subclass.required: true`). The picker must surface
# the subclass prompt from THAT engine flag, not only from a pendingSubclass / a /subclass/ feature
# name — otherwise an L11 sheet reads "Choose your subclass" nowhere and the archetype goes unenforced.
_PLANNER_OVERDUE_SUBCLASS = json.dumps({
    "ok": True,
    "planner": {
        "options": [{
            "class_name": "fighter",
            "from": {"level": 10}, "to": {"level": 11, "class_level": 11},
            "hp_gain": 7,
            # L11 fighter gains Extra Attack (2) — NOT a subclass feature; the name has no /subclass/.
            "features_gained": [{"name": "Extra Attack (2)"}],
            "choices": {"asi_required": False, "feat_allowed": False, "multiclass_allowed": True},
            "subclass": {
                "required": True,            # the engine flags the OVERDUE archetype choice
                "group_label": "Martial Archetype",
                "current": None,
                "options": [
                    {"name": "Champion", "desc": "Improved Critical.",
                     "features": [{"name": "Improved Critical", "desc": "Crit on 19-20."}]},
                    {"name": "Battle Master", "desc": "Combat maneuvers.",
                     "features": [{"name": "Combat Superiority", "desc": "Maneuvers + dice."}]},
                ],
            },
        }],
    },
})
# The hero has NO pendingSubclass flag — the prompt must come purely from the engine's option.subclass.required.
_HERO_L10_NO_ARCHETYPE = (
    "{ id: 'char_1', name: 'Karlach', class: 'fighter', level: 10, archetype: '', "
    "xp: 100000, xpMax: 120000, spells: [], spellSlots: {} }"
)


# RRI-25e55fa optimizer #4 — a hero with Hit Dice available to spend on a short rest. The sheet
# shows "11/11d10" but had no control to actually spend them for HP; the engine
# short_rest(hit_dice_to_spend=N) supports it.
_HERO_WITH_HIT_DICE = (
    "{ id: 'char_1', name: 'Karlach', class: 'fighter', level: 11, "
    "xp: 100, xpMax: 999999, spells: [], spellSlots: {}, "
    "stats: { hitDice: '11d10', hitDiceRemaining: 11 } }"
)


class ShortRestHitDiceTests(_Harness):
    """RRI-25e55fa optimizer #4 — the Short Rest flow gains a Hit-Dice-SPEND control; the chosen
    count rides the relayed `do` move so the engine's short_rest(hit_dice_to_spend=N) can apply it.
    The viewer stays a move-sink: it composes the intent, the engine resolves the HP."""

    def _mount(self):
        return (
            "h.mountRest({ hero: " + _HERO_WITH_HIT_DICE + ", party: [{ id:'char_1', name:'Karlach' }],"
            " campaignId: 'camp1', canAct: true, dmBusy: false,"
            " onClose: function(){}, onDone: function(){}, toast: function(){}, setState: function(){} });"
        )

    def test_hit_dice_control_appears_on_short_rest(self):
        out = self._run(
            self._mount() +
            "await h.click('rest-card-short');"
            "return ({ control: h.exists('short-rest-hit-dice'), text: h.text() });"
        )
        self.assertGreaterEqual(out["control"], 1, "the short rest must expose a Hit-Dice-spend control")
        # the available pool (11/11d10) is shown so the player knows how many they can spend
        self.assertIn("11", out["text"])

    def test_hit_dice_count_rides_the_short_rest_move(self):
        out = self._run(
            self._mount() +
            "await h.click('rest-card-short');"
            "await h.click('short-rest-hd-inc');"
            "await h.click('short-rest-hd-inc');"
            "await h.click('rest-make-camp');"
            "return ({ posts: h.posts() });"
        )
        moves = [p for p in out["posts"] if p["url"] == "/move"]
        self.assertTrue(moves, "Make camp must relay a /move")
        text = moves[0]["body"]["text"].lower()
        self.assertIn("short rest", text)
        # the chosen hit-dice count must be in the relayed intent so the engine can spend exactly N.
        self.assertIn("2 hit dice", text)

    def test_hit_dice_control_absent_when_none_remaining(self):
        """A hero with no hit dice remaining (0/11d10) shows no spend control — never a dead stepper."""
        hero0 = _HERO_WITH_HIT_DICE.replace("hitDiceRemaining: 11", "hitDiceRemaining: 0")
        out = self._run(
            "h.mountRest({ hero: " + hero0 + ", party: [{ id:'char_1', name:'Karlach' }],"
            " campaignId: 'camp1', canAct: true, dmBusy: false,"
            " onClose: function(){}, onDone: function(){}, toast: function(){}, setState: function(){} });"
            "await h.click('rest-card-short');"
            "return ({ control: h.exists('short-rest-hit-dice') });"
        )
        self.assertEqual(out["control"], 0, "no hit dice remaining -> no spend control")

    def test_long_rest_has_no_hit_dice_control(self):
        """Hit-dice spend is a SHORT-rest mechanic; switching to a long rest hides the control."""
        out = self._run(
            self._mount() +
            "await h.click('rest-card-long');"
            "return ({ control: h.exists('short-rest-hit-dice') });"
        )
        self.assertEqual(out["control"], 0, "the long-rest path shows no hit-dice spend control")


class LevelUpOverdueSubclassTests(_Harness):
    """RRI-25e55fa optimizer #1 — the engine's overdue-archetype flag (option.subclass.required)
    drives the subclass prompt even when the level grants no fresh subclass feature + no
    pendingSubclass flag (the L11-no-archetype case)."""

    def _mount(self, hero=_HERO_L10_NO_ARCHETYPE, planner=_PLANNER_OVERDUE_SUBCLASS):
        return (
            "h.setPlanner(" + planner + ");"
            "h.mountLevelUp({ hero: " + hero + ", campaignId: 'camp1',"
            " onClose: function(){}, onDone: function(){}, toast: function(){} });"
            "await h.settle();"
        )

    def test_overdue_subclass_prompt_shows_from_engine_required_flag(self):
        out = self._run(
            self._mount() +
            "return ({"
            "  section: h.exists('levelup-subclass-section'),"
            "  champion: h.exists('levelup-subclass-option-champion'),"
            "  battlemaster: h.exists('levelup-subclass-option-battle-master'),"
            "  text: h.text() });"
        )
        self.assertGreaterEqual(out["section"], 1,
                                "the subclass section must render for an OVERDUE archetype (engine required flag)")
        self.assertGreaterEqual(out["champion"], 1, "Champion option must render")
        self.assertGreaterEqual(out["battlemaster"], 1,
                                "Battle Master must render — ALL engine-returned options show, not just Champion")
        self.assertIn("Martial Archetype", out["text"])

    def test_overdue_confirm_blocks_until_archetype_named(self):
        """With the archetype overdue + unnamed, Confirm is disabled with the group-label reason —
        so an L11 level-up can't silently skip the missed subclass (archetype stays enforced)."""
        out = self._run(
            self._mount() +
            "var before = h.props('levelup-confirm');"
            "await h.click('levelup-subclass-option-battle-master');"
            "var after = h.props('levelup-confirm');"
            "return ({ disabled_before: !!before.disabled, title: (before.title || ''),"
            "  disabled_after: !!after.disabled });"
        )
        self.assertTrue(out["disabled_before"], "an overdue, unnamed archetype must block Confirm")
        self.assertIn("Martial Archetype", out["title"], "the reason must name the overdue archetype group")
        self.assertFalse(out["disabled_after"], "naming the archetype enables Confirm")


if __name__ == "__main__":
    unittest.main()
