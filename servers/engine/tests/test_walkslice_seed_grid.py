"""WALKABLE-SLICE crypt COHERENCE (#1396 defect class): the walkslice smoke's crypt must render the
SAME room as the combat demo, so its scene_grid REUSES the canonical combat crypt grid
(``seed_gfx_combat._build_crypt_grid``) rather than a divergent hand-authored one. These pin that the
only difference is a single back-center DOORWAY the walkslice needs, and that the reused props (the
sarcophagus floor footprint cols2-7 x rows7-9 + both pillars, #1386 corrected by #1505) plus every
actor spawn stay coherent.

Pure/unit (no server, no state dir) — exercises the ``build_crypt_grid`` helper directly.
"""

from __future__ import annotations

import os
import sys

# the gfx seeds live in <repo>/qa; add it to the path (pythonpath is servers/engine only).
_QA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "qa"))
if _QA not in sys.path:
    sys.path.insert(0, _QA)

import server  # noqa: E402, F401  (import-first: resolves the models<->scene_grid import cycle)
import scene_grid as sg  # noqa: E402
import seed_gfx_combat as combat  # noqa: E402
import seed_gfx_walkslice as ws  # noqa: E402

W, H = combat.GRID_W, combat.GRID_H  # 14x11
DOOR = (ws.DOOR[0], ws.DOOR[1])
TAVERN_DOOR = (ws.TAVERN_DOOR[0], ws.TAVERN_DOOR[1])
TW, TH = ws.TAVERN_W, ws.TAVERN_H  # 12x10


def _impassable(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, W, H))}


def _impassable_t(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, TW, TH))}


def test_walkslice_crypt_reuses_canonical_grid_minus_the_doors():
    """The walkslice crypt's impassable set == the canonical combat crypt's, minus ONLY the two punched
    door cells (back-center -> camp, left-wall -> tavern). Proves the grid is REUSED (not re-authored) —
    same walls, same props, same footprints."""
    canonical = combat._build_crypt_grid(combat.CID, "loc")
    walk = ws.build_crypt_grid("loc")
    assert _impassable(walk) == _impassable(canonical) - {DOOR, TAVERN_DOOR}


def test_walkslice_crypt_keeps_the_canonical_props():
    """The sarcophagus floor footprint (cols2-7 x rows7-9, #1505) + both pillars — the props the
    adopted plate is painted around — are all still impassable in the walkslice crypt."""
    blocked = _impassable(ws.build_crypt_grid("loc"))
    for cell in combat.SARCOPHAGUS_CELLS + combat.PILLAR_L_CELLS + combat.PILLAR_R_CELLS:
        assert (cell[0], cell[1]) in blocked, f"canonical prop cell {cell} no longer impassable"


def test_walkslice_crypt_has_two_walkable_doorways():
    """The three-room-world crypt has BOTH doorways (camp + tavern), each walkable."""
    grid = ws.build_crypt_grid("loc")
    assert grid.door_cells == [DOOR, TAVERN_DOOR]
    blocked = _impassable(grid)
    assert DOOR not in blocked, "camp door cell must be walkable (cross_door lands here)"
    assert TAVERN_DOOR not in blocked, "tavern door cell must be walkable (cross_door lands here)"


def test_walkslice_spawns_are_walkable_and_off_the_tomb():
    """Party + Mira spawn on clear floor — never on the sarcophagus (the 'actor on the tomb' bug),
    a pillar, or a wall."""
    grid = ws.build_crypt_grid("loc")
    blocked = _impassable(grid)
    sarco = {(c, r) for (c, r) in combat.SARCOPHAGUS_CELLS}
    for role, cells in grid.spawns.items():
        for (c, r) in cells:
            assert (c, r) not in blocked, f"{role} spawn {(c, r)} is BLOCKED (wall/prop)"
            assert (c, r) not in sarco, f"{role} spawn {(c, r)} stands on the sarcophagus"


def test_walkslice_crypt_grid_validates_clean():
    """The full protected-pathing gate passes (door zone clear, connected, enough combat floor)."""
    assert sg.validate_scene_grid(ws.build_crypt_grid("loc"), W, H) == []


# ── the NEW-ROOM tavern (three-room world, epic #1508) ───────────────────────────────────────────
def test_walkslice_tavern_grid_validates_clean():
    """The tavern's 12x10 scene_grid passes the full protected-pathing gate."""
    assert sg.validate_scene_grid(ws.build_tavern_grid("tav"), TW, TH) == []


def test_walkslice_tavern_has_a_walkable_back_door():
    grid = ws.build_tavern_grid("tav")
    back_door = (ws.TAVERN_BACK_DOOR[0], ws.TAVERN_BACK_DOOR[1])
    assert grid.door_cells == [back_door]
    assert back_door not in _impassable_t(grid), "tavern back-door must be walkable"


def test_walkslice_tavern_props_match_the_authored_geometry():
    """Every tavern prop footprint (from tools/author_room_geometry.py, the SAME geometry the greybox /
    registered plate / derived manifest were built from) is impassable in the grid — so pathing and
    paint agree by construction (the true-greybox premise)."""
    blocked = _impassable_t(ws.build_tavern_grid("tav"))
    for _pid, _kind, footprint, _band, _sil in ws._TAVERN_PROPS:
        for (c, r) in footprint:
            assert (c, r) in blocked, f"tavern prop cell {(c, r)} is not impassable"


def test_walkslice_tavern_spawns_are_walkable_and_off_props():
    """Party + NPC spawn on clear tavern floor — never on a prop, wall, or the door zone."""
    grid = ws.build_tavern_grid("tav")
    blocked = _impassable_t(grid)
    prop_cells = {(c, r) for (*_, footprint, _b, _s) in
                  ((p[0], p[1], p[2], p[3], p[4]) for p in ws._TAVERN_PROPS) for (c, r) in footprint}
    for role, cells in grid.spawns.items():
        for (c, r) in cells:
            assert (c, r) not in blocked, f"tavern {role} spawn {(c, r)} is BLOCKED (wall/prop)"
            assert (c, r) not in prop_cells, f"tavern {role} spawn {(c, r)} stands on a prop"
