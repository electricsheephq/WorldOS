import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import importlib.util


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt, *args):  # noqa: D401 - silence test HTTP logs
        return


class OpenWorldsStaticRouteTests(unittest.TestCase):
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
        host, port = self._httpd.server_address
        self._base = f"http://{host}:{port}"

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state

    def _get(self, path):
        with urllib.request.urlopen(f"{self._base}{path}", timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def _status(self, path):
        try:
            return self._get(path)[0]
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            return code

    def test_openworlds_index_uses_local_runtime_assets(self):
        status, ctype, body = self._get("/openworlds/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'vendor/react-18.3.1.development.js', body)
        self.assertIn(b'vendor/react-dom-18.3.1.development.js', body)
        self.assertIn(b'vendor/babel-standalone-7.29.0.min.js', body)
        self.assertIn(b'vendor/google-fonts.css', body)
        self.assertNotIn(b"https://unpkg.com", body)
        self.assertNotIn(b"https://fonts.googleapis.com", body)
        self.assertNotIn(b"tweaks-panel.jsx", body)

    def test_openworlds_config_is_browser_safe_metadata(self):
        status, ctype, body = self._get("/openworlds/config.json")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        config = json.loads(body.decode("utf-8"))
        self.assertEqual(config["surface"], "openworlds")
        self.assertEqual(config["state_authority"], "engine")
        self.assertEqual(config["write_lane"], "/move")
        self.assertTrue(config["demo_data"])

    def test_openworlds_static_assets_are_same_origin_and_local(self):
        status, ctype, body = self._get("/openworlds/vendor/google-fonts.css")

        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(b"font-family: 'Cinzel'", body)
        self.assertIn(b"fonts/", body)
        self.assertNotIn(b"https://", body)

    def test_openworlds_rejects_path_traversal(self):
        self.assertEqual(self._status("/openworlds/../server.py"), 404)
        self.assertEqual(self._status("/openworlds/%2e%2e/server.py"), 404)
        self.assertEqual(self._status("/openworlds/vendor/../../server.py"), 404)


if __name__ == "__main__":
    unittest.main()
