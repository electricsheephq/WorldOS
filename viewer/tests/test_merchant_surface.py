"""Route + projection tests for /merchant-surface — the Market screen's LIVE supply chain
(#602) plus the Sell-tab → /inventory-surface wiring (#604).

Before this lane the Market screen (viewer/openworlds/screen-merchant.jsx) painted a hardcoded
demo MERCHANTS / GATE_MARKET_STOCK block and an always-empty sell stash. #602 wires the buy
side to the engine's REAL `is_merchant` canon NPCs + the bundled SRD item catalog; #604 points
the Sell tab at the existing /inventory-surface (the party's real pack), not a stub stash.

This lane asserts the route contract over HTTP against the SHIPPED baldurs-gate world, using the
real engine (the test interpreter carries the engine deps — same harness as test_roster_surface
/ test_bestiary_surface): a threaded server, GETs over http.client.

INVARIANT: the surface is a pure READER. It consumes the canonical `is_merchant` predicate
(content.find_canon_characters) + the SRD catalog (itemcatalog) and never writes engine state.
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


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_merchant", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, *args, **kwargs):  # silence access logging in tests
        pass


@unittest.skipIf(
    server._load_engine_server() is None,
    f"engine unavailable in this interpreter: {server._ENGINE_IMPORT_ERROR}",
)
class MerchantSurfaceTests(unittest.TestCase):
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

    def _get_json(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=15)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    # ── #602: the buy side — real is_merchant NPCs + the SRD catalog ─────────────

    def test_no_campaign_browses_default_world_merchants(self):
        status, surface = self._get_json("/merchant-surface")
        self.assertEqual(status, 200)
        self.assertEqual(surface.get("world_id"), "baldurs-gate")
        self.assertNotIn("error", surface)
        merchants = surface.get("merchants", [])
        self.assertTrue(merchants, "the shipped baldurs-gate world has dozens of is_merchant NPCs")
        # state authority is the engine; this surface is a pure reader (no write lane of its own).
        self.assertEqual(surface.get("state_authority"), "engine")

    def test_every_merchant_is_a_real_is_merchant_npc(self):
        # The surface must consume the canonical is_merchant predicate, never a hardcoded list.
        # Cross-check each returned merchant against the engine's own is_merchant slice by name.
        status, surface = self._get_json("/merchant-surface?limit=500")
        self.assertEqual(status, 200)
        returned = {m.get("name") for m in surface.get("merchants", [])}
        eng = server._load_engine_server()
        canonical = {
            r["name"]
            for r in eng.content_mod.find_canon_characters("baldurs-gate", is_merchant=True, limit=500)
        }
        self.assertTrue(returned, "merchants must be populated")
        self.assertTrue(
            returned <= canonical,
            f"every surface merchant must be a real is_merchant NPC; strays: {returned - canonical}",
        )

    def test_merchant_cards_carry_the_screen_fields(self):
        status, surface = self._get_json("/merchant-surface?limit=50")
        self.assertEqual(status, 200)
        merchants = surface.get("merchants", [])
        self.assertTrue(merchants)
        for m in merchants:
            for field in ("id", "name", "portrait_scope"):
                self.assertIn(field, m)
            # the portrait scope is keyed off the merchant id slug (what the screen renders by).
            self.assertEqual(m["portrait_scope"], "portrait-" + m["id"])

    def test_old_troutman_the_demo_dockside_merchant_is_a_real_npc(self):
        # The hardcoded demo opened on "Old Troutman"; he is a REAL is_merchant canon NPC in the
        # shipped world, so the live surface must offer him (the demo is now backed by real data).
        status, surface = self._get_json("/merchant-surface?limit=500")
        self.assertEqual(status, 200)
        ids = {m.get("id") for m in surface.get("merchants", [])}
        self.assertIn("old-troutman", ids)

    def test_catalog_is_the_real_srd_item_catalog(self):
        # Field shape on an unfiltered slice…
        status, surface = self._get_json("/merchant-surface?limit=10")
        self.assertEqual(status, 200)
        catalog = surface.get("catalog", [])
        self.assertTrue(catalog, "the surface must carry the SRD item catalog wares")
        for w in catalog:
            for field in ("id", "name", "type", "weight", "price"):
                self.assertIn(field, w)
        # …and the catalog is the REAL SRD index — a canonical name resolves through a query.
        _status, ls = self._get_json("/merchant-surface?q=Longsword&limit=50")
        ls_names = {w.get("name") for w in ls.get("catalog", [])}
        self.assertIn("Longsword", ls_names, "the SRD catalog must hold the canonical Longsword")

    def test_default_supply_is_mostly_priced_buyable_gear(self):
        # The "empty market" complaint: the raw alphabetical catalog slice is dominated by
        # PRICELESS magic items (price None → disabled Take), so only a handful are buyable. The
        # default (no-query) supply must lead with PRICED, mundane gear so the market reads stocked.
        status, surface = self._get_json("/merchant-surface?limit=40")
        self.assertEqual(status, 200)
        catalog = surface.get("catalog", [])
        self.assertTrue(catalog, "the default supply must carry wares")
        priced = [w for w in catalog if isinstance(w.get("price"), int) and w["price"] > 0]
        self.assertGreater(
            len(priced), len(catalog) // 2,
            f"the default merchant supply must be MOSTLY priced/buyable gear, "
            f"got {len(priced)}/{len(catalog)} priced",
        )

    def test_default_supply_leads_with_priced_before_priceless(self):
        # Sort contract: every priced ware sorts AHEAD of every priceless one in the default slice
        # (priceless items still appear, just after the buyable gear — never dropped).
        status, surface = self._get_json("/merchant-surface?limit=80")
        self.assertEqual(status, 200)
        flags = [bool(isinstance(w.get("price"), int) and w["price"] > 0)
                 for w in surface.get("catalog", [])]
        first_priceless = next((i for i, p in enumerate(flags) if not p), len(flags))
        self.assertFalse(
            any(flags[first_priceless:]),
            "no priced ware may appear after the first priceless one in the default supply",
        )

    def test_catalog_query_filters_wares(self):
        status, surface = self._get_json("/merchant-surface?q=longsword&limit=50")
        self.assertEqual(status, 200)
        names = [w.get("name", "") for w in surface.get("catalog", [])]
        self.assertTrue(names, "a 'longsword' query should match at least the Longsword")
        self.assertTrue(
            all("longsword" in n.lower() for n in names),
            f"the catalog filter must narrow to matching wares; got {names}",
        )

    def test_priceless_ware_carries_a_null_price_not_a_fabricated_zero(self):
        # F09-3 honesty: a magic item with no listed SRD price must surface price=None, never a
        # silent 0 gp (which would let the screen mis-sell a Bag of Holding for free).
        status, surface = self._get_json("/merchant-surface?q=bag%20of%20holding&limit=10")
        self.assertEqual(status, 200)
        bag = next((w for w in surface.get("catalog", []) if w.get("name") == "Bag of Holding"), None)
        self.assertIsNotNone(bag, "Bag of Holding is in the bundled SRD catalog")
        self.assertIsNone(bag["price"], "a priceless item must carry a null price, never 0 gp")

    # ── pure reader: no write/mutation lane ──────────────────────────────────────

    def test_surface_exposes_no_write_lane(self):
        status, surface = self._get_json("/merchant-surface")
        self.assertEqual(status, 200)
        # the merchant surface is presentation + read only; it must not advertise a write lane.
        self.assertNotIn("write_lane", surface)


# ── served-source guards: the screen wiring the route test can't observe ───────


class _QuietWiringHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class MerchantScreenWiringTests(unittest.TestCase):
    """The screen-merchant.jsx wiring asserted against a live server: the Market reads
    /merchant-surface (#602) and the Sell tab reads /inventory-surface (#604), and the dead
    demo MERCHANTS / GATE_MARKET_STOCK block + the always-empty merchantStash are gone."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        _QuietWiringHandler.campaign_id = ""
        _QuietWiringHandler.transcript_path = ""
        _QuietWiringHandler.chat_path = ""
        _QuietWiringHandler.pinned = False
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietWiringHandler)
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

    def _get_text(self, path: str) -> str:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.read().decode("utf-8")
        finally:
            conn.close()

    def test_market_reads_the_merchant_surface(self):
        src = self._get_text("/openworlds/screen-merchant.jsx")
        self.assertIn("/merchant-surface", src)

    def test_sell_tab_reads_the_inventory_surface(self):
        # #604: the Sell tab lists the party's REAL pack from /inventory-surface — the equipped
        # compare already fetched it; the sell list now reuses the same surface.
        src = self._get_text("/openworlds/screen-merchant.jsx")
        self.assertIn("/inventory-surface", src)

    def test_dead_demo_stock_and_merchant_block_are_gone(self):
        # the always-empty merchantStash stub + the hardcoded demo stock array are removed
        # (the live surfaces replace them).
        src = self._get_text("/openworlds/screen-merchant.jsx")
        self.assertNotIn("state?.merchantStash", src)
        self.assertNotIn("GATE_MARKET_STOCK", src)


# ── #604 behavioral guard: the Sell tab populates sellStash FROM /inventory-surface d.stash ──
#
# MerchantScreenWiringTests.test_sell_tab_reads_the_inventory_surface (above) only asserts the
# "/inventory-surface" substring is present in the served source — but the equipped-compare block
# (#756) already fetched that URL BEFORE #604, so that guard stays GREEN even when #604 is reverted.
# It does not isolate the change it is named for.
#
# The real #604 wiring is: inside the /inventory-surface effect, the Sell tab populates `sellStash`
# from the response's `d.stash` (setSellStash on a `d.stash`-derived list with a defaulted item
# `type`), replacing the always-empty `state.merchantStash` stub. This class drives that real effect
# under the Node+Babel pure harness (mirroring test_merchant_haggle.py's _HARNESS) and asserts the
# behaviour — so it goes RED if screen-merchant.jsx is reverted to the pre-#925 base.

_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_MERCHANT = _OPENWORLDS / "screen-merchant.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


# The screen returns JSX that references child components (Panel/Img/…) this harness does not load,
# so the component's RETURN throws — but every React.useEffect has already registered by then, which
# is all we need. We render ScreenMerchant ONCE (tolerating that throw), capture the effects, drive
# them against a URL-aware fetch where only /inventory-surface carries a stash payload, then assert a
# setter received the stash-derived list. Setters are pure recorders, so there is no re-render loop.
_SELL_STASH_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const Babel = require(%(babel)s);

const setterCalls = [];
const effects = [];
const React = {
  useState: (i) => {
    const init = (typeof i === 'function') ? i() : i;
    return [init, (next) => { setterCalls.push((typeof next === 'function') ? next(init) : next); }];
  },
  useEffect: (fn) => { effects.push(fn); },
  useRef: (i) => ({ current: i }),
  useCallback: (fn) => fn,
  createElement: () => null,
  Fragment: 'F',
};

const sandbox = {
  React,
  document: { addEventListener(){}, removeEventListener(){}, visibilityState: 'visible',
              getElementById: () => ({}), head: { appendChild() {} }, createElement: () => ({}) },
  // URL-aware fetch: ONLY /inventory-surface returns a stash payload; the other surfaces
  // (/character-surface, /merchant-surface, /item-catalog) resolve to a benign null so their effects
  // no-op. The stash item carries NO `type`, so #604's `type || "common"` default is observable.
  fetch: (url) => {
    if (String(url).indexOf('/inventory-surface') === 0) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        party: [],
        stash: [{ id: 'sell-test-blade', name: 'Test Blade', price: 5 }],
      }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(null) });
  },
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  encodeURIComponent, URLSearchParams, Promise, JSON, Set, Array, Object, String, Boolean, Number, Math,
  console,
};
sandbox.window = sandbox;
// The only helpers the component body calls unguarded before the inventory effect registers.
sandbox.currencyTotalGp = () => 0;
sandbox.partyPurse = () => ({});
vm.createContext(sandbox);

function load(p) {
  const src = fs.readFileSync(p, 'utf8');
  const code = Babel.transform(src, { presets: ['react'], filename: p }).code;
  vm.runInContext(code, sandbox);
}
load(%(merchant)s);

const win = sandbox.window;
(async () => {
  try {
    win.ScreenMerchant({ onNavigate: () => {}, state: {}, setState: () => {} });
  } catch (e) { /* undefined JSX child refs — effects already captured */ }
  for (const fn of effects) { try { fn(); } catch (e) { /* keep draining the rest */ } }
  // Flush the fetch().then().then() microtask chain.
  await new Promise((res) => setImmediate(res));
  await new Promise((res) => setImmediate(res));
  const sell = setterCalls.find(
    (v) => Array.isArray(v) && v.some((it) => it && it.id === 'sell-test-blade'));
  process.stdout.write(JSON.stringify({ captured_effects: effects.length, sell_stash: sell || null }));
})();
"""


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + drive the Market effect")
class SellStashPopulationTests(unittest.TestCase):
    """#604 behavioral guard: the Sell tab's sellStash is populated FROM the /inventory-surface
    response's `d.stash` — the wiring #604 added. Drives the real effect under the Node+Babel
    harness so the test goes RED if #604 is reverted (unlike the substring guard above, which the
    pre-#604 equipped-compare fetch of the SAME URL already satisfied)."""

    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_MERCHANT, _BABEL):
            assert p.exists(), f"missing {p}"

    def _drive(self) -> dict:
        program = _SELL_STASH_HARNESS % {
            "babel": json.dumps(str(_BABEL)),
            "merchant": json.dumps(str(_MERCHANT)),
        }
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"node harness failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return json.loads(proc.stdout)

    def test_sell_stash_is_populated_from_the_inventory_surface_stash(self):
        result = self._drive()
        # Harness sanity only: the render reached + registered effects (a totally broken harness
        # registers none — that would make the behavioural assertion below misleading). This is NOT
        # the #604 guard and is deliberately not coupled to the exact effect count.
        self.assertGreater(
            result["captured_effects"], 0,
            "no effects registered — the harness failed to render ScreenMerchant, not a wiring revert",
        )
        sell = result["sell_stash"]
        self.assertIsNotNone(
            sell,
            "#604: the Sell tab must populate sellStash from the /inventory-surface response's "
            "`d.stash` (setSellStash). It is None — the d.stash wiring is missing/reverted.",
        )
        names = {it.get("name") for it in sell}
        self.assertIn("Test Blade", names, "the mocked /inventory-surface stash item must reach sellStash")
        blade = next(it for it in sell if it.get("id") == "sell-test-blade")
        self.assertEqual(
            blade.get("type"), "common",
            "#604 defaults a typeless backpack item to 'common' so the kind filter/pill render",
        )


if __name__ == "__main__":
    unittest.main()
