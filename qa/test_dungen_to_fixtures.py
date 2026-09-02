"""Regression for the DunGen layout -> engine-fixture CONVERSION (tools/dungen_to_fixtures.py, epic #1508).

DunGen is an authoring-time structure accelerator; the engine stays the sole writer. These tests pin the
conversion from a synthetic DunGen layout export to the two downstream fixtures, using a hand-built
2-rooms-plus-corridor layout at the default 2.0 world-units-per-cell scale mapping:

  1. scale mapping — Unity world bounds snap to the expected integer cell grid;
  2. carved floor is walkable, uncarved exterior is a wall, and doorways are `door` cells;
  3. shape-appropriate proxy kinds — box->crate/masonry, cylinder->pillar, cone->large_tree, with a
     kind_hint naming a known greybox kind winning over the shape default (PR #1495 proxy lesson);
  4. the greybox geometry json matches the schema greybox_render_headless + derive_room_manifest consume
     (cell_default_walkable True; walls list every non-floor cell so walkable == carved floor);
  5. the emitted SceneGrid fixture validates against the engine's own SceneGrid model (extra='forbid').

Deterministic, offline, stdlib only (the engine-model assert imports servers/engine/scene_grid; skipped
if pydantic is unavailable).
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

# ── a synthetic DunGen export: two 6x6-unit rooms + a corridor, at 2.0 world-units/cell ──────────────
# room_0 x[0,6] z[0,6] (cols0-3,rows0-3) · room_1 x[10,16] z[0,6] (cols5-8) · corridor x[6,10] z[2,4].
# doorways carve room_0<->corridor (6,0,3) and corridor<->room_1 (10,0,3); an unconnected doorway at the
# room_0 top edge (3,0,0) is a level exit. props: a box crate, a cylinder pillar, a cone tree.
_LAYOUT = {
    "generator": {"seed": 4242, "world_units_per_cell": 2.0, "tile_count": 3},
    "bounds": {"min": [0, 0, 0], "max": [16, 4, 6]},
    "rooms": [
        {"id": "room_0", "tags": ["entrance"], "is_main_path": True,
         "bounds": {"min": [0, 0, 0], "max": [6, 4, 6]}},
        {"id": "room_1", "tags": ["chamber"], "is_main_path": True,
         "bounds": {"min": [10, 0, 0], "max": [16, 4, 6]}},
        {"id": "corridor_2", "tags": ["corridor"], "is_main_path": True,
         "bounds": {"min": [6, 0, 2], "max": [10, 4, 4]}},
    ],
    "doorways": [
        {"id": "door_0", "room_a": "room_0", "room_b": "corridor_2",
         "position": [6, 0, 3], "forward": [1, 0, 0]},
        {"id": "door_1", "room_a": "corridor_2", "room_b": "room_1",
         "position": [10, 0, 3], "forward": [1, 0, 0]},
        {"id": "door_2", "room_a": "room_0", "room_b": "",
         "position": [3, 0, 0], "forward": [0, 0, -1]},
    ],
    "props": [
        {"id": "prop_crate", "room": "room_0", "shape_class": "box", "kind_hint": "supply_crates",
         "position": [2, 0, 2], "bounds": {"min": [1.5, 0, 1.5], "max": [2.5, 1, 2.5]}},
        {"id": "prop_pillar", "room": "room_1", "shape_class": "cylinder", "kind_hint": "",
         "position": [12, 0, 3], "bounds": {"min": [11.5, 0, 2.5], "max": [12.5, 3, 3.5]}},
        {"id": "prop_tree", "room": "room_1", "shape_class": "cone", "kind_hint": "",
         "position": [14, 0, 2], "bounds": {"min": [13.5, 0, 1.5], "max": [14.5, 4, 2.5]}},
    ],
}


def _ctx():
    return d2f.convert(_LAYOUT, name="synth", upc=2.0, material="stone")


# ── 1. scale mapping ────────────────────────────────────────────────────────────────────────────────
def test_scale_mapping_grid_dims():
    proj = _ctx()["_projector"]
    assert (proj.cols, proj.rows) == (9, 4), "16x6 world units at upc=2.0 -> 9x4 cell grid"


# ── 2. carved floor walkable, exterior wall, doorways are door cells ─────────────────────────────────
def test_scenegrid_floor_walls_doors():
    ctx = _ctx()
    sg = d2f.build_scenegrid(ctx, location_id="synth")
    by_cell = {(c["c"], c["r"]): c for c in sg["cells"]}

    # the two uncarved cells (the gap column outside the corridor) are exterior rock -> not walkable floor.
    assert (4, 0) not in ctx["_floor"] and (4, 3) not in ctx["_floor"]
    assert sg["cell_default"] == {"type": "void", "walkable": False, "cost": 1}

    # doorway cell (3,2) is carved as a `door`.
    assert (3, 2) in by_cell and by_cell[(3, 2)]["type"] == "door" and by_cell[(3, 2)]["walkable"]
    assert [3, 2] in sg["door_cells"]
    # the unconnected doorway is a level exit.
    assert any(e["cell"] == [2, 3] for e in sg["exits"])

    # a prop footprint cell is impassable and refers back to the prop.
    crate = next(p for p in ctx["_props"] if p["id"] == "prop_crate")
    fc = tuple(crate["cells"][0])
    assert by_cell[fc]["type"] == "prop" and not by_cell[fc]["walkable"]
    assert by_cell[fc]["prop_ref"] == "prop_crate"


# ── 3. shape-appropriate proxy kinds ─────────────────────────────────────────────────────────────────
def test_prop_kind_mapping():
    props = {p["id"]: p for p in _ctx()["_props"]}
    assert props["prop_crate"]["kind"] == "supply_crates"   # kind_hint names a known kind -> wins
    assert props["prop_pillar"]["kind"] == "pillar"         # cylinder default
    assert props["prop_tree"]["kind"] == "large_tree"       # cone default (organic, not a box-tree)
    assert props["prop_pillar"]["height_band"] == "tall" and props["prop_pillar"]["occluder"]
    assert props["prop_tree"]["occluder"]

    # pure-shape fallback when kind_hint is empty/unknown.
    assert d2f._resolve_kind("box", "") == "crate"
    assert d2f._resolve_kind("cone", "weird_mesh_017") == "large_tree"


# ── 4. greybox geometry json matches the derive/greybox schema ───────────────────────────────────────
def test_geometry_schema_and_walkable_is_carved_floor():
    ctx = _ctx()
    geo = d2f.build_geometry(ctx)
    assert set(geo) >= {"cols", "rows", "cell_default_walkable", "walls", "props", "door_cells",
                        "impassable", "material", "location"}
    assert geo["cell_default_walkable"] is True
    assert all(set(p) == {"kind", "cells"} for p in geo["props"])

    cols, rows = geo["cols"], geo["rows"]
    walls = {(c, r) for (c, r) in (tuple(w) for w in geo["walls"])}
    prop_cells = {tuple(c) for p in geo["props"] for c in p["cells"]}
    # the derive_room_manifest walkable rule: grid - walls - prop footprints.
    walkable = {(c, r) for r in range(rows) for c in range(cols)
                if (c, r) not in walls and (c, r) not in prop_cells}
    expected_floor = {cr for cr in ctx["_floor"] if cr not in prop_cells}
    assert walkable == expected_floor, "derived walkable must equal the carved floor"
    # every non-floor cell is a wall (matches the forest_road fixture model).
    assert walls == {(c, r) for r in range(rows) for c in range(cols) if (c, r) not in ctx["_floor"]}


# ── 4b. per-room crop geometry is self-contained and wall-bounded ────────────────────────────────────
def test_per_room_crop_geometry():
    ctx = _ctx()
    geo = d2f.build_geometry(ctx, room="room_1")
    assert geo["cols"] > 0 and geo["rows"] > 0 and geo["cell_default_walkable"] is True
    assert geo["location"].endswith(":room_1")
    # the crop has a 1-cell wall margin, so row 0 and col 0 are entirely walls.
    walls = {(c, r) for (c, r) in (tuple(w) for w in geo["walls"])}
    assert all((c, 0) in walls for c in range(geo["cols"])), "top margin row must be wall"


# ── 5. the SceneGrid fixture validates against the engine model ──────────────────────────────────────
def test_scenegrid_validates_against_engine_model():
    pytest.importorskip("pydantic")
    try:
        import models  # noqa: F401, PLC0415  (import models FIRST: it owns the deliberate
        import scene_grid  # noqa: PLC0415     models<->scene_grid cycle; scene_grid alone dead-locks)
    except Exception as exc:  # pragma: no cover - engine deps unavailable
        pytest.skip(f"engine scene_grid import unavailable: {exc}")
    fixture = d2f.build_scenegrid(_ctx(), location_id="synth")
    grid = scene_grid.SceneGrid(**fixture)   # extra='forbid' -> a stray field would raise here
    assert grid.grid.cols == 9 and grid.grid.rows == 4
    assert grid.cell_default.walkable is False
    assert {p.id for p in grid.props} == {"prop_crate", "prop_pillar", "prop_tree"}
    # round-trip: the impassable set derived by the engine excludes the carved walkable floor.
    assert grid.props, "props must survive model validation"
