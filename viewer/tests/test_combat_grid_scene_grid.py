"""A1 — the combat board's grid block consumes the location's engine-authored SceneGrid.

build_combat_surface previously hardcoded {mode, cols:16, rows:10}. With the SceneGrid
emitter, the board now uses the current location's scene_grid extents/cells when present,
and falls back to the old 16x10 default when absent (ADDITIVE — an old snapshot renders
byte-identically). Presentation-only: the viewer READS the engine-owned snapshot.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _surface(snapshot: dict) -> dict:
    return server.build_combat_surface(
        snapshot, campaign_id="c", live=False, is_live_view=False, recent_events=[]
    )


class SceneGridBoardTests(unittest.TestCase):
    def test_falls_back_to_default_extents_when_no_scene_grid(self):
        """An old snapshot whose current location has no scene_grid renders the legacy
        16x10 board (byte-identical to pre-A1)."""
        snap = {
            "current_location_id": "loc1",
            "locations": {"loc1": {"name": "A Room"}},
        }
        grid = _surface(snap)["grid"]
        self.assertEqual(grid["cols"], 16)
        self.assertEqual(grid["rows"], 10)
        self.assertIn("mode", grid)  # mode is preserved from the combat-tokens derivation
        # No scene_grid -> the block carries ONLY the legacy keys (no cells/sceneId).
        self.assertNotIn("cells", grid)
        self.assertNotIn("sceneId", grid)

    def test_uses_scene_grid_extents_and_cells_when_present(self):
        snap = {
            "current_location_id": "loc1",
            "locations": {
                "loc1": {
                    "name": "The Tavern",
                    "scene_grid": {
                        "scene_id": "w:loc1",
                        "location_id": "loc1",
                        "kind": "tavern",
                        "grid": {"cols": 14, "rows": 10, "cell_size_ft": 5,
                                 "projection": "dimetric-2to1"},
                        "cell_default": {"type": "floor", "walkable": True, "cost": 1},
                        "cells": [
                            {"c": 0, "r": 0, "type": "wall", "walkable": False, "cost": 1},
                        ],
                    },
                }
            },
        }
        grid = _surface(snap)["grid"]
        self.assertEqual(grid["cols"], 14)
        self.assertEqual(grid["rows"], 10)
        self.assertEqual(grid["sceneId"], "w:loc1")
        self.assertEqual(grid["cellDefault"], {"type": "floor", "walkable": True, "cost": 1})
        self.assertEqual(len(grid["cells"]), 1)
        self.assertEqual(grid["cells"][0]["type"], "wall")
        # mode is preserved from the combat-tokens derivation (no active combat -> theater).
        self.assertIn("mode", grid)

    def test_malformed_scene_grid_degrades_to_default(self):
        """A scene_grid with a missing/bad grid block must NOT crash — degrade to 16x10.

        The fallback must mirror the full legacy key-shape: ONLY {mode, cols, rows} — no
        partial sceneId / cells / cellDefault leak that would cause UI regressions."""
        snap = {
            "current_location_id": "loc1",
            "locations": {"loc1": {"name": "Broken", "scene_grid": {"grid": {"cols": 0}}}},
        }
        grid = _surface(snap)["grid"]
        self.assertEqual(grid["cols"], 16)
        self.assertEqual(grid["rows"], 10)
        # Full legacy key-shape: no scene-grid-specific keys must leak through.
        self.assertNotIn("cells", grid)
        self.assertNotIn("sceneId", grid)
        self.assertNotIn("cellDefault", grid)


class CombatDoorsTests(unittest.TestCase):
    """M-E room transition: build_combat_surface surfaces the current location's authored doorway
    cells + their destination room-unit (scene_grid.door_cells x Location.connections)."""

    def test_doors_empty_without_door_cells(self):
        snap = {"current_location_id": "loc1",
                "locations": {"loc1": {"name": "A", "connections": ["loc2"],
                                       "scene_grid": {"grid": {"cols": 14, "rows": 11}}},
                              "loc2": {"name": "B"}}}
        self.assertEqual(_surface(snap)["doors"], [])

    def test_doors_empty_without_connections(self):
        snap = {"current_location_id": "loc1",
                "locations": {"loc1": {"name": "Isolated",
                                       "scene_grid": {"grid": {"cols": 14, "rows": 11}, "door_cells": [[6, 0]]}}}}
        self.assertEqual(_surface(snap)["doors"], [])

    def test_surfaces_door_cell_with_destination(self):
        snap = {"current_location_id": "loc1",
                "locations": {
                    "loc1": {"name": "Crypt Stair", "connections": ["loc2"],
                             "scene_grid": {"grid": {"cols": 14, "rows": 11}, "door_cells": [[6, 0]]}},
                    "loc2": {"name": "Crypt Tomb", "connections": ["loc1"]}}}
        doors = _surface(snap)["doors"]
        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["cell"], [6, 0])
        self.assertEqual(doors[0]["to"], "loc2")
        self.assertEqual(doors[0]["toName"], "Crypt Tomb")
        self.assertFalse(doors[0]["multi"])

    def test_multi_connection_flags_ambiguity_and_surfaces_first(self):
        snap = {"current_location_id": "loc1",
                "locations": {
                    "loc1": {"name": "Hub", "connections": ["loc2", "loc3"],
                             "scene_grid": {"grid": {"cols": 14, "rows": 11}, "door_cells": [[6, 0]]}},
                    "loc2": {"name": "North"}, "loc3": {"name": "East"}}}
        doors = _surface(snap)["doors"]
        self.assertEqual(len(doors), 1)
        self.assertTrue(doors[0]["multi"])
        self.assertEqual(doors[0]["to"], "loc2")


if __name__ == "__main__":
    unittest.main()
