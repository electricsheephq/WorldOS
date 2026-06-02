"""W2c: item-icon render-bridge tests.

Asserts that screen-inventory.jsx uses the <Img scope=…> component with
the "item-" scope convention, mirroring test_openworlds_static.py style.
"""

import http.client
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_item_icons", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class ItemIconTests(unittest.TestCase):
    """screen-inventory.jsx must wire <Img scope=…> for item art (W2c)."""

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

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    def test_inventory_screen_uses_img_component(self):
        """screen-inventory.jsx must reference the <Img …> render-bridge component."""
        status, ctype, body = self._get("/openworlds/screen-inventory.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        src = body.decode("utf-8")
        self.assertIn("Img scope=", src)

    def test_inventory_screen_uses_item_scope_prefix(self):
        """Every item icon scope must start with 'item-' per the engine convention."""
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        self.assertIn("item-", src)

    def test_inventory_screen_has_item_scope_helper(self):
        """itemScope() helper must be present and delegate to the 'item-' prefix."""
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        # The helper function that assembles the scope string must exist.
        self.assertIn("itemScope", src)
        self.assertIn("item-", src)

    def test_inventory_screen_has_slug_helper(self):
        """slug() name-normaliser must be present for fallback scopes."""
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        self.assertIn("function slug(", src)

    def test_inventory_screen_img_has_framed_detail_icon(self):
        """ItemDetail must render a framed <Img> (72×72) for the selected item."""
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        # Both the Img call and the framed prop must appear in the detail section context.
        self.assertIn("itemScope(item)", src)
        self.assertIn("framed", src)

    def test_inventory_screen_img_used_in_item_slot(self):
        """ItemSlot grid cells must use <Img scope={itemScope(item)} …> for item art."""
        _status, _ctype, body = self._get("/openworlds/screen-inventory.jsx")
        src = body.decode("utf-8")
        # ItemSlot now renders Img, not only a glyph span.
        self.assertIn("scope={itemScope(item)}", src)

    def test_shared_item_art_alias_helper_exists(self):
        """Shared itemArtScope aliases table-qualified item names to reusable art."""
        _status, _ctype, body = self._get("/openworlds/chrome.jsx")
        src = body.decode("utf-8")
        self.assertIn("window.itemArtScope", src)
        self.assertIn('"travel-rations": "rations"', src)
        self.assertIn('"iron-lantern": "lantern"', src)
        self.assertIn('"wax-candle-6": "candle"', src)
        self.assertIn('"sharpened-greataxe-edge": "greataxe"', src)

    def test_merchant_uses_shared_item_art_scope(self):
        """Merchant item icons must use the shared alias helper before falling back."""
        _status, _ctype, body = self._get("/openworlds/screen-merchant.jsx")
        src = body.decode("utf-8")
        self.assertIn("window.itemArtScope", src)
        self.assertIn("return window.itemArtScope(item)", src)

    def test_forge_uses_shared_item_art_scope(self):
        """Forge recipe icons must use shared item-art aliases before falling back."""
        _status, _ctype, body = self._get("/openworlds/screen-forge.jsx")
        src = body.decode("utf-8")
        self.assertIn("window.itemArtScope", src)
        self.assertIn("return window.itemArtScope(name)", src)


if __name__ == "__main__":
    unittest.main()
