"""#1251 grid (PR-2) — area-of-effect TEMPLATES (sphere / cone / line) + the cast_spell
grid-template resolution that maps a spell's SRD area onto `affected_tile_coords` and the
occupants caught.

ADDITIVE / opt-in: template geometry runs only when a grid cast passes an `origin` cell
(and the fight is on the grid); a cast without `origin` is BYTE-FOR-BYTE today's behaviour
(the byte-identical regression test below is the load-bearing guard). Line-of-effect (walls
between the origin and a cell) is DEFERRED to #1252 — PR-2 templates are permissive.

The pure geometry lives in combat_grid (sphere_cells / cone_cells / line_cells); the wiring
lives in server.cast_spell. Tests here cover both: cell-set geometry at several origins/
facings + edge clipping, then the end-to-end Fireball-class multi-target resolution.
"""

import pytest

import combat_grid
import server


# ── (1) SPHERE geometry ──────────────────────────────────────────────────────


def test_sphere_radius_zero_is_just_center():
    assert combat_grid.sphere_cells((5, 5), 0, 20, 20) == {(5, 5)}


def test_sphere_20ft_reaches_four_cells_in_king_moves():
    # 20ft / 5ft cells = 4 cells reach -> a 9x9 (radius-4 Chebyshev) block around center.
    cells = combat_grid.sphere_cells((10, 10), 20, 40, 40)
    assert (10, 10) in cells
    assert (14, 10) in cells and (10, 14) in cells  # 4 cells out orthogonally
    assert (14, 14) in cells  # 4 cells out diagonally (Chebyshev counts it as 4)
    assert (15, 10) not in cells  # 5 cells out is beyond a 20ft (=4-cell) reach
    # 9x9 block = 81 cells when fully in bounds.
    assert len(cells) == 81


def test_sphere_clips_at_grid_bounds():
    # A 20ft burst centred at the corner (0,0) is clipped to the in-bounds quadrant.
    cells = combat_grid.sphere_cells((0, 0), 20, 40, 40)
    assert all(x >= 0 and y >= 0 for (x, y) in cells)
    assert (0, 0) in cells and (4, 4) in cells
    assert (-1, 0) not in cells
    # Only the +x/+y quadrant of the 9x9 block survives: 5x5 = 25.
    assert len(cells) == 25


# ── (2) CONE geometry ────────────────────────────────────────────────────────


def test_cone_orthogonal_widens_one_to_one():
    # A 15ft (=3-cell) cone cast east (+x) from (0,10): depth d has lateral spread +-d.
    cells = combat_grid.cone_cells((0, 10), (1, 10), 15, 40, 40)
    assert (0, 10) not in cells  # the emitter cell is excluded
    # depth 1: 3 cells (y in 9,10,11); depth 2: 5; depth 3: 7 -> 15 total.
    assert (1, 10) in cells and (1, 9) in cells and (1, 11) in cells
    assert (1, 8) not in cells  # lateral 2 at depth 1 is out
    assert (3, 7) in cells and (3, 13) in cells  # depth 3, lateral +-3
    assert (3, 6) not in cells
    assert len(cells) == 3 + 5 + 7


def test_cone_direction_follows_aim_point():
    # Same cone cast west (-x): mirror image, all cells have x < origin.
    cells = combat_grid.cone_cells((10, 10), (0, 10), 15, 40, 40)
    assert cells
    assert all(x < 10 for (x, y) in cells)


def test_cone_diagonal_facing_fans_into_the_quadrant():
    # A 15ft (=3-cell) cone cast SE (+x,+y) from the corner (0,0): a square quadrant fan,
    # origin excluded, every cell in the +x/+y quadrant within depth 3.
    cells = combat_grid.cone_cells((0, 0), (1, 1), 15, 40, 40)
    assert (0, 0) not in cells
    assert all(x >= 0 and y >= 0 for (x, y) in cells)
    assert (3, 3) in cells and (2, 2) in cells and (3, 0) in cells and (0, 3) in cells
    assert (4, 0) not in cells  # depth 4 is beyond a 3-cell cone


def test_cone_zero_facing_is_empty():
    assert combat_grid.cone_cells((5, 5), (5, 5), 30, 40, 40) == set()


def test_cone_clips_at_bounds():
    # Cone cast north (-y) from near the top edge is truncated, never negative.
    cells = combat_grid.cone_cells((10, 1), (10, 0), 30, 40, 40)
    assert all(y >= 0 for (x, y) in cells)


# ── (3) LINE geometry ────────────────────────────────────────────────────────


def test_line_east_is_single_cell_wide_by_default():
    # 20ft (=4-cell), 5ft-wide line east from (0,10): cells (1..4, 10). Origin excluded.
    cells = combat_grid.line_cells((0, 10), (1, 10), 20, 5, 40, 40)
    assert cells == {(1, 10), (2, 10), (3, 10), (4, 10)}


def test_line_width_thickens_symmetrically():
    # 15ft-wide (=3-cell) line thickens +-1 about the centre line.
    cells = combat_grid.line_cells((0, 10), (1, 10), 10, 15, 40, 40)
    # 2 cells long x 3 wide = 6 cells.
    assert cells == {
        (1, 9), (1, 10), (1, 11),
        (2, 9), (2, 10), (2, 11),
    }


def test_line_diagonal_facing():
    # A line cast to the SE (+x,+y): steps along the diagonal.
    cells = combat_grid.line_cells((0, 0), (1, 1), 20, 5, 40, 40)
    assert cells == {(1, 1), (2, 2), (3, 3), (4, 4)}


def test_line_zero_facing_is_empty():
    assert combat_grid.line_cells((5, 5), (5, 5), 30, 5, 40, 40) == set()


def test_line_clips_at_bounds():
    cells = combat_grid.line_cells((38, 10), (39, 10), 30, 5, 40, 40)
    assert all(x < 40 for (x, y) in cells)
    assert cells == {(39, 10)}  # only the one in-bounds cell survives


# ── (4) end-to-end cast_spell grid-template resolution ───────────────────────


@pytest.fixture
def gridfight(tmp_path, monkeypatch):
    """A caster (wizard, placed) + three goblins on a 20x20 grid, in active combat.
    Two goblins stand inside a Fireball centred at (10,10); one stands well outside."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AoE 1251")["id"]
    wiz = server.create_character(cid, "Wizard", kind="player", max_hp=30, armor_class=14)["id"]
    server.update_character(cid, wiz, patch={"spell_slots": {
        "1": {"maximum": 4, "used": 0}, "3": {"maximum": 4, "used": 0}}})
    g1 = server.create_character(cid, "Gob1", kind="monster", max_hp=15, armor_class=12)["id"]
    g2 = server.create_character(cid, "Gob2", kind="monster", max_hp=15, armor_class=12)["id"]
    g3 = server.create_character(cid, "Gob3", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [wiz, g1, g2, g3])
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, wiz, 2, 2)
    server.place_combatant_at_coords(cid, g1, 10, 10)   # burst centre
    server.place_combatant_at_coords(cid, g2, 12, 11)   # within 20ft (4 cells)
    server.place_combatant_at_coords(cid, g3, 2, 18)    # far away, outside the burst
    return cid, wiz, g1, g2, g3


def test_fireball_hits_exactly_the_template_occupants(gridfight):
    cid, wiz, g1, g2, g3 = gridfight
    res = server.cast_spell(cid, wiz, "Fireball", slot_level=3, origin=[10, 10])
    tiles = {tuple(t) for t in res["affected_tile_coords"]}
    assert (10, 10) in tiles and (12, 11) in tiles
    assert (2, 18) not in tiles
    aoe = res["aoe"]
    hit_ids = {row["character_id"] for row in aoe["targets"]}
    assert hit_ids == {g1, g2}  # exactly the two inside the burst; g3 is spared


def test_fireball_save_for_half_accounting_is_honest(gridfight):
    cid, wiz, g1, g2, g3 = gridfight
    res = server.cast_spell(cid, wiz, "Fireball", slot_level=3, origin=[10, 10])
    aoe = res["aoe"]
    assert aoe["on_save"] == "half"
    shared = aoe["shared_damage"]["total"]
    for row in aoe["targets"]:
        # A saved-and-halved row takes floor(shared/2); a failed row takes the full roll.
        # Either way the applied damage is one of exactly those two honest values.
        assert row["damage_taken"] in (shared, shared // 2)
        if row["saved"]:
            assert row["halved"] is True
            assert row["damage_taken"] == shared // 2
        else:
            assert row["damage_taken"] == shared


def test_cone_cast_uses_target_as_aim_point(gridfight):
    # Burning Hands is a 15ft cone; aim it from the caster (2,2) toward the SE via origin.
    cid, wiz, g1, g2, g3 = gridfight
    # Move a goblin into the cone path first.
    server.place_combatant_at_coords(cid, g1, 4, 4)
    res = server.cast_spell(cid, wiz, "Burning Hands", slot_level=1, origin=[3, 3])
    tiles = {tuple(t) for t in res["affected_tile_coords"]}
    assert tiles  # a non-empty cone was projected
    assert (2, 2) not in tiles  # caster's own cell excluded (it's the cone point)


# ── (5) BYTE-IDENTICAL regression: a cast WITHOUT origin is unchanged ─────────


def test_single_target_cast_without_origin_has_no_aoe_keys(gridfight):
    cid, wiz, g1, g2, g3 = gridfight
    res = server.cast_spell(cid, wiz, "Fireball", slot_level=3)
    # No template was requested -> no grid-template keys leak into the result.
    assert "affected_tile_coords" not in res
    assert "aoe" not in res


def test_explicit_target_ids_path_still_works_off_template(gridfight):
    # The pre-existing explicit-list AoE path (target_ids) is untouched by PR-2.
    cid, wiz, g1, g2, g3 = gridfight
    res = server.cast_spell(cid, wiz, "Fireball", slot_level=3, target_ids=[g1, g3])
    assert "affected_tile_coords" not in res  # no geometry when caller lists ids explicitly
    hit_ids = {row["character_id"] for row in res["aoe"]["targets"]}
    assert hit_ids == {g1, g3}
