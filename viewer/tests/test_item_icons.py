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
        self.assertIn('"climbing-kit": "rope"', src)
        self.assertIn('"wax-candle-6": "candle"', src)
        self.assertIn('"sharpened-greataxe-edge": "greataxe"', src)
        self.assertIn('"bandage-roll": ""', src)
        self.assertIn("Object.prototype.hasOwnProperty.call(ITEM_ART_ALIASES, s)", src)

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


class ForgePolishTests(ItemIconTests):
    """screen-forge.jsx audit polish (issue #252): F-04 unlock affordance,
    F-07 known/rumoured divider, F-08 tier numeral plate."""

    def _forge_src(self) -> str:
        status, ctype, body = self._get("/openworlds/screen-forge.jsx")
        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        return body.decode("utf-8")

    def test_forge_locked_recipes_carry_unlock_copy(self):
        """F-04: each locked recipe must record how it is learned (an `unlock` string),
        so the right pane and row tooltip can show a concrete affordance, not a dead end."""
        src = self._forge_src()
        # The locked recipes declare an `unlock:` hint…
        self.assertIn("unlock:", src)
        self.assertIn("Candlekeep", src)
        self.assertIn("Dammon", src)

    def test_forge_renders_unlock_affordance_and_tooltip(self):
        """F-04: the unlock copy is rendered in the blueprint pane and as the locked row's title."""
        src = self._forge_src()
        # Right-pane affordance reads the selected recipe's unlock hint.
        self.assertIn("selected.unlock", src)
        self.assertIn("How it is learned", src)
        # Locked recipe row exposes a hover tooltip with the unlock copy.
        self.assertIn("title={r.locked", src)

    def test_forge_separates_known_from_rumoured(self):
        """F-07: a 'Rumoured' divider must separate known recipes from locked ones."""
        src = self._forge_src()
        self.assertIn("Rumoured", src)

    def test_forge_tier_uses_numeral_plate_not_plain_pill(self):
        """F-08: the tier marker must be the wax-seal TierPlate (aria-labelled), not a bare Pill."""
        src = self._forge_src()
        self.assertIn("function TierPlate", src)
        self.assertIn("<TierPlate tier=", src)
        # The plate carries an accessible label for the otherwise opaque roman numeral.
        self.assertIn('aria-label={"Tier "', src)
        # The list row no longer renders the recipe tier via a bare <Pill>.
        self.assertNotIn("<Pill>{r.tier}</Pill>", src)


if __name__ == "__main__":
    unittest.main()
