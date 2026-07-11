#!/usr/bin/env python3
"""test_journey_visual_sweep.py — deterministic unit tests for the VISUAL JOURNEY instrument's PURE
checks (#1540). No engine, no HTTP, no live viewer: every function here is exercised against synthetic
plates / manifests so the paint-truth logic is pinned independent of the player.

  uv run --directory servers/engine python -m pytest qa/test_journey_visual_sweep.py -q
  (or plain: python -m pytest qa/test_journey_visual_sweep.py -q  — PIL is the only dependency)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

_QA = Path(__file__).resolve().parent
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))

from journey_visual_sweep import (  # noqa: E402
    cell_floor_quad, cell_silhouette_quad, feet_screen, point_in_quad, cell_edge_density,
    inverse_coherence_flags, reciprocal_door_check, hero_feet_check, chebyshev, build_report,
)

COLS, ROWS = 12, 10


# ── geometry ────────────────────────────────────────────────────────────────────────────────────────
def test_feet_project_inside_own_floor_quad_for_every_cell():
    # The contract-camera projection of a cell centre must land inside that cell's own floor quad — the
    # registration-basis invariant hero_feet_check leans on. If this ever fails, the camera basis drifted.
    for c in range(COLS):
        for r in range(ROWS):
            assert point_in_quad(feet_screen(c, r, COLS, ROWS), cell_floor_quad(c, r, COLS, ROWS))


def test_point_in_quad_rejects_a_far_point():
    quad = cell_floor_quad(5, 5, COLS, ROWS)
    assert not point_in_quad((0, 0), quad)          # top-left corner of the frame, far from a mid cell
    assert not point_in_quad((5000, 5000), quad)


# ── edge density ──────────────────────────────────────────────────────────────────────────────────
def _edge_field(painted_cells) -> Image.Image:
    """A synthetic BINARY edge mask (mode 'L', 0/255 — the shape a hard-edge mask emits) with every pixel
    inside the given cells' STANDING-SILHOUETTE bands (where the detector samples) set to 255. This
    isolates the density/flag LOGIC from plate-authoring edge-bleed; whether real paint actually produces
    such hard edges is validated by the live run, not this unit test."""
    im = Image.new("L", (1344, 768), 0)
    d = ImageDraw.Draw(im)
    for (c, r) in painted_cells:
        d.polygon(cell_silhouette_quad(c, r, COLS, ROWS), fill=255)
    return im


def test_edge_density_zero_on_flat_floor_high_on_a_painted_object():
    edges = _edge_field([(5, 5)])
    clean = cell_edge_density(edges, cell_silhouette_quad(2, 2, COLS, ROWS))
    painted = cell_edge_density(edges, cell_silhouette_quad(5, 5, COLS, ROWS))
    assert clean < 0.05
    assert painted > 0.9 and painted > clean * 5


# ── inverse coherence (the painted-object detector) ─────────────────────────────────────────────────
def test_inverse_coherence_flags_the_invented_object_and_not_distant_clean_floor():
    # A wide walkable patch; an unauthored "bench" paints a hard silhouette onto ONE cell. The detector
    # must flag that cell (a tall object's band overlaps its immediate neighbours, so a small local
    # CLUSTER flagging is expected + fine — it's a region), and must NOT flag clean floor across the room.
    walkable = [(c, r) for c in range(2, 10) for r in range(2, 8)]
    bench = (8, 3)
    far_clean = (2, 7)
    edges = _edge_field([bench])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic")
    flagged = {tuple(f["cell"]) for f in res.flagged}
    assert bench in flagged, f"the painted bench cell must flag; flagged={flagged}"
    assert far_clean not in flagged, f"distant clean floor must not flag; flagged={flagged}"
    assert len(flagged) <= 8, f"an object should flag a small local cluster, not the room; flagged={flagged}"


def test_inverse_coherence_ignores_authored_prop_cells():
    # A cell the manifest ALREADY authors as a prop footprint is excluded (it's supposed to be painted).
    prop = (5, 5)
    walkable = [(c, r) for c in range(3, 8) for r in range(3, 8)]
    edges = _edge_field([prop])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], {prop}, COLS, ROWS, "synthetic")
    assert prop not in {tuple(f["cell"]) for f in res.flagged}


# ── reciprocal door ────────────────────────────────────────────────────────────────────────────────
def test_reciprocal_door_pass_when_arrival_is_on_the_return_door():
    doors = [{"cell": [5, 0], "to": "crypt"}, {"cell": [11, 9], "to": "cellar"}]
    res = reciprocal_door_check((5, 1), doors, "crypt", max_cheb=2)
    assert res["pass"] and res["cheb"] == 1


def test_reciprocal_door_fails_when_dumped_across_the_room():
    # crossing crypt->camp today lands the party at the camp spawn, far from the door back to the crypt.
    doors = [{"cell": [5, 0], "to": "crypt"}]
    res = reciprocal_door_check((6, 9), doors, "crypt", max_cheb=2)
    assert not res["pass"] and res["cheb"] == 9 and "across the room" in res["reason"]


def test_reciprocal_door_fails_when_no_door_back_exists():
    res = reciprocal_door_check((4, 4), [{"cell": [1, 1], "to": "elsewhere"}], "crypt", max_cheb=2)
    assert not res["pass"] and "reciprocal door missing" in res["reason"]


def test_chebyshev():
    assert chebyshev((0, 0), (3, 2)) == 3 and chebyshev((6, 9), (5, 0)) == 9


# ── hero position ────────────────────────────────────────────────────────────────────────────────
def test_hero_feet_pass_on_clean_cell():
    res = hero_feet_check((3, 5), COLS, ROWS, flagged_cells=set())
    assert res["pass"] and res["feet_in_quad"] and not res["on_flagged_cell"]


def test_hero_feet_fail_when_standing_on_a_flagged_object_cell():
    res = hero_feet_check((3, 5), COLS, ROWS, flagged_cells={(3, 5)})
    assert not res["pass"] and res["on_flagged_cell"] and "actor-inside-the-object" in res["reason"]


def test_hero_feet_fail_when_no_token():
    assert not hero_feet_check(None, COLS, ROWS, set())["pass"]


# ── CLEAN% aggregation ────────────────────────────────────────────────────────────────────────────
def test_build_report_clean_pct_and_finding_counts():
    room_recs = [{"room": "r1", "plate_status": "resolved", "n_walkable_floor": 10,
                  "flagged_cells": [[6, 3]],  # 1 invented-furniture flag
                  "hero_checks": [{"step": 0, "pass": True}]}]
    steps = [{"step": 0, "kind": "spawn", "room": "r1", "hero_check": {"pass": True}},
             {"step": 1, "kind": "arrive", "room": "r1", "hero_check": {"pass": False}}]
    transitions = [{"from": "r1", "to": "r2", "crossed": True,
                    "reciprocal": {"pass": False, "reason": "across the room"}}]
    rep = build_report(room_recs, steps, transitions, "r1")
    row = rep["per_room"][0]
    # num = hero_pass(1) + clean_cells(10-1=9) = 10 ; den = hero_steps(2) + floor(10) = 12 -> 83.3%
    assert row["clean_pct"] == 83.3 and not row["meets_95"]
    assert rep["findings_by_class"] == {"invented_furniture_flags": 1,
                                        "reciprocal_door_failures": 1, "hero_position_failures": 1}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
