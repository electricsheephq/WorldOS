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
import seed_gfx_camp as camp_seed  # noqa: E402
import seed_gfx_combat as combat  # noqa: E402
import seed_gfx_walkslice as ws  # noqa: E402

W, H = combat.GRID_W, combat.GRID_H  # 14x11
DOOR = (ws.DOOR[0], ws.DOOR[1])
TAVERN_DOOR = (ws.TAVERN_DOOR[0], ws.TAVERN_DOOR[1])
TW, TH = ws.TAVERN_W, ws.TAVERN_H  # 12x10
CW, CH = camp_seed.GRID_W, camp_seed.GRID_H  # 16x12
CAMP_DOOR = (ws.CAMP_DOOR[0], ws.CAMP_DOOR[1])


def _impassable(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, W, H))}


def _impassable_t(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, TW, TH))}


def _impassable_c(grid) -> set[tuple[int, int]]:
    return {(x, y) for (x, y) in (tuple(cell) for cell in sg.impassable_cells(grid, CW, CH))}


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


def test_walkslice_camp_has_a_walkable_return_door():
    """The camp is NOT a dead end (the ship-morning smoke's 'known gap': no authored door_cells meant a
    clicking player could never leave — the smoke escaped via the QA-side travel_to primitive). One
    door cell back to the crypt, in the prop-free painted gate-post gap on the top edge, walkable, with
    a clear Chebyshev-1 landing ring."""
    grid = ws.build_camp_grid("camp")
    assert grid.door_cells == [CAMP_DOOR]
    blocked = _impassable_c(grid)
    assert CAMP_DOOR not in blocked, "camp return door must be walkable"
    for dc in range(-1, 2):
        for dr in range(0, 2):  # rows -1 are off-grid; ring rows 0-1
            cell = (CAMP_DOOR[0] + dc, CAMP_DOOR[1] + dr)
            if 0 <= cell[0] < CW and 0 <= cell[1] < CH:
                assert cell not in blocked, f"camp door landing ring cell {cell} is blocked"


def test_walkslice_camp_spawns_are_walkable_and_off_props():
    """Party + NPC spawn on clear ground. Regression: the old party spawn (8,9) collided with the
    firewood footprint after CAMP-TUNE (#1526) extended it to include (8,9) — the hero rendered
    standing ON the woodpile (ship-morning frame2, orchestrator-eyeball find)."""
    grid = ws.build_camp_grid("camp")
    blocked = _impassable_c(grid)
    prop_cells = {(c, r) for p in grid.props for (c, r) in p.cells}
    assert (8, 9) in prop_cells, "premise: (8,9) is a firewood footprint cell (#1526)"
    for role, cells in grid.spawns.items():
        for (c, r) in cells:
            assert (c, r) not in blocked, f"camp {role} spawn {(c, r)} is BLOCKED (prop)"
            assert (c, r) not in prop_cells, f"camp {role} spawn {(c, r)} stands on a prop"


def test_walkslice_camp_party_spawn_moved_off_firewood_tail():
    """CAMP-CELLS wave-2 (#1540/#1552, 2026-07-15): camp.FIREWOOD_TAIL_CELLS now claims (6,9) (the
    woodpile's painted tail, #1540-flagged), the SAME cell the walkslice's rest-mode party spawn used
    to stand on — the #1526 pattern recurring one cell over. The spawn was moved to (7,10); pin both
    that the old cell is now blocked AND the new spawn cell is clear, so this can't silently regress
    either direction."""
    grid = ws.build_camp_grid("camp")
    blocked = _impassable_c(grid)
    assert (6, 9) in blocked, "premise: (6,9) is now the firewood_tail footprint (#1540)"
    assert (6, 9) not in set(grid.spawns["party"]), "party spawn must no longer sit on (6,9)"
    assert (7, 10) in grid.spawns["party"]
    assert (7, 10) not in blocked, "new party spawn (7,10) must be walkable"


def test_walkslice_camp_1540_flagged_cells_keep_reject():
    """The #1540/#1552 inverse-coherence keep/reject call holds through the walkslice's camp grid too
    (it reuses camp._build_camp_grid verbatim, plus the door + relocated spawns) — not just the bare
    combat-demo grid qa/test_seed_gfx_camp.py pins."""
    grid = ws.build_camp_grid("camp")
    blocked = _impassable_c(grid)
    for cell in [(6, 8), (6, 9), (11, 3), (10, 4), (12, 6), (14, 5), (12, 11)]:
        assert cell in blocked, f"#1540-flagged real obstacle {cell} must be impassable in the walkslice camp"
    for cell in [(3, 10), (4, 10), (9, 5), (9, 6), (9, 7), (9, 8)]:
        assert cell not in blocked, f"#1540-flagged false-positive {cell} must stay walkable in the walkslice camp"
