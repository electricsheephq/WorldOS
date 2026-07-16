#!/usr/bin/env python3
"""Offline tests for the deterministic plate re-registration (qa/reregister_plate.py).

Three guards:
  (a) synthetic round-trip — a "correct" plate (fire blobs at the stamped-ortho brazier projections)
      is drifted by a KNOWN scale+translate (the Gemini-pass signature), and the solver+warp is shown
      to recover it back inside the shipped-registration class.
  (b) the real dwing_room_0 plate in the worktree re-registers to <= 0.35 err_cells.
  (c) overlay_boxes.py --solve CLI output shape is unchanged after the refactor.

No network, no randomness. PIL/numpy-gated via importorskip.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

QA = Path(__file__).resolve().parent
REPO = QA.parent
sys.path.insert(0, str(QA))

BOXES0 = REPO / "extensions/renderers/unity/boxes/dwing_room_0_v1_boxes.json"
PLATE0 = REPO / "extensions/renderers/unity/plates/dwing_room_0_v1_registered.png"

W, H = 1344, 768
_STAMPED = 11.0
# Two brazier stacks (y=0.85 + y=1.85) at two distinct (x,z) footprints — dress_focal's beacon shape.
_SYN_BOXES = {
    "version": "test", "ortho": _STAMPED, "cols": 20, "rows": 20,
    "boxes": [
        {"name": "brazier_a0", "kind": "brazier", "center": [-3.0, 0.85, 4.0], "size": [0.8, 0.85, 0.8]},
        {"name": "brazier_a1", "kind": "brazier", "center": [-3.0, 1.85, 4.0], "size": [0.8, 0.85, 0.8]},
        {"name": "brazier_b0", "kind": "brazier", "center": [5.0, 0.85, 4.0], "size": [0.8, 0.85, 0.8]},
        {"name": "brazier_b1", "kind": "brazier", "center": [5.0, 1.85, 4.0], "size": [0.8, 0.85, 0.8]},
    ],
}


def _max_err(solve: dict) -> float:
    return max(p["err_cells"] for p in solve["per_bowl_at_stamp"])


def test_synthetic_round_trip(tmp_path):
    """Scale+offset a synthetic correct plate by a known transform; solver+warp recovers it."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    import greybox_render_headless as G
    from overlay_boxes import blob_solve
    from reregister_plate import reregister

    # bowls = max-y box per (x,z) group — where blob_solve measures err (world_to_screen at y+0.5).
    bowls = [(-3.0, 1.85, 4.0), (5.0, 1.85, 4.0)]

    def _draw(positions, path):
        im = Image.new("RGB", (W, H), (13, 13, 18))
        d = ImageDraw.Draw(im)
        for (px, py) in positions:
            d.ellipse([px - 11, py - 11, px + 11, py + 11], fill=(255, 160, 40))  # warm blob (mask-hit)
        im.save(path)

    # "Correct" plate: fire exactly at the stamped-ortho projection of each bowl.
    correct_pos = [G.world_to_screen(x, y + 0.5, z, ortho_size=_STAMPED) for (x, y, z) in bowls]
    correct = tmp_path / "correct.png"
    _draw(correct_pos, correct)
    assert _max_err(blob_solve(_SYN_BOXES, correct)) <= 0.35  # sanity: the correct plate is registered

    # Drift it by a KNOWN forward transform k*(p-center)+center + offset (the Gemini rescale+shift).
    k, ox, oy = 1.25, 40.0, -90.0
    drift_pos = [(k * (px - W / 2) + W / 2 + ox, k * (py - H / 2) + H / 2 + oy) for (px, py) in correct_pos]
    drifted = tmp_path / "drifted.png"
    _draw(drift_pos, drifted)
    assert _max_err(blob_solve(_SYN_BOXES, drifted)) > 0.35  # the drift is a real, detectable misregistration

    out = tmp_path / "recovered.png"
    rep = reregister(_SYN_BOXES, drifted, out)
    assert rep["passed"], rep
    assert rep["after"]["max_err_cells"] <= 0.35
    assert rep["after"]["max_err_cells"] < rep["before"]["max_err_cells"]
    assert out.is_file()


@pytest.mark.skipif(not (BOXES0.is_file() and PLATE0.is_file()), reason="dwing_room_0 artifacts absent")
def test_real_dwing_room_0_registers(tmp_path):
    """The real dwing_room_0 plate in the worktree re-registers to <= 0.35 err_cells."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from reregister_plate import reregister

    boxes = json.loads(BOXES0.read_text())
    out = tmp_path / "dwing0_corrected.png"
    rep = reregister(boxes, PLATE0, out)
    assert rep["passed"], rep
    assert rep["after"]["max_err_cells"] <= 0.35


@pytest.mark.skipif(not (BOXES0.is_file() and PLATE0.is_file()), reason="dwing_room_0 artifacts absent")
def test_overlay_boxes_solve_cli_shape():
    """overlay_boxes.py --solve output shape is unchanged after the refactor."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    proc = subprocess.run(
        [sys.executable, str(QA / "overlay_boxes.py"), str(BOXES0), str(PLATE0), "--solve"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for key in ("residual_px", "fitted_ortho", "screen_offset", "stamped_ortho",
                "n_blobs", "n_bowls", "per_bowl_at_stamp"):
        assert key in out, f"missing key {key} in {out}"
    assert isinstance(out["screen_offset"], list) and len(out["screen_offset"]) == 2
    assert out["per_bowl_at_stamp"], "expected at least one per-bowl entry"
    for entry in out["per_bowl_at_stamp"]:
        for k in ("bowl_world", "err_px", "err_cells"):
            assert k in entry, f"missing per-bowl key {k}"
