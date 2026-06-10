"""App-status image probe soundness + /image X-Image-Outcome classes (audit F11-1b/F11-2).

F11-2: the old probe was ``bool(scope and _latest_descriptor(scope))`` — vacuously true on
ANY parsed descriptor, including the cached null-provider placeholder that ``_serve_image``
then 404s, and it covered the scene scope only (zero portrait coverage). These tests pin the
hardened contract:

  - a payload-less (null placeholder) SCENE descriptor must report ``image_probe_ok: false``;
  - a party portrait whose descriptor exists but cannot be served fails the probe, while a
    portrait with NO art at all stays a designed silhouette miss (does not fail it);
  - matrix invariant: for every probed scope, probe class "servable" == (GET /image < 400);
  - /image responses carry the additive ``X-Image-Outcome`` header
    (served | no-art | placeholder | error) with status/body unchanged (F11-1b).

Mirrors the test_openworlds_static.py harness: a real HTTP server over a temp state dir.
"""

import base64
import http.client
import importlib.util
import json
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
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401 - silence test HTTP logs
        return


SCENE_SCOPE = "location:loc-lower-city"
PNG_BYTES = b"not-really-png-but-bytes-are-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


class ImageProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._saved_env = {}
        for var in (
            "CLAWDND_STATE_DIR",
            "WORLDOS_ART_REPO_ROOT",
            "CLAWDND_ART_REPO_ROOT",
            "WORLDOS_REPO_ROOT",
            "CLAWDND_REPO_ROOT",
            "WORLDOS_PLAYER_MOVES",
            "CLAWDND_PLAYER_MOVES",
            "WORLDOS_PROVIDER",
            "CLAWDND_PROVIDER",
        ):
            self._saved_env[var] = os.environ.pop(var, None)
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        # Isolate from any real checkout's gitignored _private art (2k+ ingested dirs on
        # dev boxes would make probe outcomes nondeterministic via the fuzzy slug match).
        art_root = self._tmp / "art-root"
        art_root.mkdir()
        os.environ["WORLDOS_ART_REPO_ROOT"] = str(art_root)
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
        for var, value in self._saved_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    # ---- helpers -------------------------------------------------------------

    def _get(self, path: str) -> tuple[int, http.client.HTTPMessage, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def _write_campaign(self) -> None:
        campaign_dir = self._tmp / "campaigns" / "camp_live"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "snapshot.json").write_text(
            json.dumps(
                {
                    "id": "camp_live",
                    "title": "Probe Save",
                    "active_session_id": "session_live",
                    "world_id": "baldurs-gate",
                    "current_location_id": "loc-lower-city",
                    "locations": {
                        "loc-lower-city": {"id": "loc-lower-city", "name": "Lower City"},
                    },
                    "party": ["hero"],
                    "characters": {
                        "hero": {
                            "id": "hero",
                            "name": "Probe Hero",
                            "kind": "player",
                            "current_hp": 8,
                            "max_hp": 8,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        _QuietHandler.campaign_id = "camp_live"

    def _write_descriptor(self, scope: str, desc: dict) -> None:
        cache_dir = self._tmp / "images" / server._safe_scope(scope)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "cache.json").write_text(json.dumps({"scope": scope, **desc}), encoding="utf-8")

    def _app_status_health(self) -> dict:
        status, _headers, body = self._get("/app-status?campaign=camp_live")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        return payload["health"]

    def _image_status_and_outcome(self, scope: str) -> tuple[int, str]:
        status, headers, _body = self._get(f"/image?scope={scope}")
        return status, headers.get("X-Image-Outcome", "")

    def assert_probe_matches_serve(self, health: dict) -> None:
        """Matrix invariant (F11-2): probe class 'servable' == GET /image status < 400."""
        probe = health["image_probe"]
        for scope in probe["probed"]:
            status, _outcome = self._image_status_and_outcome(scope)
            self.assertEqual(
                scope in probe["servable"],
                status < 400,
                f"probe/serve disagree for {scope}: status={status} probe={probe}",
            )

    # ---- F11-2: probe soundness ----------------------------------------------

    def test_null_placeholder_scene_descriptor_fails_probe(self):
        # The F11-2 red test: a cached payload-less null-provider placeholder parses as a
        # descriptor, so the old probe reported image_probe_ok:true while /image 404s the
        # very same scope. The probe must require a SERVABLE outcome.
        self._write_campaign()
        self._write_descriptor(
            SCENE_SCOPE, {"placeholder": True, "provider": "null", "status": "ready"}
        )

        health = self._app_status_health()

        self.assertFalse(health["image_probe_ok"])
        self.assertIn(SCENE_SCOPE, health["image_probe"]["unservable"])
        status, outcome = self._image_status_and_outcome(SCENE_SCOPE)
        self.assertEqual(status, 404)
        self.assertEqual(outcome, "placeholder")
        self.assert_probe_matches_serve(health)

    def test_servable_scene_descriptor_passes_probe_and_no_art_portrait_does_not_fail_it(self):
        self._write_campaign()
        self._write_descriptor(SCENE_SCOPE, {"bytes_b64": PNG_B64, "mime_type": "image/png"})

        health = self._app_status_health()

        self.assertTrue(health["image_probe_ok"])
        probe = health["image_probe"]
        self.assertIn(SCENE_SCOPE, probe["servable"])
        # Party coverage: the hero has NO art anywhere — a designed silhouette miss,
        # recorded honestly but NOT a probe failure.
        self.assertIn("portrait-hero", probe["probed"])
        self.assertIn("portrait-hero", probe["no_art"])
        self.assertEqual(probe["unservable"], [])
        status, outcome = self._image_status_and_outcome(SCENE_SCOPE)
        self.assertEqual(status, 200)
        self.assertEqual(outcome, "served")
        self.assert_probe_matches_serve(health)

    def test_unservable_party_portrait_fails_probe_even_with_servable_scene(self):
        # Portraits are the bulk of recorded /image 404s (rc1 bugs.ndjson) — a portrait
        # descriptor that exists but cannot be served is an UNEXPECTED outcome.
        self._write_campaign()
        self._write_descriptor(SCENE_SCOPE, {"bytes_b64": PNG_B64, "mime_type": "image/png"})
        self._write_descriptor(
            "portrait-hero", {"placeholder": True, "provider": "null", "status": "ready"}
        )

        health = self._app_status_health()

        self.assertFalse(health["image_probe_ok"])
        self.assertIn("portrait-hero", health["image_probe"]["unservable"])
        self.assert_probe_matches_serve(health)

    def test_missing_scene_descriptor_keeps_probe_false(self):
        # No descriptor at all for the scene: same verdict as before the fix (false) —
        # the gate evidence cannot ride a scene that has no art.
        self._write_campaign()

        health = self._app_status_health()

        self.assertFalse(health["image_probe_ok"])
        self.assertIn(SCENE_SCOPE, health["image_probe"]["no_art"])
        status, outcome = self._image_status_and_outcome(SCENE_SCOPE)
        self.assertEqual(status, 404)
        self.assertEqual(outcome, "no-art")
        self.assert_probe_matches_serve(health)

    # ---- F11-1b: X-Image-Outcome classes ---------------------------------------

    def test_image_outcome_header_classes(self):
        self._write_campaign()
        # served (inline bytes)
        self._write_descriptor("portrait-served", {"bytes_b64": PNG_B64, "mime_type": "image/png"})
        # served (302 redirect to a remote url)
        self._write_descriptor("portrait-remote", {"url": "https://example.invalid/face.png"})
        # placeholder (descriptor with no payload at all — the null placeholder)
        self._write_descriptor("portrait-null", {"placeholder": True, "provider": "null"})
        # error (payload present but UNEXPECTEDLY unservable: path outside the containment roots)
        outside = self._tmp / "outside-roots.png"
        outside.write_bytes(PNG_BYTES)
        self._write_descriptor("portrait-escape", {"path": str(outside)})

        cases = {
            "portrait-served": (200, "served"),
            "portrait-remote": (302, "served"),
            "portrait-null": (404, "placeholder"),
            "portrait-escape": (404, "error"),
            "portrait-not-a-scope": (404, "no-art"),
        }
        for scope, (want_status, want_outcome) in cases.items():
            with self.subTest(scope=scope):
                status, outcome = self._image_status_and_outcome(scope)
                self.assertEqual(status, want_status)
                self.assertEqual(outcome, want_outcome)


if __name__ == "__main__":
    unittest.main()
