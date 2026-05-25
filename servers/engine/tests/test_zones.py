"""S2.7 — Tactical Zone System.

A deterministic, ENGINE-USED positional model for combat: named regions with
adjacency, used for melee range, movement (opportunity attacks), and AoE
targeting. Everything here is ADDITIVE — a fight with no zones declared is
theater-of-the-mind and behaves exactly as before. These tests guard both the
new gating and that the no-zones default is untouched.
"""

import pytest

import server
import store


@pytest.fixture
def fight(tmp_path, monkeypatch):
    """A campaign with a hero (player) + a goblin (monster) in active combat.
    Returns (campaign_id, hero_id, goblin_id)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("S2.7 Zones")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=20, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=10)["id"]
    server.start_combat(cid, [hero, gob])
    return cid, hero, gob


def _scene(cid):
    """A 3-zone line: doorway — hall — dais (doorway not adjacent to dais)."""
    return server.set_zones(cid, [
        {"name": "doorway", "description": "a narrow stone arch", "adjacent": ["hall"]},
        {"name": "hall", "adjacent": ["doorway", "dais"]},
        {"name": "dais", "description": "a raised altar platform", "adjacent": ["hall"]},
    ])


# --- model defaults / additive ---------------------------------------------

def test_combatant_zone_defaults_empty_and_combat_has_no_zones(fight):
    cid, hero, _gob = fight
    c = store.load_campaign(cid)
    assert c.combat.zones == []
    assert all(cb.zone == "" for cb in c.combat.order)
    # The combat view omits zone fields entirely when none are in play.
    view = server.get_state(cid)
    assert view["in_combat"] is True
    cview = server._combat_view(c)
    assert "zones" not in cview
    assert all("zone" not in entry for entry in cview["order"])


def test_set_zones_stores_and_surfaces_them(fight):
    cid, _hero, _gob = fight
    res = _scene(cid)
    assert res["warnings"] == []
    names = {z["name"] for z in res["zones"]}
    assert names == {"doorway", "hall", "dais"}
    # persisted
    c = store.load_campaign(cid)
    assert {z.name for z in c.combat.zones} == names


def test_set_zones_flags_unknown_adjacency(fight):
    cid, _hero, _gob = fight
    res = server.set_zones(cid, [{"name": "ledge", "adjacent": ["nowhere"]}])
    assert any("nowhere" in w for w in res["warnings"])


def test_set_zones_replaces_wholesale(fight):
    cid, _hero, _gob = fight
    _scene(cid)
    res = server.set_zones(cid, [{"name": "open field"}])
    assert {z["name"] for z in res["zones"]} == {"open field"}


# --- placement --------------------------------------------------------------

def test_place_combatant_sets_zone_and_surfaces_it(fight):
    cid, hero, _gob = fight
    _scene(cid)
    res = server.place_combatant(cid, hero, "doorway")
    assert res["placed"]["zone"] == "doorway"
    assert res["warnings"] == []
    entry = next(e for e in res["order"] if e["character_id"] == hero)
    assert entry["zone"] == "doorway"


def test_place_combatant_warns_on_unknown_zone(fight):
    cid, hero, _gob = fight
    _scene(cid)
    res = server.place_combatant(cid, hero, "the moon")
    assert any("not a declared zone" in w for w in res["warnings"])
    # still placed (advisory, never blocks)
    assert res["placed"]["zone"] == "the moon"


def test_place_combatant_rejects_non_combatant(fight):
    cid, _hero, _gob = fight
    _scene(cid)
    with pytest.raises(ValueError):
        server.place_combatant(cid, "char_not_in_order", "doorway")


# --- movement + opportunity attacks ----------------------------------------

def test_move_leaving_zone_with_hostile_flags_opportunity_attack(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, gob, "hall")  # hostile shares the zone
    res = server.move_to_zone(cid, hero, "dais")
    assert res["from"] == "hall" and res["to"] == "dais"
    assert res["opportunity_attack"] is True
    assert [p["id"] for p in res["provokers"]] == [gob]
    # the move actually happened
    c = store.load_campaign(cid)
    assert server._combatant(c, hero).zone == "dais"


def test_move_no_oa_when_no_hostile_in_left_zone(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "dais")  # hostile is elsewhere
    res = server.move_to_zone(cid, hero, "hall")
    assert res["opportunity_attack"] is False
    assert res["provokers"] == []


def test_move_no_oa_from_ally_only_zone(fight, tmp_path, monkeypatch):
    # Two allies stacked together; one leaves — an ally does not provoke.
    cid, hero, _gob = fight
    ally = server.create_character(cid, "Cleric", kind="companion", max_hp=18)["id"]
    server.end_combat(cid)
    server.start_combat(cid, [hero, ally])
    _scene(cid)
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, ally, "hall")
    res = server.move_to_zone(cid, hero, "dais")
    assert res["opportunity_attack"] is False


def test_move_no_oa_from_dead_hostile(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, gob, "hall")
    server.apply_damage(cid, gob, 999)  # goblin dies -> can't take an OA
    res = server.move_to_zone(cid, hero, "dais")
    assert res["opportunity_attack"] is False


def test_move_to_nonadjacent_zone_warns_but_allows(fight):
    cid, hero, _gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    res = server.move_to_zone(cid, hero, "dais")  # doorway not adjacent to dais
    assert any("not adjacent" in w for w in res["warnings"])
    assert res["to"] == "dais"  # allowed anyway


def test_move_to_adjacent_zone_no_warning(fight):
    cid, hero, _gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    res = server.move_to_zone(cid, hero, "hall")  # adjacent
    assert not any("not adjacent" in w for w in res["warnings"])


# --- melee range gate (advisory, never blocks) -----------------------------

def test_melee_attack_same_zone_no_warning(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, gob, "hall")
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3")
    assert "range_warning" not in res


def test_melee_attack_adjacent_zone_no_warning(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "hall")  # adjacent
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3")
    assert "range_warning" not in res


def test_melee_attack_far_zone_warns_but_still_resolves(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "dais")  # 2 hops away, not adjacent
    res = server.attack(cid, hero, gob, attack_bonus=99, damage_dice="1d6+3")
    assert "range_warning" in res
    assert "not in melee reach" in res["range_warning"]
    # NOT hard-blocked: the warning is ADVISORY — the attack still RESOLVED through the
    # dice. (Don't assert a hit: a natural 1 auto-misses even at +99, and which roll lands
    # here depends on RNG order across the suite. "It resolved, not blocked" is the point —
    # so assert the attack-roll resolved, which happens on hit OR miss.)
    assert "hit" in res
    assert "attack_roll" in res and res["attack_roll"]["total"] is not None
    # On a hit, damage was rolled and applied; on a miss (nat-1), damage stays None. Either
    # way the engine RESOLVED the attack rather than refusing it for being out of melee reach.
    assert res["hit"] is False or res["damage"] is not None


def test_ranged_attack_far_zone_never_warns(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "dais")
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d8", is_ranged=True)
    assert "range_warning" not in res  # ranged reaches any zone


def test_unplaced_combatant_no_range_warning(fight):
    # zones declared, but a combatant hasn't been placed yet (zone="") -> don't
    # invent a constraint.
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")  # goblin left unplaced
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6")
    assert "range_warning" not in res


# --- touch/melee spell range gate (cast_spell) ------------------------------

def test_touch_spell_far_zone_warns(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "dais")
    # Shock Grasp / any cantrip — cantrips spend no slot, so this works on a
    # vanilla character. is_melee=True triggers the touch-range check.
    res = server.cast_spell(cid, hero, "Shocking Grasp", target_id=gob, is_melee=True)
    assert "range_warning" in res


def test_ranged_spell_far_zone_no_warning(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "dais")
    res = server.cast_spell(cid, hero, "Fire Bolt", target_id=gob, is_melee=False)
    assert "range_warning" not in res  # ranged spell reaches any zone


# --- AoE targeting ----------------------------------------------------------

def test_combatants_in_zone_lists_occupants_for_aoe(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "dais")
    server.place_combatant(cid, gob, "dais")
    res = server.combatants_in_zone(cid, "dais")
    assert res["count"] == 2
    ids = {o["id"] for o in res["combatants"]}
    assert ids == {hero, gob}


def test_combatants_in_zone_empty_for_vacant_zone(fight):
    cid, hero, gob = fight
    _scene(cid)
    server.place_combatant(cid, hero, "hall")
    server.place_combatant(cid, gob, "hall")
    res = server.combatants_in_zone(cid, "dais")
    assert res["count"] == 0
    assert res["combatants"] == []


# --- THE ADDITIVE GUARANTEE: a no-zones fight still works unchanged ---------

def test_no_zones_combat_runs_unchanged(fight):
    """The whole point: declare no zones and combat is theater-of-the-mind. No
    range warnings, no positional state, full attack flow intact."""
    cid, hero, gob = fight
    # No set_zones / place_combatant calls at all.
    # A melee attack across "nowhere in particular" is never gated.
    res = server.attack(cid, hero, gob, attack_bonus=99, damage_dice="1d6+3")
    assert "range_warning" not in res
    # The attack RESOLVES ungated (no zones = theater-of-the-mind). Don't assert a hit: a
    # natural 1 auto-misses even at +99 and the dice are non-deterministic per run, so a
    # hit-assertion is ~5% flaky. The point here is "ungated + normal result", not a roll.
    assert "hit" in res and (res["hit"] is False or res["damage"] is not None)

    # combatants_in_zone returns empty (nobody is in any named zone).
    assert server.combatants_in_zone(cid, "anywhere")["count"] == 0

    # The turn flow is untouched. (attack() now wires into the action economy — an
    # Attack action by the current combatant consumes that turn's action — so we assert
    # the budget against the combatant whose turn it freshly is after next_turn, which is
    # deterministic regardless of who won initiative.)
    nxt = server.next_turn(cid)
    assert nxt["active"] is True
    assert "zones" not in nxt  # view stays clean
    new_cur = server.get_state(cid)["current_turn"]
    assert server.use_action(cid, new_cur, "action")["ok"] is True  # fresh turn, action available


def test_no_zones_move_to_zone_still_safe(fight):
    """move_to_zone with no declared zones: it just records a free-text position;
    no OA (no one shares a named zone), no adjacency warning (nothing to check)."""
    cid, hero, _gob = fight
    res = server.move_to_zone(cid, hero, "over there")
    assert res["opportunity_attack"] is False
    assert res["warnings"] == []  # no zones declared -> no adjacency check
    assert res["to"] == "over there"
