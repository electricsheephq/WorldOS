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


class OpenWorldsStaticRouteTests(unittest.TestCase):
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
        status, headers, body = self._get_with_headers(path)
        return status, headers.get("Content-Type", ""), body

    def _get_with_headers(self, path: str) -> tuple[int, http.client.HTTPMessage, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def _status(self, path: str) -> int:
        return self._get(path)[0]

    def test_openworlds_without_trailing_slash_redirects_to_directory_route(self):
        status, headers, body = self._get_with_headers("/openworlds")

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/openworlds/")
        self.assertEqual(body, b"")

    def test_openworlds_index_uses_local_runtime_assets(self):
        status, ctype, body = self._get("/openworlds/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b'vendor/react-18.3.1.development.js', body)
        self.assertIn(b'vendor/react-dom-18.3.1.development.js', body)
        self.assertIn(b'vendor/babel-standalone-7.29.0.min.js', body)
        self.assertIn(b'vendor/google-fonts.css', body)
        self.assertIn(b'native-bridge.js', body)
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
        self.assertEqual(config["campaign_catalog"], "/openworlds/campaigns.json")
        self.assertFalse(config["demo_data"])
        self.assertTrue(config["demo_data_fallback"])

    def test_openworlds_static_assets_are_same_origin_and_local(self):
        status, ctype, body = self._get("/openworlds/vendor/google-fonts.css")

        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(b"font-family: Cinzel", body)
        self.assertIn(b"fonts/", body)
        self.assertNotIn(b"https://", body)

    def test_openworlds_combat_screen_binds_viewer_combat_surface(self):
        status, ctype, body = self._get("/openworlds/screen-combat.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('fetch("/combat-surface', source)
        self.assertIn('fetch("/move"', source)
        self.assertIn("window.combatSurfaceFromCampaign", source)
        self.assertNotIn("TOKENS.map", source)
        self.assertNotIn("setTokens", source)

    def test_openworlds_map_screen_binds_viewer_atlas_surface(self):
        status, ctype, body = self._get("/openworlds/screen-map.jsx")

        self.assertEqual(status, 200)
        self.assertIn("text/babel", ctype)
        source = body.decode("utf-8")
        self.assertIn('fetch("/atlas-surface', source)
        self.assertIn('fetch("/move"', source)
        self.assertIn("window.atlasSurfaceFromCampaign", source)
        self.assertNotIn("state?.locations", source)

    def test_openworlds_rejects_path_traversal(self):
        self.assertEqual(self._status("/openworlds/../server.py"), 404)
        self.assertEqual(self._status("/openworlds/%2e%2e/server.py"), 404)
        self.assertEqual(self._status("/openworlds/vendor/../../server.py"), 404)

    def test_openworlds_campaigns_projects_current_state_without_private_fields(self):
        campaign_dir = self._tmp / "campaigns" / "camp_live"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_live",
                "title": "Road After Moonrise",
                "ruleset": "SRD 5.2",
                "world_id": "baldurs-gate",
                "day": 12,
                "time_of_day": "dusk",
                "current_location_id": "last-light",
                "summary": "The party reached the inn and caught its breath.",
                "locations": {"last-light": {"name": "Last Light Inn"}},
                "party": ["hero", "jaheira"],
                "characters": {
                    "hero": {"name": "Tav", "kind": "player", "current_hp": 22, "max_hp": 30},
                    "jaheira": {"name": "Jaheira", "kind": "companion", "current_hp": 34, "max_hp": 34},
                },
                "quests": {"q1": {"status": "active"}},
                "notes": "private note must not leave the snapshot",
                "scenes": [{"dm_notes": "hidden agenda"}],
                "lore": ["private canon recall input"],
            },
        )
        (campaign_dir / "sessions").mkdir()
        (campaign_dir / "sessions" / "sess_1.jsonl").write_text("{}", encoding="utf-8")
        _QuietHandler.campaign_id = "camp_live"

        status, ctype, body = self._get("/openworlds/campaigns.json")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        catalog = json.loads(body.decode("utf-8"))
        self.assertEqual(catalog["state_authority"], "engine")
        self.assertEqual(catalog["write_lane"], "/move")
        self.assertEqual(catalog["total"], 1)
        campaign = catalog["campaigns"][0]
        self.assertEqual(campaign["id"], "play:state:camp_live")
        self.assertEqual(campaign["campaign_id"], "camp_live")
        self.assertEqual(campaign["source"], "play")
        self.assertEqual(campaign["runId"], "state")
        self.assertEqual(campaign["title"], "Road After Moonrise")
        self.assertEqual(campaign["world"], "baldurs-gate")
        self.assertEqual(campaign["day"], "Day 12 · dusk")
        self.assertEqual(campaign["location"], "Last Light Inn")
        self.assertEqual(campaign["region"], "Last Light Inn")
        self.assertEqual(campaign["system"], "SRD 5.2")
        self.assertEqual(campaign["sessions"], 1)
        self.assertTrue(campaign["current"])
        self.assertTrue(campaign["canResume"])
        self.assertFalse(campaign["readOnly"])
        self.assertEqual(campaign["resumeUrl"], "/dashboard?campaign=camp_live")
        self.assertEqual([p["name"] for p in campaign["party"]], ["Tav", "Jaheira"])
        self.assertEqual(campaign["recap"], "The party reached the inn and caught its breath.")
        encoded = json.dumps(campaign)
        self.assertNotIn("private note", encoded)
        self.assertNotIn("hidden agenda", encoded)
        self.assertNotIn("private canon", encoded)
        self.assert_no_private_keys(json.loads(encoded))

    def test_openworlds_campaigns_includes_repo_play_state_and_qa_runs_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        play_campaign = repo_root / "play-state" / "play-20260525" / "campaigns" / "camp_play"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            play_campaign,
            {
                "id": "camp_play",
                "title": "Owner Save",
                "world_id": "baldurs-gate",
                "current_location_id": "baldurs-gate",
                "locations": {"baldurs-gate": {"name": "Baldur's Gate"}},
                "party": [],
                "characters": {},
            },
        )
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Save",
                "world_id": "baldurs-gate",
                "day": 3,
                "party": [],
                "characters": {},
            },
        )

        status, _ctype, body = self._get("/openworlds/campaigns.json")

        self.assertEqual(status, 200)
        campaigns = json.loads(body.decode("utf-8"))["campaigns"]
        by_id = {c["id"]: c for c in campaigns}
        self.assertIn("play:play-20260525:camp_play", by_id)
        self.assertIn("qa:wave3-red:camp_qa", by_id)
        self.assertEqual(by_id["play:play-20260525:camp_play"]["provider"], "Local")
        self.assertEqual(by_id["qa:wave3-red:camp_qa"]["provider"], "QA")
        self.assertTrue(by_id["play:play-20260525:camp_play"]["readOnly"])
        self.assertTrue(by_id["qa:wave3-red:camp_qa"]["readOnly"])
        self.assertFalse(by_id["play:play-20260525:camp_play"]["canResume"])
        self.assertFalse(by_id["qa:wave3-red:camp_qa"]["canResume"])
        self.assertEqual(by_id["qa:wave3-red:camp_qa"]["monitorUrl"], "/monitor")

    def test_session_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_table"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_table",
                "title": "Table Save",
                "summary": "A quiet table scene.",
                "world_id": "baldurs-gate",
                "current_location_id": "lower-city",
                "locations": {
                    "lower-city": {
                        "name": "Lower City",
                        "description": "Cobbles shine after rain.",
                        "notes": "private route note",
                    },
                },
                "party": ["hero"],
                "characters": {
                    "hero": {
                        "id": "hero",
                        "name": "Tav",
                        "kind": "player",
                        "current_hp": 12,
                        "max_hp": 20,
                        "notes": "private character note",
                    },
                },
                "dm_notes": "hidden route agenda",
            },
        )

        status, ctype, body = self._get("/session-surface?campaign=camp_table")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_table")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["title"], "Table Save")
        self.assertEqual(surface["location"]["name"], "Lower City")
        self.assertEqual(surface["party"][0]["name"], "Tav")
        encoded = json.dumps(surface)
        self.assertNotIn("private route", encoded)
        self.assertNotIn("private character", encoded)
        self.assertNotIn("hidden route", encoded)
        self.assert_no_private_keys(surface)

    def test_session_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Table Save",
                "summary": "A QA-only session surface.",
                "world_id": "baldurs-gate",
                "current_location_id": "qa-location",
                "locations": {"qa-location": {"name": "QA Location"}},
                "party": ["hero"],
                "characters": {"hero": {"id": "hero", "name": "QA Tav", "kind": "player"}},
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {
                "id": "camp_live",
                "title": "Live Table Save",
                "party": [],
                "characters": {},
            },
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/session-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Table Save")
        self.assertEqual(surface["location"]["name"], "QA Location")
        self.assertEqual(surface["party"][0]["name"], "QA Tav")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def test_combat_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_combat"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_combat",
                "title": "Combat Save",
                "summary": "A fight at the gate.",
                "current_location_id": "gate",
                "locations": {
                    "gate": {"name": "Basilisk Gate", "notes": "private map bypass"},
                },
                "party": ["hero"],
                "characters": {
                    "hero": {
                        "id": "hero",
                        "name": "Tav",
                        "kind": "player",
                        "current_hp": 12,
                        "max_hp": 20,
                        "armor_class": 16,
                    },
                    "gob": {
                        "id": "gob",
                        "name": "Goblin",
                        "kind": "monster",
                        "armor_class": 13,
                        "notes": "private monster note",
                    },
                },
                "combat": {
                    "active": True,
                    "round": 2,
                    "turn_index": 0,
                    "order": [
                        {"character_id": "hero", "initiative": 18},
                        {"character_id": "gob", "initiative": 9},
                    ],
                },
                "dm_notes": "private route agenda",
            },
        )

        status, ctype, body = self._get("/combat-surface?campaign=camp_combat")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_combat")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["encounter"]["name"], "Basilisk Gate")
        self.assertTrue(surface["encounter"]["active"])
        self.assertEqual([t["id"] for t in surface["tokens"]], ["hero", "gob"])
        self.assertNotIn("ac", surface["tokens"][1])
        encoded = json.dumps(surface)
        self.assertNotIn("private map", encoded)
        self.assertNotIn("private monster", encoded)
        self.assertNotIn("private route", encoded)
        self.assert_no_private_keys(surface)

    def test_combat_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Combat Save",
                "current_location_id": "qa-arena",
                "locations": {"qa-arena": {"name": "QA Arena"}},
                "party": ["hero"],
                "characters": {
                    "hero": {"id": "hero", "name": "QA Tav", "kind": "player"},
                    "gob": {"id": "gob", "name": "QA Goblin", "kind": "monster"},
                },
                "combat": {
                    "active": True,
                    "round": 4,
                    "turn_index": 1,
                    "order": [
                        {"character_id": "hero", "initiative": 18},
                        {"character_id": "gob", "initiative": 9},
                    ],
                },
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {
                "id": "camp_live",
                "title": "Live Combat Save",
                "party": [],
                "characters": {},
            },
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/combat-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Combat Save")
        self.assertEqual(surface["encounter"]["round"], 4)
        self.assertEqual(surface["selectedTokenId"], "gob")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def test_atlas_surface_route_projects_selected_campaign_safely(self):
        campaign_dir = self._tmp / "campaigns" / "camp_map"
        self._write_snapshot(
            campaign_dir,
            {
                "id": "camp_map",
                "title": "Atlas Save",
                "world_id": "baldurs-gate",
                "current_location_id": "gate",
                "locations": {
                    "gate": {
                        "name": "Basilisk Gate",
                        "connections": ["market", "hidden"],
                        "visited": True,
                        "tags": ["town", "rest"],
                        "notes": "private gate note",
                    },
                    "market": {
                        "name": "Rain Market",
                        "connections": ["gate"],
                        "discovered": True,
                    },
                    "hidden": {
                        "name": "Hidden Crypt",
                        "hidden": True,
                        "notes": "private crypt note",
                    },
                },
                "quests": {
                    "q1": {"title": "Find the Seller", "status": "active", "location_id": "market"},
                },
                "dm_notes": "private atlas agenda",
            },
        )

        status, ctype, body = self._get("/atlas-surface?campaign=camp_map")

        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_map")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["current_location"]["name"], "Basilisk Gate")
        self.assertEqual([loc["id"] for loc in surface["known_locations"]], ["gate", "market"])
        self.assertEqual(surface["edges"], [{"from": "gate", "to": "market"}])
        self.assertTrue(surface["camp_available"])
        encoded = json.dumps(surface)
        self.assertNotIn("Hidden Crypt", encoded)
        self.assertNotIn("private", encoded)
        self.assert_no_private_keys(surface)

    def test_atlas_surface_route_projects_catalog_run_read_only(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        self._write_snapshot(
            qa_campaign,
            {
                "id": "camp_qa",
                "title": "QA Atlas Save",
                "current_location_id": "qa-gate",
                "locations": {
                    "qa-gate": {"name": "QA Gate", "connections": ["qa-market"], "visited": True},
                    "qa-market": {"name": "QA Market", "visited": True},
                },
            },
        )
        self._write_snapshot(
            self._tmp / "campaigns" / "camp_live",
            {"id": "camp_live", "title": "Live Atlas Save", "party": [], "characters": {}},
        )
        _QuietHandler.campaign_id = "camp_live"

        status, _ctype, body = self._get("/atlas-surface?source=qa&run=wave3-red&campaign=camp_qa")

        self.assertEqual(status, 200)
        surface = json.loads(body.decode("utf-8"))
        self.assertEqual(surface["campaign_id"], "camp_qa")
        self.assertEqual(surface["title"], "QA Atlas Save")
        self.assertEqual(surface["current_location"]["name"], "QA Gate")
        self.assertFalse(surface["can_act"])
        self.assertFalse(surface["is_live_view"])

    def _write_snapshot(self, campaign_dir: Path, payload: dict) -> None:
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "snapshot.json").write_text(json.dumps(payload), encoding="utf-8")

    def assert_no_private_keys(self, value) -> None:
        private_keys = {"notes", "scenes", "lore", "dm_notes", "sealed_agenda", "agenda"}
        if isinstance(value, dict):
            for key, child in value.items():
                self.assertNotIn(key, private_keys)
                self.assert_no_private_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_private_keys(child)


if __name__ == "__main__":
    unittest.main()
