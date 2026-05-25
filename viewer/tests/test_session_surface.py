import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _find_action(surface: dict, action_id: str) -> dict:
    return next(a for a in surface["availableActions"] if a["id"] == action_id)


class SessionSurfaceTests(unittest.TestCase):
    def test_session_surface_projects_safe_read_model_without_private_fields(self):
        snapshot = {
            "id": "camp_safe",
            "title": "Smoke at the Gate",
            "summary": "The party studies the sealed gate as dusk gathers.",
            "world_id": "baldurs-gate",
            "day": 12,
            "time_of_day": "dusk",
            "current_location_id": "lower-city",
            "locations": {
                "lower-city": {
                    "name": "Lower City",
                    "region": "Baldur's Gate",
                    "description": "Rain gathers along the cobbles outside the Basilisk Gate.",
                    "notes": "secret Zhentarim cache under the third stone",
                },
            },
            "party": ["tav", "jaheira"],
            "characters": {
                "tav": {
                    "id": "tav",
                    "name": "Tav",
                    "kind": "player",
                    "classes": [{"name": "Fighter", "level": 5}],
                    "current_hp": 31,
                    "max_hp": 42,
                    "armor_class": 18,
                    "conditions": ["blessed"],
                    "inventory": [
                        {"name": "Torch", "quantity": 2, "type": "gear", "notes": "private stash marker"},
                        {"name": "Potion of Healing", "qty": 1},
                    ],
                    "notes": "secret player note",
                    "personality": "private personality seed",
                    "backstory": "private backstory seed",
                    "companion_dossier": {"sealed_agenda": "private agenda"},
                },
                "jaheira": {
                    "id": "jaheira",
                    "name": "Jaheira",
                    "kind": "companion",
                    "classes": [{"name": "Druid", "level": 5}],
                    "current_hp": 38,
                    "max_hp": 38,
                    "armor_class": 16,
                    "conditions": [],
                },
            },
            "quests": {
                "q_gate": {
                    "title": "Ashes at the Gate",
                    "description": "Find who sealed the Basilisk Gate after moonrise.",
                    "status": "active",
                    "objectives": ["Speak to Harper Tull", "Inspect the ash circle"],
                    "completed_objectives": ["Inspect the ash circle"],
                    "location_id": "lower-city",
                    "notes": "secret quest solution",
                },
                "q_done": {"title": "Closed Door", "status": "completed"},
            },
            "scenes": [{"dm_notes": "hidden agenda"}],
            "lore": ["private canon recall input"],
            "dm_notes": "private dm note",
        }

        surface = server.build_session_surface(
            snapshot,
            campaign_id="camp_safe",
            live=False,
            is_live_view=False,
            recent_events=[{"kind": "narration", "detail": "A bell rings once beyond the gate."}],
        )

        self.assertEqual(surface["campaign_id"], "camp_safe")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertFalse(surface["can_act"])
        self.assertEqual(surface["title"], "Smoke at the Gate")
        self.assertEqual(surface["world"], "baldurs-gate")
        self.assertEqual(surface["dayLabel"], "Day 12 · dusk")
        self.assertEqual(surface["location"]["name"], "Lower City")
        self.assertEqual(surface["location"]["region"], "Baldur's Gate")
        self.assertEqual(surface["scene"]["summary"], "The party studies the sealed gate as dusk gathers.")
        self.assertEqual([p["name"] for p in surface["party"]], ["Tav", "Jaheira"])
        self.assertEqual(surface["party"][0]["class"], "Fighter")
        self.assertEqual(surface["party"][0]["level"], 5)
        self.assertEqual(surface["party"][0]["hp"], 31)
        self.assertEqual(surface["party"][0]["hpMax"], 42)
        self.assertEqual(surface["party"][0]["ac"], 18)
        self.assertEqual(surface["conditions"][0]["name"], "Blessed")
        self.assertEqual(surface["conditions"][0]["who"], "Tav")
        self.assertEqual(surface["activeQuests"][0]["title"], "Ashes at the Gate")
        self.assertEqual(surface["activeQuests"][0]["objective"], "Speak to Harper Tull")
        self.assertEqual([i["name"] for i in surface["quickInventory"]], ["Torch", "Potion of Healing"])
        self.assertEqual(surface["recentEvents"][0]["text"], "A bell rings once beyond the gate.")

        encoded = json.dumps(surface)
        for forbidden in (
            "notes",
            "dm_notes",
            "scenes",
            "personality",
            "backstory",
            "companion_dossier",
            "sealed_agenda",
            "private canon",
            "secret",
            "private",
            "hidden agenda",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assert_no_private_keys(surface)

    def test_session_surface_routes_enabled_actions_through_move_contract(self):
        snapshot = {
            "title": "Live Save",
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }

        surface = server.build_session_surface(snapshot, campaign_id="camp_live", live=True, is_live_view=True)

        self.assertTrue(surface["can_act"])
        self.assertEqual(surface["write_lane"], "/move")
        continue_action = _find_action(surface, "continue")
        say_action = _find_action(surface, "say")
        self.assertTrue(continue_action["available"])
        self.assertEqual(continue_action["move"], {"kind": "do", "text": "continue"})
        self.assertTrue(say_action["available"])
        self.assertEqual(say_action["ui"], "focus-say")
        self.assertNotIn("snapshot", json.dumps(surface))

    def test_session_surface_includes_combat_order_and_disabled_reasons(self):
        snapshot = {
            "party": ["pc"],
            "characters": {
                "pc": {
                    "id": "pc",
                    "name": "Vela",
                    "kind": "player",
                    "current_hp": 12,
                    "max_hp": 20,
                    "armor_class": 15,
                },
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster", "current_hp": 7, "max_hp": 7},
            },
            "combat": {
                "active": True,
                "round": 3,
                "turn_index": 0,
                "action_used": True,
                "bonus_action_used": False,
                "order": [
                    {"character_id": "pc", "initiative": 18, "reaction_used": True},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

        surface = server.build_session_surface(snapshot, campaign_id="camp_combat", live=True, is_live_view=True)

        self.assertTrue(surface["encounter"]["active"])
        self.assertEqual(surface["encounter"]["summary"], "Combat round 3")
        self.assertEqual([row["name"] for row in surface["roundOrder"]], ["Vela", "Goblin"])
        self.assertTrue(surface["roundOrder"][0]["active"])
        self.assertEqual(_find_action(surface, "attack")["disabled_reason"], "action spent")
        self.assertEqual(_find_action(surface, "reaction")["disabled_reason"], "reaction spent")

    def test_session_event_tail_rejects_unsafe_active_session_id(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        campaign_dir = root / "campaigns" / "camp_safe"
        (campaign_dir / "sessions").mkdir(parents=True)
        (campaign_dir / "evil.jsonl").write_text(
            json.dumps({"kind": "narration", "detail": "private traversal event"}) + "\n",
            encoding="utf-8",
        )
        (campaign_dir / "sessions" / "sess_1.jsonl").write_text(
            "".join(
                json.dumps({"kind": "narration", "detail": f"event {i}"}) + "\n"
                for i in range(20)
            ),
            encoding="utf-8",
        )

        unsafe = server._session_event_tail_from_dir(campaign_dir, {"active_session_id": "../evil"})
        safe_tail = server._session_event_tail_from_dir(campaign_dir, {"active_session_id": "sess_1"}, limit=3)

        self.assertEqual(unsafe, [])
        self.assertEqual([row["detail"] for row in safe_tail], ["event 17", "event 18", "event 19"])

    def test_session_surface_rejects_unsafe_active_session_id(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        campaign_dir = root / "campaigns" / "camp_surface"
        (campaign_dir / "sessions").mkdir(parents=True)
        (campaign_dir / "evil.jsonl").write_text(
            json.dumps({"kind": "narration", "detail": "private traversal event"}) + "\n",
            encoding="utf-8",
        )
        snapshot = {
            "title": "Unsafe Session",
            "active_session_id": "../evil",
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }
        recent_events = server._session_event_tail_from_dir(campaign_dir, snapshot)

        surface = server.build_session_surface(
            snapshot,
            campaign_id="camp_surface",
            live=False,
            is_live_view=False,
            recent_events=recent_events,
        )

        self.assertEqual(surface["recentEvents"], [])
        self.assertEqual(surface["title"], "Unsafe Session")

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
