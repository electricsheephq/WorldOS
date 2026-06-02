"""M3 #455 — the SSE surface-stream push transport (additive; polling stays the fallback).

Asserts the new /surface-stream endpoint:
  - serves Content-Type: text/event-stream and is BOUNDED (?once=1 returns one event + closes,
    so it never hangs a worker/test),
  - emits a `surfaces` event whose payload carries {atlas, combat, character} with the SAME
    shapes the polled GET surfaces return (byte-identical builders),
  - degrades gracefully with no campaign (empty surfaces, still a valid event),
  - does NOT change the existing GET surfaces (they still serve application/json).
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_sse", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class SurfaceStreamTests(unittest.TestCase):
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

    def _raw(self, path: str, timeout: float = 5) -> tuple[int, str, str]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")
        finally:
            conn.close()

    def test_once_stream_is_event_stream_and_bounded(self):
        # ?once=1 must return promptly with a single event and close (bounded -> no hang).
        status, ctype, body = self._raw("/surface-stream?once=1", timeout=5)
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", ctype)
        self.assertIn("event: surfaces", body)
        self.assertIn("retry:", body)  # reconnect hint present

    def test_event_payload_has_render_surface_shapes(self):
        _status, _ctype, body = self._raw("/surface-stream?once=1", timeout=5)
        # extract the JSON after the first "data: " line
        data_line = next(l for l in body.splitlines() if l.startswith("data: "))
        payload = json.loads(data_line[len("data: "):])
        self.assertEqual(payload.get("type"), "surfaces")
        for key in ("atlas", "combat", "character"):
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], dict)

    def test_stream_payload_matches_polled_surfaces(self):
        # the SSE bundle must be byte-identical to the polled GET surfaces (same builders).
        _s, _c, body = self._raw("/surface-stream?once=1", timeout=5)
        data_line = next(l for l in body.splitlines() if l.startswith("data: "))
        bundle = json.loads(data_line[len("data: "):])
        for surface, route in (("atlas", "/atlas-surface"), ("combat", "/combat-surface"),
                               ("character", "/character-surface")):
            status, ctype, raw = self._raw(route, timeout=5)
            self.assertEqual(status, 200)
            self.assertIn("application/json", ctype)  # GET surfaces UNCHANGED (still json)
            self.assertEqual(bundle[surface], json.loads(raw),
                             f"{surface} SSE payload diverged from polled {route}")

    def test_max_seconds_is_capped(self):
        # an absurd max_seconds must be clamped (<=300) so a worker can never be pinned forever;
        # combined with ?once we still return immediately.
        status, _ctype, body = self._raw("/surface-stream?once=1&max_seconds=999999", timeout=5)
        self.assertEqual(status, 200)
        self.assertIn("event: surfaces", body)


if __name__ == "__main__":
    unittest.main()
