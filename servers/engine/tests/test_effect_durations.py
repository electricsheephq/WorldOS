"""Engine-tracked spell/effect durations (timed effects auto-expire instead of
relying on the DM to remember). Covers the ActiveEffect lifecycle: parsed from the
spell `duration`, set on cast, decremented per combat round and reported, expired by
the out-of-combat clock tools, and kept consistent with concentration (one source of
truth — when concentration breaks, the effect ends, and vice-versa)."""

import json

import pytest

import combat
import spells
import server
import store
from models import ActiveEffect, Campaign, Character


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def _cleric(cid, name="Pious", **abil):
    ab = {"wisdom": 16, "constitution": 12}
    ab.update(abil)
    return server.create_character(
        cid, name, kind="player", class_name="Cleric",
        apply_srd_defaults=True, abilities=ab,
    )["id"]


def _effects(cid, char_id):
    return server.get_character(cid, char_id)["active_effects"]


def _load(cid, char_id) -> Character:
    """The real persisted Character object (for pure combat.* calls) — get_character augments
    the dict with non-model view keys, which a strict Character would reject."""
    return store.load_campaign(cid).characters[char_id]


def _advance_turn(cid):
    """Pass the current PC/companion (if any) then call next_turn.
    Required after #160 enforcement: a PC who can act must act or pass before next_turn."""
    cur = server.get_state(cid).get("current_turn")
    if cur:
        ch = server.get_character(cid, cur)
        if ch.get("kind") in ("player", "companion") and ch.get("current_hp", 1) > 0:
            server.use_action(cid, cur, "skip")
    return server.next_turn(cid)


# --- duration parsing (pure) -------------------------------------------------
@pytest.mark.parametrize(
    "text,scale,rounds",
    [
        ("1 minute", "minutes", 10),              # srd524 singular
        ("10 minute", "minutes", 100),
        ("Concentration, up to 1 minute", "minutes", 10),   # curated prose
        ("Concentration, up to 10 minutes", "minutes", 100),
        ("1 round", "rounds", 1),
        ("6 round", "rounds", 6),
    ],
)
def test_parse_duration_rounds_and_minutes(text, scale, rounds):
    d = spells.parse_duration(text)
    assert d["scale"] == scale and d["rounds"] == rounds


@pytest.mark.parametrize(
    "text,scale,mag",
    [("1 hour", "hours", 1), ("8 hour", "hours", 8), ("8 hours", "hours", 8),
     ("24 hour", "hours", 24), ("10 day", "days", 10)],
)
def test_parse_duration_clock_scales(text, scale, mag):
    d = spells.parse_duration(text)
    assert d["scale"] == scale
    assert (d["hours"] if scale == "hours" else d["days"]) == mag


@pytest.mark.parametrize("text", ["Instantaneous", "instantaneous", "until dispelled",
                                  "special", "", None, "permanent"])
def test_parse_duration_untimed_is_none(text):
    assert spells.parse_duration(text) is None


# --- additive contract -------------------------------------------------------
def test_old_snapshot_roundtrips_unchanged():
    """A character snapshot with NO active_effects key loads with [] (today's
    behavior); a typo'd field still raises (extra='forbid' preserved)."""
    old = {
        "id": "camp_x", "title": "Old",
        "characters": {"c1": {"id": "c1", "name": "Hero", "kind": "player",
                              "concentration": "Bless"}},
    }
    c = Campaign.model_validate_json(json.dumps(old))
    assert c.characters["c1"].active_effects == []
    assert c.characters["c1"].concentration == "Bless"  # untouched
    with pytest.raises(Exception):
        Character.model_validate({"name": "X", "active_effectz": []})


def test_empty_active_effects_is_default():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    assert _effects(cid, c) == []  # nothing cast -> no effects


# --- set on cast -------------------------------------------------------------
def test_cast_bless_registers_round_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    out = server.cast_spell(cid, c, "Bless")
    ae = out["active_effect"]
    assert ae["name"] == "Bless" and ae["scale"] == "minutes"
    assert ae["rounds_remaining"] == 10  # 1 minute = 10 rounds
    assert ae["concentration"] is True and ae["holder_id"] == c
    eff = _effects(cid, c)
    assert len(eff) == 1 and eff[0]["name"] == "Bless" and eff[0]["source_id"] == c


def test_instantaneous_spell_registers_no_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    out = server.cast_spell(cid, c, "Cure Wounds")  # Instantaneous
    assert "active_effect" not in out
    assert _effects(cid, c) == []


def test_buff_with_target_tracks_on_target():
    """A non-concentration buff with an explicit target is held on the TARGET."""
    cid = server.create_campaign("S")["id"]
    caster = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16},
    )["id"]
    server.learn_spells(cid, caster, ["Mage Armor"])
    server.prepare_spells(cid, caster, ["Mage Armor"])
    ally = _cleric(cid, "Ally")
    out = server.cast_spell(cid, caster, "Mage Armor", target_id=ally)
    assert out["active_effect"]["holder_id"] == ally
    assert _effects(cid, caster) == []  # caster carries nothing
    assert [e["name"] for e in _effects(cid, ally)] == ["Mage Armor"]
    assert _effects(cid, ally)[0]["source_id"] == caster  # source recorded


# --- attack-roll spell ON-HIT riders defer until the spell attack hits (#186) ----
# Guiding Bolt grants its "next attack has Advantage" rider ONLY on a hit. cast_spell
# must NOT write that marker to the target at cast time (a miss would leave a phantom
# free-advantage marker, and a re-cast would stack a second one). Instead it records a
# PENDING rider on the caster; the next attack() materializes it on a HIT and discards
# it on a MISS. Save spells and self/ally buffs are unaffected (the regressions below).

import dice as dice_mod  # noqa: E402  (deterministic hit/miss for the attack() resolution)
from dice import DiceRoll  # noqa: E402


def _d20_roll(natural: int):
    """A dice.roll stub that forces the d20 NATURAL face (so a spell attack is a
    deterministic hit/miss) while honoring the expression's flat modifier; non-d20
    expressions (damage) roll a fixed mid value. Used via monkeypatch on the attack."""
    def _roll(expression: str, advantage: bool = False, disadvantage: bool = False, seed=None) -> DiceRoll:
        if expression.startswith("1d20"):
            mod = 0
            if "+" in expression:
                mod = int(expression.split("+", 1)[1])
            elif "-" in expression.split("d", 1)[1]:
                mod = -int(expression.rsplit("-", 1)[1])
            return DiceRoll(
                expression=expression, total=natural + mod, rolls=[natural], modifier=mod,
                detail=f"{expression}[{natural}] = {natural + mod}", is_d20=True,
                natural=natural, crit=(natural == 20), fumble=(natural == 1),
            )
        return DiceRoll(expression=expression, total=6, rolls=[3], detail=f"{expression}[3] = 6")
    return _roll


def _gb_cleric(cid, name="Pious"):
    c = _cleric(cid, name)
    server.learn_spells(cid, c, ["Guiding Bolt"])
    server.prepare_spells(cid, c, ["Guiding Bolt"])
    return c


def _advance_to(cid, who):
    """Pass non-`who` combatants (PCs must act/pass before next_turn, #160) until it
    is `who`'s turn so an action attack by `who` is legal."""
    for _ in range(12):
        cur = server.get_state(cid).get("current_turn")
        if cur == who:
            return
        ch = server.get_character(cid, cur)
        if ch.get("kind") in ("player", "companion") and ch.get("current_hp", 1) > 0:
            server.use_action(cid, cur, "skip")
        server.next_turn(cid)
    raise AssertionError(f"never reached {who}'s turn")


def test_guiding_bolt_cast_defers_rider_does_not_touch_target():
    """cast(Guiding Bolt at foe) must NOT write the GB marker to the target — it returns
    a `pending_effect` (not `active_effect`) and records a pending rider on the caster."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=12)["id"]
    out = server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    # NOT applied to the target yet — surfaced as a pending on-hit effect instead.
    assert "active_effect" not in out
    assert out["pending_effect"]["name"] == "Guiding Bolt"
    assert out["pending_effect"]["target_id"] == foe and out["pending_effect"]["on_hit"] is True
    assert _effects(cid, foe) == []  # the target carries NOTHING at cast time
    # The pending rider lives on the CASTER, keyed to the target.
    riders = server.get_character(cid, cleric)["pending_on_hit_riders"]
    assert [r["name"] for r in riders] == ["Guiding Bolt"]
    assert riders[0]["target_id"] == foe and riders[0]["source_id"] == cleric


def test_guiding_bolt_miss_does_not_apply_rider(monkeypatch):
    """THE BUG (#186): cast(Guiding Bolt) + attack(MISS) -> the target has NO GB marker,
    and the pending rider is discarded (no free advantage on a missed bolt)."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=18)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    server.start_combat(cid, [cleric, foe])
    _advance_to(cid, cleric)
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(2))  # natural 2, +bonus -> misses AC 18
    res = server.attack(cid, cleric, foe, attack_bonus=5, damage_dice="4d6",
                        damage_type="radiant", is_ranged=True)
    assert res["hit"] is False
    assert res.get("on_hit_effect_discarded") == ["Guiding Bolt"]
    assert "on_hit_effect_applied" not in res
    assert _effects(cid, foe) == []  # <-- the fix: NO marker on a miss
    assert server.get_character(cid, cleric)["pending_on_hit_riders"] == []  # rider consumed


def test_guiding_bolt_hit_applies_rider(monkeypatch):
    """cast(Guiding Bolt) + attack(HIT) -> the GB marker IS written to the target (a
    round-scale effect, sourced from the caster), and the pending rider is consumed."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=10)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    server.start_combat(cid, [cleric, foe])
    _advance_to(cid, cleric)
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))  # natural 15, +5 -> hits AC 10
    res = server.attack(cid, cleric, foe, attack_bonus=5, damage_dice="4d6",
                        damage_type="radiant", is_ranged=True)
    assert res["hit"] is True
    assert res.get("on_hit_effect_applied") == ["Guiding Bolt"]
    assert "on_hit_effect_discarded" not in res
    eff = _effects(cid, foe)
    assert [e["name"] for e in eff] == ["Guiding Bolt"]  # <-- marker written on a hit
    assert eff[0]["scale"] == "rounds" and eff[0]["source_id"] == cleric
    assert server.get_character(cid, cleric)["pending_on_hit_riders"] == []  # rider consumed


def test_guiding_bolt_recast_does_not_phantom_stack_pending():
    """Re-casting Guiding Bolt at the same target replaces the pending rider (one record),
    so a second cast can't leave a phantom marker (the re-cast half of the bug)."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    for _ in range(2):  # a level-3 cleric has two 1st-level slots to spend
        server.level_up(cid, cleric, "Cleric")
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30, armor_class=12)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)  # re-cast
    riders = server.get_character(cid, cleric)["pending_on_hit_riders"]
    assert len(riders) == 1 and riders[0]["target_id"] == foe  # single rider, no phantom
    assert _effects(cid, foe) == []  # still nothing on the target


def test_guiding_bolt_materialized_rider_auto_expires(monkeypatch):
    """Once a hit materializes the GB rider on the target, it auto-expires like any other
    timed effect — its 1-round duration ends a round later via next_turn."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=60, armor_class=10)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    server.start_combat(cid, [cleric, foe])
    _advance_to(cid, cleric)
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))  # hit -> GB lands on foe
    server.attack(cid, cleric, foe, attack_bonus=5, damage_dice="4d6", is_ranged=True)
    assert [e["name"] for e in _effects(cid, foe)] == ["Guiding Bolt"]
    # (the d20 stub stays patched; next_turn / use_action(skip) roll no attack, so the
    # turn advance below is unaffected — don't monkeypatch.undo(), which would also revert
    # the autouse CLAWDND_STATE_DIR fixture and orphan the campaign.)
    expired = None
    for _ in range(6):
        v = _advance_turn(cid)
        if v["expired_effects"]:
            expired = v["expired_effects"]
            break
    assert expired == [{"character_id": foe, "name": "Guiding Bolt"}]
    assert _effects(cid, foe) == []


# --- the materialized GB marker auto-grants + is consumed by the NEXT attack (#194) ----
# #186/#188 fixed the on-HIT registration; #194 is the OTHER half — the marker on the
# target must make the next attack against it carry advantage WITHOUT the DM passing
# advantage=True, and be consumed so it benefits exactly one attack.


def _fighter(cid, name="Brawn", ac=10):
    return server.create_character(
        cid, name, kind="player", class_name="Fighter", apply_srd_defaults=True,
        armor_class=ac, abilities={"strength": 16, "constitution": 14},
    )["id"]


def test_guiding_bolt_marker_auto_grants_advantage_to_next_attack_and_is_consumed(monkeypatch):
    """cast(GB)+hit lands the marker on the foe; the NEXT attack against that foe (by a
    DIFFERENT combatant, who passes NO advantage flag) auto-carries advantage=True, names
    its source, and CONSUMES the marker — so it benefits exactly one attack (#194)."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    fighter = _fighter(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=80, armor_class=10)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    # The fixed-natural-15 stub also makes initiative tie -> input order [cleric, fighter, foe],
    # so the cleric acts first and the fighter follows in the SAME round (marker still live).
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))
    server.start_combat(cid, [cleric, fighter, foe])
    _advance_to(cid, cleric)
    # Cleric's GB attack hits -> the "next attack has advantage" marker lands on the foe.
    gb = server.attack(cid, cleric, foe, attack_bonus=5, damage_dice="4d6",
                       damage_type="radiant", is_ranged=True)
    assert gb["hit"] is True and gb.get("on_hit_effect_applied") == ["Guiding Bolt"]
    # The GB spell attack itself must NOT have consumed any advantage (none pre-existed).
    assert "advantage_source" not in gb
    eff = _effects(cid, foe)
    assert [e["name"] for e in eff] == ["Guiding Bolt"] and eff[0]["grants_advantage"] is True
    # Advance to the fighter (still round 1) and attack the SAME foe with NO advantage flag.
    server.next_turn(cid)
    assert server.get_state(cid)["current_turn"] == fighter
    res = server.attack(cid, fighter, foe, attack_bonus=4, damage_dice="1d8+3",
                        damage_type="slashing")  # DM passes NO advantage=True
    assert res["advantage"] is True  # <-- the fix: engine auto-granted it
    assert res["disadvantage"] is False
    assert res["advantage_source"] == "Guiding Bolt" and res["advantage_consumed"] is True
    # The marker is consumed — gone from the foe, so it benefits exactly ONE attack. Because
    # attack_modifiers derives the auto-advantage purely from this (now empty) effect list, a
    # SECOND attack against the foe necessarily gets NO advantage from the marker.
    assert _effects(cid, foe) == []
    adv2, dis2 = combat.attack_modifiers(_load(cid, fighter), _load(cid, foe))
    assert adv2 is False and dis2 is False


def test_guiding_bolt_marker_not_consumed_for_attack_on_other_target(monkeypatch):
    """The marker is the FOE's; an attack against a DIFFERENT (unmarked) target neither gets
    advantage from it nor consumes it — the marker stays live for the marked foe."""
    cid = server.create_campaign("S")["id"]
    cleric = _gb_cleric(cid)
    fighter = _fighter(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=80, armor_class=10)["id"]
    other = server.create_character(cid, "Rat", kind="monster", max_hp=80, armor_class=10)["id"]
    server.cast_spell(cid, cleric, "Guiding Bolt", target_id=foe)
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))
    server.start_combat(cid, [cleric, fighter, foe, other])
    _advance_to(cid, cleric)
    server.attack(cid, cleric, foe, attack_bonus=5, damage_dice="4d6", is_ranged=True)  # marker on foe
    server.next_turn(cid)
    assert server.get_state(cid)["current_turn"] == fighter
    # Fighter strikes the UNMARKED 'other' -> no auto-advantage, marker untouched.
    res = server.attack(cid, fighter, other, attack_bonus=4, damage_dice="1d8+3")
    assert res["advantage"] is False
    assert "advantage_source" not in res and "advantage_consumed" not in res
    assert [e["name"] for e in _effects(cid, foe)] == ["Guiding Bolt"]  # still live on the foe
    assert _effects(cid, other) == []


def test_attack_on_unmarked_target_has_no_advantage_machinery(monkeypatch):
    """A plain weapon attack against a target carrying NO advantage rider is byte-identical
    to before — no advantage, no advantage_source/consumed keys (non-marked unaffected)."""
    cid = server.create_campaign("S")["id"]
    fighter = _fighter(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=80, armor_class=10)["id"]
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))
    server.start_combat(cid, [fighter, foe])
    _advance_to(cid, fighter)
    res = server.attack(cid, fighter, foe, attack_bonus=4, damage_dice="1d8+3")
    assert res["advantage"] is False and res["disadvantage"] is False
    assert "advantage_source" not in res and "advantage_consumed" not in res


def test_condition_advantage_still_works_with_no_marker(monkeypatch):
    """REGRESSION: condition-derived advantage (a PRONE target, melee) is unchanged by the
    #194 marker path — it still grants advantage with no marker present and no source key."""
    cid = server.create_campaign("S")["id"]
    fighter = _fighter(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=80, armor_class=10)["id"]
    monkeypatch.setattr(server.dice_mod, "roll", _d20_roll(15))
    server.start_combat(cid, [fighter, foe])
    _advance_to(cid, fighter)
    server.add_condition(cid, foe, "prone")  # melee attacker vs prone -> advantage
    res = server.attack(cid, fighter, foe, attack_bonus=4, damage_dice="1d8+3")  # melee
    assert res["advantage"] is True  # condition advantage intact
    assert "advantage_source" not in res  # NOT from a marker
    assert _effects(cid, foe) == []  # no spurious marker created/consumed


def test_self_buff_attack_spell_not_deferred_unchanged():
    """REGRESSION: a self/ally buff still writes its effect at cast even if the SRD record
    happens to carry attack_roll (Mirror Image is a self-buff). The defer is scoped to a
    SEPARATE target, so a no-target / self-target cast is byte-identical to before."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.level_up(cid, w, "Wizard")
    server.level_up(cid, w, "Wizard")  # level 3 -> a 2nd-level slot for the level-2 spell
    server.learn_spells(cid, w, ["Mirror Image"])
    server.prepare_spells(cid, w, ["Mirror Image"])
    out = server.cast_spell(cid, w, "Mirror Image")  # no target -> holder is the caster
    assert "pending_effect" not in out
    assert out["active_effect"]["holder_id"] == w
    assert [e["name"] for e in _effects(cid, w)] == ["Mirror Image"]  # applied at cast, as before


# --- decrement + auto-expire in combat ---------------------------------------
def test_next_turn_decrements_and_expires_bless():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")  # 10 rounds
    server.start_combat(cid, [c, foe])  # round 1
    expired = None
    last_round = 0
    # Two combatants -> 2 next_turns per round. Bless ticks once per round; the 10th
    # decrement (start of round 11) expires it. Use _advance_turn to pass PCs first (#160).
    for _ in range(30):
        v = _advance_turn(cid)
        last_round = v["round"]
        if v["expired_effects"]:
            expired = v
            break
    assert expired is not None, "Bless never expired"
    assert expired["round"] == 11  # cast at round 1, 10 rounds later
    assert expired["expired_effects"] == [{"character_id": c, "name": "Bless"}]
    assert _effects(cid, c) == []
    # Concentration cleared too when its effect timed out (one source of truth).
    assert server.get_character(cid, c)["concentration"] is None


def test_round_decrement_is_per_round_not_per_turn():
    """A single turn (no round wrap) does not decrement a round effect."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")
    server.start_combat(cid, [c, foe])
    before = _effects(cid, c)[0]["rounds_remaining"]
    _advance_turn(cid)  # advance one turn (1 of 2) — no round wrap yet; pass PC if needed (#160)
    assert _effects(cid, c)[0]["rounds_remaining"] == before  # unchanged mid-round
    _advance_turn(cid)  # wrap -> round 2 -> decrement
    assert _effects(cid, c)[0]["rounds_remaining"] == before - 1


# --- concentration linkage ---------------------------------------------------
def test_concentration_break_on_failed_save_expires_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    assert len(_effects(cid, c)) == 1
    res = server.concentration_save(cid, c, 999)  # forced fail
    assert res["maintained"] is False
    assert res["expired_effects"] == ["Bless"]
    assert _effects(cid, c) == []
    assert server.get_character(cid, c)["concentration"] is None


def test_concentration_break_on_incapacitation_expires_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    server.add_condition(cid, c, "stunned")  # incapacitating -> breaks concentration
    assert server.get_character(cid, c)["concentration"] is None
    assert _effects(cid, c) == []


def test_concentration_break_on_drop_to_zero_hp_expires_effect():
    """Pure combat layer: dropping to 0 HP ends concentration AND its effect."""
    ch = Character(name="Caster", kind="player", max_hp=20, current_hp=20,
                   concentration="Bless",
                   active_effects=[ActiveEffect(name="Bless", concentration=True,
                                                scale="minutes", rounds_remaining=10)])
    combat.apply_damage(ch, 25)  # to 0 HP
    assert ch.concentration is None and ch.active_effects == []


def test_replacing_concentration_drops_prior_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    server.cast_spell(cid, c, "Shield of Faith")  # 2nd concentration spell
    eff = _effects(cid, c)
    assert [e["name"] for e in eff] == ["Shield of Faith"]  # Bless effect dropped
    assert server.get_character(cid, c)["concentration"] == "Shield of Faith"


def test_recasting_same_spell_refreshes_not_stacks():
    """Recasting a spell on the same holder refreshes its duration (one effect, full
    timer), it does not accumulate duplicates."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")
    server.start_combat(cid, [c, foe])
    _advance_turn(cid)       # pass PC if needed, then advance (#160)
    _advance_turn(cid)  # round 2 -> Bless 10 -> 9
    assert _effects(cid, c)[0]["rounds_remaining"] == 9
    server.cast_spell(cid, c, "Bless")  # recast refreshes
    eff = _effects(cid, c)
    assert len(eff) == 1 and eff[0]["rounds_remaining"] == 10  # single, full timer


# --- out-of-combat clock expiry ----------------------------------------------
def test_advance_time_expires_minute_scale():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")  # minute-scale
    out = server.advance_time(cid, phases=1)  # any phase advance ends sub-hour effects
    assert out["expired_effects"] == [{"character_id": c, "name": "Bless"}]
    assert _effects(cid, c) == []


def test_advance_time_no_movement_keeps_effects():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    out = server.advance_time(cid, phases=0)  # clock didn't move
    assert out["expired_effects"] == []
    assert len(_effects(cid, c)) == 1


# --- C1: advance_time must NOT nuke combat buffs/concentration mid-combat -----
def test_advance_time_during_combat_does_not_expire_buffs_or_break_concentration():
    """C1 regression: combat runs in ROUNDS (next_turn), not world phases. advance_time
    mid-combat must be a no-op for the clock AND for effect expiry — Bless + concentration
    SURVIVE. (The harness soft-tick fires advance_time(phases=1) on every frozen beat; without
    this guard it stripped every round-scale buff and dropped concentration each beat.)"""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")  # rounds/minute-scale + concentration
    server.start_combat(cid, [c, foe])
    st = server.get_state(cid)
    day_before, tod_before = (st["day"], st["time_of_day"])

    out = server.advance_time(cid, phases=1)  # would expire Bless + drop concentration if unguarded

    assert out["phases_advanced"] == 0
    assert out["expired_effects"] == []
    assert "combat" in out["note"].lower()
    # Clock did NOT move.
    assert out["day"] == day_before and out["time_of_day"] == tod_before
    # Buff + concentration SURVIVE the mid-combat call.
    assert [e["name"] for e in _effects(cid, c)] == ["Bless"]
    assert server.get_character(cid, c)["concentration"] == "Bless"
    # Even a target-phase jump (`to=`) is refused while combat is active.
    out2 = server.advance_time(cid, to="evening")
    assert out2["phases_advanced"] == 0 and out2["expired_effects"] == []


def test_advance_time_out_of_combat_still_expires_after_end_combat():
    """The C1 guard is combat-scoped: once combat ends, advance_time expires sub-hour effects
    exactly as before (the out-of-combat path is unchanged)."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")
    server.start_combat(cid, [c, foe])
    server.advance_time(cid, phases=1)  # no-op while in combat
    assert [e["name"] for e in _effects(cid, c)] == ["Bless"]  # untouched
    server.end_combat(cid)
    out = server.advance_time(cid, phases=1)  # now out of combat -> expires
    assert out["expired_effects"] == [{"character_id": c, "name": "Bless"}]
    assert _effects(cid, c) == []


# --- H1: set_hp(0) clears concentration + effect + downs; healing wakes -------
def test_set_hp_zero_clears_concentration_and_downs_then_heal_wakes():
    """H1 regression: set_hp(…, 0) must match the combat path — clear concentration AND its
    engine-tracked effect, and apply the unconscious/dying transition (not just clamp HP).
    Raising HP from 0 wakes the character (mirrors apply_healing)."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")  # concentration + a tracked effect
    assert server.get_character(cid, c)["concentration"] == "Bless"

    sheet = server.set_hp(cid, c, 0)
    assert sheet["current_hp"] == 0
    assert sheet["concentration"] is None                  # concentration cleared
    assert sheet["active_effects"] == []                   # ...and its twin effect removed
    assert "unconscious" in sheet["conditions"]            # downed
    assert sheet["stable"] is False and sheet["dead"] is False
    assert sheet["death_saves"]["failures"] == 0           # fresh death saves

    # Heal back up -> wake.
    sheet2 = server.set_hp(cid, c, 7)
    assert sheet2["current_hp"] == 7
    assert "unconscious" not in sheet2["conditions"]
    assert sheet2["stable"] is False


def test_set_hp_negative_clamps_to_zero_and_still_downs():
    """A negative input clamps to 0 (validator) AND still triggers the downed transition —
    the transition runs on the CLAMPED value, not the raw input."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    sheet = server.set_hp(cid, c, -5)
    assert sheet["current_hp"] == 0 and "unconscious" in sheet["conditions"]


def test_mage_armor_survives_combat_but_expires_on_long_rest():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16, "constitution": 12},
    )["id"]
    server.learn_spells(cid, w, ["Mage Armor"])
    server.prepare_spells(cid, w, ["Mage Armor"])
    out = server.cast_spell(cid, w, "Mage Armor")  # 8h, non-concentration
    assert out["active_effect"]["scale"] == "hours"
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=20)["id"]
    server.start_combat(cid, [w, foe])
    for _ in range(12):  # several rounds of combat; pass PCs before advancing (#160)
        _advance_turn(cid)
    assert [e["name"] for e in _effects(cid, w)] == ["Mage Armor"]  # still up
    server.end_combat(cid)
    lr = server.long_rest(cid, w)  # overnight ends the 8h buff
    assert lr["expired_effects"] == [{"character_id": w, "name": "Mage Armor"}]
    assert _effects(cid, w) == []


def test_short_rest_expires_sub_hour_but_keeps_mage_armor():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16, "constitution": 14},
    )["id"]
    for s in ("Mage Armor", "Bless"):
        server.learn_spells(cid, w, [s])
    server.prepare_spells(cid, w, ["Mage Armor", "Bless"])
    server.cast_spell(cid, w, "Mage Armor")  # 8h
    server.cast_spell(cid, w, "Bless")       # 1 minute (sub-hour)
    out = server.short_rest(cid, w)
    names_expired = sorted(e["name"] for e in out["expired_effects"])
    assert names_expired == ["Bless"]  # sub-hour gone, Mage Armor survives
    assert [e["name"] for e in _effects(cid, w)] == ["Mage Armor"]


# --- M1: downtime expires timed effects like every sibling time-seam ---------
def test_downtime_expires_hours_scale_effect():
    """M1 regression: a multi-day downtime jumps the clock forward, so it must expire timed
    effects (like advance_time/travel_to/long_rest/short_rest) — it was the only seam omitting
    it. An 8h Mage Armor does not survive days of downtime."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16, "constitution": 12},
    )["id"]
    server.learn_spells(cid, w, ["Mage Armor"])
    server.prepare_spells(cid, w, ["Mage Armor"])
    server.cast_spell(cid, w, "Mage Armor")  # 8h, hour-scale
    assert [e["name"] for e in _effects(cid, w)] == ["Mage Armor"]
    out = server.downtime(cid, 3)  # three days pass
    assert out["expired_effects"] == [{"character_id": w, "name": "Mage Armor"}]
    assert _effects(cid, w) == []


def test_downtime_zero_days_is_noop_for_effects():
    """downtime(0) doesn't move the clock, so nothing expires (guarded on elapsed > 0)."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    out = server.downtime(cid, 0)
    assert out["expired_effects"] == []
    assert len(_effects(cid, c)) == 1


# --- pure helper edges -------------------------------------------------------
def test_tick_round_effects_only_touches_round_minute_scale():
    ch = Character(name="T", active_effects=[
        ActiveEffect(name="Bless", scale="minutes", rounds_remaining=2),
        ActiveEffect(name="Mage Armor", scale="hours", expires_day=5,
                     expires_phase_index=0, until_long_rest=True),
    ])
    assert combat.tick_round_effects(ch) == []  # Bless 2->1, nothing expires
    assert combat.tick_round_effects(ch) == ["Bless"]  # 1->0 expires
    assert [e.name for e in ch.active_effects] == ["Mage Armor"]  # hour untouched


def test_expire_clock_effects_deadline_and_long_rest():
    ch = Character(name="T", active_effects=[
        ActiveEffect(name="Hex", scale="hours", expires_day=1, expires_phase_index=2),
        ActiveEffect(name="Mage Armor", scale="hours", expires_day=9,
                     expires_phase_index=0, until_long_rest=True),
    ])
    # clock at day 1, phase 1: Hex's deadline (1,2) not yet reached
    assert combat.expire_clock_effects(ch, 1, 1) == []
    # advance to day 1 phase 2: Hex deadline reached
    assert combat.expire_clock_effects(ch, 1, 2) == ["Hex"]
    # Mage Armor's deadline is far off, but a long rest ends it
    assert combat.expire_clock_effects(ch, 1, 3, long_rest=True) == ["Mage Armor"]
    assert ch.active_effects == []
