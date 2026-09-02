"""Self-tests for qa/seed_gfx_forest_road.py's authored forest_road scene_grid (PLATE SPRINT Phase 2).

Pins the load-bearing WALKABILITY CONTRACT for the FOREST-ROAD generalization fixture: every painted
tree / boulder / fallen log is an impassable pathing obstacle, the central dirt road is walkable, and
the pre-greybox gate stays clean — so a plate can never paint an obstacle the engine lets you walk over
(the owner's #1 complaint class). Mirrors qa/test_seed_gfx_camp.py.

Run with the engine venv (pydantic + pytest live there):
    uv run --directory servers/engine python -m pytest ../../qa/test_seed_gfx_forest_road.py -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "qa"))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import server  # noqa: E402,F401  imported FIRST: resolves the models<->scene_grid import cycle
import seed_gfx_forest_road as fr  # noqa: E402
from scene_grid import validate_scene_grid, impassable_cells  # noqa: E402


def _grid():
    return fr.build_forest_road_grid(fr.CID, "loc-test")


def test_scene_grid_has_zero_validate_violations():
    """The pre-greybox gate (door zones / protected lanes / connectivity / clear-floor) must be clean —
    a road plate can never reach generation if pathing is broken."""
    assert validate_scene_grid(_grid(), fr.GRID_W, fr.GRID_H) == []


def test_grid_dims_16x12():
    """16x12 — the dims the greybox contract camera (orthoSize 13, 1344x768) frames, same as the
    camp_clearing_night outdoor fixture."""
    assert (fr.GRID_W, fr.GRID_H) == (16, 12)


def test_every_painted_prop_is_impassable():
    """The walkability contract: every tree / boulder / fallen-log footprint cell must be an engine
    pathing obstacle (no painted obstacle the engine lets you walk over)."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, fr.GRID_W, fr.GRID_H)}
    for prop in grid.props:
        for (c, r) in prop.cells:
            assert (int(c), int(r)) in imp, f"prop {prop.id} cell {(c, r)} must be impassable"


def test_central_road_corridor_is_walkable():
    """The dirt road (cols 5-10) must be walkable floor — minus only the few authored road obstacles
    (road_log at (5,4),(6,4); road_boulder_l (5,2); road_boulder_r (10,7)). A road you can't walk is
    not a road."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, fr.GRID_W, fr.GRID_H)}
    authored_road_obstacles = {(5, 4), (6, 4), (5, 2), (10, 7)}
    walkable_road = 0
    for col in range(5, 11):
        for row in range(fr.GRID_H):
            cell = (col, row)
            if cell in authored_road_obstacles:
                assert cell in imp
            else:
                assert cell not in imp, f"road cell {cell} must be walkable"
                walkable_road += 1
    assert walkable_road >= 60  # a long, usable corridor


def test_forest_flanks_are_impassable_no_walkthrough():
    """The dense forest bands flanking the road (cols 0-4 and 11-15) must be fully impassable — no
    walkable pocket trapped behind the tree line (validate already guards connectivity; this asserts
    the flanks themselves are solid forest, the structural novelty of this room class)."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, fr.GRID_W, fr.GRID_H)}
    for col in list(range(0, 5)) + list(range(11, 16)):
        for row in range(fr.GRID_H):
            assert (col, row) in imp, f"forest flank cell {(col, row)} must be impassable"


def test_spawns_stay_walkable():
    """Party + npc spawn cells must never collide with a prop footprint."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, fr.GRID_W, fr.GRID_H)}
    for cells in grid.spawns.values():
        for (c, r) in cells:
            assert (int(c), int(r)) not in imp, f"spawn {(c, r)} must be walkable"
