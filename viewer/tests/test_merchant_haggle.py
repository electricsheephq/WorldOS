"""#755 — the Market haggle price-cell concat + the 'Take' double-fire.

Two rc2-adversarial Market bugs, both in viewer/openworlds/screen-merchant.jsx:

  1. The haggle price cell rendered the LIST price and the DISCOUNTED price adjacently, so
     "24" + "23" read as one run-together number "2423gp". The fix renders the two prices as
     separated parts (an explicit arrow between them) — a pure helper so the parts can never
     collapse into one string.
  2. The 'Take' button (add-to-cart) had NO debounce: a fast double-click queued the SAME ware
     twice. The Confirm button already guarded with submittingRef; 'Take' now guards too.

The price-cell helper is PURE, so it runs under the same Node+Babel harness the other Market
helper tests use (test_item_inspector.py). The 'Take' debounce is render wiring the pure harness
can't see, so a served-source guard asserts the guard ref exists.
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
_MERCHANT = _OPENWORLDS / "screen-merchant.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


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


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the Market helpers")
class HagglePriceCellTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        for p in (_MERCHANT, _BABEL):
            assert p.exists(), f"missing {p}"

    def _run(self, script: str) -> object:
        loads_js = f"load({json.dumps(str(_MERCHANT))});"
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

    def test_haggle_cell_separates_list_and_discounted_price(self):
        """A haggled cell (list 24, discounted 23) must yield SEPARATED parts — never the
        run-together "2423". The helper returns the listed price, the discounted price, and a
        flag/separator so the two numbers can never render adjacent."""
        parts = self._run("return win.marketPriceCellParts(24, 23);")
        self.assertEqual(parts["list"], 24)
        self.assertEqual(parts["price"], 23)
        self.assertTrue(parts["haggled"], "a real discount must flag as haggled")
        self.assertTrue(parts["separated"], "the list+discount prices must be flagged separated")

    def test_no_haggle_cell_shows_a_single_price_no_struck_list(self):
        # No discount (haggledPrice null) -> one price, no struck list, never a concat.
        parts = self._run("return win.marketPriceCellParts(24, null);")
        self.assertEqual(parts["price"], 24)
        self.assertFalse(parts["haggled"])
        self.assertFalse(parts["separated"], "a single price must not flag a struck-list separator")

    def test_haggle_to_same_price_is_not_treated_as_a_discount(self):
        # A 0% (or rounding no-op) haggle where discounted == list must NOT render the struck
        # list (that's where the adjacency bug bit) — one clean price.
        parts = self._run("return win.marketPriceCellParts(24, 24);")
        self.assertEqual(parts["price"], 24)
        self.assertFalse(parts["separated"], "list==discount must collapse to one price, no struck run-together")


# ── served-source guard: the 'Take' debounce the pure harness can't observe ────


class _QuietHandler:
    pass


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_haggle", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietWiringHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class TakeDebounceWiringTests(unittest.TestCase):
    """#755 part 2: the 'Take' (add-to-cart) button must debounce a double-fire, asserted
    against the served source (a render guard the pure-helper harness can't see)."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
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
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _get_text(self, path: str) -> str:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.read().decode("utf-8")
        finally:
            conn.close()

    def test_take_button_has_a_debounce_guard(self):
        src = self._get_text("/openworlds/screen-merchant.jsx")
        # the add-to-cart 'Take' handler debounces a double-fire via a guarded helper (addToCart),
        # mirroring submittingRef on Confirm — not a bare inline setCart([...cart, …]).
        self.assertIn("addToCart", src)
        self.assertIn("takingRef", src)

    def test_price_cell_uses_the_separated_helper(self):
        src = self._get_text("/openworlds/screen-merchant.jsx")
        self.assertIn("marketPriceCellParts", src)


if __name__ == "__main__":
    unittest.main()
