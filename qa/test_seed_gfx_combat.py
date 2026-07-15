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
    """CRYPT-ALIGN-V2 (M-ALIGN, 2026-07-15): fit-camera overlay forensics (ortho=10.5224) proved flux
    depth-CN RELOCATED the sarcophagus during the style pass — the crypt_fresh_v1 plate paints it as a
    MONUMENTAL tomb across the BACK band (cols 7-12 x rows 3-4), NOT the authored 2x2 coffin at cols4-5 x
    rows7-8 (which the plate paints as OPEN FLOOR). The collision is realigned to the paint; the coffin is
    trimmed one cell at the east end to cols 7-11 (a 5x2 tomb) so a prop never sits in the tavern-door
    (13,4) zone. The old coffin cells (rows 7-8) are freed back to walkable floor. See
    qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md (v2 addendum)."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.SARCOPHAGUS_CELLS == [
        [7, 3], [8, 3], [9, 3], [10, 3], [11, 3],
        [7, 4], [8, 4], [9, 4], [10, 4], [11, 4],
    ]
    for cell in sg.SARCOPHAGUS_CELLS:
        assert cell in imp, f"sarcophagus cell {cell} must be impassable (paint/grid registration)"
    # the old authored 2x2 coffin cells (cols4-5 x rows7-8) are now painted OPEN FLOOR -> must be walkable.
    for cell in ([4, 7], [5, 7], [4, 8], [5, 8]):
        assert cell not in imp, f"old coffin cell {cell} is painted clear floor now — must be walkable (v2)"
    # the tomb's trimmed east end: (12,4) is walkable (door landing); the 1-cell paint overhang residual.
    assert [12, 4] not in imp, "coffin east end trimmed to col 11 for the tavern-door zone (v2 residual)"


def test_pillars_match_painted_cells():
    """CRYPT-ALIGN-V2: the painted LEFT pillar plinth sits at (4,2)/(4,3) (authored (3,3)/(3,4) is painted
    clear floor). pillar_r (8,9)/(9,9) is DELETED — it renders behind the wall_height=5 cutaway's south
    wall band (invisible in the greybox) and its cells are painted clear floor."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.PILLAR_L_CELLS == [[4, 2], [4, 3]]
    assert not hasattr(sg, "PILLAR_R_CELLS"), "pillar_r is deleted in CRYPT-ALIGN-V2"
    for cell in sg.PILLAR_L_CELLS:
        assert cell in imp, f"pillar_l base cell {cell} must be impassable"
    # the deleted pillar_r cells + stale pre-align prop cells must no longer be authored as prop footprints.
    prop_cells = {(c0, r0) for prop in grid.props for (c0, r0) in prop.cells}
    for stale in [(3, 3), (3, 4), (8, 9), (9, 9), (2, 9), (3, 9), (11, 9)]:
        assert stale not in prop_cells, f"stale/deleted prop cell {stale} must not be a prop footprint (v2)"


def test_hero_and_goblin_spawn_cells_stay_walkable():
    """The hero(11,8)/goblin(1,8) spawn cells must never collide with the prop footprints. CRYPT-ALIGN-V2:
    the tomb moved to the back band (rows 3-4), so the old hero cell (11,3) now sits ON it — hero moved to
    the open south-right floor."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.HERO_CELL not in imp
    assert sg.GOBLIN_CELL not in imp
    # the tomb's floor-footprint cells — pin a couple so a future footprint edit can't silently re-open
    # them (an actor standing ON the painted tomb). CRYPT-ALIGN-V2: tomb is now cols7-11 x rows3-4.
    assert [7, 3] in imp
    assert [11, 4] in imp


def test_obstacles_list_matches_authored_props():
    """OBSTACLES (used for the printed seed summary + kept in lock-step with set_grid) must be
    exactly the flattened pillar_l/sarcophagus footprints — no silent drift between the two."""
    assert sg.OBSTACLES == sg.PILLAR_L_CELLS + sg.SARCOPHAGUS_CELLS


def test_fresh_plate_ornament_cells_are_impassable():
    """CRYPT-ALIGN-V2: the fresh crypt plate's wall-band ornament cells are all impassable, so the engine
    collision agrees with the fresh geometry (qa/evidence/crypt-fresh/crypt_fresh_geometry.json). The
    free-standing/floor props are ORNAMENT_PROPS; the 3 door-flanking wall-mounted ones (ORNAMENT_WALL_CELLS)
    are impassable wall cells so they never trip the free-standing-prop door-zone gate in the walkslice
    reuse. skull_pile + urn_spill are DELETED (painted outside the playable walls)."""
    grid = _grid()
    imp = {(x, y) for (x, y) in (tuple(p) for p in impassable_cells(grid, sg.GRID_W, sg.GRID_H))}
    ornament_cells = [tuple(c) for (_pid, _k, fp, *_rest) in sg.ORNAMENT_PROPS for c in fp]
    ornament_cells += [tuple(c) for c in sg.ORNAMENT_WALL_CELLS]
    assert len(ornament_cells) == 13, "the fresh plate contributes 13 ornament cells after v2 deletions"
    for cell in ornament_cells:
        assert cell in imp, f"fresh-plate ornament cell {cell} must be impassable (v2)"
    # the deleted skull_pile / urn_spill cells are painted clear floor -> must be walkable.
    for cell in [(2, 9), (3, 9), (11, 9)]:
        assert cell not in imp, f"deleted ornament cell {cell} is painted clear floor — must be walkable (v2)"
