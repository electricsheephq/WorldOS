#!/usr/bin/env python3
"""Red-first units for the per-object alignment gate (qa/object_align_check.py).

Both cases here are the review findings the instrument shipped with (2026-09-02): a FEATURELESS crop
pair correlates to a perfect dx=dy=0 with a dead peak (a deleted object reads as aligned), and the
kinds the gate cannot measure were dropped from the verdict entirely (a green line that checked
nothing). Each test drives the real CLI over synthetic plates, so a regression in either is red
pre-merge.

Run: uv run --directory servers/engine python -m pytest qa/test_object_align_check.py -q -p no:xdist
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import object_align_check as OAC  # noqa: E402

ORTHO = 10.5224  # the shipped tavern pin — px/cell lands at the real gate's scale


def _write_png(path: Path, arr):
    Image.fromarray(arr.astype(np.uint8), mode="L").save(path)


def _flat_frame(value=128):
    return np.full((OAC.PX_H, OAC.PX_W), value, dtype=np.uint8)


def _textured_patch(h, w, seed=7):
    """A high-contrast blocky patch — a real correlation peak, not smooth noise."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, size=(h // 8 + 1, w // 8 + 1))
    return np.kron(small, np.ones((8, 8), dtype=np.int64))[:h, :w]


def _sidecar(tmp_path, boxes):
    p = tmp_path / "boxes.json"
    p.write_text(json.dumps({"version": 1, "ortho": ORTHO, "cols": 14, "rows": 11, "boxes": boxes}))
    return p


def _run(tmp_path, boxes, base, styled, extra=()):
    """Drive the real CLI; returns (exit_code, stdout)."""
    bp, sp = tmp_path / "base.png", tmp_path / "styled.png"
    _write_png(bp, base)
    _write_png(sp, styled)
    argv = ["object_align_check.py", str(_sidecar(tmp_path, boxes)), str(bp), str(sp), *extra]
    old = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit) as exc:
            OAC.main()
        return exc.value.code, None
    finally:
        sys.argv = old


def _run_capture(capsys, tmp_path, boxes, base, styled, extra=()):
    code, _ = _run(tmp_path, boxes, base, styled, extra)
    return code, capsys.readouterr().out


# --- finding 1: a featureless pair must NOT report a perfect alignment -----------------------------
def test_featureless_pair_is_low_confidence_and_fails(capsys, tmp_path):
    """Two flat crops mean-centre to zero: argmax returns (0,0) with a dead peak. Before the response
    floor this printed OK and exited 0 — a deleted object certified as aligned."""
    box = {"kind": "table", "center": [0.0, 0.4, 0.0], "size": [2.8, 0.8, 2.8]}
    code, out = _run_capture(capsys, tmp_path, [box], _flat_frame(), _flat_frame())

    assert "LOW-CONFIDENCE" in out, out
    assert "resp=" in out, out
    assert "PER-OBJECT-ALIGNED" not in out, out
    assert "1 low-confidence" in out, out
    assert code == 1, out


def test_min_resp_is_tunable_from_the_cli(capsys, tmp_path):
    """The floor is a calibration knob, not a constant — a room whose known-aligned fixtures measure
    lower must be able to lower it without editing the instrument."""
    box = {"kind": "table", "center": [0.0, 0.4, 0.0], "size": [2.8, 0.8, 2.8]}
    code, out = _run_capture(capsys, tmp_path, [box], _flat_frame(), _flat_frame(),
                             extra=["--min-resp", "0.0"])
    assert "LOW-CONFIDENCE" not in out, out
    assert "min_resp=0.0" in out, out
    assert code == 0, out


# --- the measurement itself: a shifted textured object reports its real offset ---------------------
def test_shifted_textured_square_measures_its_offset(capsys, tmp_path):
    """A real object displaced by a known pixel offset must be measured within a couple of px —
    otherwise the budget comparison is meaningless."""
    box = {"kind": "table", "center": [0.0, 0.4, 0.0], "size": [2.8, 0.8, 2.8]}
    x0, y0, x1, y1 = OAC.box_screen_bbox(box, ORTHO, 40)
    h, w = y1 - y0, x1 - x0
    assert h >= OAC.MIN_WINDOW_PX and w >= OAC.MIN_WINDOW_PX, "fixture box must be measurable"

    patch = _textured_patch(h, w)
    base = _flat_frame()
    base[y0:y1, x0:x1] = patch

    shift_x, shift_y = 6, 4
    styled = _flat_frame()
    styled[y0 + shift_y:y1 + shift_y, x0 + shift_x:x1 + shift_x] = patch

    code, out = _run_capture(capsys, tmp_path, [box], base, styled)

    line = next(ln for ln in out.splitlines() if "table#0" in ln)
    dx = int(line.split("dx=")[1].split()[0])
    dy = int(line.split("dy=")[1].split()[0])
    assert abs(dx - shift_x) <= 2, line
    assert abs(dy - shift_y) <= 2, line
    assert "LOW-CONFIDENCE" not in line, line
    # 7.2px at ~73 px/cell is ~0.1 cells — inside the default 0.35 budget.
    assert code == 0, out


# --- finding 3: cell error must go through the ground-plane basis, not a scalar --------------------
def test_ground_plane_basis_round_trips_a_one_cell_step():
    """A one-cell step along +X must invert back to exactly (1, 0) cells, and along +Z to (0, 1)."""
    basis = OAC.ground_plane_basis(ORTHO)
    (ux, uy), (vx, vy) = basis
    cx, cz = OAC.screen_to_cells(ux, uy, basis)
    assert abs(cx - 1.0) < 1e-6 and abs(cz) < 1e-6, (cx, cz)
    cx, cz = OAC.screen_to_cells(vx, vy, basis)
    assert abs(cx) < 1e-6 and abs(cz - 1.0) < 1e-6, (cx, cz)


def test_the_scalar_px_per_cell_understated_drift():
    """The old scalar was (PX_H / 2*ortho) * 2 — the LARGER of the two ground-plane cell spans, so it
    divided every offset by the most generous number available. These two screen offsets both scored
    under a 0.35-cell budget under the scalar and are over it through the basis."""
    basis = OAC.ground_plane_basis(ORTHO)
    scalar = (OAC.PX_H / (2.0 * ORTHO)) * 2.0
    for dx, dy in ((21, -10), (0, -15)):
        old = math.hypot(dx, dy) / scalar
        cx, cz = OAC.screen_to_cells(dx, dy, basis)
        new = math.hypot(cx, cz)
        assert old < 0.35 < new, f"screen({dx},{dy}): old={old:.3f} new={new:.3f}"


@pytest.mark.parametrize("shift_x,shift_y,label", [(21, -10, "screen-horizontal"), (0, -15, "screen-vertical")])
def test_over_budget_shift_is_drifted_on_both_axes(capsys, tmp_path, shift_x, shift_y, label):
    """The boundary case on both projected axes: an over-budget displacement must FAIL, including the
    screen-vertical direction where the ground plane is most foreshortened (~36px/cell, half the
    horizontal span) and the scalar was most wrong."""
    box = {"kind": "table", "center": [0.0, 0.4, 0.0], "size": [2.8, 0.8, 2.8]}
    x0, y0, x1, y1 = OAC.box_screen_bbox(box, ORTHO, 40)
    patch = _textured_patch(y1 - y0, x1 - x0)
    base = _flat_frame()
    base[y0:y1, x0:x1] = patch
    styled = _flat_frame()
    styled[y0 + shift_y:y1 + shift_y, x0 + shift_x:x1 + shift_x] = patch

    code, out = _run_capture(capsys, tmp_path, [box], base, styled)

    line = next(ln for ln in out.splitlines() if "table#0" in ln)
    assert "DRIFTED" in line, f"{label}: {line}"
    assert code == 1, out


# --- finding 2: unmeasured objects must be reported, never silently exempted -----------------------
def test_skipped_kinds_are_counted_and_named_in_the_verdict(capsys, tmp_path):
    """wall/parapet/floor/door stay skipped (a long uniform run has no localisable peak) but the
    verdict must say how many and which — a green line that measured nothing is the failure mode."""
    boxes = [
        {"kind": "wallback", "center": [0.0, 2.7, 10.0], "size": [26.0, 5.4, 1.4]},
        {"kind": "parapet", "center": [0.0, 0.28, -10.0], "size": [26.0, 0.55, 1.4]},
        {"kind": "floor", "center": [0.0, -0.05, 0.0], "size": [28.0, 0.1, 22.0]},
        {"kind": "table", "center": [0.0, 0.4, 0.0], "size": [2.8, 0.8, 2.8]},
    ]
    x0, y0, x1, y1 = OAC.box_screen_bbox(boxes[3], ORTHO, 40)
    frame = _flat_frame()
    frame[y0:y1, x0:x1] = _textured_patch(y1 - y0, x1 - x0)

    code, out = _run_capture(capsys, tmp_path, boxes, frame, frame)

    verdict = out.strip().splitlines()[-1]
    assert "skipped_kinds=3" in verdict, verdict
    for kind in ("wallback", "parapet", "floor"):
        assert kind in verdict, verdict
    assert "checked=1" in verdict, verdict
    assert code == 0, out


def test_too_small_boxes_are_reported_not_dropped(capsys, tmp_path):
    """A box whose projected bbox is under the FFT window is unmeasurable — it must still surface in
    the verdict's skipped_too_small count."""
    boxes = [{"kind": "mug", "center": [0.0, 0.9, 0.0], "size": [0.05, 0.05, 0.05]}]
    # pad=0 so the tiny box is not inflated past the window by its context margin
    code, out = _run_capture(capsys, tmp_path, boxes, _flat_frame(), _flat_frame(), extra=["--pad", "0"])

    assert "SKIPPED-TOO-SMALL" in out, out
    verdict = out.strip().splitlines()[-1]
    assert "skipped_too_small=1" in verdict, verdict
    assert "checked=0" in verdict, verdict
    assert code == 0, out
