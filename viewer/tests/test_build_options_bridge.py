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


class BuildOptionsBridgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state

    def test_build_options_response_uses_engine_planner_without_mutating_snapshot(self):
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Planner")["id"]
        character_id = engine.create_character(
            campaign_id,
            "Ren",
            kind="player",
            class_name="Fighter",
            level=3,
            apply_srd_defaults=True,
            abilities={"strength": 16, "dexterity": 14, "constitution": 12},
        )["id"]
        snapshot_path = self._tmp / "campaigns" / campaign_id / "snapshot.json"
        before = json.loads(snapshot_path.read_text(encoding="utf-8"))

        response = server.build_options_response(campaign_id, character_id)
        after = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertTrue(response["ok"])
        self.assertEqual(response["source"], "engine.build_options")
        self.assertEqual(response["campaign_id"], campaign_id)
        self.assertEqual(response["planner"]["character_id"], character_id)
        fighter = next(o for o in response["planner"]["options"] if o["class_name"] == "fighter")
        self.assertEqual(fighter["to"], {"level": 4, "class": "fighter"})
        self.assertTrue(fighter["choices"]["asi_required"])
        self.assertEqual(after, before)

    def test_build_options_response_rejects_unsafe_or_missing_campaign_id(self):
        response = server.build_options_response("../secret", "pc")

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "invalid_campaign")
        self.assertIn("campaign", response["errors"][0])


if __name__ == "__main__":
    unittest.main()
