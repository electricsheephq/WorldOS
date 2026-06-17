import importlib.util
import json
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _find_action(surface: dict, action_id: str) -> dict:
    found = next((a for a in surface["actionBar"] if a["id"] == action_id), None)
    if found is None:
        available = [a.get("id") for a in surface["actionBar"] if isinstance(a, dict)]
        raise AssertionError(f"action {action_id!r} not found in actionBar: {available}")
    return found


class CombatSurfaceTests(unittest.TestCase):
    def test_combat_surface_projects_engine_owned_board_without_private_fields(self):
        snapshot = {
            "id": "camp_combat",
            "title": "Ambush at the Gate",
            "summary": "Steel rings under the Basilisk Gate.",
            "world_id": "baldurs-gate",
            "current_location_id": "gate",
            "locations": {
                "gate": {
                    "name": "Basilisk Gate",
                    "description": "A rain-slick gatehouse with overturned carts.",
                    "notes": "private route through the guard tower",
                },
            },
            "party": ["hero"],
            "characters": {
                "hero": {
                    "id": "hero",
                    "name": "Tav",
                    "kind": "player",
                    "current_hp": 18,
                    "max_hp": 24,
                    "armor_class": 17,
                    "conditions": ["blessed"],
                    "notes": "private player note",
                },
                "gob": {
                    "id": "gob",
                    "name": "Goblin Sapper",
                    "kind": "monster",
                    "current_hp": 3,
                    "max_hp": 7,
                    "armor_class": 13,
                    "conditions": ["prone"],
                    "notes": "secret satchel weakness",
                },
            },
            "combat": {
                "active": True,
                "round": 2,
                "turn_index": 0,
                "action_used": False,
                "bonus_action_used": True,
                "zones": [
                    {"name": "gate", "description": "broken carts and torchlight", "adjacent": ["road"]},
                    {"name": "road", "description": "muddy road", "adjacent": ["gate"]},
                ],
                "order": [
                    {"character_id": "hero", "initiative": 18, "reaction_used": False, "zone": "gate"},
                    {"character_id": "gob", "initiative": 9, "reaction_used": True, "zone": "road"},
                ],
            },
            "dm_notes": "private combat agenda",
        }
        recent_events = [
            {
                "kind": "combat",
                "text": "Tav strikes the sapper.",
                "payload": {
                    "schema": "worldos.combat_event.v1",
                    "event": "attack",
                    "actor": {"id": "hero", "name": "Tav", "notes": "private actor note"},
                    "target": {"id": "gob", "name": "Goblin Sapper", "ac": 13, "notes": "private target note"},
                    "roll": {"natural": 13, "total": 18},
                    "damage": {"total": 6, "type": "slashing"},
                    "secret": "private payload strategy",
                },
            }
        ]

        surface = server.build_combat_surface(
            snapshot,
            campaign_id="camp_combat",
            live=True,
            is_live_view=True,
            recent_events=recent_events,
        )

        self.assertEqual(surface["campaign_id"], "camp_combat")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertTrue(surface["can_act"])
        self.assertEqual(surface["encounter"]["name"], "Basilisk Gate")
        self.assertEqual(surface["encounter"]["round"], 2)
        self.assertEqual(surface["selectedTokenId"], "hero")
        self.assertEqual(surface["grid"], {"mode": "zones", "cols": 16, "rows": 10})
        self.assertEqual([t["id"] for t in surface["tokens"]], ["hero", "gob"])
        self.assertEqual(surface["tokens"][0]["ac"], 17)
        self.assertEqual(surface["tokens"][0]["hp"], 18)
        self.assertEqual(surface["tokens"][0]["conditions"], ["blessed"])
        self.assertEqual(surface["tokens"][1]["team"], "foe")
        self.assertNotIn("ac", surface["tokens"][1])
        self.assertFalse(surface["tokens"][1]["hpKnown"])
        self.assertEqual(surface["tokens"][1]["health"], "bloodied")
        self.assertEqual(surface["initiative"][0]["name"], "Tav")
        self.assertTrue(surface["initiative"][0]["active"])
        self.assertEqual(surface["zones"][0]["occupants"], ["hero"])
        self.assertEqual(surface["zones"][1]["occupants"], ["gob"])
        self.assertEqual(_find_action(surface, "attack")["move"], {"kind": "attack", "name": "Attack"})
        self.assertFalse(_find_action(surface, "bonus-action")["available"])
        self.assertEqual(_find_action(surface, "bonus-action")["disabled_reason"], "bonus action spent")
        self.assertEqual(surface["battleLog"][0]["event"], "attack")
        self.assertEqual(surface["battleLog"][0]["title"], "Tav -> Goblin Sapper")
        self.assertEqual(surface["battleLog"][0]["meta"][0], {"label": "d20", "value": 13})
        self.assertNotIn("combatView", surface)
        self.assertNotIn("actionModel", surface)

        encoded = json.dumps(surface)
        for forbidden in (
            "notes",
            "dm_notes",
            "secret",
            "private",
            "payload strategy",
            "guard tower",
            "satchel weakness",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assert_no_private_keys(surface)

    def test_combat_command_center_projects_turn_cues_and_targetability(self):
        snapshot = {
            "id": "camp_center",
            "title": "Command Center Fight",
            "current_location_id": "crypt",
            "locations": {"crypt": {"name": "Moon Crypt"}},
            "party": ["hero", "cleric"],
            "characters": {
                "hero": {
                    "id": "hero",
                    "name": "Renn",
                    "kind": "player",
                    "current_hp": 14,
                    "max_hp": 30,
                    "armor_class": 16,
                    "extra_attacks": 1,
                    "concentration": "Bless",
                    "conditions": ["blessed"],
                    "notes": "private tactical note",
                },
                "cleric": {
                    "id": "cleric",
                    "name": "Vela",
                    "kind": "companion",
                    "current_hp": 0,
                    "max_hp": 22,
                    "armor_class": 18,
                    "conditions": ["unconscious"],
                    "death_saves": {"successes": 1, "failures": 2},
                },
                "gob": {
                    "id": "gob",
                    "name": "Goblin Sapper",
                    "kind": "monster",
                    "current_hp": 3,
                    "max_hp": 7,
                    "armor_class": 13,
                    "conditions": ["prone"],
                    "notes": "private fuse",
                },
            },
            "combat": {
                "active": True,
                "round": 3,
                "turn_index": 0,
                "action_used": False,
                "bonus_action_used": True,
                "action_attacks_made": 1,
                "surge_actions": 0,
                "zones": [{"name": "altar"}, {"name": "stairs", "adjacent": ["altar"]}],
                "order": [
                    {"character_id": "hero", "initiative": 20, "reaction_used": False, "zone": "altar"},
                    {"character_id": "cleric", "initiative": 15, "reaction_used": True, "zone": "altar"},
                    {"character_id": "gob", "initiative": 9, "reaction_used": False, "zone": "stairs"},
                ],
            },
        }
        recent_events = [
            {
                "kind": "combat",
                "text": "Renn presses the attack.",
                "payload": {
                    "schema": "worldos.combat_event.v1",
                    "event": "attack",
                    "actor": {"id": "hero", "name": "Renn", "notes": "private actor note"},
                    "target": {"id": "gob", "name": "Goblin Sapper", "notes": "private target note"},
                    "roll": {"natural": 14, "total": 21},
                    "damage": {"total": 5, "type": "piercing"},
                },
            }
        ]

        surface = server.build_combat_surface(
            snapshot,
            campaign_id="camp_center",
            live=True,
            is_live_view=True,
            recent_events=recent_events,
        )

        center = surface["commandCenter"]
        self.assertEqual(center["activeActor"]["id"], "hero")
        self.assertEqual(center["activeActor"]["concentration"], "Bless")
        self.assertEqual(center["slots"]["action"], {"available": True, "spent": False, "reason": ""})
        self.assertEqual(center["slots"]["bonusAction"]["reason"], "bonus action spent")
        self.assertEqual(center["slots"]["reaction"], {"available": True, "spent": False, "reason": ""})
        self.assertEqual(
            center["attackBudget"],
            {
                "made": 1,
                "allowed": 2,
                "remaining": 1,
                "extraAttacks": 1,
                "surgeActions": 0,
                "multiattack": 0,
            },
        )
        by_id = {row["id"]: row for row in center["targetability"]}
        self.assertEqual(by_id["gob"]["targetable"], True)
        self.assertEqual(by_id["cleric"]["reason"], "ally")
        self.assertEqual(by_id["hero"]["reason"], "self")
        self.assertIn(
            {"type": "concentration", "severity": "info", "character_id": "hero", "label": "Renn concentrating", "text": "Bless"},
            center["cues"],
        )
        self.assertIn(
            {
                "type": "death_saves",
                "severity": "danger",
                "character_id": "cleric",
                "label": "Vela dying",
                "text": "1 success / 2 fail",
            },
            center["cues"],
        )
        self.assertEqual(center["eventCards"][0]["event"], "attack")

        encoded = json.dumps(center)
        self.assertNotIn("private", encoded)
        self.assertNotIn("notes", encoded)
        self.assert_no_private_keys(center)

    def test_combat_surface_fails_closed_when_not_live_or_not_current_turn(self):
        snapshot = {
            "party": ["hero"],
            "characters": {
                "hero": {"id": "hero", "name": "Tav", "kind": "player"},
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster"},
            },
            "combat": {
                "active": True,
                "round": 1,
                "turn_index": 1,
                "order": [
                    {"character_id": "hero", "initiative": 18},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

        surface = server.build_combat_surface(
            snapshot,
            campaign_id="camp_readonly",
            live=False,
            is_live_view=False,
        )

        self.assertFalse(surface["can_act"])
        self.assertEqual(_find_action(surface, "attack")["disabled_reason"], "no live move sink")
        self.assertEqual(_find_action(surface, "end-turn")["disabled_reason"], "no live move sink")
        self.assertEqual(surface["selectedTokenId"], "gob")
        self.assertEqual(surface["tokens"][0]["team"], "ally")
        self.assertEqual(surface["tokens"][1]["team"], "foe")

    def test_combat_surface_degrades_to_empty_state_without_active_combat(self):
        surface = server.build_combat_surface(
            {"title": "Quiet Camp"},
            campaign_id="camp_empty",
            live=True,
            is_live_view=True,
        )

        self.assertFalse(surface["encounter"]["active"])
        self.assertEqual(surface["tokens"], [])
        self.assertEqual(surface["initiative"], [])
        self.assertFalse(surface["can_act"])
        self.assertEqual(_find_action(surface, "attack")["disabled_reason"], "not in combat")

    def assert_no_private_keys(self, value) -> None:
        private_keys = {
            "notes",
            "dm_notes",
            "scenes",
            "lore",
            "memory",
            "personality",
            "backstory",
            "companion_dossier",
            "sealed_agenda",
            "agenda",
            "secret",
        }
        if isinstance(value, dict):
            for key, child in value.items():
                self.assertNotIn(key, private_keys)
                self.assert_no_private_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_private_keys(child)


if __name__ == "__main__":
    unittest.main()
