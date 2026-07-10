"""Regression for the Tessera Pro layout -> engine-fixture CONVERSION (epic #1508 stage-2, the Tessera
arm of the generator comparison). Tessera Pro is a tile-WFC authoring-time accelerator (same fence as
DunGen — see docs/roadmap/GENERATOR-EXPORT-CONTRACT.md); TesseraLayoutExporter.cs emits the SAME
top-level dungen_layout.json shape (generator/bounds/rooms/doorways/props), one tile instance = one
`rooms[]` entry, so tools/dungen_to_fixtures.py needed NO changes for the core path.

The one genuine 1:1-mapping gap this suite pins down: a Tessera "big tile" can occupy a NON-rectangular
multi-cell footprint (e.g. an L-shape). Rasterizing its bounds AABB (DunGen's original room-graph
approach) would over-carve a cell that was never actually part of the tile. The additive
`rooms[].cell_positions` field (a world-space center point per footprint cell) closes that gap:

  1. `_room_footprint` prefers `cell_positions` when present and carves the EXACT non-rectangular set;
  2. `_room_footprint` falls back to bounds-AABB rasterization when `cell_positions` is absent — pinning
     that DunGen layouts (no cell_positions field at all) are byte-identical to the pre-Tessera behaviour;
  3. an end-to-end synthetic Tessera-shaped layout (one normal single-cell tile + one L-shaped 3-cell big
     tile, a prop, and NO doorways at all — Tessera has no native doorway object) converts through
     `convert()` / `build_scenegrid()` / `build_geometry()` without error, the L-tile's 4th cell is
     correctly EXCLUDED from walkable floor, and the emitted SceneGrid fixture still validates against
     the engine's own SceneGrid model (extra='forbid').

Deterministic, offline, stdlib only (the engine-model assert imports servers/engine/scene_grid; skipped
if pydantic is unavailable). Coordinates below are chosen to be exact integer multiples of the cell
pitch (never a k.5 ratio) so `round()` behavior can't introduce test flakiness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_ROOT = _QA_DIR.parent
for _p in (_ROOT / "tools", _ROOT / "servers" / "engine"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dungen_to_fixtures as d2f  # noqa: E402

# ── 1. _room_footprint: cell_positions carves the EXACT footprint, not the AABB ─────────────────────────
# A would-be 2x2 cell block (cols 3-4, rows 2-3 at upc=10, overall bounds [0,0,0]-[100,0,100]) whose AABB
# spans all 4 cells, but the tile is actually an L-shape missing (col 4, row 3). cell_positions names only
# the 3 real cells. All coordinates are exact multiples of the 10-unit cell pitch (never a k.5 ratio) so
# `round()` can't introduce ambiguity.
_L_SHAPE_BOUNDS = {"min": [25, 0, 65], "max": [45, 2, 85]}
_L_SHAPE_CELL_POSITIONS = [[30, 0, 80], [40, 0, 80], [30, 0, 70]]  # -> (3,2), (4,2), (3,3); (4,3) excluded


def test_room_footprint_prefers_cell_positions_over_aabb():
    proj = d2f.Projector([0, 0, 0], [100, 0, 100], 10.0)
    rm = {"bounds": _L_SHAPE_BOUNDS, "cell_positions": _L_SHAPE_CELL_POSITIONS}

    exact = d2f._room_footprint(rm, proj)
    assert exact == {(3, 2), (4, 2), (3, 3)}, "cell_positions must carve the exact L-shape, not the AABB"

    # the AABB alone (no cell_positions) DOES include the 4th cell — proves the two paths differ, i.e.
    # the additive field is actually doing something rather than being silently ignored.
    aabb_only = d2f._room_footprint({"bounds": _L_SHAPE_BOUNDS}, proj)
    assert aabb_only == {(3, 2), (4, 2), (3, 3), (4, 3)}
    assert exact < aabb_only


def test_room_footprint_falls_back_to_aabb_when_cell_positions_absent():
    """DunGen layouts never carry `cell_positions` — pin byte-identical old behaviour."""
    proj = d2f.Projector([0, 0, 0], [100, 0, 100], 10.0)
    rm = {"bounds": _L_SHAPE_BOUNDS}
    assert d2f._room_footprint(rm, proj) == set(
        proj.cells_in_xz_bounds(_L_SHAPE_BOUNDS["min"], _L_SHAPE_BOUNDS["max"])
    )


# ── 2. end-to-end synthetic Tessera-shaped layout ────────────────────────────────────────────────────────
# room_0: an ordinary single-cell tile at (col0,row0). room_1: the L-shaped 3-cell big tile from above,
# re-expressed at this layout's own bounds/upc. No "doorways" key at all (Tessera has no native doorway
# object) and one prop inside room_0.
_TESSERA_LAYOUT = {
    "generator": {"kind": "tessera_wfc", "seed": 99, "world_units_per_cell": 2.0, "tile_count": 2},
    "bounds": {"min": [0, 0, 0], "max": [10, 4, 10]},
    "rooms": [
        {"id": "room_0", "tags": [], "is_main_path": False, "tile_name": "BasicRoomTile",
         "cell_rotation": "Identity",
         "bounds": {"min": [0, 0, 8], "max": [2, 2, 10]},
         "cell_positions": [[1, 0, 9]]},                      # -> (col0, row0)
        {"id": "room_1", "tags": [], "is_main_path": False, "tile_name": "LShapeBigTile",
         "cell_rotation": "Identity",
         "bounds": {"min": [5, 0, 3], "max": [9, 2, 7]},       # AABB would over-include (4,3)
         "cell_positions": [[6, 0, 6], [8, 0, 6], [6, 0, 4]]},  # -> (3,2), (4,2), (3,3) only
    ],
    # no "doorways" key at all — Tessera has no native doorway/connection object (WFC connects tiles by
    # face-matching, not an explicit Doorway type); the converter must tolerate this.
    "props": [
        {"id": "prop_crate", "room": "room_0", "shape_class": "box", "kind_hint": "supply_crates",
         "position": [1, 0, 9], "bounds": {"min": [0.5, 0, 8.5], "max": [1.5, 1, 9.5]}},
    ],
}


def _ctx():
    return d2f.convert(_TESSERA_LAYOUT, name="tessera_synth", upc=2.0, material="stone")


def test_tessera_layout_converts_without_a_doorways_key():
    """No 'doorways' key at all (not even an empty list) must not raise, and yields no doors/exits."""
    ctx = _ctx()
    assert ctx["_doors"] == [] and ctx["_door_set"] == set()
    assert ctx["_exits"] == []


def test_tessera_big_tile_footprint_excludes_the_uncarved_corner():
    ctx = _ctx()
    assert ctx["_room_cells"]["room_0"] == {(0, 0)}
    assert ctx["_room_cells"]["room_1"] == {(3, 2), (4, 2), (3, 3)}
    assert (4, 3) not in ctx["_floor"], "the L-shape's excluded corner must stay uncarved rock"
    assert ctx["_floor"] >= {(0, 0), (3, 2), (4, 2), (3, 3)}


def test_tessera_scenegrid_and_geometry_reflect_the_exact_footprint():
    ctx = _ctx()
    sg = d2f.build_scenegrid(ctx, location_id="tessera_synth")
    by_cell = {(c["c"], c["r"]): c for c in sg["cells"]}

    assert (4, 3) not in by_cell, "excluded corner must not appear as any carved cell type"
    assert by_cell[(3, 2)]["type"] == "floor" and by_cell[(3, 2)]["walkable"]
    assert sg["door_cells"] == [] and sg["exits"] == []

    # room_0 is a single-cell tile and the crate's bounds fully cover that one cell, so its only cell
    # is impassable prop footprint rather than open floor (same "prop wins the cell" rule as DunGen).
    assert by_cell[(0, 0)]["type"] == "prop" and not by_cell[(0, 0)]["walkable"]
    assert by_cell[(0, 0)]["prop_ref"] == "prop_crate"

    geo = d2f.build_geometry(ctx)
    walls = {tuple(w) for w in geo["walls"]}
    assert (4, 3) in walls, "the uncarved corner must be a wall in the greybox geometry too"
    assert (3, 2) not in walls and (3, 3) not in walls and (4, 2) not in walls


def test_tessera_prop_flows_through_unchanged():
    props = {p["id"]: p for p in _ctx()["_props"]}
    assert props["prop_crate"]["kind"] == "supply_crates"
    assert props["prop_crate"]["cells"] == [[0, 0]]


def test_tessera_scenegrid_validates_against_engine_model():
    pytest.importorskip("pydantic")
    try:
        import models  # noqa: F401, PLC0415  (import models FIRST: it owns the deliberate
        import scene_grid  # noqa: PLC0415     models<->scene_grid cycle; scene_grid alone dead-locks)
    except Exception as exc:  # pragma: no cover - engine deps unavailable
        pytest.skip(f"engine scene_grid import unavailable: {exc}")
    fixture = d2f.build_scenegrid(_ctx(), location_id="tessera_synth")
    grid = scene_grid.SceneGrid(**fixture)  # extra='forbid' -> a stray field would raise here
    assert grid.cell_default.walkable is False
    assert {p.id for p in grid.props} == {"prop_crate"}
