"""gfx M-B (#1194): a fight bound to a painted room AUTO-derives its combat obstacles from
the location's SceneGrid (walls + prop footprints), so movement routes around the painted
geometry without anyone hand-calling set_grid(obstacles=...).

ADDITIVE: a fight with no scene_grid keeps zone/theater combat byte-for-byte (grid_enabled
stays False == today) — pinned by test_no_scene_grid_combat_unchanged. Reuses the P1 patterns
in test_grid_walkability_p1.py (route-around-wall, reject-onto-wall).
"""

from __future__ import annotations

import pytest

import server  # imported FIRST: resolves the models<->scene_grid import cycle in the right order
import combat_grid
import scene_grid as scene_grid_mod
from scene_grid import (
    SceneGrid,
    SceneGridSpec,
    SceneCell,
    SceneCellDefault,
    SceneProp,
)


# ── pure derivation: impassable_cells(scene_grid, w, h) ───────────────────────────────


def _grid_with_wall_column() -> SceneGrid:
    """A 4x3 scene with a solid wall down the middle column x=1 (rows 0..2) and a 1-cell
    prop at (3, 0). Cols->x, rows->y. The wall splits left (x=0) from right (x>=2)."""
    cells = [SceneCell(c=1, r=r, type="wall", walkable=False) for r in range(3)]
    props = [SceneProp(id="crate", kind="crates", cells=[(3, 0)], anchor_cell=(3, 0))]
    # prop footprint cells are also emitted as non-walkable cells (mirrors the generators)
    cells.append(SceneCell(c=3, r=0, type="prop", walkable=False, prop_ref="crate"))
    return SceneGrid(
        scene_id="w:loc", location_id="loc", kind="dungeon",
        grid=SceneGridSpec(cols=4, rows=3, cell_size_ft=5),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
    )


def test_impassable_cells_collects_walls_and_props():
    g = _grid_with_wall_column()
    imp = scene_grid_mod.impassable_cells(g, 4, 3)
    # the full x=1 column + the (3,0) prop cell, [x, y] pairs, sorted
    assert imp == [[1, 0], [1, 1], [1, 2], [3, 0]]


def test_impassable_cells_clips_to_grid_bounds():
    # a scene bigger than the combat grid: cells outside [0,w)x[0,h) are dropped
    g = _grid_with_wall_column()  # has a cell at (3,0) and wall col x=1
    imp = scene_grid_mod.impassable_cells(g, 2, 2)  # only x<2, y<2 survive
    assert imp == [[1, 0], [1, 1]]  # (1,2) out of y-bounds, (3,0) out of x-bounds


def test_impassable_cells_excludes_occupied_so_nobody_is_trapped():
    g = _grid_with_wall_column()
    # a combatant standing on the prop cell (3,0) must NOT be walled in
    imp = scene_grid_mod.impassable_cells(g, 4, 3, occupied={(3, 0)})
    assert [3, 0] not in imp
    assert [1, 0] in imp  # the wall column is still impassable


def test_impassable_cells_empty_when_all_walkable():
    # an all-floor scene (no walls/props) -> no obstacles (empty == open floor == today)
    g = SceneGrid(
        scene_id="w:open", location_id="open",
        grid=SceneGridSpec(cols=5, rows=5),
        cell_default=SceneCellDefault(walkable=True),
        cells=[], props=[],
    )
    assert scene_grid_mod.impassable_cells(g, 5, 5) == []


# ── integration: start_combat on a painted room auto-populates grid_impassable ────────


def _hand_room() -> SceneGrid:
    """A deterministic 5x5 room with a 3-tall solid wall column at x=2 (rows 1,2,3) that
    SPLITS the room left/right — a barrier that genuinely lengthens a left->right path (you
    must go up and over the top of the wall at y=0). Floor everywhere else. This isolates the
    route-around proof from procedural-room layout variance."""
    cells = [SceneCell(c=2, r=r, type="wall", walkable=False) for r in (1, 2, 3)]
    return SceneGrid(
        scene_id="w:hand", location_id="hand", kind="dungeon",
        grid=SceneGridSpec(cols=5, rows=5, cell_size_ft=5),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=[],
    )


@pytest.fixture
def fight_in_room(tmp_path, monkeypatch):
    """A campaign whose CURRENT location carries a deterministic SceneGrid (the _hand_room
    wall-split), with a Hero + a Goblin in a started fight. start_combat should have
    auto-derived the grid + obstacles from the painted walls."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("M-B painted room")["id"]
    # add_location makes the first location current AND emits a procedural scene_grid; we
    # OVERWRITE it with the deterministic hand-room so the wall geometry is known + stable.
    loc_id = server.add_location(cid, "The Split Chamber")["id"]
    c = server._require(cid)
    c.locations[loc_id].scene_grid = _hand_room()
    server.save_campaign(c)
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])
    return cid, hero, gob


# The wall column the _hand_room paints (x=2, rows 1..3).
HAND_WALL = {(2, 1), (2, 2), (2, 3)}


def test_start_combat_auto_enables_grid_from_scene(fight_in_room):
    cid, _hero, _gob = fight_in_room
    c = server._require(cid)
    loc = c.locations[c.current_location_id]
    assert loc.scene_grid is not None
    # the fight was switched ONTO the grid automatically (no manual set_grid)
    assert c.combat.grid_enabled is True
    assert c.combat.grid_width == 5 and c.combat.grid_height == 5
    # obstacles were derived from the room's walls — exactly the painted wall column
    derived = {(x, y) for x, y in c.combat.grid_impassable}
    assert derived == HAND_WALL
    # every derived obstacle is in-bounds and matches a non-walkable scene cell
    solid = {(sc.c, sc.r) for sc in loc.scene_grid.cells if not sc.walkable}
    for x, y in derived:
        assert 0 <= x < c.combat.grid_width and 0 <= y < c.combat.grid_height
        assert (x, y) in solid


def test_move_routes_around_a_painted_wall(fight_in_room):
    """The load-bearing proof: a move from the LEFT of the wall column to the RIGHT routes
    AROUND it (over the top at y=0), costs MORE than the straight-line Chebyshev (which would
    cut through the wall), and never steps onto an impassable cell."""
    cid, hero, gob = fight_in_room
    c = server._require(cid)
    start, goal = (1, 2), (3, 2)  # straight across is blocked by the wall at (2,2)
    server.place_combatant_at_coords(cid, hero, *start)
    server.place_combatant_at_coords(cid, gob, 4, 4)  # parked far away
    res = server.move_to_coords(cid, hero, *goal)

    path = res.get("path") or []
    assert path, "expected a routed path around the wall"
    assert all(tuple(s) not in HAND_WALL for s in path)  # never steps onto a wall
    assert res["to"] == [3, 2]                            # reached the far side
    # straight Chebyshev (1,2)->(3,2) is 2 cells (would cut through the wall); the routed
    # detour over the top costs strictly MORE — proves it walked around the painted geometry.
    assert res["cost_cells"] > combat_grid.chebyshev_cells(start, goal)


def test_move_onto_a_painted_wall_is_rejected(fight_in_room):
    cid, hero, gob = fight_in_room
    server.place_combatant_at_coords(cid, hero, 1, 2)   # free, just left of the wall
    server.place_combatant_at_coords(cid, gob, 4, 4)
    res = server.move_to_coords(cid, hero, 2, 2)        # straight onto the wall cell
    assert res.get("move_blocked")        # rejected, not landed
    assert res["to"] == [1, 2]            # stayed put


# ── ADDITIVITY: a fight with NO scene_grid is byte-for-byte unchanged ─────────────────


def test_no_scene_grid_combat_unchanged(tmp_path, monkeypatch):
    """A campaign whose current location has NO scene_grid (or no location at all) keeps
    zone/theater combat: grid_enabled stays False, grid_impassable empty == today."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("M-B no room")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])  # no current location -> no scene_grid
    c = server._require(cid)
    assert c.combat.grid_enabled is False
    assert c.combat.grid_impassable == []


def test_explicit_set_grid_is_not_overridden(tmp_path, monkeypatch):
    """If the DM/engine already called set_grid (grid_enabled True) before start_combat would
    derive, the explicit grid wins — auto-derivation never clobbers a configured fight."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("M-B explicit grid")["id"]
    server.add_location(cid, "The Elfsong Tavern")
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])
    # start_combat already derived from the scene; now prove the guard by simulating the
    # reverse order via a fresh fight: end, set_grid first, then a re-derive call is a no-op.
    server.end_combat(cid)
    server.start_combat(cid, [hero, gob])
    c = server._require(cid)
    c.combat.grid_enabled = True
    c.combat.grid_impassable = [[9, 9]]  # a hand-set obstacle
    scene_grid_mod_obstacles_before = list(c.combat.grid_impassable)
    server._derive_grid_from_scene(c)  # must be a no-op (already enabled)
    assert c.combat.grid_impassable == scene_grid_mod_obstacles_before
