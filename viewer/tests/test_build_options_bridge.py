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

    def test_build_options_surfaces_subclass_options_at_subclass_level(self):
        # #624: a Wizard one level below its subclass-choice level (L2 -> L3) must see
        # the legal Arcane Tradition options (with previews) on its continue-wizard path,
        # so the /character picker renders a real list instead of a free-text box.
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Subclass")["id"]
        character_id = engine.create_character(
            campaign_id, "Gale", kind="player", class_name="Wizard", level=2,
            apply_srd_defaults=True,
            abilities={"intelligence": 16, "constitution": 14, "dexterity": 12},
        )["id"]
        response = server.build_options_response(campaign_id, character_id)
        self.assertTrue(response["ok"])
        wizard = next(o for o in response["planner"]["options"] if o["class_name"] == "wizard")
        sub = wizard.get("subclass")
        self.assertIsNotNone(sub, "wizard at L2->L3 must carry a subclass-choice block")
        self.assertTrue(sub["required"])
        self.assertEqual(sub["group_label"], "Arcane Tradition")
        names = {o["name"] for o in sub["options"]}
        self.assertIn("Evoker", names)
        self.assertTrue(all(o.get("desc") for o in sub["options"]))
        self.assertTrue(all(o.get("features") for o in sub["options"]))

    def test_build_options_surfaces_higher_level_subclass_features(self):
        # #607 regression guard at the BRIDGE seam: the rich subclass features the engine
        # planner carries (full_features=True) must survive build_options_response
        # serialization to the viewer picker — not just the level-3 pair. A Fighter L2->L3
        # Champion must arrive with its higher-level archetype features (Superior Critical
        # L15, Survivor L18), each with rules text. Fails if the bridge strips feature
        # detail OR the engine call site reverts to the terse two-feature list.
        engine = server._engine_server()
        campaign_id = engine.create_campaign("Higher")["id"]
        character_id = engine.create_character(
            campaign_id, "Sera", kind="player", class_name="Fighter", level=2,
            apply_srd_defaults=True,
            abilities={"strength": 16, "constitution": 14, "dexterity": 12},
        )["id"]
        response = server.build_options_response(campaign_id, character_id)
        self.assertTrue(response["ok"])
        fighter = next(o for o in response["planner"]["options"] if o["class_name"] == "fighter")
        sub = fighter.get("subclass")
        self.assertIsNotNone(sub, "fighter at L2->L3 must carry a subclass-choice block")
        champion = next(o for o in sub["options"] if o["name"] == "Champion")
        feat_names = {f["name"] for f in champion["features"]}
        self.assertLessEqual(
            {"Additional Fighting Style", "Superior Critical", "Survivor"},
            feat_names,
            f"higher-level archetype features must survive the bridge, got {sorted(feat_names)}",
        )
        self.assertTrue(all(f.get("desc") for f in champion["features"]))


if __name__ == "__main__":
    unittest.main()
