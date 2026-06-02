"""M3 #453/#442 — the UGC render-profile store: validated, versioned, additive, traversal-safe.

A render-profile is PRESENTATION, not game state, so persisting UGC profiles needs no engine
change and never touches the engine's sole-writership. This asserts:
  - save_profile VALIDATES + GATES against the frozen contract: an invalid profile is REJECTED
    and nothing is written; a valid one persists as v1, then v2 (append-only versioning).
  - load_profile returns the latest by default and an exact version on request; list_profiles
    enumerates stored games.
  - traversal-proof: a game_id / owner containing '../' or '/' is slugified, never escapes root.
  - the HTTP routes round-trip: POST /ugc/profile (save-intent) -> GET /ugc/profiles (list) ->
    GET /ugc/profile (load); a bad profile is rejected by the route with the gate reason.
"""

import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path


_VIEWER = Path(__file__).resolve().parents[1]
_REPO = _VIEWER.parent
_BL = _VIEWER / "openworlds" / "render" / "build_loop"
_SCHEMA_PATH = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"
_SEED_PATH = _BL / "example-seed.json"

# load the store + the generator (sibling-importable; mirror server.py's bootstrap)
if str(_VIEWER) not in sys.path:
    sys.path.insert(0, str(_VIEWER))
if str(_BL) not in sys.path:
    sys.path.insert(0, str(_BL))
import ugc_store  # noqa: E402


def _gen():
    spec = importlib.util.spec_from_file_location("gp_for_ugc", _BL / "generate_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


generate_profile = _gen()


def _good_profile():
    seed = json.loads(_SEED_PATH.read_text())
    return generate_profile.strip_unmapped(
        generate_profile.generate_profile(seed, date="2026-06-02"))


class UgcStoreUnitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ugc"
        self.prof = _good_profile()

    def test_save_validates_and_versions(self):
        r1 = ugc_store.save_profile(self.root, self.prof, owner="alice")
        self.assertTrue(r1["accepted"])
        self.assertEqual(r1["version"], 1)
        r2 = ugc_store.save_profile(self.root, self.prof, owner="alice")
        self.assertEqual(r2["version"], 2)  # append-only
        # load latest + exact version
        latest = ugc_store.load_profile(self.root, self.prof["game_id"], owner="alice")
        self.assertEqual(latest["game_id"], self.prof["game_id"])
        v1 = ugc_store.load_profile(self.root, self.prof["game_id"], owner="alice", version=1)
        self.assertEqual(v1, self.prof)

    def test_invalid_profile_rejected_and_not_written(self):
        bad = json.loads(json.dumps(self.prof))
        bad["core"]["actors"][0]["art"]["scope_key"] = ""  # empty art -> gate fails
        r = ugc_store.save_profile(self.root, bad, owner="bob")
        self.assertFalse(r["accepted"])
        self.assertIsNone(r["version"])
        self.assertIsNone(ugc_store.load_profile(self.root, bad["game_id"], owner="bob"))

    def test_coords_in_core_rejected(self):
        bad = json.loads(json.dumps(self.prof))
        bad["core"]["locations"][0]["x"] = 3
        r = ugc_store.save_profile(self.root, bad, owner="bob")
        self.assertFalse(r["accepted"])

    def test_list_profiles(self):
        ugc_store.save_profile(self.root, self.prof, owner="alice")
        listing = ugc_store.list_profiles(self.root)
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["owner"], "alice")
        self.assertEqual(listing[0]["game_id"], self.prof["game_id"])
        self.assertEqual(listing[0]["scene_kind"], "backdrop")
        self.assertEqual(listing[0]["latest_version"], 1)

    def test_resolve_under_rejects_escape(self):
        # the realpath+commonpath barrier (CWE-22 fix): a segment that resolves outside root raises.
        with self.assertRaises(ValueError):
            ugc_store._resolve_under(self.root, "..", "..", "etc")
        # a normal slugified segment resolves cleanly under root
        p = ugc_store._resolve_under(self.root, "alice", "game-1")
        self.assertTrue(str(p.resolve()).startswith(str(self.root.resolve())))

    def test_traversal_is_slugified(self):
        evil = json.loads(json.dumps(self.prof))
        evil["game_id"] = "../../etc/passwd"
        r = ugc_store.save_profile(self.root, evil, owner="../../root")
        self.assertTrue(r["accepted"])
        # nothing was written outside the store root
        written = list(self.root.rglob("v*.json"))
        for p in written:
            self.assertTrue(str(p.resolve()).startswith(str(self.root.resolve())))


class UgcRouteTests(unittest.TestCase):
    def setUp(self):
        # load server.py fresh with an isolated state dir
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        spec = importlib.util.spec_from_file_location("viewer_server_ugc", _VIEWER / "server.py")
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)

        class _QuietHandler(self.server._Handler):
            def log_message(self, fmt, *args):
                return
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
        self._httpd = self.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
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

    def _req(self, method, path, body=None):
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            return r.status, json.loads(r.read().decode() or "{}")
        finally:
            conn.close()

    def test_route_round_trip(self):
        prof = _good_profile()
        # POST save-intent
        status, body = self._req("POST", "/ugc/profile", {"profile": prof, "owner": "alice"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["version"], 1)
        self.assertIn("human_gate_queue", body)
        # GET list
        status, body = self._req("GET", "/ugc/profiles")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["profiles"]), 1)
        self.assertEqual(body["profiles"][0]["game_id"], prof["game_id"])
        # GET load
        status, loaded = self._req("GET", f"/ugc/profile?game_id={prof['game_id']}&owner=alice")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["game_id"], prof["game_id"])
        self.assertEqual(loaded["core"]["scene_kind"], "backdrop")

    def test_route_rejects_bad_profile(self):
        prof = _good_profile()
        prof["core"]["actors"][0]["art"]["scope_key"] = ""  # gate fail
        status, body = self._req("POST", "/ugc/profile", {"profile": prof})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("failed_gates", body)

    def test_route_missing_profile(self):
        status, body = self._req("POST", "/ugc/profile", {"owner": "x"})
        self.assertFalse(body["ok"])
        status, body = self._req("GET", "/ugc/profile?game_id=nope&owner=alice")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
