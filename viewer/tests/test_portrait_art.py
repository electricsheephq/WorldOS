"""W2d: PC/companion portrait render-bridge tests.

Assert that screen-character.jsx and screen-table.jsx wire the <Img> render-bridge
component for PC portraits using scope="portrait-<character_id>", mirroring the
pattern already present in screen-relations.jsx (W2a).
"""

import http.client
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        return


class PortraitArtRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
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
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    def test_screen_character_uses_img_render_bridge_for_pc_portraits(self):
        # screen-character.jsx must use <Img scope="portrait-…"> in the party rail
        # (roster thumbnails) and in the hero header card — not bare <Placeholder>.
        status, ctype, body = self._get("/openworlds/screen-character.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("Img scope=", source)
        self.assertIn("portrait-", source)

    def test_screen_character_portrait_scope_does_not_collide_with_create_gallery_scope(self):
        # screen-create.jsx owns a gallery helper named portraitScope(index). The character
        # sheet must not use the same global helper name for party members, or the later-loaded
        # create screen overwrites it and Heroes falls back to portrait placeholders.
        status, ctype, body = self._get("/openworlds/screen-character.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("function characterPortraitScope(p)", source)
        self.assertIn("scope={characterPortraitScope(p)}", source)
        self.assertIn("scope={characterPortraitScope(hero)}", source)
        self.assertNotIn("function portraitScope(p)", source)

    def test_screen_table_uses_img_render_bridge_for_party_portraits(self):
        # screen-table.jsx must use <Img scope="portrait-…"> in the PartyRow component
        # for each PC in the party list / turn order panel.
        status, ctype, body = self._get("/openworlds/screen-table.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn("Img scope=", source)
        self.assertIn("portrait-", source)


if __name__ == "__main__":
    unittest.main()
