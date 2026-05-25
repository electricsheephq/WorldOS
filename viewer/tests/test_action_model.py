import importlib.util
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(server)


def _action(model, group_id, action_id):
    group = next(g for g in model["groups"] if g["id"] == group_id)
    return next(a for a in group["actions"] if a["id"] == action_id)


class ActionModelTests(unittest.TestCase):
    def test_action_model_treats_viewer_only_empty_state_as_no_campaign(self):
        raw_model = server.build_action_model({}, live=True, is_live_view=True)

        self.assertIsNone(raw_model["actor"])
        self.assertEqual(_action(raw_model, "exploration", "continue")["disabled_reason"], "no active campaign")
        self.assertEqual(_action(raw_model, "combat", "attack")["disabled_reason"], "no active campaign")

    def test_action_model_disables_exploration_without_live_move_sink(self):
        snapshot = {
            "title": "Cellar Rats",
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }

        model = server.build_action_model(snapshot, live=False, is_live_view=False)

        self.assertEqual(model["actor"], {"id": "pc", "name": "Vela", "kind": "player"})
        self.assertIs(model["live"], False)
        self.assertIs(model["is_live_view"], False)
        self.assertEqual(_action(model, "exploration", "continue")["disabled_reason"], "no live move sink")
        self.assertEqual(_action(model, "combat", "attack")["disabled_reason"], "not in combat")

    def test_action_model_does_not_treat_viewer_only_keys_as_campaign(self):
        model = server.build_action_model(
            {"combat_view": {"active": False}, "live": False, "is_live_view": False},
            live=False,
            is_live_view=False,
        )

        self.assertEqual(_action(model, "exploration", "continue")["disabled_reason"], "no active campaign")

    def test_action_model_uses_combat_turn_and_action_economy(self):
        snapshot = {
            "party": ["pc"],
            "characters": {
                "pc": {"id": "pc", "name": "Vela", "kind": "player"},
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster"},
            },
            "combat": {
                "active": True,
                "round": 2,
                "turn_index": 0,
                "action_used": True,
                "bonus_action_used": False,
                "order": [
                    {"character_id": "pc", "initiative": 18, "reaction_used": True},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

        model = server.build_action_model(snapshot, live=True, is_live_view=True)

        self.assertEqual(
            model["combat"],
            {
                "active": True,
                "round": 2,
                "current_actor_id": "pc",
                "is_current_turn": True,
            },
        )
        self.assertEqual(
            model["economy"],
            {
                "action_available": False,
                "bonus_available": True,
                "reaction_available": False,
            },
        )
        self.assertEqual(_action(model, "combat", "attack")["disabled_reason"], "action spent")
        self.assertIsNone(_action(model, "combat", "bonus-action")["disabled_reason"])
        self.assertEqual(_action(model, "combat", "reaction")["disabled_reason"], "reaction spent")

    def test_action_model_marks_party_actor_waiting_for_turn(self):
        snapshot = {
            "party": ["pc"],
            "characters": {
                "pc": {"id": "pc", "name": "Vela", "kind": "player"},
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster"},
            },
            "combat": {
                "active": True,
                "turn_index": 1,
                "action_used": False,
                "bonus_action_used": False,
                "order": [
                    {"character_id": "pc", "initiative": 18, "reaction_used": False},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

        model = server.build_action_model(snapshot, live=True, is_live_view=True)

        self.assertEqual(model["combat"]["current_actor_id"], "gob")
        self.assertIs(model["combat"]["is_current_turn"], False)
        self.assertEqual(_action(model, "combat", "attack")["disabled_reason"], "not current turn")
        self.assertIsNone(_action(model, "combat", "reaction")["disabled_reason"])


if __name__ == "__main__":
    unittest.main()
