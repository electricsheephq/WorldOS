"""#1252 grid (PR-3) — line-of-sight ray traversal + SRD 5.2 cover, plus the
server-side wiring (AoE line-of-effect cull in cast_spell + cover AC in attack()).

ADDITIVE / opt-in: cover applies only on the grid with BOTH combatants placed; the
line-of-effect cull runs only for a grid AoE cast. Off-grid / unplaced combat is
BYTE-FOR-BYTE today's behaviour — the byte-identical regression test below is the
load-bearing guard.

The pure geometry lives in combat_grid (line_blockers / has_line_of_effect /
cover_between / cover_ac_bonus); the wiring lives in server.attack + server.cast_spell.

RAY / TIE-BREAK: supercover line between cell centres; a pure corner graze severs the
ray only when BOTH shoulder cells of the diagonal step are blockers (permissive — a lone
diagonal blocker is slipped past). COVER: 0 blockers -> none, 1 -> half (+2), 2+ ->
three-quarters (+5), a fully-walled ray -> total (untargetable).
"""

import pytest

import combat_grid
import server


# ── (1) LINE-OF-SIGHT ray: clear / blocked / corner-graze / adjacent ─────────


def test_los_clear_open_floor():
    # No blockers => always a clear line (additive: nothing culled off open floor).
    assert combat_grid.has_line_of_effect((0, 0), (5, 5), set()) is True
    assert combat_grid.line_blockers((0, 0), (5, 5), set()) == set()


def test_los_blocked_by_wall_on_the_ray():
    # A wall cell squarely on the horizontal ray severs it.
    blocking = {(3, 0)}
    assert combat_grid.has_line_of_effect((0, 0), (6, 0), blocking) is False
    assert (3, 0) in combat_grid.line_blockers((0, 0), (6, 0), blocking)


def test_los_endpoints_are_never_their_own_blockers():
    # A target standing IN a blocking cell (a doorway/prop) is still reachable — the
    # endpoints are excluded from the between-set.
    blocking = {(0, 0), (6, 0)}
    assert combat_grid.has_line_of_effect((0, 0), (6, 0), blocking) is True


def test_los_adjacent_cells_have_no_interior():
    # Adjacent (incl. diagonal) cells have nothing strictly between them.
    assert combat_grid.line_blockers((2, 2), (3, 2), {(2, 2), (3, 2)}) == set()
    assert combat_grid.has_line_of_effect((2, 2), (3, 3), {(9, 9)}) is True


def test_los_corner_graze_single_diagonal_blocker_slips_past():
    # Pure diagonal ray (0,0)->(4,4) passes through the vertices between shoulder pairs
    # like {(1,0),(0,1)}. A SINGLE diagonal blocker at one shoulder must NOT sever it
    # (permissive corner-graze tie-break).
    assert combat_grid.has_line_of_effect((0, 0), (4, 4), {(1, 0)}) is True
    assert combat_grid.has_line_of_effect((0, 0), (4, 4), {(2, 1)}) is True


def test_los_corner_graze_both_shoulders_block():
    # BOTH shoulders of a diagonal step are blockers => the corner is sealed, ray severed.
    assert combat_grid.has_line_of_effect((0, 0), (4, 4), {(1, 0), (0, 1)}) is False


def test_los_is_symmetric():
    # a->b blocked iff b->a blocked (the ray model must be direction-independent).
    blocking = {(2, 1), (1, 2)}
    fwd = combat_grid.has_line_of_effect((0, 0), (3, 3), blocking)
    rev = combat_grid.has_line_of_effect((3, 3), (0, 0), blocking)
    assert fwd == rev


# ── (2) COVER tiers + AC arithmetic ──────────────────────────────────────────


def test_cover_none_clear_shot():
    assert combat_grid.cover_between((0, 0), (6, 0), set()) == "none"
    assert combat_grid.cover_ac_bonus("none") == 0


def test_cover_half_one_intervening_blocker():
    # A single low wall/pillar between attacker and target => half cover (+2).
    blocking = {(3, 0)}
    # Target one cell PAST the blocker so the interior isn't fully walled (=> not total).
    assert combat_grid.cover_between((0, 0), (5, 0), blocking) == "half"
    assert combat_grid.cover_ac_bonus("half") == 2


def test_cover_three_quarters_two_intervening_blockers():
    # Two separate blockers on the ray with an open cell between them => three-quarters (+5).
    blocking = {(2, 0), (4, 0)}
    assert combat_grid.cover_between((0, 0), (6, 0), blocking) == "three_quarters"
    assert combat_grid.cover_ac_bonus("three_quarters") == 5


def test_cover_total_when_ray_fully_walled():
    # Every interior cell on the ray is a blocker (a solid wall band) => total cover.
    blocking = {(1, 0), (2, 0), (3, 0)}
    assert combat_grid.cover_between((0, 0), (4, 0), blocking) == "total"
    assert combat_grid.cover_ac_bonus("total") == 0  # total isn't an AC bump


def test_cover_open_floor_is_none():
    assert combat_grid.cover_between((0, 0), (9, 9), set()) == "none"


# ── (3) attack() applies cover AC when on-grid + both placed ─────────────────


def _grid_fight_two_combatants(cover_wall):
    """A 2-combatant grid fight: attacker at (0,0), target at (6,0), with `cover_wall`
    the list of impassable [x,y] cells. Returns (campaign_id, attacker_id, target_id)."""
    cid = server.create_campaign("los-test")["id"]
    a = server.create_character(cid, "Archer", kind="player", max_hp=30, armor_class=14)["id"]
    t = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=14)["id"]
    server.start_combat(cid, [a, t])
    server.set_grid(cid, 20, 20, obstacles=cover_wall)
    server.place_combatant_at_coords(cid, a, 0, 0)
    server.place_combatant_at_coords(cid, t, 6, 0)
    return cid, a, t


def test_attack_half_cover_raises_target_ac_by_two():
    cid, a, t = _grid_fight_two_combatants([[3, 0]])  # one wall between => half
    base_ac = 14
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d6", is_ranged=True,
    )
    assert res.get("cover") is not None
    assert res["cover"]["tier"] == "half"
    assert res["cover"]["ac_bonus"] == 2
    assert res["cover"]["effective_ac"] == base_ac + 2


def test_attack_three_quarters_cover_raises_ac_by_five():
    cid, a, t = _grid_fight_two_combatants([[2, 0], [4, 0]])  # two walls => three-quarters
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=0, damage_dice="1d6", is_ranged=True,
    )
    assert res["cover"]["tier"] == "three_quarters"
    assert res["cover"]["ac_bonus"] == 5


def test_attack_total_cover_refuses_the_shot():
    cid, a, t = _grid_fight_two_combatants([[3, 0]])
    # Put the target immediately behind a solid wall band so EVERY interior cell of the
    # attacker->target ray is a blocker (fully-walled ray => total cover). attacker (0,0),
    # target (4,0), walls (1,0),(2,0),(3,0) => interior [(1,0),(2,0),(3,0)] all blocked.
    server.set_grid(cid, 20, 20, obstacles=[[1, 0], [2, 0], [3, 0]])
    server.place_combatant_at_coords(cid, t, 4, 0)
    with pytest.raises(ValueError, match="(?i)total cover"):
        server.attack(
            campaign_id=cid, attacker_id=a, target_id=t,
            attack_bonus=0, damage_dice="1d6", is_ranged=True,
        )


def test_attack_no_cover_is_unchanged_when_clear():
    cid, a, t = _grid_fight_two_combatants([])  # open floor
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=5, damage_dice="1d6", is_ranged=True,
    )
    # No blockers => no cover key surfaced (or tier "none"); AC unmodified.
    assert res.get("cover") is None or res["cover"]["tier"] == "none"


# ── (4) AoE line-of-effect exclusion (a wall shields cells behind it) ────────


def test_aoe_sphere_excludes_cells_with_no_line_of_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("aoe-loe")["id"]
    caster = server.create_character(cid, "Mage", kind="player", max_hp=30, armor_class=14)["id"]
    server.update_character(cid, caster, patch={"spell_slots": {
        "3": {"maximum": 4, "used": 0}}})
    behind = server.create_character(cid, "Hidden", kind="monster", max_hp=30, armor_class=12)["id"]
    exposed = server.create_character(cid, "Exposed", kind="monster", max_hp=30, armor_class=12)["id"]
    server.start_combat(cid, [caster, behind, exposed])
    # A wall band shields (8,5) from a burst centred at (5,5); (5,7) is in the open.
    server.set_grid(cid, 20, 20, obstacles=[[6, 5], [7, 5], [6, 4], [6, 6]])
    server.place_combatant_at_coords(cid, caster, 5, 9)
    server.place_combatant_at_coords(cid, behind, 8, 5)
    server.place_combatant_at_coords(cid, exposed, 5, 7)
    res = server.cast_spell(cid, caster, "Fireball", slot_level=3, origin=[5, 5])
    coords = {tuple(xy) for xy in (res.get("affected_tile_coords") or [])}
    # The exposed cell (in the burst, clear LoE) is affected; the shielded one is culled.
    assert (5, 7) in coords
    assert (8, 5) not in coords
    # The origin cell always has line of effect to itself.
    assert (5, 5) in coords


# ── (5) BYTE-IDENTICAL regression: off-grid / unplaced == today ──────────────


def test_offgrid_attack_has_no_cover_key():
    cid = server.create_campaign("offgrid")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=14)["id"]
    t = server.create_character(cid, "B", kind="monster", max_hp=30, armor_class=12)["id"]
    server.start_combat(cid, [a, t])
    # NO set_grid => zone/theater. Cover must never appear.
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=5, damage_dice="1d6", is_ranged=True,
    )
    assert "cover" not in res or res["cover"] is None


def test_ongrid_but_unplaced_target_has_no_cover():
    cid = server.create_campaign("unplaced")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=14)["id"]
    t = server.create_character(cid, "B", kind="monster", max_hp=30, armor_class=12)["id"]
    server.start_combat(cid, [a, t])
    server.set_grid(cid, 20, 20, obstacles=[[3, 0]])
    server.place_combatant_at_coords(cid, a, 0, 0)
    # target NOT placed => cover can't be derived => no cover key.
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=5, damage_dice="1d6", is_ranged=True,
    )
    assert res.get("cover") is None
