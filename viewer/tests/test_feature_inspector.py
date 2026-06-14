"""RRI-25e55fa optimizer #1 (the "#1 min-maxer pain point") — the CLASS-FEATURE INSPECTOR.

The optimizer persona's top finding: every class/subclass feature on the character sheet (Extra
Attack, Action Surge, Indomitable, …) was static text with NO click-through to the full SRD rules
text. The engine read-model ALREADY projects the rules text as `classFeatures[].detail`
(server.py `_feature_desc` + data/srd/class_features.json) — the viewer was just rendering a
truncated muted line.

This fix makes each feature CLICK-THROUGH to a read-only PANEL showing its full rules text, mirroring
the #872 item-Examine read-only PANEL pattern. It stays a pure READER — no /move, no engine write.

These tests drive the REAL screen-character.jsx: a render-capturing harness (effects run, setState
re-renders, onClick is invokable — mirrors test_rest_camp_levelup_wiring.py) renders the AbilitiesTab
with a hero carrying class features, finds a feature's clickable node by testid, INVOKES its onClick,
and asserts the read-only panel surfaces the full detail. A served-source guard pins the
read-only-ness (a dialog, no /move, a Close control) so the inspector can't silently regress.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_CHARACTER = _OPENWORLDS / "screen-character.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# The SAME render-capturing harness shape test_rest_camp_levelup_wiring.py uses (hook cells +
# effects persist; setState re-renders; createElement builds a walkable tree; find-by-testid +
# invoke-onClick). Trimmed to what the feature inspector needs (no /build-options planner).
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
  function createElement(type, props) { const children = Array.prototype.slice.call(arguments, 2); return { type, props: props || {}, children }; }
  function render() { sIdx = 0; rIdx = 0; cIdx = 0; eIdx = 0; result = renderFn(); }
  function flushEffects() { if (flushing) return; flushing = true; try { while (pendingEffects.length) pendingEffects.shift()(); } finally { flushing = false; } }
  const React = { useState, useRef, useCallback, useMemo, useEffect, createElement, Fragment: 'F' };
  function mount(fn) { renderFn = fn; render(); flushEffects(); }
  function commit() { flushEffects(); }
  function api() { return result; }
  return { React, mount, commit, api };
}

const reactHost = makeReact();
function passthrough(name) { return function (props) { const p = props || {}; const kids = (p.children !== undefined) ? [].concat(p.children) : []; return reactHost.React.createElement(name, p, ...kids); }; }

const sandbox = {
  React: reactHost.React,
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  addEventListener() {}, removeEventListener() {},
  encodeURIComponent, URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math,
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

function load(p) { const src = fs.readFileSync(p, 'utf8'); const code = Babel.transform(src, { presets: ['react'], filename: p }).code; vm.runInContext(code, sandbox); }
for (const name of ['Panel','BrassButton','Divider','SectionTitle','Pill','Placeholder','Img','Glyph','CornerOrnament','Tooltip','InfoTooltip']) { sandbox[name] = passthrough(name); }
sandbox.Tooltip = passthrough('Tooltip');
sandbox.InfoTooltip = passthrough('InfoTooltip');
sandbox.useToast = () => (t) => {};
sandbox.slug = (n) => (n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
sandbox.combatSurfaceFromCampaign = () => '';

load(%(screen_character)s);

// Expand a captured element tree into a plain tree, INVOKING any function-component `type` so a
// nested component (e.g. <FeatureInspector/>) is walkable. Function components here use only a
// no-op useEffect (already shimmed on the host React), so a shallow re-invoke is safe for reads.
function expand(node) {
  if (node == null || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map(expand);
  if (typeof node.type === 'function') {
    const props = Object.assign({}, node.props || {});
    const kids = (node.children && node.children.length) ? node.children : (props.children !== undefined ? [].concat(props.children) : []);
    if (kids.length && props.children === undefined) props.children = kids.length === 1 ? kids[0] : kids;
    let rendered;
    try { rendered = node.type(props); } catch (e) { rendered = null; }
    return expand(rendered);
  }
  const props = node.props || {};
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  return { type: node.type, props: props, children: kids.map(expand) };
}
function findByTestId(rawNode, id, hits) {
  const node = (hits === undefined) ? expand(rawNode) : rawNode;
  hits = hits || [];
  if (node == null || typeof node !== 'object') return hits;
  if (Array.isArray(node)) { for (const c of node) findByTestId(c, id, hits); return hits; }
  const props = node.props || {};
  if (props['data-worldos-testid'] === id || props.testId === id) hits.push(node);
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) findByTestId(c, id, hits);
  return hits;
}
function findByTestIdPrefix(rawNode, prefix, hits) {
  const node = (hits === undefined) ? expand(rawNode) : rawNode;
  hits = hits || [];
  if (node == null || typeof node !== 'object') return hits;
  if (Array.isArray(node)) { for (const c of node) findByTestIdPrefix(c, prefix, hits); return hits; }
  const props = node.props || {};
  const tid = props['data-worldos-testid'] || props.testId;
  if (typeof tid === 'string' && tid.indexOf(prefix) === 0) hits.push(node);
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) findByTestIdPrefix(c, prefix, hits);
  return hits;
}
function collectText(rawNode, out) {
  const node = (out === undefined) ? expand(rawNode) : rawNode;
  out = out || [];
  if (node == null || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') { out.push(String(node)); return out; }
  if (Array.isArray(node)) { for (const c of node) collectText(c, out); return out; }
  const props = node.props || {};
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  for (const c of kids) collectText(c, out);
  return out;
}
function hasRoleDialog(rawNode, expanded) {
  const node = expanded ? rawNode : expand(rawNode);
  if (node == null || typeof node !== 'object') return false;
  if (Array.isArray(node)) return node.some((c) => hasRoleDialog(c, true));
  const props = node.props || {};
  if (props.role === 'dialog') return true;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [].concat(props.children) : []));
  return kids.some((c) => hasRoleDialog(c, true));
}
async function settle() { await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r)); reactHost.commit(); }

const h = {
  // Mount the REAL shipped ClassFeatureList component directly (it carries the hooks the
  // render-host tracks). AbilitiesTab/FeatsTab merely embed it — the wiring is pinned by the
  // source guards below; this exercises the live click-through + panel behaviour.
  mountFeatureList: (features, contextLabel) => { reactHost.mount(() => sandbox.window.ClassFeatureList({ features: features, contextLabel: contextLabel })); },
  tree: () => reactHost.api(),
  settle,
  click: async (id) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onClick; if (typeof oc !== 'function') throw new Error('no onClick on ' + id); oc(); await settle(); },
  countPrefix: (p) => findByTestIdPrefix(reactHost.api(), p).length,
  exists: (id) => findByTestId(reactHost.api(), id).length,
  hasDialog: () => hasRoleDialog(reactHost.api()),
  text: () => collectText(reactHost.api()).join(' ␟ '),
};

const script = %(script)s;
eval('(async () => { ' + script + ' })()')
  .then((result) => { process.stdout.write(JSON.stringify(result)); })
  .catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


# The optimizer's exact Fighter features — each carrying its SRD rules text in `detail`.
_FEATURES_FIGHTER = (
    "["
    "  { name: 'Extra Attack', detail: 'Attack twice instead of once when you take the Attack action.' },"
    "  { name: 'Action Surge', detail: 'Take one additional action on your turn. Recharges on a short or long rest.' },"
    "  { name: 'Indomitable', detail: 'Reroll a failed saving throw once per long rest; uses increase at higher levels.' }"
    "]"
)
# A feature with NO detail (the engine couldn't resolve it) — must keep today's static blurb,
# NOT a dead click-through to an empty panel.
_FEATURES_NODETAIL = "[ { name: 'Unknowable Gift', detail: '' } ]"


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


class FeatureInspectorRenderTests(_Harness):
    def test_each_class_feature_is_click_through(self):
        """Each class feature with rules text renders a clickable node (testid
        class-feature-<slug>) — not a flat static line. Three features -> three click targets."""
        out = self._run(
            "h.mountFeatureList(" + _FEATURES_FIGHTER + ", 'Level 9 · Fighter · Champion');"
            "return { count: h.countPrefix('class-feature-') };"
        )
        self.assertEqual(out["count"], 3, "all three class features must be click-through")

    def test_clicking_a_feature_opens_a_readonly_panel_with_full_rules_text(self):
        """Clicking 'Action Surge' opens a read-only dialog showing the FULL SRD rules text —
        the optimizer's '#1 min-maxer pain point' (no click-through to full rules)."""
        out = self._run(
            "h.mountFeatureList(" + _FEATURES_FIGHTER + ", 'Level 9 · Fighter');"
            "await h.click('class-feature-action-surge');"
            "return { hasDialog: h.hasDialog(), text: h.text() };"
        )
        self.assertTrue(out["hasDialog"], "clicking a feature must open a role=dialog panel")
        self.assertIn("Take one additional action on your turn", out["text"],
                      "the panel must show the feature's full SRD rules text")

    def test_feature_without_detail_is_not_click_through(self):
        """A feature the engine couldn't resolve (no detail) keeps today's static blurb — never a
        dead click-through to an empty panel (graceful degrade, per the additive invariant)."""
        out = self._run(
            "h.mountFeatureList(" + _FEATURES_NODETAIL + ", 'Level 3 · Homebrew');"
            "return { count: h.countPrefix('class-feature-'), text: h.text() };"
        )
        self.assertEqual(out["count"], 0, "a detail-less feature must not be click-through")
        self.assertIn("Unknowable Gift", out["text"], "its name must still render as static text")

    def test_closing_the_panel_returns_to_the_list(self):
        """The read-only panel closes back to the feature list (no keyboard trap; a Close control)."""
        out = self._run(
            "h.mountFeatureList(" + _FEATURES_FIGHTER + ", 'Level 9 · Fighter');"
            "await h.click('class-feature-indomitable');"
            "var opened = h.hasDialog();"
            "await h.click('feature-inspector-close');"
            "return { opened: opened, closed: !h.hasDialog() };"
        )
        self.assertTrue(out["opened"], "clicking a feature opens the panel")
        self.assertTrue(out["closed"], "Close dismisses the panel back to the list")


class FeatureInspectorSourceGuards(unittest.TestCase):
    """Static guards the render harness can't see — the inspector stays a READ-ONLY panel."""

    @classmethod
    def setUpClass(cls):
        cls.src = _SCREEN_CHARACTER.read_text(encoding="utf-8")

    def test_inspector_is_a_readonly_dialog_with_a_close_control(self):
        # the feature panel is a dialog (mirrors the #872 item-Examine pattern) with a Close.
        self.assertIn('role="dialog"', self.src)
        self.assertIn("FeatureInspector", self.src)

    def test_both_tabs_use_the_shared_feature_list(self):
        """The optimizer hit static features on BOTH the Abilities tab and the Feats tab — both
        must route their Class Features through the shared <ClassFeatureList> (so neither keeps a
        raw static map). Scope each check to its tab body."""
        for fn_name in ("AbilitiesTab", "FeatsTab"):
            start = self.src.index("function " + fn_name)
            # bound the body by the next top-level `function `, or the trailing Object.assign for
            # the last function in the file (FeatsTab).
            nxt = self.src.find("\nfunction ", start + 1)
            end = self.src.find("\nObject.assign(window", start + 1)
            bound = min(b for b in (nxt, end) if b != -1)
            body = self.src[start:bound]
            self.assertIn("ClassFeatureList", body, f"{fn_name} must render features via ClassFeatureList")
            # the old raw static map ("hero.classFeatures.map" / a bare classFeatures.map render
            # of a name-only <div>) must be gone from the tab body.
            self.assertNotIn("classFeatures.map((c)", body,
                             f"{fn_name} must not keep a raw static feature map")

    def test_feature_inspector_never_writes_to_the_engine(self):
        """The class-feature inspector is a pure reader — it must NOT POST /move (no engine write
        from a read-only rules panel). Scope the check to the FeatureInspector component body."""
        start = self.src.index("function FeatureInspector")
        nxt = self.src.index("\nfunction ", start + 1)
        body = self.src[start:nxt]
        self.assertNotIn("/move", body, "the feature inspector must never POST a move")
        self.assertNotIn("fetch(", body, "the feature inspector reads only props, never fetches")


if __name__ == "__main__":
    unittest.main()
