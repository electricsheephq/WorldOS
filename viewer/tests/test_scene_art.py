"""W2e: screen-map.jsx and screen-dialogue.jsx render scene art via the Img bridge.

Mirrors test_openworlds_static.py style: spin up the viewer's HTTP server against a
temp state dir and assert that both JSX files contain the ``Img scope=`` pattern wired
to location-keyed scopes, confirming the render bridge is used for scene art.
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


class SceneArtRenderBridgeTests(unittest.TestCase):
    """Assert that scene art is wired via the Img render bridge in the map and parley screens."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        self._old_here = server._HERE
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
        server._HERE = self._old_here

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    def test_map_screen_renders_scene_art_via_img_bridge(self):
        """screen-map.jsx must use <Img scope={...}> for location scene art (W2e).

        The atlas sidebar shows a painted location illustration for the selected location
        (scope = location id from atlas surface's known_locations[].id).  A banner-style
        Img is also shown for the current/selected location at the top of the map panel.
        The hover tooltip vignette uses Img as well.
        """
        status, ctype, body = self._get("/openworlds/screen-map.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        # The Img component is used for scene art, not just a raw Placeholder
        self.assertIn("Img scope=", source)
        # The scope is wired to the location id (selected.id) from the atlas surface
        self.assertIn("selected.id", source)
        # Graceful placeholder fallback is inherited from the Img component itself
        self.assertIn("Img", source)

    def test_parley_screen_renders_scene_art_via_img_bridge(self):
        """screen-dialogue.jsx (Parley tab) must use <Img scope={...}> as scene backdrop (W2e).

        The live parley menu uses location_id from the surface when present, otherwise
        falls back to portrait-<anchor_npc_id> from the event block so the conversation
        always has a visual backdrop when art has been generated.
        """
        status, ctype, body = self._get("/openworlds/screen-dialogue.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        # The Img component is used for scene art in the parley screen
        self.assertIn("Img scope=", source)
        # The scope resolves location_id first, then portrait fallback
        self.assertIn("location_id", source)
        self.assertIn("portrait-", source)
        # The anchor NPC fallback scope is wired to the event block
        self.assertIn("anchor_npc_id", source)


if __name__ == "__main__":
    unittest.main()
