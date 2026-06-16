"""Route + projection tests for GET /feat-catalog — the browsable SRD feat list the level-up
planner's feat pane reads (the planner's one real gap: 17 SRD feats existed but the feat choice
was a BLIND free-text box). The viewer bridges to the engine-owned PURE ``featcatalog`` module
(the same data the engine ``feats`` tool returns) and projects each feat into
``{name, desc, prerequisite, type}``.

INVARIANT: the surface is a pure READER. It exposes NO write/mutate lane — the chosen feat name
still rides the existing /move level_up relay; the engine stays the sole writer. It never
fabricates a feat (an engine-import failure returns an empty list with an ``error``).
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
_SPEC = importlib.util.spec_from_file_location("viewer_server_featcatalog", _SERVER_PATH)
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
class FeatCatalogTests(unittest.TestCase):
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

    # --- the projection helper -------------------------------------------------

    def test_response_lists_all_srd_feats_with_planner_fields(self):
        surface = server.feat_catalog_response("")
        self.assertNotIn("error", surface)
        self.assertEqual(surface["count"], 17)
        feats = surface["feats"]
        self.assertTrue(feats)
        for f in feats:
            for field in ("name", "desc", "prerequisite", "type"):
                self.assertIn(field, f)
        names = {f["name"] for f in feats}
        self.assertIn("Alert", names)
        self.assertIn("Grappler", names)

    def test_response_query_filters(self):
        surface = server.feat_catalog_response("grappler")
        self.assertGreaterEqual(surface["count"], 1)
        names = {f["name"] for f in surface["feats"]}
        self.assertIn("Grappler", names)
        grappler = next(f for f in surface["feats"] if f["name"] == "Grappler")
        # the prerequisite rides through so the picker can show it
        self.assertIn("13", grappler["prerequisite"])

    # --- the HTTP route --------------------------------------------------------

    def test_route_returns_the_feat_catalog(self):
        status, surface = self._get_json("/feat-catalog")
        self.assertEqual(status, 200)
        self.assertEqual(surface["count"], 17)
        names = {f["name"] for f in surface["feats"]}
        self.assertIn("Magic Initiate", names)
        # each feat carries its effect text (the planner shows it on each option)
        alert = next(f for f in surface["feats"] if f["name"] == "Alert")
        self.assertTrue(alert["desc"], "each feat must carry its effect text")

    def test_route_query_param_filters_wares(self):
        status, surface = self._get_json("/feat-catalog?q=fighting%20style")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(surface["count"], 1)
        names = {f["name"] for f in surface["feats"]}
        self.assertIn("Archery", names)
        self.assertTrue(
            all("fighting style" in (f["name"] + f["prerequisite"] + f["desc"]).lower()
                for f in surface["feats"]),
            "the feat filter must narrow to matching feats",
        )

    def test_route_exposes_no_write_lane(self):
        status, surface = self._get_json("/feat-catalog")
        self.assertEqual(status, 200)
        # the feat catalog is read only; it must not advertise a write lane.
        self.assertNotIn("write_lane", surface)
        self.assertEqual(surface.get("state_authority"), "engine")


if __name__ == "__main__":
    unittest.main()
