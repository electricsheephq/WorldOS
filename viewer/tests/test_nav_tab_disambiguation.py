"""Behaviour tests for the newcomer-clarity fix: disambiguate the opaque D&D-jargon
nav tabs "Parley", "Battle" and "Codex" for a no-prior-knowledge player.

THE FINDING (two newbie dogfoods): the play-screen sub-tabs "Parley", "Battle" and
"Codex" are opaque jargon to a first-timer ("unclear what they do vs just typing in the
box"; "Codex as a nav label is opaque jargon"). North star = a no-prior-knowledge player
never feels lost. The fix is NOT to rename away the flavor — it is to ADD a concise,
accessible plain-language disambiguator alongside the thematic label so the tab is
unmistakable to a first-timer while keeping the Baldur's-Gate voice.

These tabs are config-driven entries in `NAV_GROUPS` (chrome.jsx) rendered by the real
`TabBar` component. This file renders the ACTUAL `TabBar` by transpiling the shipped
chrome.jsx with the SAME vendored Babel-standalone the browser uses and walking its
element tree — so it tracks the shipped JSX, not a reimplementation (mirrors
test_dm_beat_wait_alive.py / test_cold_open_progress.py).

Contract pinned here, for EACH opaque tab (Parley/Battle/Codex):
  1. the thematic label is PRESERVED (flavor stays — we don't rename it away), and
  2. the tab exposes an accessible plain-language disambiguator — a visible sub-label
     AND a title/aria-label — containing the plain hint (talk / combat / lore-journal),
     so a first-timer (and a screen reader) reads what the tab does.
The non-opaque tabs in the same groups (Session/Quests/Acts) are NOT given a redundant
sub-label, so the disambiguator stays a targeted newcomer aid, not chrome noise.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_CHROME = _OPENWORLDS / "chrome.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A self-contained Node harness. React.createElement captures a plain node tree; hooks are
# stubbed. We load the REAL chrome.jsx (which assigns TabBar + NAV_GROUPS onto window), then
# render TabBar for a chosen `current` tab and walk the rendered tree to collect, per tab
# button, its visible text, the text NOT under an aria-hidden subtree (what a screen reader /
# ariaSnapshot sees), and its title + aria-label attributes.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

function makeReact() {
  function useState(init) { const v = (typeof init === 'function') ? init() : init; return [v, function () {}]; }
  function useEffect() {}
  function useRef(init) { return { current: init }; }
  function useCallback(fn) { return fn; }
  function createElement(type, props) {
    const children = Array.prototype.slice.call(arguments, 2);
    return { type, props: props || {}, children };
  }
  return { useState, useEffect, useRef, useCallback, createElement, Fragment: 'F' };
}

const React = makeReact();
const sandbox = {
  React,
  ReactDOM: { createRoot: () => ({ render() {} }) },
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible', getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
  fetch: () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }),
  console,
};
sandbox.window = sandbox;
sandbox.Date = { now: () => 1000000 };
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(chrome)s);

const TabBar = sandbox.window.TabBar;
const NAV_GROUPS = sandbox.window.NAV_GROUPS;
if (typeof TabBar !== 'function') throw new Error('TabBar not exported');
if (!Array.isArray(NAV_GROUPS)) throw new Error('NAV_GROUPS not exported');

// Collect text under a node; accessibleOnly drops any subtree with aria-hidden (mirrors a
// screen reader / ariaSnapshot).
function collectText(node, accessibleOnly, hiddenAncestor) {
  let out = [];
  if (node == null || node === false) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    if (!(accessibleOnly && hiddenAncestor)) out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) { for (const c of node) out = out.concat(collectText(c, accessibleOnly, hiddenAncestor)); return out; }
  const props = node.props || {};
  const hidden = hiddenAncestor || props['aria-hidden'] === 'true' || props['aria-hidden'] === true;
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) out = out.concat(collectText(c, accessibleOnly, hidden));
  return out;
}

// Walk the tree, gathering every tab button (a node carrying data-worldos-tab-id) keyed by
// tab id, with its full text, accessible (non-aria-hidden) text, title + aria-label.
function collectTabs(node, acc) {
  if (node == null || typeof node !== 'object') return acc;
  if (Array.isArray(node)) { for (const c of node) collectTabs(c, acc); return acc; }
  const props = node.props || {};
  const tabId = props['data-worldos-tab-id'];
  if (tabId) {
    acc[tabId] = {
      allText: collectText(node, false, false).join(' '),
      accessibleText: collectText(node, true, false).join(' '),
      title: props.title == null ? null : String(props.title),
      ariaLabel: props['aria-label'] == null ? null : String(props['aria-label']),
    };
  }
  const kids = (node.children && node.children.length ? node.children : (props.children !== undefined ? [props.children] : []));
  for (const c of kids) collectTabs(c, acc);
  return acc;
}

// Render TabBar with `current` set to a tab so the group containing it is the active group
// (TabBar renders nothing for a group with < 2 tabs). Returns the per-tab map for that group.
function renderGroupOf(current) {
  const tree = TabBar({ current, onNavigate: function () {} });
  return collectTabs(tree, {});
}

const h = { renderGroupOf, NAV_GROUPS };

const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + render the JSX")
class NavTabDisambiguationTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    # The three opaque tabs and the plain-language hint each disambiguator must surface for a
    # first-timer. The hint substrings are matched case-insensitively against the tab's visible
    # sub-label AND its title/aria-label.
    OPAQUE = {
        "combat": {"label": "Battle", "hint": "combat"},
        "dialogue": {"label": "Parley", "hint": "talk"},
        "bestiary": {"label": "Codex", "hint": "lore"},
    }

    @classmethod
    def setUpClass(cls):
        for p in (_CHROME, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str):
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "chrome": json.dumps(str(_CHROME)),
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

    def _tab(self, tab_id):
        # render the group that contains tab_id (current=tab_id) and pull that tab's rendered info
        out = self._run(f"h.renderGroupOf({json.dumps(tab_id)})")
        self.assertIn(tab_id, out, f"TabBar did not render a tab button for {tab_id!r}")
        return out[tab_id]

    # === CONTRACT 1: the thematic flavor label is PRESERVED (we ADD, we do not rename) ========

    def test_opaque_tabs_keep_their_thematic_label(self):
        for tab_id, spec in self.OPAQUE.items():
            with self.subTest(tab=tab_id):
                info = self._tab(tab_id)
                self.assertIn(
                    spec["label"], info["allText"],
                    f"the thematic label {spec['label']!r} must be PRESERVED on the {tab_id} tab "
                    "— the fix ADDS a disambiguator, it does not rename away the flavor",
                )

    # === CONTRACT 2: each opaque tab exposes a VISIBLE plain-language sub-label ===============

    def test_opaque_tabs_expose_visible_plain_language_sublabel(self):
        for tab_id, spec in self.OPAQUE.items():
            with self.subTest(tab=tab_id):
                info = self._tab(tab_id)
                # the plain hint must appear in the tab's VISIBLE text, in addition to the label,
                # so a sighted first-timer reads what the tab does without hovering.
                self.assertIn(
                    spec["hint"], info["allText"].lower(),
                    f"the {tab_id} tab ({spec['label']!r}) must show a visible plain-language "
                    f"sub-label containing {spec['hint']!r} so a no-prior-knowledge player knows "
                    "what it does — dogfood newcomer-clarity",
                )

    # === CONTRACT 3: the disambiguator is in the ACCESSIBLE tree (screen reader / ariaSnapshot)

    def test_opaque_tabs_disambiguator_is_accessible(self):
        for tab_id, spec in self.OPAQUE.items():
            with self.subTest(tab=tab_id):
                info = self._tab(tab_id)
                # a title or aria-label carrying the hint is what a hover + a screen reader read.
                tip = " ".join(filter(None, [info["title"], info["ariaLabel"]])).lower()
                self.assertTrue(
                    spec["hint"] in tip,
                    f"the {tab_id} tab must carry a title/aria-label disambiguator containing "
                    f"{spec['hint']!r} (a11y consumers: hover tooltip + screen reader) — got "
                    f"title={info['title']!r} aria-label={info['ariaLabel']!r}",
                )
                # and the label must stay inside that accessible affordance too (flavor + plain hint
                # read together), not be replaced by the bare hint.
                self.assertIn(
                    spec["label"].lower(), tip,
                    f"the {tab_id} tab's title/aria-label must keep the thematic label "
                    f"{spec['label']!r} alongside the plain hint",
                )

    # === GUARD: the disambiguator is targeted — non-opaque tabs are NOT given a sub-label ======

    def test_self_explanatory_tabs_are_not_cluttered_with_redundant_hints(self):
        # "Session" (the Table group) is plain English already; it must NOT sprout a redundant
        # sub-label — the disambiguator is a newcomer aid for the jargon tabs, not chrome noise.
        info = self._tab("table")
        self.assertIn("Session", info["allText"])
        sub = info["allText"].replace("Session", "").strip()
        self.assertEqual(
            sub, "",
            "self-explanatory tabs (Session) must not carry an extra sub-label — the newcomer "
            f"disambiguator is targeted at the opaque jargon tabs only; got extra text {sub!r}",
        )

    # === GUARD: every opaque tab id still exists in config (no accidental rename/removal) =======

    def test_opaque_tab_ids_still_present_in_nav_config(self):
        out = self._run("h.NAV_GROUPS.flatMap(g => g.tabs.map(t => ({ id: t.id, label: t.label })))")
        by_id = {t["id"]: t["label"] for t in out}
        for tab_id, spec in self.OPAQUE.items():
            with self.subTest(tab=tab_id):
                self.assertIn(tab_id, by_id, f"{tab_id} tab must remain in NAV_GROUPS")
                self.assertEqual(
                    by_id[tab_id], spec["label"],
                    f"the {tab_id} tab's thematic label config must stay {spec['label']!r}",
                )


if __name__ == "__main__":
    unittest.main()
