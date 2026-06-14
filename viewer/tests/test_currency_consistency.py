"""Behavior tests for the shared currency-display helpers (RRI-5e98e6f optimizer finding:
"Stash shows 35 GP, Market shows 232 GP (same character)").

ROOT CAUSE the optimizer caught: the Market (screen-merchant.jsx) read `surface.currency`
off `/character-surface`, but that endpoint returns NO top-level `currency` — the live coin
purse lives PER party member (`surface.party[i].currency`, the same engine field
`_currency_for(ch)` the inventory's `/inventory-surface` carries). So `surface.currency` was
always `undefined` and the Market fell through to a HARDCODED demo purse (`{ gp: 232 }`),
while the Stash (screen-inventory.jsx) showed the live per-hero purse (e.g. 35 GP) — the
exact 35-vs-232 contradiction.

The fix is a SINGLE shared currency layer in chrome.jsx (loaded before every screen):

  • window.normalizeCurrency(cur)  — coerce ANY currency object to {pp,gp,sp,ep,cp} ints
    (mirrors the engine `_currency_for`), so both screens speak one shape.
  • window.currencyTotalGp(cur)    — the ONE gp-equivalent conversion (5e: 1pp=10gp,
    1ep=0.5gp, 1sp=0.1gp, 1cp=0.01gp) so a "total" never diverges between screens.
  • window.partyPurse(party, activeId) — select the SAME hero's purse both screens render:
    the active hero by id, else the first party member. Both surfaces are projected from the
    same snapshot in the same `party` order, so this picks ONE source-of-truth character.

Both screens now derive their displayed coins from `window.partyPurse(...)` of the same
live `party`, so the Market and the Stash render the IDENTICAL purse for the same character
(no hardcoded-232 fallback when a live session is attached). The viewer stays read-only.

These tests transpile the REAL chrome.jsx with the SAME bundled Babel the browser uses and
exercise the pure helpers under Node (mirrors the pure-selector tests in
test_recovery_timing.py — computeColdOpenAwaiting / shouldApplySurface), so they track the
shipped helper, not a reimplementation.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_CHROME = _OPENWORLDS / "chrome.jsx"
_INVENTORY = _OPENWORLDS / "screen-inventory.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# chrome.jsx defines its helpers + JSX components as plain functions and ends with an
# Object.assign(window, {...}) export — NO ReactDOM bootstrap. Its module-scope JSX is only
# INSIDE function bodies (transpiled to React.createElement calls that run on invocation), so a
# trivial React stub is enough to load it; we only call the pure currency helpers here.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

const sandbox = {
  React: { createElement: () => null, Fragment: 'F' },
  document: { addEventListener() {}, removeEventListener() {}, getElementById: () => ({}),
              head: { appendChild() {} }, createElement: () => ({}) },
  JSON, Object, Array, String, Boolean, Number, Math, console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(chrome)s);

const win = sandbox.window;
const script = %(script)s;
const result = (function () { %(body)s })();
process.stdout.write(JSON.stringify(result));
""".replace("%(body)s", "return eval(script);")


class _CurrencyHarness(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_CHROME, _BABEL):
            cls.assertTrue(p.exists(), f"missing {p}")

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


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run chrome.jsx")
class CurrencyHelperContractTests(_CurrencyHarness):
    """The shared helpers exist with the contract both screens rely on."""

    def test_helpers_are_exported_on_window(self):
        out = self._run(
            "({ norm: typeof win.normalizeCurrency, total: typeof win.currencyTotalGp,"
            "   purse: typeof win.partyPurse })"
        )
        self.assertEqual(out["norm"], "function", "normalizeCurrency must be a shared window helper")
        self.assertEqual(out["total"], "function", "currencyTotalGp must be a shared window helper")
        self.assertEqual(out["purse"], "function", "partyPurse must be a shared window helper")

    def test_normalize_coerces_to_int_coin_fields(self):
        out = self._run("win.normalizeCurrency({ gp: '35', sp: 4, junk: 99 })")
        # mirrors the engine _currency_for shape: all five coins as ints, no stray fields.
        self.assertEqual(set(out.keys()), {"pp", "gp", "sp", "ep", "cp"})
        self.assertEqual(out["gp"], 35)
        self.assertEqual(out["sp"], 4)
        self.assertEqual(out["pp"], 0)

    def test_normalize_tolerates_missing_or_garbage_input(self):
        for js in ("win.normalizeCurrency(undefined)", "win.normalizeCurrency(null)",
                   "win.normalizeCurrency('nope')"):
            out = self._run(js)
            self.assertEqual(out, {"pp": 0, "gp": 0, "sp": 0, "ep": 0, "cp": 0})

    def test_total_gp_uses_the_canonical_5e_conversion(self):
        # 2pp=20gp + 35gp + 1ep=0.5gp + 4sp=0.4gp + 50cp=0.5gp => 56.4 gp-equivalent.
        out = self._run("win.currencyTotalGp({ pp: 2, gp: 35, ep: 1, sp: 4, cp: 50 })")
        self.assertAlmostEqual(out, 56.4, places=2)

    def test_total_gp_of_plain_gold_is_that_gold(self):
        self.assertEqual(self._run("win.currencyTotalGp({ gp: 35 })"), 35)
        self.assertEqual(self._run("win.currencyTotalGp({ gp: 232 })"), 232)


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run chrome.jsx")
class PartyPurseSelectionTests(_CurrencyHarness):
    """partyPurse picks ONE source-of-truth hero — the SAME selection both screens make."""

    _PARTY = (
        "[{ id: 'h1', currency: { gp: 35, sp: 4 } },"
        " { id: 'h2', currency: { gp: 999 } }]"
    )

    def test_active_hero_purse_is_selected(self):
        out = self._run(f"win.partyPurse({self._PARTY}, 'h1')")
        self.assertEqual(out["gp"], 35)
        self.assertEqual(out["sp"], 4)

    def test_falls_back_to_first_member_when_active_absent(self):
        # Both screens default to party[0] when no active id matches — the Market has no hero
        # switcher, so it always lands on party[0], which is the Stash's default active hero.
        out = self._run(f"win.partyPurse({self._PARTY}, 'nobody')")
        self.assertEqual(out["gp"], 35, "absent active id -> first party member (party[0])")
        out2 = self._run(f"win.partyPurse({self._PARTY}, '')")
        self.assertEqual(out2["gp"], 35)

    def test_empty_party_yields_zeroed_purse(self):
        out = self._run("win.partyPurse([], 'h1')")
        self.assertEqual(out, {"pp": 0, "gp": 0, "sp": 0, "ep": 0, "cp": 0})

    def test_result_is_normalized_coin_shape(self):
        out = self._run(f"win.partyPurse({self._PARTY}, 'h1')")
        self.assertEqual(set(out.keys()), {"pp", "gp", "sp", "ep", "cp"})


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run chrome.jsx")
class MarketStashParityTests(_CurrencyHarness):
    """THE optimizer finding: for one live party state, the Market and the Stash must derive
    the IDENTICAL purse + total from the SAME shared helper — no 35-vs-232 divergence."""

    # A single live party (the shape BOTH /character-surface and /inventory-surface carry under
    # `party[i].currency`, projected from the same snapshot in the same order). Hero one holds 35gp.
    _LIVE_PARTY = (
        "[{ id: 'astarion', currency: { gp: 35, sp: 12, cp: 7 } },"
        " { id: 'shadowheart', currency: { gp: 80 } }]"
    )

    def test_market_and_stash_render_the_same_purse_and_total(self):
        out = self._run(
            # STASH path (screen-inventory): active hero of the live party, via the shared helper.
            f"var party = {self._LIVE_PARTY};"
            "var stashPurse = win.partyPurse(party, 'astarion');"
            "var stashTotal = win.currencyTotalGp(stashPurse);"
            # MARKET path (screen-merchant): no hero switcher, so it lands on the same party[0]
            # via the SAME shared helper — NOT a hardcoded 232 demo purse.
            "var marketPurse = win.partyPurse(party, '');"
            "var marketTotal = win.currencyTotalGp(marketPurse);"
            "({ stashPurse: stashPurse, marketPurse: marketPurse,"
            "   stashTotal: stashTotal, marketTotal: marketTotal,"
            "   stashGp: stashPurse.gp, marketGp: marketPurse.gp })"
        )
        # The 35-vs-232 contradiction is gone: identical purse object, identical gp, identical total.
        self.assertEqual(out["stashPurse"], out["marketPurse"],
                         "Market and Stash must read the SAME purse for the same character")
        self.assertEqual(out["stashGp"], out["marketGp"],
                         "the displayed GP must match across screens (no hardcoded 232)")
        self.assertEqual(out["stashGp"], 35, "both screens show the live 35 GP, not the demo 232")
        self.assertAlmostEqual(out["stashTotal"], out["marketTotal"], places=6,
                               msg="the gp-equivalent total must be computed by the one shared converter")

    def test_market_does_not_invent_232_from_a_live_party(self):
        # Whatever party[0] holds, the shared selection never yields the legacy demo 232.
        out = self._run(
            f"win.currencyTotalGp(win.partyPurse({self._LIVE_PARTY}, ''))"
        )
        self.assertNotEqual(out, 232)
        # 35 + 12sp(1.2) + 7cp(0.07) = 36.27
        self.assertAlmostEqual(out, 36.27, places=2)


# screen-inventory.jsx defines its components + the pure `packContents` helper as plain
# functions and ends with an Object.assign(window, {...}) export — NO ReactDOM bootstrap, and its
# module-scope JSX is only inside function bodies. A trivial React stub loads it; we call only the
# pure packContents helper. (window.Tooltip etc. are referenced inside render bodies, never at load.)
_INV_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

const sandbox = {
  React: { createElement: () => null, Fragment: 'F' },
  document: { addEventListener() {}, removeEventListener() {}, getElementById: () => ({}),
              head: { appendChild() {} }, createElement: () => ({}) },
  JSON, Object, Array, String, Boolean, Number, Math, RegExp, Set, console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(inventory)s);

const win = sandbox.window;
const script = %(script)s;
const result = (function () { return eval(script); })();
process.stdout.write(JSON.stringify(result));
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run screen-inventory.jsx")
class PackContentsTests(unittest.TestCase):
    """RRI-5e98e6f minor: itemize a pack/kit's contents from the engine-provided description —
    read-only reformatting, never fabrication (returns [] for non-packs / prose / a thin list)."""

    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_INVENTORY, _BABEL):
            cls.assertTrue(p.exists(), f"missing {p}")

    def _run(self, script: str):
        program = _INV_HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "inventory": json.dumps(str(_INVENTORY)),
            "script": json.dumps(script),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)

    def test_explorers_pack_is_itemized_from_its_engine_description(self):
        # The exact engine grant (servers/engine/server.py): an "Explorer's Pack" whose description
        # is the manifest "Bedroll, rations, rope, torches, and the like."
        out = self._run(
            "win.packContents({ name: \"Explorer's Pack\","
            " desc: 'Bedroll, rations, rope, torches, and the like.' })"
        )
        # The real contents are listed; the "…and the like" catch-all is dropped (not an item).
        self.assertEqual(out, ["Bedroll", "Rations", "Rope", "Torches"])
        self.assertNotIn("The like", out)

    def test_non_pack_item_is_not_itemized(self):
        # A weapon's prose description must NOT be shredded into a fake contents list.
        out = self._run(
            "win.packContents({ name: 'Longsword',"
            " desc: 'A versatile blade, balanced and keen.' })"
        )
        self.assertEqual(out, [], "a non-pack item must not be itemized")

    def test_pack_with_prose_description_is_not_itemized(self):
        # A pack whose desc is a single prose sentence (no list) is left to the paragraph.
        out = self._run(
            "win.packContents({ name: 'Burglar\\'s Pack', desc: 'A discreet bag of tricks.' })"
        )
        self.assertEqual(out, [], "a one-line prose description is not a contents manifest")

    def test_empty_or_missing_desc_yields_empty(self):
        for js in ("win.packContents({ name: \"Explorer's Pack\" })",
                   "win.packContents({ name: \"Explorer's Pack\", desc: '' })",
                   "win.packContents(null)"):
            self.assertEqual(self._run(js), [])

    def test_dungeoneers_pack_dedupes_and_titlecases(self):
        out = self._run(
            "win.packContents({ name: \"Dungeoneer's Pack\","
            " desc: 'a crowbar, a hammer, pitons, torches, torches, rations and a waterskin' })"
        )
        # de-duped (one "Torches"), title-cased, leading article stripped.
        self.assertEqual(out, ["Crowbar", "Hammer", "Pitons", "Torches", "Rations", "Waterskin"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
