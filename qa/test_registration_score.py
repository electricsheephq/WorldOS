#!/usr/bin/env python3
"""Offline, deterministic tests for the REGISTRATION instrument (#1680).

No live player, no box, no LLM. The pure agreement math is exercised with an injected paint-score map, and
the end-to-end path is exercised on a SYNTHETIC contract-frame (1344x768) plate whose covered cells are
painted with a high-edge checker (must read painted-blocking) over smooth floor (must read open) — with a
DELIBERATE invisible wall (a collision cell painted open) and a DELIBERATE walk-through (an open cell
painted blocked), so the exact agreement % and both disagreement lists are known ahead of time. Red-first:
these assertions FAIL against a broken projection, a broken floor baseline, or broken agreement math.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

import paint_coherence as P
import registration_score as R
from greybox_render_headless import _fit_ortho_size, PX_W, PX_H


# ── synthetic fixtures ───────────────────────────────────────────────────────────────────────────────
def _synthetic_geometry(cols: int = 9, rows: int = 9) -> dict:
    """Perimeter wall, two doors punched, one interior prop at (4,4). Interior otherwise open floor; the
    plate decides painted open-vs-blocked per cell."""
    walls = [[c, 0] for c in range(cols)] + [[c, rows - 1] for c in range(cols)] \
        + [[0, r] for r in range(rows)] + [[cols - 1, r] for r in range(rows)]
    door_cells = [[cols // 2, 0], [cols - 1, rows // 2]]
    return {"location": "reg_synth", "cols": cols, "rows": rows, "cell_default_walkable": True,
            "walls": walls, "door_cells": door_cells,
            "props": [{"id": "crate", "kind": "crate", "cells": [[4, 4]]}]}


def _paint_plate(geo: dict, ortho: float, covered_cells) -> Image.Image:
    """Smooth mid-grey floor everywhere; a fine high-contrast checker (many edges) over each covered cell
    so it reads furniture-covered. The checker is masked to the cell's projected DIAMOND (not its
    axis-aligned bbox) so it cannot bleed into a neighbour cell's inset floor sample — the exact-score
    assertions depend on that isolation."""
    arr = np.full((PX_H, PX_W, 3), (118, 112, 104), dtype=np.uint8)
    cols, rows = geo["cols"], geo["rows"]
    yy, xx = np.mgrid[0:PX_H, 0:PX_W]
    dark = ((xx // 4) + (yy // 4)) % 2 == 0             # global-pixel-keyed checker (stable across cells)
    for (c, r) in covered_cells:
        quad = P.cell_quad_px(c, r, cols, rows, ortho, inset=0.0)
        pm = Image.new("L", (PX_W, PX_H), 0)
        ImageDraw.Draw(pm).polygon(quad, fill=255)
        poly = np.asarray(pm, dtype=bool)
        arr[poly & dark] = (20, 18, 16)
        arr[poly & ~dark] = (235, 228, 210)
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def geo():
    return _synthetic_geometry()


@pytest.fixture
def ortho(geo):
    return _fit_ortho_size(geo["cols"], geo["rows"])


# ── box footprint -> cells (the boxes-sidecar proxy collision) ───────────────────────────────────────
def test_box_footprint_maps_world_volume_to_cells():
    cols, rows = 9, 9
    # A box exactly over cell (5,3): centre at that cell's world x/z, ~1 cell (2 world units) on a side.
    wx, _, wz = R.cell_to_world(5, 3, cols, rows)
    boxes = [{"kind": "sarcophagus", "center": [wx, 0.55, wz], "size": [1.5, 1.1, 1.5]}]
    assert R.box_footprint_cells(boxes, cols, rows) == {(5, 3)}


def test_box_footprint_excludes_floor_boxes():
    boxes = [{"kind": "floor", "center": [0, -0.05, 0], "size": [32, 0.1, 24]},
             {"kind": "FloorGroutV1", "center": [4, 0.015, 0], "size": [0.13, 0.05, 24]}]
    assert R.box_footprint_cells(boxes, 9, 9) == set()


def test_box_footprint_spans_multiple_cells():
    cols, rows = 9, 9
    wx, _, wz = R.cell_to_world(4, 4, cols, rows)          # a 3-wide box straddles (3,4),(4,4),(5,4)
    boxes = [{"kind": "bar", "center": [wx, 1.0, wz], "size": [5.5, 2.0, 1.5]}]
    got = R.box_footprint_cells(boxes, cols, rows)
    assert {(3, 4), (4, 4), (5, 4)} <= got and (2, 4) not in got and (6, 4) not in got


# ── collision = walkmask-blocked ∪ boxes − doors ─────────────────────────────────────────────────────
def test_collision_union_minus_doors(geo):
    model = P.derive_room(geo)
    cols, rows = geo["cols"], geo["rows"]
    wx, _, wz = R.cell_to_world(3, 3, cols, rows)
    box_cells = R.box_footprint_cells([{"kind": "crate", "center": [wx, 1, wz], "size": [1.5, 1, 1.5]}],
                                      cols, rows)
    coll = R.collision_cells(model, box_cells)
    assert (4, 4) in coll                 # geometry prop footprint
    assert (0, 0) in coll                 # perimeter wall
    assert (3, 3) in coll                 # box footprint
    for d in model.doors:                 # doors are walkable passages, never collision
        assert d not in coll


# ── pure agreement math (injected paint scores) ──────────────────────────────────────────────────────
def test_score_registration_exact_agreement():
    model = P.RoomModel(room="t", cols=5, rows=5, walkable=[], blocked=set(), doors=set(), spawns=[])
    collision = {(1, 1), (2, 2)}
    scores = {(c, r): 0.0 for r in range(5) for c in range(5)}
    scores[(1, 1)] = 0.2      # collision but painted OPEN  -> invisible wall
    scores[(2, 2)] = 5.0      # collision AND painted blocked -> agree
    scores[(3, 3)] = 5.0      # painted blocked, NOT collision -> walk-through
    rep = R.score_registration(model, collision, scores, bar=0.99, block_t=R.BLOCK_T)
    assert rep.invisible_wall_cells == [[1, 1]]
    assert rep.walkthrough_cells == [[3, 3]]
    assert rep.scored_cells == 25
    assert rep.agreement_pct == pytest.approx(round(100.0 * 23 / 25, 3))   # 92.0
    assert rep.passed is False                                             # below the 99% bar


def test_score_registration_all_agree_passes():
    model = P.RoomModel(room="t", cols=3, rows=3, walkable=[], blocked=set(), doors=set(), spawns=[])
    collision = {(1, 1)}
    scores = {(c, r): (5.0 if (c, r) == (1, 1) else 0.0) for r in range(3) for c in range(3)}
    rep = R.score_registration(model, collision, scores, bar=0.99)
    assert rep.invisible_wall_cells == [] and rep.walkthrough_cells == []
    assert rep.agreement_pct == 100.0 and rep.passed is True


# ── end-to-end on a synthetic plate: exact score with a known invisible wall + walk-through ───────────
def test_run_room_synthetic_plate_exact_score(tmp_path):
    # A perimeter-free room so the collision set is exactly the one interior prop cell — the checker paint
    # helper fills each cell's axis-aligned bbox, so a fully-checkered perimeter would bleed into its
    # neighbours; an isolated collision cell keeps the expected lists exact.
    geo = {"location": "reg_synth_open", "cols": 9, "rows": 9, "cell_default_walkable": True,
           "walls": [], "door_cells": [], "props": [{"id": "crate", "kind": "crate", "cells": [[4, 4]]}]}
    ortho = _fit_ortho_size(9, 9)
    model = P.derive_room(geo)
    collision = R.collision_cells(model, set())
    invisible_wall = (4, 4)          # the interior prop footprint cell we leave painted OPEN (on-frame)
    walkthrough = (2, 2)             # an open interior cell we paint BLOCKED (well clear of the prop)
    assert collision == {(4, 4)}
    assert walkthrough not in collision
    plate = _paint_plate(geo, ortho, [walkthrough])   # only the walk-through cell is painted blocked
    plate_path = tmp_path / "synth.png"
    plate.save(plate_path)

    heatmap = tmp_path / "synth_heatmap.png"
    rep = R.run_room(plate_path, geo, ortho, boxes=None, bar=0.99, heatmap_path=heatmap)

    assert rep.invisible_wall_cells == [[4, 4]]
    assert rep.walkthrough_cells == [[2, 2]]
    expected = round(100.0 * (rep.scored_cells - 2) / rep.scored_cells, 3)
    assert rep.agreement_pct == pytest.approx(expected)
    assert rep.passed is False
    assert heatmap.is_file()
    # report shape the seed/CI consumers rely on
    d = rep.as_dict()
    assert set(["room", "agreement_pct", "invisible_wall_cells", "walkthrough_cells", "per_cell"]) <= set(d)
    assert d["counts"] == {"invisible_wall": 1, "walkthrough": 1}


def test_run_room_rejects_off_contract_plate(tmp_path, geo, ortho):
    bad = Image.new("RGB", (640, 480), (118, 112, 104))
    p = tmp_path / "bad.png"
    bad.save(p)
    with pytest.raises(R.HarnessError):
        R.run_room(p, geo, ortho)


def test_run_room_boxes_add_collision(tmp_path, geo, ortho):
    """A boxes-sidecar volume over an open cell turns a painted-blocked cell from a walk-through into an
    agreement (the box now supplies the collision the geometry lacked)."""
    model = P.derive_room(geo)
    cell = (2, 2)
    assert cell not in R.collision_cells(model, set())
    plate = _paint_plate(geo, ortho, [cell])
    p = tmp_path / "synth.png"
    plate.save(p)
    wx, _, wz = R.cell_to_world(*cell, geo["cols"], geo["rows"])
    boxes = [{"kind": "table", "center": [wx, 1.0, wz], "size": [1.5, 1.0, 1.5]}]
    rep_no = R.run_room(p, geo, ortho, boxes=None)
    rep_box = R.run_room(p, geo, ortho, boxes=boxes)
    assert [2, 2] in rep_no.walkthrough_cells
    assert [2, 2] not in rep_box.walkthrough_cells
    assert rep_box.box_cells >= 1


def test_score_manifest_rooms_smoke(tmp_path):
    """The batch runs over the real plates_manifest and produces a per-room agreement % + evidence files
    for at least the boxed owner rooms (crypt/tavern/...). This is the honest-baseline entry point."""
    report = R.score_manifest_rooms(bar=0.99, evidence_dir=tmp_path)
    measured = [r for r in report["rooms"] if r.get("status") not in ("missing", "error")]
    assert measured, f"no manifest room could be measured: {report['rooms']}"
    for r in measured:
        assert 0.0 <= r["agreement_pct"] <= 100.0
        assert (tmp_path / f"{r['registry_key']}_registration.json").is_file()
        assert (tmp_path / f"{r['registry_key']}_heatmap.png").is_file()
