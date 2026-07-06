"""#1253 grid (PR-4/PR-5) — difficult terrain (double move cost) + creature size/reach
(>1-cell tokens). Pure geometry lives in combat_grid (footprint / footprint_distance_cells /
weighted reachable/shortest_path/path_cost_cells); the wiring lives in server.set_grid,
place_combatant_at_coords, move_to_coords, attack(), and cast_spell's AoE occupant loop.

ADDITIVE / opt-in: with NO difficult-terrain cells and all-Medium tokens, everything is
BYTE-FOR-BYTE the PR-1..PR-3 behaviour — the regression test at the bottom is the
load-bearing guard.

DIFFICULT TERRAIN (SRD 5.2): entering a difficult cell costs DOUBLE (2 cells).
SIZE: Medium/Small/Tiny = 1 cell; Large = 2×2, Huge = 3×3, Gargantuan = 4×4, anchored at
the stored (x, y) MIN-corner. Reach + occupancy + AoE measure over the whole footprint.
"""

import pytest

import combat_grid
import server


# ── (1) PURE: difficult-terrain double cost ──────────────────────────────────


def test_path_cost_open_floor_unchanged_without_difficult():
    # No difficult terrain => plain straight-line Chebyshev (PR-1 byte-for-byte).
    assert combat_grid.path_cost_cells((0, 0), (5, 0)) == 5
    assert combat_grid.path_cost_cells((0, 0), (3, 3)) == 3


def test_path_cost_straight_walk_across_difficult_costs_double_per_cell():
    # Straight walk (0,0)->(4,0) entering two difficult cells (2,0),(3,0): base 4 + 2 = 6.
    difficult = {(2, 0), (3, 0)}
    assert combat_grid.path_cost_cells((0, 0), (4, 0), None, difficult) == 6


def test_path_cost_explicit_path_surcharges_difficult_steps():
    # An explicit step path across one difficult cell: 3 hops + 1 surcharge = 4.
    path = [(1, 0), (2, 0), (3, 0)]
    difficult = {(2, 0)}
    assert combat_grid.path_cost_cells((0, 0), (3, 0), path, difficult) == 4


def test_shortest_path_routes_around_expensive_difficult_terrain():
    # A wall of difficult cells straight ahead makes the direct route cost 2/cell; a detour
    # around it can be cheaper. The path THROUGH costs more than going AROUND.
    difficult = {(1, 0), (1, 1), (1, 2)}
    w = h = 6
    routed = combat_grid.shortest_path((0, 0), (2, 0), set(), w, h, frozenset(), difficult)
    assert routed is not None
    cost = combat_grid.path_cost_cells((0, 0), (2, 0), routed, difficult)
    # Straight-diagonal detour (0,0)->(1,-?) is off-grid; the cheapest legal route avoids the
    # difficult column where possible. Cost must be < the 2-per-cell straight cost of 3 (enter
    # (1,0)=2 + (2,0)=1) — the router should not pay double if a 1-cost detour exists.
    straight_cost = combat_grid.path_cost_cells((0, 0), (2, 0), None, difficult)
    assert straight_cost == 3  # (1,0) difficult doubles: 1(base to (1,0))+1 surcharge +1 = 3
    assert cost <= straight_cost


def test_reachable_difficult_shrinks_reach():
    # A 2-cell budget reaches 2 open cells in a line, but only 1 into difficult terrain.
    open_reach = combat_grid.reachable((0, 0), 2, set(), 10, 10)
    assert (2, 0) in open_reach  # two open steps
    hard = combat_grid.reachable((0, 0), 2, set(), 10, 10, frozenset(), {(1, 0), (2, 0)})
    assert (1, 0) in hard        # entering (1,0) costs 2 == whole budget
    assert (2, 0) not in hard    # (2,0) would cost 4 — out of a 2-cell budget


# ── (2) SERVER: difficult-terrain move cost + short-stop advisory ────────────


def _grid_fight(cid_name="terr", w=20, h=20, difficult=None, obstacles=None):
    cid = server.create_campaign(cid_name)["id"]
    a = server.create_character(cid, "Fighter", kind="player", max_hp=30, armor_class=14)["id"]
    t = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=14)["id"]
    server.start_combat(cid, [a, t])
    server.set_grid(cid, w, h, obstacles=obstacles, difficult=difficult)
    return cid, a, t


def test_move_forced_path_charges_double_through_difficult(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    # Fighter speed 30 => 6 cells budget. Difficult cells (1,0),(2,0). An EXPLICIT straight
    # path through them: base 3, +2 for the two difficult entries = 5 cells, both surfaced.
    cid, a, t = _grid_fight(difficult=[[1, 0], [2, 0]])
    server.place_combatant_at_coords(cid, t, 10, 10)
    server.place_combatant_at_coords(cid, a, 0, 0)
    res = server.move_to_coords(cid, a, 3, 0, path=[[1, 0], [2, 0], [3, 0]])
    assert res["cost_cells"] == 5
    assert res.get("difficult_crossed") == [[1, 0], [2, 0]]


def test_move_router_prefers_cheaper_route_around_difficult(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    # With no explicit path, the auto-router (Dijkstra) routes AROUND the difficult band to
    # the cheapest arrival — a 3-cell open detour beats the 5-cell straight slog.
    cid, a, t = _grid_fight(difficult=[[1, 0], [2, 0]])
    server.place_combatant_at_coords(cid, t, 10, 10)
    server.place_combatant_at_coords(cid, a, 0, 0)
    res = server.move_to_coords(cid, a, 3, 0)
    assert res["cost_cells"] == 3           # went around (open cells), not through
    assert not res.get("difficult_crossed")  # no difficult cell entered


def test_move_over_budget_shortstop_advisory_from_difficult(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    # Speed 30 => 6 cells. A full-width difficult band (no way around) forces the double
    # cost; a long run blows the budget and trips the advisory note (never hard-blocks).
    band = [[1, y] for y in range(20)] + [[2, y] for y in range(20)] + \
           [[3, y] for y in range(20)] + [[4, y] for y in range(20)]
    cid, a, t = _grid_fight(difficult=band)
    server.place_combatant_at_coords(cid, t, 15, 15)
    server.place_combatant_at_coords(cid, a, 0, 0)
    res = server.move_to_coords(cid, a, 5, 0, path=[[1, 0], [2, 0], [3, 0], [4, 0], [5, 0]])
    # base 5 + 4 difficult surcharges = 9 cells > 6 budget.
    assert res["cost_cells"] == 9
    assert res.get("movement_illegal") is not None
    assert res["movement_illegal"]["budget_cells"] == 6


def test_move_no_difficult_is_plain_chebyshev(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid, a, t = _grid_fight()
    server.place_combatant_at_coords(cid, t, 15, 15)
    server.place_combatant_at_coords(cid, a, 0, 0)
    res = server.move_to_coords(cid, a, 3, 0)
    assert res["cost_cells"] == 3
    assert "difficult_crossed" not in res  # no key when no terrain crossed


# ── (3) PURE: creature size footprint + reach ────────────────────────────────


def test_footprint_medium_is_one_cell():
    assert combat_grid.footprint_cells("medium") == 1
    assert combat_grid.footprint((4, 4), "medium") == {(4, 4)}
    # Tiny/small collapse to 1 cell; unknown => 1 (additive default).
    assert combat_grid.footprint_cells("small") == 1
    assert combat_grid.footprint_cells("") == 1
    assert combat_grid.footprint_cells("bogus") == 1


def test_footprint_large_is_two_by_two():
    assert combat_grid.footprint_cells("large") == 2
    assert combat_grid.footprint((3, 3), "large") == {(3, 3), (4, 3), (3, 4), (4, 4)}


def test_footprint_huge_is_three_by_three():
    assert combat_grid.footprint_cells("huge") == 3
    assert len(combat_grid.footprint((0, 0), "huge")) == 9


def test_footprints_overlap_detects_collision():
    # A Large token at (3,3) (covers up to (4,4)) collides with a Medium at (4,4).
    assert combat_grid.footprints_overlap((3, 3), "large", (4, 4), "medium") is True
    # But not with a Medium two cells clear.
    assert combat_grid.footprints_overlap((3, 3), "large", (6, 6), "medium") is False


def test_footprint_edge_reach_large_adjacent_from_two_anchor_cells():
    # A Large attacker anchored at (0,0) (covers to (1,1)) melees a Medium target at (2,1):
    # anchor Chebyshev is 2, but the FOOTPRINT-EDGE distance is 1 (cell (1,1) is adjacent).
    assert combat_grid.footprint_distance_cells((0, 0), "large", (2, 1), "medium") == 1
    assert combat_grid.in_melee_reach_sized((0, 0), "large", (2, 1), "medium") is True
    # Two medium tokens reduce to the PR-1 anchor Chebyshev.
    assert combat_grid.footprint_distance_cells((0, 0), "medium", (2, 0), "medium") == 2
    assert combat_grid.in_melee_reach_sized((0, 0), "medium", (2, 0), "medium") is False


# ── (4) SERVER: size wiring — placement, reach, AoE ──────────────────────────


def test_place_large_collision_warns_on_footprint_overlap(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid, a, t = _grid_fight()
    # Place a Medium at (4,4), then a Large anchored at (3,3) — its 2×2 footprint covers
    # (4,4), so the placement must WARN about the collision (never blocks).
    server.place_combatant_at_coords(cid, t, 4, 4)
    res = server.place_combatant_at_coords(cid, a, 3, 3, size="large")
    assert any("occupied" in w for w in res["warnings"])


def test_place_large_out_of_bounds_footprint_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid, a, t = _grid_fight(w=5, h=5)
    server.place_combatant_at_coords(cid, t, 0, 0)
    # A Large token anchored at (4,4) on a 5×5 grid overflows to (5,5) — out of bounds warn.
    res = server.place_combatant_at_coords(cid, a, 4, 4, size="large")
    assert any("out of the" in w for w in res["warnings"])


def test_large_attacker_reaches_from_footprint_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid, a, t = _grid_fight()
    # Large fighter anchored (0,0) covers to (1,1); target Medium at (2,1) is edge-adjacent.
    server.place_combatant_at_coords(cid, a, 0, 0, size="large")
    server.place_combatant_at_coords(cid, t, 2, 1)
    res = server.attack(campaign_id=cid, attacker_id=a, target_id=t,
                        attack_bonus=3, damage_dice="1d6")
    # No out-of-reach range_warning: the footprint edge is within 5 ft.
    assert "range_warning" not in res


def test_medium_attacker_two_cells_away_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid, a, t = _grid_fight()
    server.place_combatant_at_coords(cid, a, 0, 0)  # medium
    server.place_combatant_at_coords(cid, t, 2, 0)  # 10 ft away
    res = server.attack(campaign_id=cid, attacker_id=a, target_id=t,
                        attack_bonus=3, damage_dice="1d6")
    assert "range_warning" in res


def test_aoe_catches_large_creature_by_any_footprint_cell(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("aoe-size")["id"]
    caster = server.create_character(cid, "Wizard", kind="player", max_hp=20, armor_class=12)["id"]
    server.update_character(cid, caster, patch={"spell_slots": {"3": {"maximum": 4, "used": 0}}})
    ogre = server.create_character(cid, "Ogre", kind="monster", max_hp=40, armor_class=11)["id"]
    server.start_combat(cid, [caster, ogre])
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, caster, 0, 0)
    # Large ogre anchored at (7,5) covers (7,5),(8,5),(7,6),(8,6). A Fireball burst centred at
    # (8,6) catches it via the (8,6) footprint cell even though its ANCHOR (7,5) is farther.
    server.place_combatant_at_coords(cid, ogre, 7, 5, size="large")
    res = server.cast_spell(cid, caster, "Fireball", slot_level=3, origin=[8, 6])
    hit_ids = {row["character_id"] for row in res["aoe"]["targets"]}
    assert ogre in hit_ids  # the large creature was caught by its (8,6) footprint cell


# ── (5) BYTE-IDENTICAL REGRESSION: no difficult + all-Medium == PR-1..PR-3 ───


def test_no_difficult_all_medium_is_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    # A fight with no difficult terrain and all-Medium tokens must produce the same
    # move result as before this PR: plain Chebyshev cost, no difficult_crossed / size keys.
    cid, a, t = _grid_fight()
    server.place_combatant_at_coords(cid, t, 10, 0)
    server.place_combatant_at_coords(cid, a, 0, 0)
    res = server.move_to_coords(cid, a, 4, 4)
    assert res["cost_cells"] == 4
    assert "difficult_crossed" not in res
    view = server._combat_view(server._require(cid))
    for entry in view["order"]:
        assert "size" not in entry        # Medium never surfaces a size key
        assert "footprint" not in entry


def test_pure_helpers_additive_defaults():
    # Empty difficult / medium size preserve every PR-1..PR-3 pure result exactly.
    assert combat_grid.reachable((0, 0), 6, set(), 20, 20) == \
        combat_grid.reachable((0, 0), 6, set(), 20, 20, frozenset(), frozenset())
    assert combat_grid.shortest_path((0, 0), (5, 5), set(), 20, 20) == \
        combat_grid.shortest_path((0, 0), (5, 5), set(), 20, 20, frozenset(), frozenset())
    assert combat_grid.in_melee_reach((2, 2), (3, 3)) is True
    assert combat_grid.footprint_distance_cells((2, 2), "medium", (3, 3), "medium") == 1
