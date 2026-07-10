"""Self-tests for qa/seed_gfx_combat.py's authored crypt scene_grid (#1396, #1386 PROBE-PLACEMENT).

#1396: crypt_dense_v1's engine impassable cells didn't match the PAINTED plate props — the
scene_grid carried the OLDER crypt_firelit_v2 greybox cells (pillarL(2,3) / pillarR(11,3) /
sarcophagus(7,1)), but the deployed crypt_dense_v1.png plate (after a Gemini polish+populate
repaint pass) shows the pillars + sarcophagus at different cells, so actors could be placed
"on" the painted sarcophagus with no pathing obstacle underneath them.

#1386 PROBE-PLACEMENT (2026-07-10): the SAME class of drift recurred when the PLATE SPRINT
ADOPT-CRYPT lane promoted `crypt_armb_iter3_v1.png` (PR #1489) as the new canonical plate —
its re-styled sarcophagus repaints noticeably LARGER (now cols 3-9 x rows 3-7, up from the
#1396 ~2-cell footprint), swallowing the hero(6,6)/goblin(9,5) spawn cells entirely (both
baseline-cast actors rendered standing ON the tomb — qa/evidence/plate-sprint/adopt-crypt/
cohesion-frames/). Re-measured against the deployed plate the same way; hero/goblin were
relocated to (11,3)/(1,8), clear of the widened footprint (a first pass at (2,7) still
read as touching the tomb's front-left corner in a blind cohesion panel; widened once more).

This is a RED-FIRST regression: before the #1396 content fix, the sarcophagus footprint
below was NOT impassable (it sat at the stale (7,1) cell instead), so this test would have
failed against the pre-fix seed. Cell values re-derived by projecting the qa/export_scene_grid.py
contract camera (orthographic, size=13, Euler(30,45,0)) onto the live crypt_dense_v1.png
captures in qa/evidence/1392/*.jpg, calibrated against the logical_cell -> screen_bbox/
floor_y_px manifests in qa/evidence/1397/ + qa/evidence/1408/ (which reproduce pixel-exact
under that camera model); the #1386 sarcophagus widening reused the identical recipe against
the crypt_armb_iter3_v1.png plate pulled off the box (itself 1344x768 == the contract
resolution, so the overlay needed no rescale).

Run with the engine venv (pydantic + pytest live there):
    uv run --directory servers/engine python -m pytest ../../qa/test_seed_gfx_combat.py -p no:cacheprovider
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "qa"))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import server  # noqa: E402,F401  imported FIRST: resolves the models<->scene_grid import cycle
import seed_gfx_combat as sg  # noqa: E402
from scene_grid import validate_scene_grid, impassable_cells  # noqa: E402


def _grid():
    return sg._build_crypt_grid(sg.CID, "loc-test")


def test_scene_grid_has_zero_validate_violations():
    """The pre-greybox gate (door zones / protected lanes / connectivity) must stay clean."""
    grid = _grid()
    assert validate_scene_grid(grid, sg.GRID_W, sg.GRID_H) == []


def test_sarcophagus_footprint_is_impassable():
    """OWNER PLAYTEST #4 correction (#1505, re-measured from #1386): the sarcophagus's impassable
    footprint is the coffin's FLOOR-CONTACT cells on the deployed crypt_armb_iter3_v1.png plate —
    cols 2-7 x rows 7-9 — so no actor stands ON the painted box. #1386 pinned the coffin's tall
    SILHOUETTE (cols3-9 x rows3-7, the open floor BEHIND the coffin under the iso projection) and
    left the true floor footprint walkable — the exact reason the owner walked his character onto
    the painted tomb. Cells derived by point-in-polygon of each cell's grounded projection vs the
    coffin's measured floor parallelogram (verified greybox rig, <1e-3 vs Unity)."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.SARCOPHAGUS_CELLS == [[c, r] for c in range(2, 8) for r in range(7, 10)]
    for cell in sg.SARCOPHAGUS_CELLS:
        assert cell in imp, f"sarcophagus cell {cell} must be impassable (paint/grid registration gap, #1505)"


def test_pillars_match_painted_cells():
    """Both pillar footprints must sit on their re-calibrated painted cells, not the stale
    crypt_firelit_v2 positions (2,3)/(11,3)."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.PILLAR_L_CELLS == [[2, 4]]
    assert sg.PILLAR_R_CELLS == [[9, 9]]
    assert [2, 4] in imp
    assert [9, 9] in imp
    # the STALE pre-#1396 prop cells must no longer be authored as prop footprints.
    prop_cells = {(c0, r0) for prop in grid.props for (c0, r0) in prop.cells}
    assert (2, 3) not in prop_cells
    assert (11, 3) not in prop_cells
    assert (7, 1) not in prop_cells


def test_hero_and_goblin_spawn_cells_stay_walkable():
    """The hero(11,3)/goblin(1,8) spawn cells (far back-right / front-left, both clear of the
    corrected front-center tomb footprint, #1505) must never collide with the prop footprints — a
    content fix here must not accidentally trap the demo's own combatants."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.HERO_CELL not in imp
    assert sg.GOBLIN_CELL not in imp
    # the coffin's front floor-footprint cells (where the OLD walkslice party at r=8 stood ON the
    # painted tomb, #1505) — pin a couple so a future footprint edit can't silently re-open them.
    assert [6, 8] in imp
    assert [4, 9] in imp


def test_obstacles_list_matches_authored_props():
    """OBSTACLES (used for the printed seed summary + kept in lock-step with set_grid) must be
    exactly the flattened pillar/sarcophagus footprints — no silent drift between the two."""
    assert sg.OBSTACLES == sg.PILLAR_L_CELLS + sg.PILLAR_R_CELLS + sg.SARCOPHAGUS_CELLS
