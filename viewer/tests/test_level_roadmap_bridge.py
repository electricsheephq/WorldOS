"""Viewer-bridge tests for the read-only level-up ROADMAP surface (#882).

`server.level_roadmap_response` validates the campaign/character scope, then calls the
engine-owned read-only `level_roadmap` projection and packages it for the /character
"see your path to 20" panel. These mirror test_build_options_bridge.py: assert the
projection survives the bridge unmutated, the campaign snapshot is never written, and the
guards (unsafe campaign, missing character, PC at the cap) behave honestly.
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(server)


class LevelRoadmapBridgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _snapshot_path(self, campaign_id: str) -> Path:
        return self._tmp / "campaigns" / campaign_id / "snapshot.json"

    def test_level_roadmap_response_projects_upcoming_levels_without_mutating_snapshot(self):
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Roadmap")["id"]
        character_id = engine.create_character(
            campaign_id,
            "Ren",
            kind="player",
            class_name="Fighter",
            level=5,
            apply_srd_defaults=True,
            abilities={"strength": 16, "dexterity": 14, "constitution": 12},
        )["id"]
        before = json.loads(self._snapshot_path(campaign_id).read_text(encoding="utf-8"))

        response = server.level_roadmap_response(campaign_id, character_id, 20)
        after = json.loads(self._snapshot_path(campaign_id).read_text(encoding="utf-8"))

        self.assertTrue(response["ok"])
        self.assertEqual(response["source"], "engine.level_roadmap")
        self.assertEqual(response["campaign_id"], campaign_id)
        roadmap = response["roadmap"]
        self.assertEqual(roadmap["primary_class"], "fighter")
        self.assertEqual(roadmap["from"], {"total_level": 5, "class_level": 5})
        rows = roadmap["roadmap"]
        # Projects 6..20.
        self.assertEqual([r["level"] for r in rows], list(range(6, 21)))
        by_level = {r["level"]: r for r in rows}
        # Fighter ASI/feat levels survive the bridge flagged.
        for asi_lvl in (6, 8, 12, 14, 16, 19):
            self.assertTrue(by_level[asi_lvl]["is_asi_or_feat"], asi_lvl)
        self.assertFalse(by_level[7]["is_asi_or_feat"])
        # Proficiency bonus rises across the bridge.
        self.assertEqual(by_level[9]["prof_bonus"], 4)
        # The snapshot is byte-identical — read-only.
        self.assertEqual(after, before)

    def test_level_roadmap_response_window_caps_at_through_level(self):
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Window")["id"]
        character_id = engine.create_character(
            campaign_id, "Ren", kind="player", class_name="Fighter", level=5,
            apply_srd_defaults=True,
        )["id"]

        response = server.level_roadmap_response(campaign_id, character_id, 8)

        self.assertTrue(response["ok"])
        self.assertEqual([r["level"] for r in response["roadmap"]["roadmap"]], [6, 7, 8])

    def test_level_roadmap_response_empty_at_cap_is_not_an_error(self):
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Capped")["id"]
        character_id = engine.create_character(
            campaign_id, "Capstone", kind="player", class_name="Fighter", level=20,
            apply_srd_defaults=True, abilities={"strength": 18, "constitution": 14},
        )["id"]

        response = server.level_roadmap_response(campaign_id, character_id, 20)

        self.assertTrue(response["ok"])
        self.assertEqual(response["roadmap"]["roadmap"], [])

    def test_level_roadmap_response_rejects_unsafe_campaign_id(self):
        response = server.level_roadmap_response("../secret", "pc", 20)

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "invalid_campaign")
        self.assertIn("campaign", response["errors"][0])

    def test_level_roadmap_response_rejects_missing_character(self):
        engine = server._engine_server()
        campaign_id = engine.create_campaign("NoChar")["id"]

        response = server.level_roadmap_response(campaign_id, "does-not-exist", 20)

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "invalid_character")


if __name__ == "__main__":
    unittest.main()
