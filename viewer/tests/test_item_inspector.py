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

    def test_merchant_reads_item_catalog_and_enriches(self):
        _status, _ctype, body = self._get("/openworlds/screen-merchant.jsx")
        src = body.decode("utf-8")
        self.assertIn("/item-catalog?", src)        # the Market fetches the catalog
        self.assertIn("enrichWare", src)            # …and merges it into the detail pane
        self.assertIn("window.itemStatRows", src)   # rendering the real stat rows (AC/damage)
        self.assertIn("Versus ", src)               # compare block in the Market inspector


if __name__ == "__main__":
    unittest.main()
