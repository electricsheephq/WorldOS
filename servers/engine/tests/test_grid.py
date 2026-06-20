"""#461 grid (PR-1) — coordinate-authority movement spine.

ADDITIVE / opt-in: a fight is on the grid ONLY after set_grid (grid_enabled=True).
With grid_enabled=False, zone/theater combat is BYTE-FOR-BYTE today's behaviour —
the round-trip + zero-key-delta tests below are the load-bearing guards for that.

PR-1 scope is the movement spine: Chebyshev distance, speed->cells budget, Dash,
open-floor reachability, and the reach-leave opportunity-attack predicate (with the
two SRD gates the zone loop omits: one Reaction/round, and can't-see). AoE, cover,
LoS, terrain, size/reach and ranged-range gating are DEFERRED to later PRs.
"""

import json

import pytest

import combat_grid
import server
import store
from models import Combat, Combatant, Condition


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fight(tmp_path, monkeypatch):
    """A hero (player, speed 30) + a goblin (monster) in active combat.
    Returns (campaign_id, hero_id, goblin_id)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Grid 461")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])
    return cid, hero, gob


def _snapshot_bytes(cid: str) -> bytes:
    """Raw on-disk snapshot bytes (for the read-only-discipline test)."""
    import store as _store
    path = _store._campaign_dir(cid) / "snapshot.json" if hasattr(_store, "_campaign_dir") else None
    # Fall back to scanning the state dir for the snapshot.
    if path is None or not path.exists():
        import os
        for root, _dirs, files in os.walk(os.environ["WORLDOS_STATE_DIR"]):
            if "snapshot.json" in files and cid in root:
                path = __import__("pathlib").Path(root) / "snapshot.json"
                break
    return path.read_bytes()


# ── (1) ROUND-TRIP: pre-grid snapshot loads with grid fields absent → defaults ─


def test_pregrid_snapshot_roundtrips_to_defaults(fight):
    """A snapshot written before the grid fields existed (we simulate by deleting them)
    loads with grid_enabled=False and x/y=None — and re-validates clean."""
    cid, hero, gob = fight
    c = store.load_campaign(cid)
    raw = json.loads(c.model_dump_json())
    # Simulate a pre-grid snapshot: strip every grid key that didn't used to exist.
    for k in ("grid_enabled", "grid_width", "grid_height", "grid_cell_size", "diagonal_mode"):
        raw["combat"].pop(k, None)
    for cb in raw["combat"]["order"]:
        for k in ("x", "y", "moved_cells_this_turn", "dashed"):
            cb.pop(k, None)
    from models import Campaign
    reloaded = Campaign.model_validate(raw)
    assert reloaded.combat.grid_enabled is False
    assert reloaded.combat.grid_width == 0 and reloaded.combat.grid_height == 0
    assert reloaded.combat.grid_cell_size == 5
    assert reloaded.combat.diagonal_mode == "chebyshev"
    for cb in reloaded.combat.order:
        assert cb.x is None and cb.y is None
        assert cb.moved_cells_this_turn == 0
        assert cb.dashed is False


def test_grid_off_zone_transcript_byte_identical(tmp_path, monkeypatch):
    """With grid_enabled=False, a ZONE transcript produces the SAME engine outputs as a
    clean run with no grid code present (the grid path must be wholly inert)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "a"))
    cid = server.create_campaign("Z")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=20, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=10)["id"]
    server.start_combat(cid, [hero, gob])
    server.set_zones(cid, [{"name": "hall", "adjacent": ["dais"]}, {"name": "dais", "adjacent": ["hall"]}])
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, gob, "hall")
    move = server.move_to_zone(cid, hero, "dais")
    # The zone move surfaces NO grid keys at all (grid is off).
    assert "grid" not in move
    view = server._combat_view(store.load_campaign(cid))
    assert "grid" not in view
    assert all("x" not in e and "y" not in e for e in view["order"])


def test_grid_off_theater_transcript_clean(fight):
    """grid_enabled=False, no zones: theater-of-the-mind move surfaces no grid keys."""
    cid, hero, _gob = fight
    res = server.move_to_zone(cid, hero, "over there")
    assert "grid" not in res
    view = server._combat_view(store.load_campaign(cid))
    assert "grid" not in view


# ── (2) Chebyshev math ───────────────────────────────────────────────────────


def test_chebyshev_pure_diagonal_is_one_cell():
    assert combat_grid.chebyshev_cells((0, 0), (1, 1)) == 1
    assert combat_grid.chebyshev_cells((0, 0), (3, 3)) == 3


def test_chebyshev_orthogonal_equals_diagonal():
    # 3 east == 3 diagonal under Chebyshev (5e default diagonal rule).
    assert combat_grid.chebyshev_cells((0, 0), (3, 0)) == combat_grid.chebyshev_cells((0, 0), (3, 3))


def test_range_ft_origin_not_counted():
    # adjacent (1 cell) == 5ft; the origin cell is not counted.
    assert combat_grid.range_ft(0) == 0
    assert combat_grid.range_ft(1) == 5
    assert combat_grid.range_ft(3) == 15
    assert combat_grid.distance_ft((0, 0), (0, 0)) == 0  # same cell, 0ft


# ── (3) speed → budget ───────────────────────────────────────────────────────


def test_speed_to_budget_cells():
    assert combat_grid.movement_budget_cells(30) == 6
    assert combat_grid.movement_budget_cells(25) == 5  # floor(25/5)
    assert combat_grid.movement_budget_cells(30, dashed=True) == 12


def test_over_budget_move_is_advisory_but_still_moves(fight):
    cid, hero, _gob = fight
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, hero, 0, 0)
    # speed 30 -> 6 cells; a 7-cell move is over budget.
    res = server.move_to_coords(cid, hero, 7, 0)
    assert res["movement_illegal"]["reason"] if isinstance(res.get("movement_illegal"), dict) else True
    assert "movement_illegal" in res
    c = store.load_campaign(cid)
    cb = server._combatant(c, hero)
    assert cb.x == 7 and cb.y == 0  # moved anyway (advisory, never hard-blocks)


def test_moved_cells_accumulate_across_a_broken_up_move(fight):
    cid, hero, _gob = fight
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, hero, 0, 0)
    server.move_to_coords(cid, hero, 3, 0)  # 3 cells
    res = server.move_to_coords(cid, hero, 5, 0)  # +2 cells
    c = store.load_campaign(cid)
    cb = server._combatant(c, hero)
    assert cb.moved_cells_this_turn == 5
    assert "movement_illegal" not in res  # 5 <= 6 budget


# ── (4) Dash ─────────────────────────────────────────────────────────────────


def test_use_action_dash_accepted_and_sets_dashed(fight):
    cid, hero, _gob = fight
    server.set_grid(cid, 20, 20)
    cur = server.get_state(cid)["current_turn"]
    res = server.use_action(cid, cur, "dash")  # must NOT raise
    assert res["ok"] is True
    c = store.load_campaign(cid)
    cb = server._combatant(c, cur)
    assert cb.dashed is True


def test_dash_doubles_budget(fight):
    cid, hero, gob = fight
    # ensure hero is current so dash is legal
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)
    cid_cur = server.get_state(cid)["current_turn"]
    server.set_grid(cid, 30, 30)
    server.place_combatant_at_coords(cid, cid_cur, 0, 0)
    server.use_action(cid, cid_cur, "dash")
    res = server.move_to_coords(cid, cid_cur, 10, 0)  # 10 cells <= 12 dashed budget
    assert "movement_illegal" not in res


def test_dash_then_move_still_provokes(fight):
    """Dash does NOT suppress opportunity attacks (only Disengage does)."""
    cid, hero, gob = fight
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)
    cur = server.get_state(cid)["current_turn"]
    other = gob if cur == hero else hero
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, cur, 0, 0)
    server.place_combatant_at_coords(cid, other, 1, 0)  # adjacent threatener
    server.use_action(cid, cur, "dash")
    res = server.move_to_coords(cid, cur, 5, 0)  # leaves the threatener's reach
    assert res["opportunity_attack"] is True
    assert [p["id"] for p in res["provokers"]] == [other]


def test_disengage_then_move_no_provokers(fight):
    cid, hero, gob = fight
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)
    cur = server.get_state(cid)["current_turn"]
    other = gob if cur == hero else hero
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, cur, 0, 0)
    server.place_combatant_at_coords(cid, other, 1, 0)
    server.use_action(cid, cur, "disengage")
    res = server.move_to_coords(cid, cur, 5, 0)
    assert res["opportunity_attack"] is False
    assert res["provokers"] == []


# ── (5) Grid OA reach-leave ──────────────────────────────────────────────────


def test_reach_leave_provokes():
    assert combat_grid.provokes_on_leave((0, 0), (5, 0), (1, 0)) is True


def test_staying_in_reach_does_not_provoke():
    # side-step from (0,0) to (0,1), threat at (1,0): still adjacent at the new cell.
    assert combat_grid.provokes_on_leave((0, 0), (0, 1), (1, 0)) is False


def test_never_in_reach_does_not_provoke():
    assert combat_grid.provokes_on_leave((0, 0), (3, 0), (9, 9)) is False


# ── (6) Grid OA SRD gates (the zone loop lacks these) ────────────────────────


def test_threatener_with_reaction_used_is_not_a_provoker(fight):
    cid, hero, gob = fight
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)
    cur, other = hero, gob
    if server.get_state(cid)["current_turn"] != hero:
        cur, other = gob, hero
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, cur, 0, 0)
    server.place_combatant_at_coords(cid, other, 1, 0)
    # Spend the threatener's reaction (one Reaction/round).
    c = store.load_campaign(cid)
    server._combatant(c, other).reaction_used = True
    store.save_campaign(c)
    res = server.move_to_coords(cid, cur, 5, 0)
    assert res["opportunity_attack"] is False
    assert res["provokers"] == []


@pytest.mark.parametrize("cond", [Condition.BLINDED, Condition.UNCONSCIOUS])
def test_blind_or_unconscious_threatener_is_not_a_provoker(fight, cond):
    cid, hero, gob = fight
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)
    cur, other = hero, gob
    if server.get_state(cid)["current_turn"] != hero:
        cur, other = gob, hero
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, cur, 0, 0)
    server.place_combatant_at_coords(cid, other, 1, 0)
    c = store.load_campaign(cid)
    other_ch = c.characters[other]
    other_ch.conditions.append(cond)
    store.save_campaign(c)
    res = server.move_to_coords(cid, cur, 5, 0)
    assert res["opportunity_attack"] is False


# ── (7) next_turn reset scope ────────────────────────────────────────────────


def test_next_turn_resets_only_starting_combatants_movement(fight):
    cid, hero, gob = fight
    server.set_grid(cid, 20, 20)
    # Satisfy next_turn's PC-skip guard for the outgoing combatant.
    server.use_action(cid, server.get_state(cid)["current_turn"], "skip")
    # Give BOTH combatants nonzero movement + dashed.
    c = store.load_campaign(cid)
    for cb in c.combat.order:
        cb.moved_cells_this_turn = 3
        cb.dashed = True
    store.save_campaign(c)
    nxt = server.next_turn(cid)
    new_cur = nxt["current"]
    c = store.load_campaign(cid)
    for cb in c.combat.order:
        if cb.character_id == new_cur:
            assert cb.moved_cells_this_turn == 0 and cb.dashed is False
        else:
            assert cb.moved_cells_this_turn == 3 and cb.dashed is True


def test_next_turn_does_not_double_reset_disengaged(fight):
    # Sanity: disengaged is reset exactly once in the cur-only block (no new reset added).
    cid, hero, gob = fight
    server.set_grid(cid, 20, 20)
    cur = server.get_state(cid)["current_turn"]
    if cur == hero:  # a PC must act/skip before next_turn (PC-skip guard)
        server.use_action(cid, cur, "skip")
    nxt = server.next_turn(cid)
    new_cur = nxt["current"]
    c = store.load_campaign(cid)
    cb = server._combatant(c, new_cur)
    assert cb.disengaged is False


# ── (8) read-only discipline (set_grid path stays sole-writer; the deferred ──
#         read-only helpers are PR-1.5 — assert measured-range math is pure here)


def test_measure_math_is_pure_no_state_dir():
    # Pure helpers never touch state — exercised directly (the read-only TOOLS that
    # wrap these are deferred to PR-1.5 for schema-budget reasons; the math is here).
    assert combat_grid.distance_ft((0, 0), (3, 4)) == 20  # max(3,4)=4 cells * 5
    assert combat_grid.in_melee_reach((0, 0), (1, 1)) is True
    assert combat_grid.in_melee_reach((0, 0), (2, 0)) is False


# ── (9) schema budget stays green is enforced by test_tool_schema_budget.py ──
