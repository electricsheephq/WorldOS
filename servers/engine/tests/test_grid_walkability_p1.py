"""P1: authored walkability + A*/BFS routing — combatants route AROUND impassable cells
(walls/props) and never end on/through one. ADDITIVE: empty impassable == open floor
(PR-1 behaviour byte-for-byte unchanged), guarded by test_open_floor_additive_unchanged.
"""

import pytest

import combat_grid
import server

# A 2-wide notch in a 3x3 that forces (0,0)->(2,0) to take the long way around.
WALL = {(1, 0), (1, 1)}


# ── pure combat_grid helpers ─────────────────────────────────────────────────


def test_shortest_path_routes_around_wall():
    p = combat_grid.shortest_path((0, 0), (2, 0), occupied=set(), width=3, height=3, impassable=WALL)
    assert p is not None and p[-1] == (2, 0)
    assert all(tuple(s) not in WALL for s in p)  # never steps onto a wall
    # the detour costs MORE than the straight-line Chebyshev (proves it routed around)
    assert combat_grid.path_cost_cells((0, 0), (2, 0), p) > combat_grid.chebyshev_cells((0, 0), (2, 0))


def test_shortest_path_blocked_goal_is_none():
    assert combat_grid.shortest_path((0, 0), (2, 2), occupied=set(), width=4, height=4,
                                     impassable={(2, 2)}) is None


def test_shortest_path_unreachable_is_none():
    box = {(2, 2), (2, 3), (3, 2)}  # walls the (3,3) corner off in a 4x4
    assert combat_grid.shortest_path((0, 0), (3, 3), occupied=set(), width=4, height=4,
                                     impassable=box) is None


def test_reachable_excludes_and_blocks_through_impassable():
    r = combat_grid.reachable((0, 0), 4, occupied=set(), width=4, height=2,
                              impassable={(1, 0), (1, 1)})
    assert (1, 0) not in r and (1, 1) not in r        # can't end on a wall
    assert (3, 0) not in r                            # full x=1 column walls off the right (2-row board)


def test_open_floor_additive_unchanged():
    # no obstacles -> routed cost == straight-line Chebyshev == old behaviour
    p = combat_grid.shortest_path((0, 0), (3, 3), occupied=set(), width=8, height=8)
    assert combat_grid.path_cost_cells((0, 0), (3, 3), p) == combat_grid.chebyshev_cells((0, 0), (3, 3)) == 3


# ── integration via the engine tools (set_grid obstacles + move_to_coords) ───


@pytest.fixture
def gf(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("P1 walk")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])
    return cid, hero, gob


def test_move_routes_around_obstacle(gf):
    cid, hero, gob = gf
    server.set_grid(cid, 3, 3, obstacles=[[1, 0], [1, 1]])
    server.place_combatant_at_coords(cid, hero, 0, 0)
    server.place_combatant_at_coords(cid, gob, 2, 2)
    res = server.move_to_coords(cid, hero, 2, 0)
    path = res.get("path") or []
    assert path and all(tuple(s) not in WALL for s in path)
    assert res["cost_cells"] > combat_grid.chebyshev_cells((0, 0), (2, 0))
    assert res["to"] == [2, 0]  # the goal IS reachable around the wall


def test_move_onto_wall_is_rejected(gf):
    cid, hero, gob = gf
    server.set_grid(cid, 4, 4, obstacles=[[1, 1]])
    server.place_combatant_at_coords(cid, hero, 0, 1)
    server.place_combatant_at_coords(cid, gob, 3, 3)
    res = server.move_to_coords(cid, hero, 1, 1)  # straight onto the wall cell
    assert res.get("move_blocked")        # rejected, not landed
    assert res["to"] == [0, 1]            # stayed put


def test_open_floor_combat_move_unchanged(gf):
    # no obstacles -> move behaves exactly as PR-1 (straight Chebyshev cost, lands on the cell)
    cid, hero, gob = gf
    server.set_grid(cid, 8, 8)
    server.place_combatant_at_coords(cid, hero, 0, 0)
    server.place_combatant_at_coords(cid, gob, 7, 7)
    res = server.move_to_coords(cid, hero, 3, 0)
    assert res["to"] == [3, 0] and res["cost_cells"] == 3 and not res.get("move_blocked")
