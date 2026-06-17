"""M2 — the GT2 backdrop-isometric renderer serves + its backdrop render-profile is contract-valid.

Mirrors test_render_tilemap.py. Asserts the net-new GT2 sub-app files
(viewer/openworlds/render/backdrop.html, renderer-backdrop.js, render-profile.backdrop.example.json)
serve with correct MIME (zero server change), Phaser is vendored-not-CDN, and the backdrop
render-profile is a valid instance of the M0 schema with scene_kind="backdrop" + the contract
invariants (theater|zone positioning, named zones not x,y, walkmask/depth data confined to the
phaser renderer block, never core).
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_backdrop", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)

_RENDER = _REPO / "viewer" / "openworlds" / "render"
_SCHEMA = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"
_PROFILE = _RENDER / "render-profile.backdrop.example.json"


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class BackdropServeTests(unittest.TestCase):
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
            r = conn.getresponse()
            return r.status, r.headers.get("Content-Type", ""), r.read()
        finally:
            conn.close()

    def test_backdrop_html_serves(self):
        status, ctype, body = self._get("/openworlds/render/backdrop.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"GT2", body)
        # vendored Phaser, not CDN
        self.assertIn(b"vendor/phaser-3.80.1.min.js", body)
        self.assertNotIn(b"cdn.jsdelivr.net", body)

    def test_backdrop_js_and_profile_serve(self):
        status, ctype, body = self._get("/openworlds/render/renderer-backdrop.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertTrue(body)
        status, ctype, body = self._get("/openworlds/render/render-profile.backdrop.example.json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        json.loads(body)

    def test_no_spike_or_tmp_refs_in_backdrop_profile(self):
        text = _PROFILE.read_text()
        for bad in ("/tmp/", "/Volumes/", "_comment", "decision-worldos-render-contract"):
            self.assertNotIn(bad, text)


class BackdropProfileContractTests(unittest.TestCase):
    def setUp(self):
        if not _SCHEMA.exists() or not _PROFILE.exists():
            self.skipTest("schema or backdrop profile not present")
        self.schema = json.loads(_SCHEMA.read_text())
        self.ex = json.loads(_PROFILE.read_text())

    def test_scene_kind_is_backdrop_and_positioning_v1(self):
        core = self.ex["core"]
        self.assertEqual(core["scene_kind"], "backdrop")
        self.assertIn(core["positioning"], ("theater", "zone"))

    def test_strict_no_disallowed_keys(self):
        self.assertEqual(set(self.ex) - set(self.schema["properties"]), set())
        core_schema = self.schema["properties"]["core"]
        self.assertEqual(set(self.ex["core"]) - set(core_schema["properties"]), set())

    def test_fk_ids_present_and_zones_named(self):
        core = self.ex["core"]
        self.assertTrue(core["locations"] and all("engine_location_id" in l for l in core["locations"]))
        self.assertTrue(core["actors"] and all("engine_actor_id" in a for a in core["actors"]))
        forbidden = {"x", "y", "col", "row", "grid_x", "grid_y", "coords", "position"}
        for loc in core["locations"]:
            self.assertTrue(forbidden.isdisjoint(loc))
            for z in loc.get("zones", []):
                self.assertTrue(isinstance(z, str) and z.strip())

    def test_walkmask_and_depth_data_confined_to_phaser_block(self):
        # The contract INVARIANT: coordinate/walkmask/depth-shaped data lives ONLY in
        # renderer_profiles.phaser, NEVER in core (core is renderer-agnostic).
        core = self.ex["core"]
        self.assertFalse(any(k in core for k in ("walkmask_ref", "depth_bands", "backdrop_layout")))
        ph = self.ex["renderer_profiles"]["phaser"]
        self.assertIn("walkmask_ref", ph)
        self.assertIn("depth_bands", ph)
        # walkmask is renderer-owned presentation, not engine coordinates
        self.assertEqual(ph["walkmask_ref"], "procedural")

    def test_full_jsonschema_when_available(self):
        jsonschema = __import__("pytest").importorskip("jsonschema")
        jsonschema.validate(self.ex, self.schema)


if __name__ == "__main__":
    unittest.main()
