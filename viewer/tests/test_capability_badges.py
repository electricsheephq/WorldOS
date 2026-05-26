"""
test_capability_badges.py — assert that each display-only OpenWorlds screen
contains a <CapabilityBadge> component, signalling to players that the screen
is a prototype backed by demo data rather than a live engine read-model.

Mirrors the style of test_openworlds_static.py.
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


class CapabilityBadgeTests(unittest.TestCase):
    """Each display-only screen must declare a CapabilityBadge so players know
    the screen is not yet backed by live engine state."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        self._old_here = server._HERE
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
        server._HERE = self._old_here

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    def _assert_has_capability_badge(self, screen_path: str) -> None:
        status, ctype, body = self._get(screen_path)
        self.assertEqual(status, 200, f"{screen_path} should return HTTP 200")
        self.assertIn("text/babel", ctype, f"{screen_path} should be served as text/babel")
        source = body.decode("utf-8")
        self.assertIn(
            "CapabilityBadge",
            source,
            f"{screen_path} must render a <CapabilityBadge> so players know it is display-only",
        )

    def test_bestiary_screen_has_capability_badge(self):
        self._assert_has_capability_badge("/openworlds/screen-bestiary.jsx")

    def test_create_screen_has_capability_badge(self):
        self._assert_has_capability_badge("/openworlds/screen-create.jsx")

    def test_forge_screen_has_capability_badge(self):
        self._assert_has_capability_badge("/openworlds/screen-forge.jsx")

    def test_merchant_screen_has_capability_badge(self):
        self._assert_has_capability_badge("/openworlds/screen-merchant.jsx")

    def test_seed_screen_has_capability_badge(self):
        self._assert_has_capability_badge("/openworlds/screen-seed.jsx")


if __name__ == "__main__":
    unittest.main()
