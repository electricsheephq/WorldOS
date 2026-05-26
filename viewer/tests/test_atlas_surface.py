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


class AtlasSurfaceTests(unittest.TestCase):
    def test_atlas_surface_projects_known_map_without_private_fields(self):
        snapshot = {
            "id": "camp_map",
            "title": "Roads of the Gate",
            "world_id": "baldurs-gate",
            "day": 12,
            "time_of_day": "dusk",
            "current_location_id": "gate",
            "locations": {
                "gate": {
                    "id": "gate",
                    "name": "Basilisk Gate",
                    "description": "A guarded gatehouse.",
                    "connections": ["market", "hidden-crypt"],
                    "visited": True,
                    "hex": [0, 0],
                    "region": "Lower City",
                    "travel_times": {"market": 20, "hidden-crypt": 5},
                    "tags": ["town", "rest"],
                    "notes": "private gate bypass",
                },
                "market": {
                    "id": "market",
                    "name": "Rain Market",
                    "description": "Canvas stalls bright with rain.",
                    "connections": ["gate"],
                    "visited": False,
                    "discovered": True,
                    "hex": [1, 0],
                    "region": "Lower City",
                    "tags": ["danger"],
                },
                "hidden-crypt": {
                    "id": "hidden-crypt",
                    "name": "Hidden Crypt",
                    "description": "Spoilers.",
                    "hidden": True,
                    "connections": ["gate"],
                    "notes": "private undead cache",
                },
            },
            "quests": {
                "q_market": {
                    "title": "Find the Rain Seller",
                    "status": "active",
                    "location_id": "market",
                    "objectives": ["Question the blue awning merchant"],
                    "notes": "private quest solution",
                },
                "q_done": {"title": "Closed Road", "status": "completed", "location_id": "gate"},
            },
            "factions": {
                "harpers": {"id": "harpers", "name": "Harpers", "notes": "private faction note"}
            },
            "strategic_state": {
                "last_tick_day": 11,
                "regions": {
                    "gate": {
                        "location_id": "gate",
                        "controller_id": "harpers",
                        "stability": 62,
                        "unrest": 12,
                        "tags": ["watched"],
                        "note": "private region note",
                    }
                },
                "clocks": {
                    "c1": {
                        "id": "c1",
                        "title": "Sapper Cell Regroups",
                        "kind": "threat",
                        "scope": "region",
                        "region_id": "market",
                        "progress": 5,
                        "target": 6,
                        "note": "private clock note",
                    }
                },
                "projects": {
                    "p1": {
                        "id": "p1",
                        "title": "Repair Gate Winch",
                        "kind": "construction",
                        "location_id": "gate",
                        "progress_days": 2,
                        "duration_days": 5,
                        "status": "active",
                        "note": "private project note",
                    }
                },
            },
            "dm_notes": "private map agenda",
        }

        surface = server.build_atlas_surface(
            snapshot,
            campaign_id="camp_map",
            live=True,
            is_live_view=True,
        )

        self.assertEqual(surface["campaign_id"], "camp_map")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertEqual(surface["current_location"]["id"], "gate")
        self.assertEqual([loc["id"] for loc in surface["known_locations"]], ["gate", "market"])
        self.assertEqual(surface["known_locations"][0]["tags"], ["town", "rest"])
        self.assertEqual(surface["edges"], [{"from": "gate", "to": "market"}])
        self.assertEqual(surface["travel_options"][0]["to"], "market")
        self.assertEqual(surface["travel_options"][0]["minutes"], 20)
        self.assertEqual(surface["travel_options"][0]["move"], {"kind": "do", "text": "Travel to Rain Market"})
        self.assertTrue(surface["camp_available"])
        self.assertEqual(surface["quest_markers"][0]["title"], "Find the Rain Seller")
        self.assertEqual(surface["strategic_clocks"][0]["title"], "Sapper Cell Regroups")
        self.assertTrue(surface["strategic_clocks"][0]["urgent"])
        self.assertEqual(surface["downtime_projects"][0]["title"], "Repair Gate Winch")
        self.assertEqual(surface["region_control"][0]["controller"], "Harpers")

        encoded = json.dumps(surface)
        for forbidden in (
            "Hidden Crypt",
            "hidden-crypt",
            "notes",
            "dm_notes",
            "private",
            "undead cache",
            "quest solution",
            "clock note",
            "project note",
            "region note",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assert_no_private_keys(surface)

    def test_atlas_surface_fails_closed_when_not_live(self):
        snapshot = {
            "current_location_id": "gate",
            "locations": {
                "gate": {"name": "Gate", "connections": ["market"], "visited": True},
                "market": {"name": "Market", "visited": True},
            },
        }

        surface = server.build_atlas_surface(
            snapshot,
            campaign_id="camp_readonly",
            live=False,
            is_live_view=False,
        )

        self.assertFalse(surface["can_act"])
        self.assertEqual(surface["travel_options"][0]["disabled_reason"], "no live move sink")
        self.assertNotIn("move", surface["travel_options"][0])

    def test_atlas_surface_degrades_for_legacy_saves(self):
        surface = server.build_atlas_surface(
            {"title": "Old Save"},
            campaign_id="camp_old",
            live=True,
            is_live_view=True,
        )

        self.assertEqual(surface["title"], "Old Save")
        self.assertEqual(surface["known_locations"], [])
        self.assertEqual(surface["edges"], [])
        self.assertEqual(surface["travel_options"], [])
        self.assertFalse(surface["camp_available"])

    def assert_no_private_keys(self, value) -> None:
        private_keys = {
            "notes",
            "dm_notes",
            "scenes",
            "lore",
            "memory",
            "secret",
            "agenda",
            "note",
            "effect",
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
