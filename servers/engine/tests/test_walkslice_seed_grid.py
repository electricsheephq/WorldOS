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


def _impassable(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, W, H))}


def test_walkslice_crypt_reuses_canonical_grid_minus_the_door():
    """The walkslice crypt's impassable set == the canonical combat crypt's, minus ONLY the punched
    door cell. Proves the grid is REUSED (not re-authored) — same walls, same props, same footprints."""
    canonical = combat._build_crypt_grid(combat.CID, "loc")
    walk = ws.build_crypt_grid("loc")
    assert _impassable(walk) == _impassable(canonical) - {DOOR}


def test_walkslice_crypt_keeps_the_canonical_props():
    """The sarcophagus floor footprint (cols2-7 x rows7-9, #1505) + both pillars — the props the
    adopted plate is painted around — are all still impassable in the walkslice crypt."""
    blocked = _impassable(ws.build_crypt_grid("loc"))
    for cell in combat.SARCOPHAGUS_CELLS + combat.PILLAR_L_CELLS + combat.PILLAR_R_CELLS:
        assert (cell[0], cell[1]) in blocked, f"canonical prop cell {cell} no longer impassable"


def test_walkslice_crypt_has_a_walkable_doorway():
    grid = ws.build_crypt_grid("loc")
    assert grid.door_cells == [DOOR]
    assert DOOR not in _impassable(grid), "door cell must be walkable (cross_door lands here)"


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
