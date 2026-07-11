"""Tests for the EXTENT CONTRACT (#1543 / M-ALIGN): the painted room must equal the playable grid.

Covers the RECIPE half — greybox_render_headless's opt-in CAMERA-FIT mode + tools/author_room_geometry's
perimeter wall band — and, load-bearingly, that both are STRICTLY opt-in so the fixed-rig math the
registration/coherence instruments (check_grid_paint_coherence.py, check_plate_drift.py,
journey_visual_sweep.py) share is byte-identical for every existing room.

    python3 -m pytest qa/test_greybox_extent_contract.py -q -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

_QA = Path(__file__).resolve().parent
_TOOLS = _QA.parent / "tools"
sys.path.insert(0, str(_QA))
sys.path.insert(0, str(_TOOLS))

import greybox_render_headless as g  # noqa: E402
import author_room_geometry as ar  # noqa: E402

_BG = (13, 13, 18)  # the render's background clear colour


def _width_fill(path) -> float:
    """Fraction of frame width spanned by non-background pixels."""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    xs = [x for x in range(w) if any(px[x, y] != _BG for y in range(0, h, 2))]
    assert xs, "render is entirely background"
    return (max(xs) - min(xs)) / w


# ---------------------------------------------------------------------------
# CAMERA-FIT: the grid diamond fills the frame edge-to-edge
# ---------------------------------------------------------------------------

def test_camera_fit_fills_at_least_90pct_of_frame_width(tmp_path):
    """The deterministic pixel assert the issue names: a camera-fit greybox of the tavern geometry
    shows the diamond + wall band filling >=90% of the 1344-px frame width."""
    geo = ar.author_tavern_fit()
    out = tmp_path / "camerafit.png"
    g.render(geo, str(out), camera_fit=True)
    assert _width_fill(out) >= 0.90


def test_camera_fit_reads_the_geometry_opt_in_field(tmp_path):
    """author_tavern_fit() stamps camera_fit:true, so render() picks it up WITHOUT an explicit arg."""
    geo = ar.author_tavern_fit()
    assert geo.get("camera_fit") is True
    out = tmp_path / "field.png"
    g.render(geo, str(out))  # no camera_fit arg — comes from the geometry field
    assert _width_fill(out) >= 0.90


def test_fixed_rig_leaves_margin_that_camera_fit_removes(tmp_path):
    """The defect and the fix in one assert: the same geometry under the fixed ortho=13 rig fills far
    less of the frame (the margin the style pass out-paints) than under camera-fit."""
    geo = ar.author_tavern_fit()
    fixed = tmp_path / "fixed.png"
    fit = tmp_path / "fit.png"
    g.render(geo, str(fixed), camera_fit=False)
    g.render(geo, str(fit), camera_fit=True)
    assert _width_fill(fixed) < 0.75          # fixed rig leaves a wide margin
    assert _width_fill(fit) > _width_fill(fixed) + 0.15


# ---------------------------------------------------------------------------
# OPT-IN: the fixed projection the instruments share is untouched
# ---------------------------------------------------------------------------

def test_world_to_screen_default_is_the_fixed_ortho():
    """Every existing caller omits the ortho_size arg; it must be identical to passing ORTHO_SIZE."""
    for w in [(0.0, 0.0, 0.0), (5.0, 2.0, -3.0), (-11.0, 9.0, 9.0)]:
        assert g.world_to_screen(*w) == g.world_to_screen(*w, g.ORTHO_SIZE)


def test_camera_fit_zooms_in_relative_to_the_fixed_rig():
    """A room smaller than the fixed rig's field fits to a SMALLER ortho (zoom in), never larger."""
    assert g._fit_ortho_size(12, 10) < g.ORTHO_SIZE


def test_non_camera_fit_render_is_byte_identical_with_and_without_explicit_false(tmp_path):
    """render(camera_fit=None) with no geometry field == render(camera_fit=False) — the default path."""
    geo = ar.author_tavern()  # the deployed tavern geometry: no camera_fit field
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    g.render(geo, str(a))
    g.render(geo, str(b), camera_fit=False)
    assert a.read_bytes() == b.read_bytes()


def test_pre_existing_stone_wall_kind_is_not_treated_as_a_wall_run():
    """camp's 'stone_wall' prop must keep its _DEFAULT_SPEC square-box rendering — only the exact
    'wall_run'/'perimeter_wall' kinds get the thin-wall-band treatment (guards the substring trap)."""
    assert not g._is_wall_run_kind("stone_wall")
    assert g._is_wall_run_kind("wall_run")
    assert g._is_wall_run_kind("perimeter_wall")


# ---------------------------------------------------------------------------
# PERIMETER WALL BAND: continuous runs, door left open, cells impassable
# ---------------------------------------------------------------------------

def test_wall_band_is_authored_as_continuous_runs_not_per_cell():
    """Each edge is one wall_run prop per contiguous run (the #1539 no-crenellation rule), and the
    door gap splits its edge into two runs — never a box per cell."""
    geo = ar.author_tavern_fit()
    runs = [p for p in geo["props"] if p["kind"] == "wall_run"]
    assert runs, "no wall_run props authored"
    for p in runs:
        cells = [tuple(c) for c in p["cells"]]
        assert len(cells) >= 1
        # a run is contiguous along exactly one axis (constant row or constant col, unit steps)
        cols = {c for c, _ in cells}
        rows = {r for _, r in cells}
        assert len(cols) == 1 or len(rows) == 1
        varying = sorted(r for _, r in cells) if len(cols) == 1 else sorted(c for c, _ in cells)
        assert varying == list(range(varying[0], varying[0] + len(varying)))  # no gaps within a run
    # the top edge (door at (8,0)) is split into two runs around the gap
    top = sorted((tuple(c) for p in runs if p["id"].startswith("wall_n") for c in p["cells"]))
    assert (8, 0) not in top


def test_door_cell_is_walkable_and_perimeter_is_impassable():
    """Painted walls sit ON impassable cells by construction; the door cell stays walkable (the old
    per-cell perimeter walled it shut)."""
    geo = ar.author_tavern_fit()
    imp = {tuple(c) for c in geo["impassable"]}
    assert (8, 0) not in imp                       # door open
    non_door_top = [(c, 0) for c in range(12) if c != 8]
    assert all(cell in imp for cell in non_door_top)   # rest of the band is solid
