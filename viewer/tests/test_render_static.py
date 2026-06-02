"""M0 — the served Phaser render/ thin-client serves correctly + stays contract-clean.

Mirrors test_openworlds_static.py's in-process ThreadingHTTPServer harness. Asserts the
new viewer/openworlds/render/ subtree (promoted from spikes/m0-phaser-thin-client/) is
served by viewer/server.py with the right MIME types and zero server change, that Phaser
is loaded from the LOCAL vendor copy (no runtime CDN), and that the bundled
render-profile.example.json stays a valid instance of the M0 schema
(docs/roadmap/contracts/render-profile.schema.json) with the contract's invariants
(theater|zone positioning, named zones not x,y).
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
_SPEC = importlib.util.spec_from_file_location("viewer_server_render", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)

_RENDER = _REPO / "viewer" / "openworlds" / "render"
_SCHEMA = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class RenderStaticRouteTests(unittest.TestCase):
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
            r = conn.getresponse()
            return r.status, r.headers.get("Content-Type", ""), r.read()
        finally:
            conn.close()

    # ---- serving (zero server change) ----

    def test_render_index_serves_html(self):
        status, ctype, body = self._get("/openworlds/render/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"renderer-as-thin-client", body)

    def test_render_js_assets_serve_with_js_mime(self):
        for name in ("renderer.js", "surface-client.js"):
            with self.subTest(asset=name):
                status, ctype, body = self._get(f"/openworlds/render/{name}")
                self.assertEqual(status, 200)
                self.assertIn("javascript", ctype)
                self.assertTrue(body)

    def test_render_profile_and_fixtures_serve_as_json(self):
        for path in (
            "/openworlds/render/render-profile.example.json",
            "/openworlds/render/fixtures/atlas-surface.json",
            "/openworlds/render/fixtures/combat-surface.json",
            "/openworlds/render/fixtures/character-surface.json",
        ):
            with self.subTest(path=path):
                status, ctype, body = self._get(path)
                self.assertEqual(status, 200)
                self.assertIn("application/json", ctype)
                json.loads(body)  # parses

    def test_phaser_is_vendored_locally_not_cdn(self):
        # The render index must load Phaser from the local vendor copy — no runtime CDN.
        status, _ctype, body = self._get("/openworlds/render/")
        self.assertEqual(status, 200)
        self.assertIn(b"vendor/phaser-3.80.1.min.js", body)
        self.assertNotIn(b"cdn.jsdelivr.net", body)
        self.assertNotIn(b"https://unpkg.com", body)
        # And the vendored file actually serves.
        status, ctype, vbody = self._get("/openworlds/vendor/phaser-3.80.1.min.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertGreater(len(vbody), 500_000)  # the real ~1.18MB Phaser, not a stub

    # ---- contract cleanliness (no leakage of spike/dev refs) ----

    def test_render_profile_has_no_spike_or_tmp_refs(self):
        text = (_RENDER / "render-profile.example.json").read_text()
        for bad in ("/tmp/", "/Volumes/", "_comment", "decision-worldos-render-contract"):
            self.assertNotIn(bad, text, f"stale ref {bad!r} leaked into the served profile")


class RenderProfileContractTests(unittest.TestCase):
    """The served render-profile must stay a valid instance of the M0 schema and honor the
    contract invariants (theater|zone only; named zones, never x,y). Dependency-free strict
    checks (jsonschema may be absent), with a bonus full validate when it's installed."""

    def setUp(self):
        if not _SCHEMA.exists() or not (_RENDER / "render-profile.example.json").exists():
            self.skipTest("schema or render profile not present in this checkout")
        self.schema = json.loads(_SCHEMA.read_text())
        self.ex = json.loads((_RENDER / "render-profile.example.json").read_text())

    def test_required_top_and_core_fields(self):
        self.assertEqual(self.ex.get("schema_version"), 1)
        core = self.ex["core"]
        self.assertIn(core["scene_kind"], ("tilemap", "backdrop"))
        self.assertIn(core["positioning"], ("theater", "zone"))  # grid excluded from v1
        self.assertTrue(core["locations"] and all("engine_location_id" in l for l in core["locations"]))
        self.assertTrue(core["actors"] and all("engine_actor_id" in a for a in core["actors"]))

    def test_no_disallowed_keys_at_strict_objects(self):
        top_allowed = set(self.schema["properties"])
        self.assertEqual(set(self.ex) - top_allowed, set())
        core_schema = self.schema["properties"]["core"]
        self.assertEqual(set(self.ex["core"]) - set(core_schema["properties"]), set())

    def test_zones_are_named_strings_not_coordinates(self):
        forbidden = {"x", "y", "col", "row", "grid_x", "grid_y", "coords", "position"}
        for loc in self.ex["core"]["locations"]:
            self.assertTrue(forbidden.isdisjoint(loc), f"coordinate leaked into a location: {loc}")
            for z in loc.get("zones", []):
                self.assertTrue(isinstance(z, str) and z.strip())

    def test_full_jsonschema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        jsonschema.validate(self.ex, self.schema)


if __name__ == "__main__":
    unittest.main()
