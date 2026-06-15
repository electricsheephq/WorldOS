"""#721 — 'Bind the hero' must SURFACE the missing-native-bridge failure, not phantom-succeed.

`screen-create.jsx`'s `bindHero` mints a real provider session through the native supervisor
(`window.OpenWorldsNative`). Outside the native app (a plain browser preview) there is no bridge.
The old behavior silently called `onNavigate("table")` — a PHANTOM SUCCESS: the player pressed
"Bind the hero", the wizard vanished onto a read-only table, and nothing told them the bind never
actually happened (no game was minted).

This guard mounts the REAL ScreenCreate through the babel-vm harness (the established pattern from
test_rest_camp_levelup_wiring.py), drives the wizard to its final Bind step, and clicks
'Bind the hero' with `window.OpenWorldsNative` undefined. It asserts:

  * the failure is SURFACED — an inline summonError is rendered, AND a danger toast fires;
  * the bind does NOT phantom-succeed — onNavigate("table") is NOT called.

Read-only / additive: this is a presentation-only reliability fix — no engine state is written
(there is no bridge, so no /move, no startProviderSession). The viewer stays a move-sink.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_CREATE = _OPENWORLDS / "screen-create.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A minimal-but-real React (hook cells + effects + a walkable createElement tree), a passthrough for
# every chrome component screen-create renders, and a tree-walk to find a node by testid + invoke its
# onClick — the exact harness shape proven in test_rest_camp_levelup_wiring.py.
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

const reactHost = makeReact();

function passthrough(name) { return function (props) { const p = props || {}; const kids = (p.children !== undefined) ? [].concat(p.children) : []; return reactHost.React.createElement(name, p, ...kids); }; }

const navCalls = [];
const toastCalls = [];

const sandbox = {
  React: reactHost.React,
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setTimeout: (fn) => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  addEventListener() {}, removeEventListener() {},
  encodeURIComponent, URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math, Date,
  console,
  location: { replace() {} },
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
sandbox.useToast = () => (t) => { toastCalls.push(t); };
sandbox.inkInput = {};
// OpenWorldsBuilding stub (begin/clear are called around the mint). NO OpenWorldsNative on window —
// that is exactly the no-bridge condition under test.
sandbox.OpenWorldsBuilding = { begin() {}, clear() {} };

load(%(screen_create)s);

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
  mountCreate: (props) => { reactHost.mount(() => sandbox.window.ScreenCreate(props)); },
  tree: () => reactHost.api(),
  settle,
  props: (id) => firstProps(reactHost.api(), id),
  click: async (id) => { const hits = findByTestId(reactHost.api(), id); if (!hits.length) throw new Error('no node ' + id); const oc = (hits[0].props || {}).onClick; if (typeof oc !== 'function') throw new Error('no onClick on ' + id); oc(); await settle(); },
  text: () => collectText(reactHost.api()).join(' ' + String.fromCharCode(31) + ' '),
  navCalls: () => navCalls,
  toasts: () => toastCalls,
  exists: (id) => findByTestId(reactHost.api(), id).length,
};
sandbox.__navCalls = navCalls;

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
        for p in (_SCREEN_CREATE, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "screen_create": json.dumps(str(_SCREEN_CREATE)),
            "script": json.dumps(script),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)


# Mount ScreenCreate, drive to the final Bind step (the left-rail 'Bind' step button jumps there),
# and click 'Bind the hero'. onNavigate records its calls so a phantom-success is observable.
_MOUNT = (
    "var nav = h.navCalls();"
    "h.mountCreate({ onNavigate: function(dest){ nav.push(dest); }, state: {}, setState: function(){} });"
    "await h.settle();"
    "await h.click('create-step-review');"        # jump to the last (review/Bind) step
)


class BindHeroNoBridgeTests(_Harness):
    """#721 — without the native bridge, Bind surfaces the failure (error + toast) and does NOT
    silently navigate to the read-only table (phantom success)."""

    def test_bind_without_bridge_surfaces_error_and_does_not_phantom_navigate(self):
        out = self._run(
            _MOUNT +
            "await h.click('bind-hero');"
            "var nav = h.navCalls();"
            "return ({ nav: nav, toasts: h.toasts(), text: h.text() });"
        )
        # PHANTOM SUCCESS guard: the bind must NOT navigate to the read-only table.
        self.assertNotIn("table", out["nav"],
                         "without the native bridge, Bind must NOT silently navigate to the read-only table")
        # The failure is SURFACED inline (summonError) — the player learns the bind didn't happen.
        joined = out["text"].lower()
        self.assertTrue(
            ("native" in joined) or ("worldos app" in joined) or ("app to bind" in joined),
            "the no-bridge failure must be surfaced inline (an error mentioning the native app)",
        )
        # AND a danger toast fires (the established failure-surface pattern in bindHero's catch).
        danger = [t for t in out["toasts"] if (t or {}).get("kind") == "danger"]
        self.assertTrue(danger, "a danger toast must fire so the no-bridge failure is not silent")

    def test_bind_button_has_a_stable_test_id(self):
        # The Bind CTA must be findable (a stable testId) so its behavior is guardable — this is the
        # affordance the player presses; an un-testid'd button can silently regress.
        out = self._run(
            _MOUNT +
            "return ({ bind: h.exists('bind-hero') });"
        )
        self.assertGreaterEqual(out["bind"], 1, "the 'Bind the hero' button must carry a stable testId")


if __name__ == "__main__":
    unittest.main()
