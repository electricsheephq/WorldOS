"""Self-tests for qa/seed_gfx_camp.py's authored camp_clearing_night-coherent scene_grid (#1441 P2).

RED-FIRST regression for the scene<->grid coherence bug: the player's WorldOSPlayer.app build bakes
the camp_clearing_night plate (painted fire pit, log seat, bedrolls, supply crates, boulders, tree
line), but campaign camp_gfxdemo01's combat grid was seed_gfx_combat.py's CRYPT layout (14x11,
pillars + a sarcophagus) — none of the camp's painted props were in the engine's impassable set, so
actors could walk onto/through the fire pit and log seat (the owner's "stacking on everything"
report). Before this fix existed, importing seed_gfx_camp and checking the camp prop cells against
seed_gfx_combat's crypt-shaped impassable set would fail every assertion below (wrong grid dims,
none of the camp prop cells impassable) — this file pins the FIXED grid so that regression can't
silently return.

Run with the engine venv (pydantic + pytest live there):
    uv run --directory servers/engine python -m pytest ../../qa/test_seed_gfx_camp.py -p no:cacheprovider
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "qa"))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import server  # noqa: E402,F401  imported FIRST: resolves the models<->scene_grid import cycle
import seed_gfx_camp as sg  # noqa: E402
from scene_grid import validate_scene_grid, impassable_cells  # noqa: E402


def _grid():
    return sg._build_camp_grid(sg.CID, "loc-test")


def test_scene_grid_has_zero_validate_violations():
    """The pre-greybox gate (door zones / protected lanes / connectivity) must stay clean."""
    grid = _grid()
    assert validate_scene_grid(grid, sg.GRID_W, sg.GRID_H) == []


def test_grid_dims_match_camp_clearing_night_recipe():
    """16x12 — the dims qa/seed_gfx_camp_clearing.py authored and room_recipes.json's
    "camp_clearing_night" entry cites as its source of truth. Deliberately NOT 14x11 (the crypt's
    dims) — a coherent fixture is allowed to differ in size from the fixture it replaces; the client
    reads dims from the surface, not a hardcoded constant."""
    assert (sg.GRID_W, sg.GRID_H) == (16, 12)


def test_painted_camp_props_are_all_impassable():
    """OWNER PLAYTEST #5 RED-FIRST: every painted solid on the DEPLOYED camp_clearing_night_v2.png plate
    — the fire pit, firewood, all four crate clusters, both stone walls, the gate posts, the shelter
    frame, and both bedroll groups — must be an engine pathing obstacle. This is the exact "walks THROUGH
    the campfire / over bedrolls / crates / logs — essentially open grid" felt-bug: the pre-fix footprints
    were authored for the OLDER greybox/v1 layout, so NONE of the v2 plate's painted solids were
    impassable (they sat on open ground) and the owner walked straight through them."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    for cell in sg.OBSTACLES:
        assert cell in imp, f"camp prop cell {cell} must be impassable (owner playtest #5 collision-coherence)"


def test_the_fire_and_bedrolls_are_impassable():
    """The felt-bug pinned explicitly on the v2 plate: the central fire pit and both bedroll groups —
    the cells the owner watched his character walk straight through — must all block."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, sg.GRID_W, sg.GRID_H)}
    for cell in [(4, 9), (5, 9), (1, 8), (2, 8), (5, 10), (6, 10)]:
        assert cell in imp, f"painted fire/bedroll cell {cell} must block (owner playtest #5)"


def test_obstacle_prop_cell_count_matches_plate():
    """39 disjoint prop cells total across the v2 layout — pinned so a future edit can't silently drop a
    painted solid from the impassable set. Footprints are DISJOINT (no cell claimed by two props), so the
    flattened OBSTACLES has no duplicates."""
    assert len(sg.OBSTACLES) == 39
    assert len(sg.OBSTACLES) == len({tuple(c) for c in sg.OBSTACLES})  # no duplicate cells
    assert len(sg.CAMPFIRE_CELLS) == 4
    assert len(sg.WALL_BR_CELLS) == 9


def test_no_perimeter_walls_open_air_clearing():
    """Coherence with scene_grid.py::_gen_forest's convention: an outdoor clearing has NO hard
    perimeter walls (unlike the crypt's solid perimeter) — only named props are impassable. Corner
    and edge-midpoint cells that aren't part of a prop footprint must stay walkable."""
    grid = _grid()
    imp = {tuple(c) for c in impassable_cells(grid, sg.GRID_W, sg.GRID_H)}
    obstacle_set = {tuple(c) for c in sg.OBSTACLES}
    for cell in [(0, 0), (15, 0), (0, 11), (15, 11), (0, 6), (15, 5)]:
        assert cell not in obstacle_set and cell not in imp, \
            f"open-air clearing cell {cell} must not be a perimeter wall"


def test_hero_and_goblin_spawn_cells_stay_walkable():
    """The fixed hero(7,9)/goblin(10,8) spawn cells must never collide with the painted prop
    footprints — a coherence fix here must not accidentally trap the demo's own combatants."""
    grid = _grid()
    imp = impassable_cells(grid, sg.GRID_W, sg.GRID_H)
    assert sg.HERO_CELL not in imp
    assert sg.GOBLIN_CELL not in imp


def test_obstacles_list_matches_authored_props():
    """OBSTACLES (used for the printed seed summary + kept in lock-step with set_grid) must be
    exactly the flattened v2-plate prop footprints — no silent drift between the two."""
    assert sg.OBSTACLES == (
        sg.CAMPFIRE_CELLS + sg.FIREWOOD_CELLS + sg.CRATE_L_CELLS + sg.CRATE_C_CELLS
        + sg.CRATE_WALL_CELLS + sg.CRATE_R_CELLS + sg.WALL_BL_CELLS + sg.WALL_BR_CELLS
        + sg.POST_CELLS + sg.SHELTER_CELLS + sg.BEDROLL_L_CELLS + sg.BEDROLL_R_CELLS
    )


def test_same_campaign_id_as_crypt_seed():
    """seed_gfx_camp.py must mint the SAME campaign id seed_gfx_combat.py does (camp_gfxdemo01) — the
    id the box renderer + qa/ui_playtest_player.sh hardcode. This is a swap of the GRID under a
    stable id, not a new fixture id."""
    import seed_gfx_combat as combat_sg  # noqa: PLC0415
    assert sg.CID == combat_sg.CID == "camp_gfxdemo01"
