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
    """WALKSLICE-CRYPT-ALIGN (#1565): the fresh crypt plate (author_crypt_fresh, adopted at neutral-anchor
    parity 7.0) paints the sarcophagus as the TRUE 2x2 coffin — the box body at cols4-5 x rows7-8. This
    replaces the over-large 12-cell drift blob (cols3-7 x rows6-8) the owner-playtest-#5 re-measure read
    off the SUPERSEDED crypt_armb_iter3_v1.png plate. The 2x2 is a strict subset of that blob, so no actor
    stands ON the painted box while 8 over-large drift cells return to walkable floor. See
    qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.SARCOPHAGUS_CELLS == [
        [4, 7], [5, 7],
        [4, 8], [5, 8],
    ]
    for cell in sg.SARCOPHAGUS_CELLS:
        assert cell in imp, f"sarcophagus cell {cell} must be impassable (paint/grid registration gap)"
    # the open lit floor RIGHT of the tomb (between the coffin and the right pillar) must be WALKABLE
    # (never blocked by any coffin re-measure — the owner "cannot walk right of the tomb" regression guard).
    for cell in ([8, 7], [9, 7], [8, 8], [9, 8]):
        assert cell not in imp, f"floor right of the tomb {cell} must be walkable (owner playtest #5)"
    # #1565: the 8 drift cells the old 12-cell blob over-blocked are freed to walkable floor by the 2x2.
    for cell in ([4, 6], [5, 6], [6, 6], [7, 6], [3, 7], [6, 7], [7, 7], [6, 8]):
        assert cell not in imp, f"freed drift cell {cell} must be walkable now (2x2 coffin, #1565)"


def test_pillars_match_painted_cells():
    """OWNER PLAYTEST #5: each pillar footprint is its painted 2-cell floor base, not the single stale
    cell (2,4)/(9,9) that missed the base entirely and let the owner walk THROUGH the columns."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.PILLAR_L_CELLS == [[3, 3], [3, 4]]
    assert sg.PILLAR_R_CELLS == [[8, 9], [9, 9]]
    for cell in sg.PILLAR_L_CELLS + sg.PILLAR_R_CELLS:
        assert cell in imp, f"pillar base cell {cell} must be impassable (owner walk-through fix)"
    # the STALE single-cell + pre-#1396 prop cells must no longer be authored as prop footprints.
    prop_cells = {(c0, r0) for prop in grid.props for (c0, r0) in prop.cells}
    for stale in [(2, 4), (2, 3), (11, 3), (7, 1)]:
        assert stale not in prop_cells


def test_hero_and_goblin_spawn_cells_stay_walkable():
    """The hero(11,3)/goblin(1,8) spawn cells (far back-right / front-left, both clear of the
    corrected front-center tomb footprint, #1505) must never collide with the prop footprints — a
    content fix here must not accidentally trap the demo's own combatants."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.HERO_CELL not in imp
    assert sg.GOBLIN_CELL not in imp
    # the 2x2 coffin's floor-footprint cells — pin a couple so a future footprint edit can't silently
    # re-open them (an actor standing ON the painted tomb). #1565: coffin is now cols4-5 x rows7-8.
    assert [4, 8] in imp
    assert [5, 7] in imp


def test_obstacles_list_matches_authored_props():
    """OBSTACLES (used for the printed seed summary + kept in lock-step with set_grid) must be
    exactly the flattened pillar/sarcophagus footprints — no silent drift between the two."""
    assert sg.OBSTACLES == sg.PILLAR_L_CELLS + sg.PILLAR_R_CELLS + sg.SARCOPHAGUS_CELLS


def test_fresh_plate_ornament_cells_are_impassable():
    """WALKSLICE-CRYPT-ALIGN (#1565): the 16 wall-band ornament cells the fresh crypt plate paints
    (reconciliation section B) are all impassable now, so the engine collision agrees with the fresh
    geometry (qa/evidence/crypt-fresh/crypt_fresh_geometry.json). 13 are free-standing/floor props
    (ORNAMENT_PROPS); the 3 door-flanking wall-mounted ones (ORNAMENT_WALL_CELLS) are impassable wall
    cells so they never trip the free-standing-prop door-zone gate in the walkslice reuse."""
    grid = _grid()
    imp = {(x, y) for (x, y) in (tuple(p) for p in impassable_cells(grid, sg.GRID_W, sg.GRID_H))}
    ornament_cells = [tuple(c) for (_pid, _k, fp, *_rest) in sg.ORNAMENT_PROPS for c in fp]
    ornament_cells += [tuple(c) for c in sg.ORNAMENT_WALL_CELLS]
    assert len(ornament_cells) == 16, "the fresh plate contributes exactly 16 ornament cells"
    for cell in ornament_cells:
        assert cell in imp, f"fresh-plate ornament cell {cell} must be impassable (#1565)"
