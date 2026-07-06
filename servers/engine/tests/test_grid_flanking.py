"""#1254 grid (PR-7) — flanking ADVANTAGE from an opposite-side ally (the common DMG-style
OPTIONAL rule; SRD 5.2 core has no flanking). Pure geometry lives in combat_grid.flanking
(opposite-sides, footprint-aware); the wiring lives in server.attack() — it consults the
existing `HouseRules.flanking_advantage` flag + the grid positions and folds the resulting
advantage into the roll, surfacing `attack_roll.flanking_advantage: True` in the payload.

ADDITIVE / opt-in: with the house rule OFF (its default) NOTHING in attack() changes — the
disabled-rule regression test is the load-bearing byte-identical guard. Only fires when
`flanking_advantage=True` AND the fight is on a grid.

CONVENTION (documented in combat_grid.flanking): attacker + a conscious ally are on OPPOSITE
SIDES of the target (line-through-centres) — directly opposite (same row/column with the
target between) OR diagonally opposite corners. Footprint-aware for Large+ tokens.
"""

import combat_grid
import server


# ── (1) PURE geometry: combat_grid.flanking ──────────────────────────────────


def test_flanking_directly_opposite_medium():
    # target (5,5); attacker directly left, ally directly right => flank.
    assert combat_grid.flanking((4, 5), "medium", (6, 5), "medium", (5, 5), "medium")
    # vertically opposite too (above/below).
    assert combat_grid.flanking((5, 4), "medium", (5, 6), "medium", (5, 5), "medium")


def test_flanking_diagonally_opposite_corners_medium():
    # opposite corners across the target => flank (standard grid convention).
    assert combat_grid.flanking((4, 4), "medium", (6, 6), "medium", (5, 5), "medium")


def test_same_side_allies_do_not_flank():
    # both attacker and ally on the target's LEFT => not across => no flank.
    assert not combat_grid.flanking((4, 5), "medium", (4, 4), "medium", (5, 5), "medium")
    # attacker left, ally directly ABOVE (perpendicular, beside not across) => no flank.
    assert not combat_grid.flanking((4, 5), "medium", (5, 4), "medium", (5, 5), "medium")


def test_flanking_footprint_aware_large_attacker():
    # A LARGE (2×2) attacker anchored at (3,4) spans [3,5)×[4,6): footprint centre (8,10)
    # in half-cell units => sits to the LEFT of the target at (6,5) (centre (13,11)). A
    # Medium ally directly to the RIGHT at (7,5) flanks WITH the large attacker.
    assert combat_grid.flanking((3, 4), "large", (7, 5), "medium", (6, 5), "medium")
    # And a same-side (also-left) large placement does NOT flank with the left attacker.
    assert not combat_grid.flanking((3, 4), "large", (4, 5), "medium", (6, 5), "medium")


# ── (2) SERVER wiring: attack() folds flanking into advantage ────────────────


def _flank_fight(flanking=True, ally_kind="companion"):
    """A grid fight: attacker (player) at (4,5), target (monster) at (5,5), ally at (6,5)
    — attacker + ally on OPPOSITE sides of the target. Returns (cid, attacker, target, ally).
    `flanking` toggles the house rule; `ally_kind` lets a test place an incapacitated ally."""
    cid = server.create_campaign("flank")["id"]
    a = server.create_character(cid, "Fighter", kind="player", max_hp=30, armor_class=14)["id"]
    t = server.create_character(cid, "Ogre", kind="monster", max_hp=40, armor_class=14)["id"]
    ally = server.create_character(cid, "Ally", kind=ally_kind, max_hp=30, armor_class=14)["id"]
    server.start_combat(cid, [a, t, ally])
    if flanking:
        server.set_house_rules(cid, {"flanking_advantage": True})
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, a, 4, 5)
    server.place_combatant_at_coords(cid, t, 5, 5)
    server.place_combatant_at_coords(cid, ally, 6, 5)
    return cid, a, t, ally


def test_flanking_grants_advantage_opposite_sides():
    cid, a, t, ally = _flank_fight(flanking=True)
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d8",
    )
    assert res["advantage"] is True
    assert res["attack_roll"].get("flanking_advantage") is True


def test_same_side_ally_does_not_grant_advantage():
    cid, a, t, ally = _flank_fight(flanking=True)
    # Move the ally to the SAME side as the attacker (also left of the target).
    server.place_combatant_at_coords(cid, ally, 4, 6)  # left/below, not across
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d8",
    )
    assert res["attack_roll"].get("flanking_advantage") is None
    # No other advantage source in this fixture, so the roll is not advantaged by flanking.
    assert res["advantage"] is False


def test_flanking_disabled_house_rule_is_byte_identical():
    cid, a, t, ally = _flank_fight(flanking=False)  # rule OFF (default)
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d8",
    )
    # Same opposite-side geometry, but the rule is off => no advantage, no payload key.
    assert res["advantage"] is False
    assert res["attack_roll"].get("flanking_advantage") is None


def test_incapacitated_ally_does_not_flank():
    cid, a, t, ally = _flank_fight(flanking=True)
    server.add_condition(cid, character_id=ally, condition="incapacitated")
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d8",
    )
    # An incapacitated ally is not a threatening flanker (SRD-consistent).
    assert res["attack_roll"].get("flanking_advantage") is None
    assert res["advantage"] is False


def test_flanking_advantage_and_disadvantage_cancel():
    # Flanking gives advantage; pass an explicit disadvantage in the same attack. Standard
    # 5e adv/dis folding: one of each => a straight roll (neither adv nor dis on the die).
    cid, a, t, ally = _flank_fight(flanking=True)
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d8", disadvantage=True,
    )
    # The flanking source still fires (surfaced), but the die roll is normal (adv & dis both
    # set => dice_mod rolls straight). The result flags reflect both inputs were present.
    assert res["attack_roll"].get("flanking_advantage") is True
    assert res["advantage"] is True
    assert res["disadvantage"] is True
    # Straight roll: the recorded natural die equals a single d20 (no adv/dis pick pair).
    assert 1 <= res["attack_roll"]["natural"] <= 20


def test_ranged_attack_never_flanks():
    # Flanking is a MELEE-only rule; a ranged attack from a flanking position doesn't get it.
    cid, a, t, ally = _flank_fight(flanking=True)
    res = server.attack(
        campaign_id=cid, attacker_id=a, target_id=t,
        attack_bonus=3, damage_dice="1d6", is_ranged=True,
    )
    assert res["attack_roll"].get("flanking_advantage") is None
