"""Behaviour tests for the #756 item-inspector fix (from the RRI-5e98e6f optimizer sweep).

The optimizer filed #756 Critical with three concrete symptoms, all VIEWER-side render/
wire gaps where the engine catalog already holds the data (PR #862):

  1. Market -> Armor inspector: "Studded Leather shows no AC value — impossible to evaluate
     the upgrade" (the CRITICAL gate-flipper).
  2. Stash -> Quarterstaff "Examine" fires a toast ONLY; the inspector is missing the
     Versatile property AND the 1d8 two-handed damage.
  3. No compare-on-hover anywhere in inventory or market.

These tests transpile the REAL screen-inventory.jsx + screen-merchant.jsx with the SAME
bundled Babel-standalone the browser uses and run the pure inspector helpers under Node
(mirrors test_recovery_timing.py). They drive the SHIPPED code — `itemStatRows`,
`itemCompareRows`, `enrichWare` — so a passing test tracks real behaviour, not a string
grep. A separate served-source guard asserts the wiring the harness can't see (Examine
opens a PANEL/dialog instead of a toast; the Market reads /item-catalog; the read endpoint
exists).
"""

import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_INVENTORY = _OPENWORLDS / "screen-inventory.jsx"
_MERCHANT = _OPENWORLDS / "screen-merchant.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# A minimal Node harness: a stub React (createElement returns null — we only call the PURE
# helpers, never render), a `window` sandbox, and the bundled Babel. It transpiles the real
# screen JSX (whose module body just DEFINES functions + Object.assign(window, …)) then
# evaluates a JS `script` with `win` (= window) in scope; the script's return → JSON stdout.
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

const React = {
  useState: (i) => [typeof i === 'function' ? i() : i, () => {}],
  useEffect: () => {},
  useRef: (i) => ({ current: i }),
  useCallback: (fn) => fn,
  createElement: () => null,
  Fragment: 'F',
};
const sandbox = {
  React,
  document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible',
              getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  encodeURIComponent, URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math,
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
%(loads)s

const win = sandbox.window;
const script = %(script)s;
eval('(() => { ' + script + ' })()');
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX inspector helpers")
class ItemInspectorBehaviourTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_INVENTORY, _MERCHANT, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str, loads=(_INVENTORY, _MERCHANT)) -> dict:
        loads_js = "\n".join(f"load({json.dumps(str(p))});" for p in loads)
        program = _HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "loads": loads_js,
            "script": json.dumps("var __r = (function(){ " + script + " })(); "
                                 "process.stdout.write(JSON.stringify(__r));"),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)

    # --- symptom 1 (CRITICAL): an ARMOR inspector shows its AC ----------------
    def test_armor_inspector_shows_ac(self):
        """An armor item with a resolved base AC must surface an "Armor Class" row — the
        CRITICAL "Studded Leather shows no AC value" gate-flipper."""
        rows = self._run(
            "return win.itemStatRows({ name: 'Studded Leather', type: 'armor', "
            "  weight: '13 lb', value: '45 gp', ac: 12 });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertIn("Armor Class", kv, "the armor inspector must render an Armor Class row")
        self.assertEqual(kv["Armor Class"], "12")

    def test_armor_with_no_ac_omits_the_row_no_fabrication(self):
        # A free-text item the catalog can't resolve (ac absent) must NOT fabricate an AC.
        rows = self._run(
            "return win.itemStatRows({ name: 'Mystery Vest', type: 'armor', weight: '—', value: '—' });"
        )
        self.assertNotIn("Armor Class", {r["k"] for r in rows})

    def test_acdisplay_dex_rule_row_for_medium_armor(self):
        # F09-6: when the read-model supplies the composed acDisplay, the armor row reads the
        # full dex rule under an "Armor" label (not the redundant "Armor Class AC 14…").
        rows = self._run(
            "return win.itemStatRows({ name: 'Breastplate', type: 'armor', ac: 14, "
            "  armorCategory: 'medium', acDisplay: 'AC 14 + DEX (max +2)' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Armor"), "AC 14 + DEX (max +2)")
        self.assertNotIn("Armor Class", kv)  # the acDisplay branch supersedes the bare row

    def test_acdisplay_shield_shows_bonus_not_flat_ac(self):
        # F09-6: a shield grants a +N bonus, so the row reads "+2" under a "Shield" label —
        # never the misleading flat "AC 2".
        rows = self._run(
            "return win.itemStatRows({ name: 'Shield', type: 'armor', ac: 2, "
            "  armorCategory: 'shield', acDisplay: '+2' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Shield"), "+2")
        self.assertNotIn("Armor Class", kv)

    # --- symptom 2: a VERSATILE weapon shows its 1d8 two-handed damage --------
    def test_versatile_weapon_shows_two_handed_die(self):
        """A Versatile weapon must read its one-handed damage AND its two-handed die — the
        optimizer's "Quarterstaff Examine is missing the 1d8 two-handed damage"."""
        rows = self._run(
            "return win.itemStatRows({ name: 'Quarterstaff', type: 'weapon', "
            "  damage: '1d6', damageType: 'bludgeoning', versatile: '1d8' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertIn("Damage", kv)
        self.assertIn("1d6", kv["Damage"])
        self.assertIn("1d8 two-handed", kv["Damage"],
                      "a Versatile weapon must show its 1d8 two-handed damage")

    def test_non_versatile_weapon_shows_plain_damage(self):
        rows = self._run(
            "return win.itemStatRows({ name: 'Dagger', type: 'weapon', damage: '1d4', damageType: 'piercing' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv["Damage"], "1d4 piercing")
        self.assertNotIn("two-handed", kv["Damage"])

    # --- RRI-25e55fa optimizer #3: weapon RANGE + VALUE rows --------------------
    def test_ranged_weapon_shows_range_row(self):
        """A ranged weapon must render a Range row reading its real bracket — the optimizer's
        'Heavy Crossbow no 100/320 ft'. The display string comes from the read-model."""
        rows = self._run(
            "return win.itemStatRows({ name: 'Heavy Crossbow', type: 'weapon', "
            "  damage: '1d10', damageType: 'piercing', rangeDisplay: '100/400 ft' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Range"), "100/400 ft")

    def test_melee_weapon_omits_range_row(self):
        """A pure melee weapon (no rangeDisplay) must NOT render a Range row — never '0/0 ft'."""
        rows = self._run(
            "return win.itemStatRows({ name: 'Longsword', type: 'weapon', "
            "  damage: '1d8', damageType: 'slashing', rangeDisplay: '' });"
        )
        self.assertNotIn("Range", {r["k"] for r in rows})

    def test_value_falls_back_to_catalog_cost_when_blank(self):
        """The optimizer's 'Value — blank while Price populated': when an item carries no own
        `value` but a catalog cost is known (costValue), the Value row reads the cost — never '—'."""
        rows = self._run(
            "return win.itemStatRows({ name: 'Heavy Crossbow', type: 'weapon', costValue: '50 gp' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Value"), "50 gp")

    def test_value_prefers_items_own_value_over_cost_fallback(self):
        rows = self._run(
            "return win.itemStatRows({ name: 'Thing', value: '12 gp', costValue: '99 gp' });"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Value"), "12 gp")

    # --- symptom 3: compare-to-equipped --------------------------------------
    def test_compare_to_equipped_armor_shows_ac_delta(self):
        """The inspector compares a candidate to the equipped peer in the same slot — the
        optimizer's "no compare-on-hover anywhere". Studded Leather (AC 12) over Leather
        Armor (AC 11) is a +1 upgrade."""
        out = self._run(
            "return win.itemCompareRows("
            "  { name: 'Studded Leather Armor', ac: 12 },"
            "  [{ name: 'Leather Armor', ac: 11 }]);"
        )
        self.assertIsNotNone(out, "a comparable equipped peer must yield a compare block")
        self.assertEqual(out["peer"], "Leather Armor")
        ac_row = next(r for r in out["rows"] if r["k"] == "Armor Class")
        self.assertEqual(ac_row["mine"], 12)
        self.assertEqual(ac_row["theirs"], 11)
        self.assertEqual(ac_row["delta"], 1, "Studded Leather over Leather is a +1 AC upgrade")

    def test_compare_returns_null_without_a_peer(self):
        # Nothing equipped in the slot -> no compare block (not a fabricated 0-delta).
        out = self._run(
            "return win.itemCompareRows({ name: 'Studded Leather Armor', ac: 12 }, []);"
        )
        self.assertIsNone(out)

    # --- the Market enrichment merge (symptom 1 in the Market) ----------------
    def test_enrich_ware_fills_ac_from_catalog(self):
        """The Market's hardcoded "Studded leather" ware (no AC) gains the catalog AC so the
        Market inspector can finally show it — the literal CRITICAL Market finding."""
        out = self._run(
            "return win.enrichWare("
            "  { name: 'Studded leather', type: 'armor', weight: '20 lb', price: 25 },"
            "  { 'Studded leather': { resolved: true, ac: 12, kind: 'armor', properties: [] } });"
        )
        self.assertEqual(out["ac"], 12, "the Market ware must pick up its catalog AC")
        # the ware's own price/weight are preserved (its explicit fields win)
        self.assertEqual(out["price"], 25)

    def test_enrich_ware_folds_versatile_and_properties(self):
        out = self._run(
            "return win.enrichWare("
            "  { name: 'Quarterstaff', type: 'weapon', weight: '4 lb', price: 1 },"
            "  { 'Quarterstaff': { resolved: true, damage: '1d6', damageType: 'bludgeoning',"
            "      versatile: '1d8', properties: ['Versatile'] } });"
        )
        self.assertEqual(out["damage"], "1d6")
        self.assertEqual(out["versatile"], "1d8")
        self.assertIn("Versatile", out["properties"])

    def test_enrich_ware_folds_weapon_range(self):
        """RRI-25e55fa optimizer #3: the Market's Heavy Crossbow ware (no range) gains the
        catalog range bracket so the Market inspector can finally show '100/400 ft'."""
        out = self._run(
            "return win.enrichWare("
            "  { name: 'Heavy crossbow', type: 'weapon', weight: '8 lb', price: 50 },"
            "  { 'Heavy crossbow': { resolved: true, damage: '1d10', damageType: 'piercing',"
            "      range: 100, rangeLong: 400, rangeDisplay: '100/400 ft', properties: [] } });"
        )
        self.assertEqual(out["rangeDisplay"], "100/400 ft")

    def test_enrich_ware_backfills_value_from_catalog_for_value_row(self):
        """The optimizer's 'Value — blank while Price populated' in the Market: a ware carries
        `price` but no `value`; enrichWare folds the catalog `value` (gp string) so the Value
        row reads it. The ware's own price is preserved (Price row unchanged)."""
        out = self._run(
            "return win.enrichWare("
            "  { name: 'Heavy crossbow', type: 'weapon', weight: '8 lb', price: 50 },"
            "  { 'Heavy crossbow': { resolved: true, value: '50 gp', properties: [] } });"
        )
        self.assertEqual(out["value"], "50 gp")
        self.assertEqual(out["price"], 50)
        rows = self._run(
            "var ware = win.enrichWare("
            "  { name: 'Heavy crossbow', type: 'weapon', weight: '8 lb', price: 50 },"
            "  { 'Heavy crossbow': { resolved: true, value: '50 gp', properties: [] } });"
            "return win.itemStatRows(ware);"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Value"), "50 gp")

    def test_enrich_ware_unresolved_name_is_untouched(self):
        """An item the catalog can't resolve (resolved:false / absent) is returned AS-IS —
        weight/price only, never a fabricated stat."""
        out = self._run(
            "return win.enrichWare("
            "  { name: 'Crossbow bolts', type: 'weapon', weight: '3 lb', price: 6 },"
            "  { 'Crossbow bolts': { resolved: false } });"
        )
        self.assertNotIn("ac", out)
        self.assertEqual(out["price"], 6)
        out2 = self._run(
            "return win.enrichWare({ name: 'Whatsit', price: 3 }, {});"
        )
        self.assertEqual(out2["price"], 3)

    # --- F09-6 / #874: the Market path renders the same honest armor dex rule -----
    # The FULL Market render: enrichWare() must carry the catalog's composed acDisplay +
    # armorCategory onto the ware, so window.itemStatRows() (the SAME helper the Stash uses)
    # reads "AC 14 + DEX (max +2)" for medium armor and a shield's bonus "+2" — never the
    # misleading flat "AC 14"/"AC 2" the bare ac would render.
    def test_market_breastplate_renders_acdisplay_dex_rule(self):
        rows = self._run(
            "var ware = win.enrichWare("
            "  { name: 'Breastplate', type: 'armor', weight: '20 lb', price: 400 },"
            "  { 'Breastplate': { resolved: true, ac: 14, kind: 'armor',"
            "      armorCategory: 'medium', acDexMod: 'capped', acDexCap: 2,"
            "      acDisplay: 'AC 14 + DEX (max +2)', properties: [] } });"
            "return win.itemStatRows(ware);"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Armor"), "AC 14 + DEX (max +2)")
        self.assertNotIn("Armor Class", kv)  # the acDisplay branch supersedes the bare AC

    def test_market_shield_renders_bonus_not_flat_ac(self):
        rows = self._run(
            "var ware = win.enrichWare("
            "  { name: 'Shield', type: 'armor', weight: '6 lb', price: 10 },"
            "  { 'Shield': { resolved: true, ac: 2, kind: 'armor',"
            "      armorCategory: 'shield', acDexMod: 'none', acDexCap: null,"
            "      acDisplay: '+2', properties: [] } });"
            "return win.itemStatRows(ware);"
        )
        kv = {r["k"]: r["v"] for r in rows}
        self.assertEqual(kv.get("Shield"), "+2")
        self.assertNotIn("Armor Class", kv)


# ── served-source guards: the wiring the pure-helper harness can't observe ─────

_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_item_inspector", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class ItemInspectorWiringTests(unittest.TestCase):
    """The render-wiring + the /item-catalog read endpoint, asserted against a live server."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state

    def _get(self, path: str):
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    # the /item-catalog read endpoint exists + returns the real AC for the CRITICAL item
    def test_item_catalog_endpoint_resolves_studded_leather_ac(self):
        status, ctype, body = self._get("/item-catalog?name=Studded%20leather")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        payload = json.loads(body)
        rec = payload["items"]["Studded leather"]
        self.assertTrue(rec["resolved"])
        self.assertEqual(rec["ac"], 12, "the CRITICAL Studded Leather AC must come back from the endpoint")

    def test_item_catalog_endpoint_exposes_versatile_die_and_properties(self):
        status, _ctype, body = self._get("/item-catalog?name=Quarterstaff")
        rec = json.loads(body)["items"]["Quarterstaff"]
        self.assertEqual(rec["damage"], "1d6")
        self.assertEqual(rec["versatile"], "1d8")
        self.assertIn("Versatile", rec["properties"])

    # RRI-25e55fa optimizer #3: the catalog endpoint (the Market's source of truth) composes a
    # weapon's RANGE bracket so the Market/Stash inspector reads "100/400 ft" for a Heavy
    # Crossbow — the optimizer's "ranged weapon missing RANGE field" finding. Re-derived against
    # data/srd/srd524/Weapon.json (SRD 5.2 Heavy Crossbow is 100/400).
    def test_item_catalog_endpoint_composes_weapon_range(self):
        status, _ctype, body = self._get("/item-catalog?name=Heavy%20Crossbow&name=Dagger&name=Longsword")
        items = json.loads(body)["items"]
        hc = items["Heavy Crossbow"]
        self.assertTrue(hc["resolved"])
        self.assertEqual(hc["range"], 100)
        self.assertEqual(hc["rangeLong"], 400)
        self.assertEqual(hc["rangeDisplay"], "100/400 ft")
        # a thrown melee weapon reads its throwing bracket
        self.assertEqual(items["Dagger"]["rangeDisplay"], "20/60 ft")
        # a pure melee weapon has no range bracket -> empty display (the row is hidden)
        self.assertEqual(items["Longsword"]["rangeDisplay"], "")

    # RRI-25e55fa optimizer #3 (the "Value —" blank): the catalog endpoint already carries the
    # gp value string; a resolved weapon with a real cost must expose a non-blank value so the
    # inspector's Value row matches Price instead of reading "—".
    def test_item_catalog_endpoint_value_is_not_blank_for_priced_item(self):
        status, _ctype, body = self._get("/item-catalog?name=Longsword")
        rec = json.loads(body)["items"]["Longsword"]
        self.assertTrue(rec["resolved"])
        self.assertNotEqual(rec["value"], "—")
        self.assertIn("gp", rec["value"])

    def test_item_catalog_endpoint_unresolved_name_is_honest(self):
        status, _ctype, body = self._get("/item-catalog?name=Totally%20Made%20Up%20Gizmo")
        rec = json.loads(body)["items"]["Totally Made Up Gizmo"]
        self.assertFalse(rec["resolved"])

    def test_item_catalog_endpoint_batches_multiple_names(self):
        status, _ctype, body = self._get("/item-catalog?name=Longsword&name=Plate")
        items = json.loads(body)["items"]
        self.assertTrue(items["Longsword"]["resolved"])
        self.assertEqual(items["Longsword"]["damage"], "1d8")
        self.assertTrue(items["Plate"]["resolved"])
        self.assertEqual(items["Plate"]["ac"], 18)

    # F09-6 / #874: the catalog endpoint (the Market's source of truth) composes the SAME
    # honest armor dex-rule the Stash inspector carries — medium armor reads its DEX cap and
    # a shield reads its bonus, so the Market never re-exhibits the flat "AC 14"/"AC 2" bug.
    def test_item_catalog_endpoint_composes_armor_acdisplay(self):
        status, _ctype, body = self._get("/item-catalog?name=Breastplate&name=Shield")
        items = json.loads(body)["items"]
        bp = items["Breastplate"]
        self.assertTrue(bp["resolved"])
        self.assertEqual(bp["armorCategory"], "medium")
        self.assertEqual(bp["acDisplay"], "AC 14 + DEX (max +2)")
        sh = items["Shield"]
        self.assertTrue(sh["resolved"])
        self.assertEqual(sh["armorCategory"], "shield")
        self.assertEqual(sh["acDisplay"], "+2")  # a +N bonus, never the flat "AC 2"

    # Examine opens a PANEL (dialog), not a toast
    def test_examine_opens_a_panel_not_a_toast(self):
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        # the Examine action button opens the read-only panel state…
        self.assertIn("setExamineOpen(true)", src)
        self.assertIn('role="dialog"', src)
        # …and the right-click "Examine" raises the panel nonce instead of toasting.
        self.assertIn("setExamineNonce", src)
        # the old toast-only Examine is gone (the right-click Examine no longer toasts desc).
        self.assertNotIn(
            'onClick: () => toast({ kind: "item", title: ctxMenu.item.name, body: ctxMenu.item.desc })',
            src,
        )

    def test_inventory_renders_versatile_and_compare(self):
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        self.assertIn("two-handed", src)            # versatile die folded into the damage row
        self.assertIn("Versus Equipped", src)       # compare-to-equipped block
        self.assertIn("itemStatRows", src)
        self.assertIn("itemCompareRows", src)

    # RRI-25e55fa optimizer #5 (Stash/Market examine PARITY): the Stash inspector must enrich
    # its selected item from the SAME read-only /item-catalog endpoint the Market uses, so a
    # stash item missing a stat (range/value/properties the granted item didn't persist) backfills
    # from the catalog exactly like a Market ware — closing the "Stash thinner than Market" gap.
    def test_inventory_enriches_selected_item_from_item_catalog(self):
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        # the Stash fetches the catalog…
        self.assertIn("/item-catalog?", src)
        # …and merges it through the SAME shared enrichWare helper the Market uses.
        self.assertIn("window.enrichWare", src)

    def test_merchant_reads_item_catalog_and_enriches(self):
        _status, _ctype, body = self._get("/openworlds/screen-merchant.jsx")
        src = body.decode("utf-8")
        self.assertIn("/item-catalog?", src)        # the Market fetches the catalog
        self.assertIn("enrichWare", src)            # …and merges it into the detail pane
        self.assertIn("window.itemStatRows", src)   # rendering the real stat rows (AC/damage)
        self.assertIn("Versus ", src)               # compare block in the Market inspector


if __name__ == "__main__":
    unittest.main()
