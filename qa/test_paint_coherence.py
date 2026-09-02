#!/usr/bin/env python3
"""Offline, deterministic tests for the paint-coverage coherence instrument (epic #1647 item 4+5).

No live player, no box, no LLM: a SYNTHETIC plate is painted at the contract 1344x768 frame with a busy
high-edge "cabinet" over chosen cells (must classify COVERED) over otherwise smooth floor (must classify
OPEN). Red-first: these assertions FAIL against a broken projection, a broken baseline, or a gate that
lets a spawn/arrival sit on painted furniture. The VQA lane is exercised with an injected STUB scorer.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import paint_coherence as P
from greybox_render_headless import _fit_ortho_size, PX_W, PX_H

_GEO_DIR = Path(__file__).resolve().parent / "room_geometries"


# ── synthetic fixtures ───────────────────────────────────────────────────────────────────────────────
def _synthetic_geometry(cols: int = 9, rows: int = 9) -> dict:
    """A simple room: solid perimeter wall, two door cells punched in it, one small prop so choose_spawns
    has a footprint to avoid. Interior is otherwise open — the plate decides open-vs-covered visually."""
    walls = [[c, 0] for c in range(cols)] + [[c, rows - 1] for c in range(cols)] \
        + [[0, r] for r in range(rows)] + [[cols - 1, r] for r in range(rows)]
    door_cells = [[cols // 2, 0], [cols - 1, rows // 2]]
    return {"location": "synthetic_room", "cols": cols, "rows": rows,
            "cell_default_walkable": True, "walls": walls, "door_cells": door_cells,
            "props": [{"id": "crate", "kind": "crate", "cells": [[2, 2]]}]}


def _paint_plate(geo: dict, ortho: float, covered_cells) -> Image.Image:
    """A contract-frame plate: smooth mid-grey floor everywhere, a fine high-contrast checker (many edges)
    over each `covered_cells` quad so those cells read as furniture-covered, everything else as open floor.
    Vectorized with a numpy checker mask (identical pixel output to the per-pixel loop) so painting a full
    cast/door set doesn't issue tens of thousands of PIL point() calls per plate on the qa/CI image lane."""
    arr = np.full((PX_H, PX_W, 3), (118, 112, 104), dtype=np.uint8)
    cols, rows = geo["cols"], geo["rows"]
    for (c, r) in covered_cells:
        quad = P.cell_quad_px(c, r, cols, rows, ortho, inset=0.0)
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]                 # GLOBAL pixel coords (the checker keys off them)
        dark = ((xx // 4) + (yy // 4)) % 2 == 0
        block = arr[y0:y1, x0:x1]
        block[dark] = (20, 18, 16)
        block[~dark] = (235, 228, 210)
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def geo():
    return _synthetic_geometry()


@pytest.fixture
def ortho(geo):
    return _fit_ortho_size(geo["cols"], geo["rows"])


# ── derive_room: the engine-truth walk model from geometry ───────────────────────────────────────────
def test_derive_room_walkable_blocked_doors(geo):
    m = P.derive_room(geo)
    assert m.doors == {(4, 0), (8, 4)}
    assert (2, 2) in m.blocked                     # the crate prop footprint
    assert (0, 0) in m.blocked and (4, 0) not in m.blocked   # door punched out of the wall
    # every door is walkable; no wall (except a punched door) is walkable
    assert m.doors <= set(m.walkable)
    assert all(cell not in m.blocked or cell in m.doors for cell in m.walkable)


def test_derive_room_matches_real_shop_geometry():
    m = P.derive_room(json.loads((_GEO_DIR / "shop_geometry.json").read_text()))
    assert m.cols == 13 and m.rows == 10
    assert len(m.walkable) == 65                    # the measured shop walkable count
    assert (6, 0) in m.doors and (12, 5) in m.doors


def test_spawns_are_walkable_and_present(geo):
    m = P.derive_room(geo)
    assert m.spawns, "choose_spawns must place a party in a non-degenerate room"
    assert all(s in set(m.walkable) for s in m.spawns)


# ── projection ───────────────────────────────────────────────────────────────────────────────────────
def test_cell_quad_projects_on_image(geo, ortho):
    q = P.cell_quad_px(geo["cols"] // 2, geo["rows"] // 2, geo["cols"], geo["rows"], ortho)
    assert len(q) == 4
    assert all(0 <= x <= PX_W and 0 <= y <= PX_H for x, y in q)


def test_inset_shrinks_the_quad(geo, ortho):
    full = P.cell_quad_px(4, 4, geo["cols"], geo["rows"], ortho, inset=0.0)
    inset = P.cell_quad_px(4, 4, geo["cols"], geo["rows"], ortho, inset=0.3)

    def area(q):
        return abs(sum(q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1] for i in range(4))) / 2
    assert area(inset) < area(full)


# ── the deterministic classifier: covered vs open ───────────────────────────────────────────────────
def test_cabinet_cell_classifies_covered_open_floor_open(geo, ortho):
    covered = [(4, 4), (5, 4)]
    im = _paint_plate(geo, ortho, covered)
    results, meta = P.classify_cells(P.derive_room(geo), ortho, im)
    for cell in covered:
        assert results[cell].verdict == "covered", (cell, results[cell].score)
    # an interior cell far from the painted checker is open floor
    assert results[(6, 6)].verdict == "open", results[(6, 6)].score
    assert meta["low_confidence"] is False


def test_all_open_plate_has_no_covered_cells(geo, ortho):
    im = _paint_plate(geo, ortho, covered_cells=[])
    results, _ = P.classify_cells(P.derive_room(geo), ortho, im)
    assert all(cr.verdict == "open" for cr in results.values())


# ── the gate: spawn / arrival MUST be open (hard fail) ──────────────────────────────────────────────
def test_open_room_gate_passes(geo, ortho):
    im = _paint_plate(geo, ortho, covered_cells=[])
    rep = P.run_room_image(im, geo, ortho)
    assert rep.passed is True
    assert rep.violations["spawn_covered"] == [] and rep.violations["arrival_covered"] == []


def test_spawn_on_painted_furniture_fails_gate(geo, ortho):
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=list(m.spawns))
    rep = P.run_room_image(im, geo, ortho)
    assert rep.passed is False
    assert rep.violations["spawn_covered"], "a spawn under painted furniture is a HARD fail"


def test_arrival_on_painted_furniture_fails_gate(geo, ortho):
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=list(m.doors))
    rep = P.run_room_image(im, geo, ortho)
    assert rep.passed is False
    assert rep.violations["arrival_covered"], "a door-arrival under painted furniture is a HARD fail"


def test_walkable_covered_is_listed_not_failing_by_default(geo, ortho):
    m = P.derive_room(geo)
    walk_only = next(c for c in m.walkable if c not in set(m.spawns) and c not in m.doors
                     and c != (2, 2))
    im = _paint_plate(geo, ortho, covered_cells=[walk_only])
    rep = P.run_room_image(im, geo, ortho)
    assert list(walk_only) in rep.violations["walkable_covered"]
    assert rep.passed is True                       # walkable-covered alone does not fail the gate
    strict = P.run_room_image(im, geo, ortho, fail_on_walkable=True)
    assert strict.passed is False                   # ... but --fail-on-walkable makes it a fail


# ── tri-state: a harness failure is an ERROR, never a verdict ────────────────────────────────────────
def test_wrong_plate_size_raises_harness_error(tmp_path, geo, ortho):
    bad = tmp_path / "bad.png"
    Image.new("RGB", (640, 480), (118, 112, 104)).save(bad)
    with pytest.raises(P.HarnessError):
        P.run_room(bad, geo, ortho)


def test_vqa_scorer_crash_is_harness_error_not_verdict(geo, ortho, tmp_path):
    im = _paint_plate(geo, ortho, covered_cells=[(4, 4)])

    def boom(_img, _qs):
        raise RuntimeError("scorer offline")
    # covered_t high enough that the checker cell lands in the ambiguous band ⇒ the scorer IS invoked.
    with pytest.raises(P.HarnessError):
        P.run_room_image(im, geo, ortho, vqa=True, scorer=boom, covered_t=99.0, overlay_dir=tmp_path)


def test_missing_plate_path_is_harness_error(tmp_path, geo, ortho):
    # #1648 review (unreadable/missing plate path): Image.open must be wrapped as a HarnessError, not a
    # bare traceback the CLI reads as an incoherent (exit 1) verdict.
    with pytest.raises(P.HarnessError):
        P.run_room(tmp_path / "does_not_exist.png", geo, ortho)


def test_incomplete_vqa_answer_is_harness_error(geo, ortho, tmp_path):
    # #1648 review: a scorer that drops a requested flag must ERROR (never silently default the missing
    # cell to "open"), mirroring journey_eval._shell_scorer.
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=[])
    results, _ = P.classify_cells(m, ortho, im)
    results[(6, 6)].verdict = "ambiguous"

    def drops_flag(_img, _questions):
        return {}                                       # answers nothing
    with pytest.raises(P.HarnessError):
        P.adjudicate_ambiguous(results, im, m, ortho, drops_flag, tmp_path / "ov.png")


def test_deterministic_ambiguous_count_survives_vqa(geo, ortho, tmp_path):
    # #1648 review: the VQA path resolves every ambiguous cell before build_report, so ambiguous_cells is
    # [] afterward — deterministic_ambiguous_cells must preserve the pre-adjudication band for auditability.
    im = _paint_plate(geo, ortho, covered_cells=[(4, 4)])

    def all_open(_img, questions):
        return {q["flag"]: False for q in questions}
    rep = P.run_room_image(im, geo, ortho, vqa=True, scorer=all_open, covered_t=99.0, overlay_dir=tmp_path)
    assert rep.method["vqa"] is True
    assert rep.method["ambiguous_cells"] == []          # every ambiguous cell resolved by VQA
    assert rep.method["deterministic_ambiguous_cells"], "the pre-VQA ambiguous band must be preserved"


# ── VQA adjudication (injected stub scorer; ONE batched call) ────────────────────────────────────────
# NOTE on covered_t=99 / injected-ambiguous below: the synthetic fixtures are deliberately BIMODAL — a
# fine high-contrast checker (clearly covered) over a smooth floor (clearly open) — so the DETERMINISTIC
# classifier is unambiguous by construction and its open/covered separation is what these unit tests pin.
# The REAL ambiguous band is a property of the production thresholds (OPEN_T/COVERED_T) against painterly
# plates and is calibration-dependent; it is evidenced by the committed real-room reports
# (qa/evidence/paint-coherence/gate_rooms_deterministic.json shows the per-room ambiguous cells), NOT
# synthesized here. These tests therefore widen covered_t (or inject an ambiguous verdict) to exercise the
# ADJUDICATOR MECHANICS deterministically, without coupling the unit suite to threshold calibration.
def test_vqa_resolves_ambiguous_with_stub(geo, ortho, tmp_path):
    # Force an ambiguous cell by classifying at thresholds that leave a band, then adjudicate.
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=[(4, 4)])
    results, _ = P.classify_cells(m, ortho, im)
    results[(6, 6)].verdict = "ambiguous"           # inject an ambiguous cell
    calls = []

    def stub(img_path, questions):
        calls.append((img_path, [q["flag"] for q in questions]))
        return {q["flag"]: False for q in questions}   # scorer says "open"
    n = P.adjudicate_ambiguous(results, im, m, ortho, stub, tmp_path / "ov.png")
    assert n == 1 and len(calls) == 1                # ONE batched call for the room
    assert results[(6, 6)].verdict == "open" and results[(6, 6)].method == "vqa"


def test_vqa_stub_can_mark_covered(geo, ortho, tmp_path):
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=[])
    results, _ = P.classify_cells(m, ortho, im)
    results[(6, 6)].verdict = "ambiguous"

    def stub(_img, questions):
        return {q["flag"]: True for q in questions}
    P.adjudicate_ambiguous(results, im, m, ortho, stub, tmp_path / "ov.png")
    assert results[(6, 6)].verdict == "covered"


def test_lattice_overlay_labels_map_back_to_cells(geo, ortho, tmp_path):
    m = P.derive_room(geo)
    im = _paint_plate(geo, ortho, covered_cells=[])
    ambiguous = [(4, 4), (5, 5), (6, 6)]
    label_map = P.build_lattice_overlay(im, m, ortho, ambiguous, tmp_path / "ov.png")
    assert (tmp_path / "ov.png").is_file()
    assert set(label_map.values()) == set(ambiguous)
    assert list(label_map.keys()) == ["1", "2", "3"]


# ── report shape (the coherence_report.json contract) ────────────────────────────────────────────────
def test_report_json_shape(geo, ortho):
    im = _paint_plate(geo, ortho, covered_cells=[(4, 4)])
    d = P.run_room_image(im, geo, ortho).as_dict()
    assert set(d) >= {"room", "passed", "cells", "violations", "method"}
    assert set(d["violations"]) == {"walkable_covered", "spawn_covered", "arrival_covered"}
    assert all("," in k for k in d["cells"])         # cells keyed "c,r"
    assert d["method"]["thresholds"]["open_t"] == P.OPEN_T
