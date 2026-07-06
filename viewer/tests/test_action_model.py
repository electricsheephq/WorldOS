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

    # ---- #598: Move / Cast / Item wired to the SAME action-economy reason Attack already
    # uses, carrying the correct already-whitelisted _MOVE_KINDS payload (move_to_zone / cast /
    # use_item) instead of the old permanent `available: False` stub. ----

    def _live_current_turn_snapshot(self, *, action_used=False):
        return {
            "party": ["pc"],
            "characters": {
                "pc": {"id": "pc", "name": "Vela", "kind": "player"},
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster"},
            },
            "combat": {
                "active": True,
                "round": 1,
                "turn_index": 0,
                "action_used": action_used,
                "bonus_action_used": False,
                "order": [
                    {"character_id": "pc", "initiative": 18, "reaction_used": False},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

    def test_move_cast_item_available_on_current_turn_with_action_unspent(self):
        model = server.build_action_model(
            self._live_current_turn_snapshot(action_used=False), live=True, is_live_view=True
        )

        move = _action(model, "combat", "move")
        cast = _action(model, "combat", "cast")
        item = _action(model, "combat", "item")

        for action, expected_kind in ((move, "move_to_zone"), (cast, "cast"), (item, "use_item")):
            self.assertTrue(action["available"], action)
            self.assertIsNone(action["disabled_reason"])
            self.assertEqual(action["move"]["kind"], expected_kind)

    def test_move_cast_item_grey_out_with_attack_when_action_spent(self):
        # #598 requirement 3: the tiles disable/grey when the engine's action-economy state
        # says the action is spent — same "action spent" reason Attack already surfaces.
        model = server.build_action_model(
            self._live_current_turn_snapshot(action_used=True), live=True, is_live_view=True
        )

        for action_id in ("move", "attack", "cast", "item"):
            action = _action(model, "combat", action_id)
            self.assertFalse(action["available"], action_id)
            self.assertEqual(action["disabled_reason"], "action spent", action_id)

    def test_move_cast_item_disabled_reason_matches_attack_outside_combat(self):
        model = server.build_action_model({}, live=True, is_live_view=True)

        expected = _action(model, "combat", "attack")["disabled_reason"]
        for action_id in ("move", "cast", "item"):
            self.assertEqual(_action(model, "combat", action_id)["disabled_reason"], expected, action_id)

    def test_move_kind_carries_no_static_target(self):
        # move_to_zone is a _TARGET_ONLY_KIND — the server cannot supply the destination zone
        # statically, so the wired action's `move` payload deliberately omits `target`/`name`;
        # the client fills `target` from the zone the player clicks before POSTing.
        model = server.build_action_model(
            self._live_current_turn_snapshot(action_used=False), live=True, is_live_view=True
        )
        move_payload = _action(model, "combat", "move")["move"]
        self.assertNotIn("target", move_payload)
        self.assertNotIn("name", move_payload)


class MoveCastItemMoveIntentTests(unittest.TestCase):
    """Server-side floor per the #598 dispatch packet: POST the three wired kinds through
    sanitize_move (the same gate /move applies) and assert acceptance with the exact payload
    shape the wired tiles now send."""

    def test_cast_accepted_with_name(self):
        # Cast/Item ride the free-text DM-resolved lane (kind+name) — the SAME shape Bonus/
        # Reaction already use, mirrored by the wired Cast tile (_action_item kind="cast",
        # name="Cast a Spell").
        move, reason = server.sanitize_move({"kind": "cast", "name": "Cast a Spell"})
        self.assertEqual(reason, "")
        self.assertEqual(move["kind"], "cast")
        self.assertEqual(move["name"], "Cast a Spell")
        self.assertEqual(move["role"], "player")

    def test_use_item_accepted_with_name(self):
        move, reason = server.sanitize_move({"kind": "use_item", "name": "Use an Item"})
        self.assertEqual(reason, "")
        self.assertEqual(move["kind"], "use_item")
        self.assertEqual(move["name"], "Use an Item")

    def test_move_to_zone_accepted_with_client_filled_target(self):
        # The Move tile's client fills `target` from the clicked zone band before POSTing
        # (server.py cannot supply a static zone name) — assert that completed shape is
        # accepted, mirroring the existing move_to_zone coverage in test_move_intents.py.
        move, reason = server.sanitize_move({"kind": "move_to_zone", "target": "the rafters"})
        self.assertEqual(reason, "")
        self.assertEqual(move["kind"], "move_to_zone")
        self.assertEqual(move["target"], "the rafters")

    def test_cast_and_use_item_reject_with_neither_text_nor_name(self):
        for kind in ("cast", "use_item"):
            with self.subTest(kind=kind):
                move, reason = server.sanitize_move({"kind": kind})
                self.assertIsNone(move)
                self.assertIn("text", reason)


if __name__ == "__main__":
    unittest.main()
