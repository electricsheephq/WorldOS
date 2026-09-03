"""Red-first for qa/sidecar_grid_check.py: the kit-derived crypt sidecar agrees with its grid (0/0);
a hand-authored legacy sidecar (snug v1, kept in-repo as the measured negative) does not."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sidecar_grid_check as sgc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UNITY = ROOT / "extensions" / "renderers" / "unity"


def _load(room_geo, boxes):
    g = json.loads((ROOT / "qa" / "room_geometries" / room_geo).read_text())
    b = json.loads((UNITY / "boxes" / boxes).read_text())
    return sgc.check(g, b)


def test_cell_projection_is_the_contract_map():
    assert sgc.cell_center(0, 0, 16, 12) == (-15.0, 11.0)
    assert sgc.cell_center(15, 11, 16, 12) == (15.0, -11.0)
    box = {"kind": "tomb", "center": [0.0, 0.7, -1.0], "size": [4.0, 1.4, 2.0]}
    # a 2x1-cell box spanning x in [-2, 2] at z = -1 covers the centres of cells (7,6) and (8,6) of a 16x12 grid
    assert sgc.cells_of_box(box, 16, 12) == {(7, 6), (8, 6)}


def test_kit_derived_crypt_sidecar_agrees():
    res = _load("crypt_v36_geometry.json", "crypt_kit_v1_boxes.json")
    assert res["blocked_without_occluder"] == []
    assert res["occluder_over_open_floor"] == []


def test_bbox_sidecar_over_an_l_shaped_footprint_disagrees():
    # the hand-authored legacy class: ONE bounding-rectangle box over an L-shaped hearth claims the notch cell,
    # and an impassable cell with no box at all goes unmasked
    g = {"cols": 12, "rows": 10, "door_cells": [],
         "impassable": [[9, 7], [9, 8], [10, 6], [10, 7], [10, 8], [1, 1]]}
    b = {"boxes": [{"kind": "hearth", "center": [8.0, 1.4, -5.0], "size": [3.6, 2.8, 5.4]}]}
    res = sgc.check(g, b)
    assert res["occluder_over_open_floor"] == [(9, 6)]
    assert res["blocked_without_occluder"] == [(1, 1)]


def test_floor_and_flat_boxes_never_count():
    g = {"cols": 4, "rows": 4, "impassable": [], "door_cells": []}
    b = {"boxes": [{"kind": "floor", "center": [0, 0, 0], "size": [8, 0.1, 8]},
                   {"kind": "decal", "center": [0, 0, 0], "size": [2, 0.1, 2]}]}
    assert sgc.check(g, b)["occluder_over_open_floor"] == []


def test_full_wall_over_a_door_cell_is_a_ghost_but_a_gate_piece_is_not():
    # codex review 2026-09-03: the blanket door exclusion hid a 1.9x1.9 wall centred on a walkable door cell
    g = {"cols": 4, "rows": 4, "impassable": [], "door_cells": [[1, 1]]}
    wx, wz = sgc.cell_center(1, 1, 4, 4)
    wall = {"boxes": [{"kind": "wallback", "center": [wx, 2.7, wz], "size": [1.9, 5.4, 1.9]}]}
    assert sgc.check(g, wall)["occluder_over_open_floor"] == [(1, 1)]
    gate = {"boxes": [{"kind": "gate", "center": [wx, 2.7, wz], "size": [1.9, 5.4, 1.9]}]}
    assert sgc.check(g, gate)["occluder_over_open_floor"] == []
    # and a door cell is never "blocked": it is not impassable
    assert sgc.check(g, {"boxes": []})["blocked_without_occluder"] == []
