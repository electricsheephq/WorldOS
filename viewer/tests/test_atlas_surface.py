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
                "settlements": {
                    "gate": {
                        "location_id": "gate",
                        "settlement_type": "district",
                        "governance": "watch council",
                        "public_safety": "tense",
                        "economy": "market day",
                        "unrest": 18,
                        "public_faction_ids": ["harpers"],
                        "establishments": ["Lantern Hall"],
                        "public_npcs": [
                            {"npc_id": "reeve", "role": "gate reeve", "pressure": "Petitions are delayed"}
                        ],
                        "notes": "private settlement agenda",
                    },
                    "hidden-crypt": {
                        "location_id": "hidden-crypt",
                        "settlement_type": "hideout",
                        "notes": "private hidden settlement",
                    },
                },
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
        self.assertEqual(surface["settlements"][0]["location_id"], "gate")
        self.assertEqual(surface["settlements"][0]["settlement_type"], "district")
        self.assertEqual(surface["settlements"][0]["public_factions"], ["Harpers"])
        self.assertEqual(surface["settlements"][0]["establishments"], ["Lantern Hall"])
        self.assertEqual(surface["settlements"][0]["public_npcs"][0]["role"], "gate reeve")

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
            "settlement agenda",
            "hideout",
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

    def test_atlas_surface_tolerates_null_optional_lists(self):
        snapshot = {
            "current_location_id": "gate",
            "locations": {
                "gate": {
                    "name": "Gate",
                    "connections": None,
                    "visited": True,
                }
            },
            "strategic_state": {
                "regions": {
                    "gate": {
                        "location_id": "gate",
                        "tags": None,
                    }
                }
            },
        }

        surface = server.build_atlas_surface(
            snapshot,
            campaign_id="camp_nulls",
            live=True,
            is_live_view=True,
        )

        self.assertEqual(surface["known_locations"][0]["connections"], [])
        self.assertEqual(surface["region_control"][0]["tags"], [])

    def test_atlas_surface_projects_world_graph_metadata_without_unlocking_hidden_routes(self):
        snapshot = {
            "id": "camp_graph",
            "title": "Graph Roads",
            "world_id": "graph-test",
            "current_location_id": "gate",
            "locations": {
                "gate": {
                    "id": "gate",
                    "name": "Gate",
                    "connections": ["market"],
                    "visited": True,
                    "travel_times": {},
                },
                "market": {
                    "id": "market",
                    "name": "Market",
                    "connections": ["gate"],
                    "visited": True,
                },
                "sealed": {
                    "id": "sealed",
                    "name": "Sealed Grove",
                    "connections": [],
                    "hidden": True,
                },
            },
            "world_graph": {
                "nodes": {
                    "gate": {
                        "location_id": "gate",
                        "biome": "coast",
                        "terrain": "cobbled gate road",
                        "danger": 2,
                        "atlas_layer": "settlement",
                        "tags": ["patrolled"],
                    },
                    "sealed": {
                        "location_id": "sealed",
                        "biome": "forest",
                        "danger": 8,
                    },
                },
                "edges": [
                    {
                        "from_id": "gate",
                        "to_id": "market",
                        "route_kind": "street",
                        "minutes": 15,
                        "difficulty": "easy",
                        "danger": 1,
                        "tags": ["lamplit"],
                    },
                    {
                        "from_id": "gate",
                        "to_id": "sealed",
                        "route_kind": "trail",
                        "minutes": 5,
                        "danger": 9,
                    },
                ],
            },
        }

        surface = server.build_atlas_surface(snapshot, campaign_id="camp_graph", live=True, is_live_view=True)

        gate = surface["known_locations"][0]
        self.assertEqual(gate["id"], "gate")
        self.assertEqual(gate["biome"], "coast")
        self.assertEqual(gate["terrain"], "cobbled gate road")
        self.assertEqual(gate["danger"], 2)
        self.assertEqual(gate["atlas_layer"], "settlement")
        self.assertEqual(surface["edges"], [
            {
                "from": "gate",
                "to": "market",
                "route_kind": "street",
                "difficulty": "easy",
                "minutes": 15,
                "danger": 1,
                "tags": ["lamplit"],
            }
        ])
        self.assertEqual(surface["travel_options"][0]["to"], "market")
        self.assertEqual(surface["travel_options"][0]["route_kind"], "street")
        self.assertEqual(surface["travel_options"][0]["minutes"], 15)
        encoded = json.dumps(surface)
        self.assertNotIn("Sealed Grove", encoded)
        self.assertNotIn("sealed", encoded)

    def test_atlas_surface_includes_calendar_projection_for_strategic_display(self):
        snapshot = {
            "title": "Calendar Map",
            "world_id": "calendar-test",
            "day": 32,
            "time_of_day": "dusk",
            "current_location_id": "gate",
            "calendar": {
                "name": "Dale Reckoning",
                "era_suffix": "DR",
                "epoch_year": 1492,
                "epoch_month": 1,
                "epoch_day": 1,
                "weekdays": ["Firstday", "Secondday", "Thirdday", "Fourthday", "Fifthday"],
                "months": [
                    {"name": "Hammer", "days": 30, "season": "Deepwinter"},
                    {"name": "Alturiak", "days": 30, "season": "The Claw of Winter"},
                ],
                "moons": [
                    {
                        "name": "Selune",
                        "cycle_days": 8,
                        "phase_names": ["new", "waxing", "full", "waning"],
                    }
                ],
            },
            "locations": {
                "gate": {"id": "gate", "name": "Gate", "visited": True, "tags": ["town"]},
            },
        }

        surface = server.build_atlas_surface(snapshot, campaign_id="camp_calendar", live=True, is_live_view=True)

        self.assertEqual(surface["dayLabel"], "Secondday, 2 Alturiak 1492 DR · dusk")
        self.assertEqual(surface["calendar"]["date_label"], "Secondday, 2 Alturiak 1492 DR")
        self.assertEqual(surface["calendar"]["season"], "The Claw of Winter")
        self.assertEqual(surface["calendar"]["moons"][0]["phase"], "waning")
        self.assertTrue(surface["camp_available"])

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
