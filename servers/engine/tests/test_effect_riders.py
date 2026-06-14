"""Engine-tracked buffs must have mechanical teeth (audit SYN-06 / #780, merges F01-6 + F03-1).

Before this fix the engine advertised Bless / Bane / Shield of Faith / Shield in
`active_effects`, ticked their durations — then authoritatively rolled WITHOUT them:
ActiveEffect had no generic modifier fields at all (only the Mage Armor and
Guiding-Bolt special cases). Worse, a concentration buff lived caster-side only, so
Bless on an ally had no target-side record and could never expire with the caster's
concentration (both sweep loops matched only repeat-save markers).

The fix (per the audited spec):
  * additive ActiveEffect fields ac_bonus / attack_bonus_dice / save_bonus_dice /
    linked_to_concentration (defaults == today; old snapshots round-trip);
  * a curated <=4-spell rider registry (Bless, Bane, Shield of Faith, Shield)
    mirroring _ADVANTAGE_GRANTING_SPELLS;
  * _effective_armor_class sums ac_bonus; attack / saving_throw / concentration_save /
    the next_turn repeat save fold the bonus dice — the ENGINE rolls the d4 and
    surfaces it in the roll detail;
  * concentration children are released by BOTH sweep paths (next_turn's inverse
    sweep and drop_concentration).
"""

import pytest

from dice import DiceRoll
from models import ActiveEffect, Character
import combat
import server


def _rig(monkeypatch, d20_natural: int = 10, d4: int = 3):
    """Deterministic dice: every 1d20 rolls `d20_natural` (no modifier), every
    1d4 rolls `d4`; everything else delegates to the real roller."""
    _orig = server.dice_mod.roll

    def _rigged(expression, **kwargs):
        if expression.startswith("1d20"):
            return DiceRoll(expression=expression, total=d20_natural, rolls=[d20_natural],
                            detail=f"{expression}[{d20_natural}] = {d20_natural}",
                            is_d20=True, natural=d20_natural)
        if expression.startswith("1d4"):
            return DiceRoll(expression=expression, total=d4, rolls=[d4],
                            detail=f"{expression}[{d4}] = {d4}")
        return _orig(expression, **kwargs)

    monkeypatch.setattr(server.dice_mod, "roll", _rigged)


# --- additive model fields: old snapshots round-trip --------------------------

def test_active_effect_old_snapshot_round_trips():
    eff = ActiveEffect.model_validate({"name": "Bless", "scale": "minutes",
                                       "rounds_remaining": 10})
    assert eff.ac_bonus == 0
    assert eff.attack_bonus_dice == ""
    assert eff.save_bonus_dice == ""
    assert eff.linked_to_concentration is False
    ch = Character.model_validate({
        "name": "Old", "max_hp": 10, "current_hp": 10,
        "active_effects": [{"name": "Mage Armor", "scale": "hours",
                            "until_long_rest": True}],
    })
    assert ch.active_effects[0].ac_bonus == 0


def test_rider_registry_curates_exactly_the_four_spells():
    assert combat.spell_effect_riders("Bless") == {
        "attack_bonus_dice": "1d4", "save_bonus_dice": "1d4"}
    assert combat.spell_effect_riders("Bane") == {
        "attack_bonus_dice": "-1d4", "save_bonus_dice": "-1d4"}
    assert combat.spell_effect_riders("Shield of Faith") == {"ac_bonus": 2}
    assert combat.spell_effect_riders("Shield") == {"ac_bonus": 5}
    assert combat.spell_effect_riders("Fireball") is None


# --- Shield of Faith: +2 AC via the effective-AC path -------------------------

def test_shield_of_faith_raises_target_effective_ac(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("SoF")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Ward", kind="player", max_hp=30,
                                   armor_class=14)["id"]
    foe = server.create_character(cid, "Thug", kind="monster", max_hp=20)["id"]
    server.cast_spell(cid, cleric, "Shield of Faith", target_id=ally)
    # the ally carries a concentration-linked child effect with the +2
    sheet = server.get_character(cid, ally)
    effs = sheet["active_effects"]
    assert len(effs) == 1 and effs[0]["name"] == "Shield of Faith"
    assert effs[0]["ac_bonus"] == 2
    assert effs[0]["linked_to_concentration"] is True
    # ...and the attack resolver honors it: effective AC is base + 2
    r = server.attack(cid, attacker_id=foe, target_id=ally,
                      attack_bonus=0, damage_dice="1d4")
    assert r["target_ac"] == 16
    assert r["target_base_ac"] == 14
    assert {"source": "Shield of Faith", "bonus": 2} in r["target_ac_detail"]["ac_bonuses"]


def test_shield_reaction_self_buff_adds_5_ac(tmp_path, monkeypatch):
    # Shield (1 round, self) — non-concentration, so the rider lands on the caster's
    # own tracked effect and the effective-AC path sums it.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shield")["id"]
    wiz = server.create_character(cid, "Mim", kind="player", class_name="Wizard",
                                  level=1, apply_srd_defaults=True, max_hp=20)["id"]
    foe = server.create_character(cid, "Thug", kind="monster", max_hp=20)["id"]
    base_ac = server.get_character(cid, wiz)["armor_class"]
    server.cast_spell(cid, wiz, "Shield")
    r = server.attack(cid, attacker_id=foe, target_id=wiz,
                      attack_bonus=0, damage_dice="1d4")
    assert r["target_ac"] == base_ac + 5
    assert {"source": "Shield", "bonus": 5} in r["target_ac_detail"]["ac_bonuses"]


# --- Bless / Bane: the engine rolls the d4 and surfaces it --------------------

def test_blessed_attacker_gets_engine_rolled_d4(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BlessAtk")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Sword", kind="player", max_hp=30)["id"]
    foe = server.create_character(cid, "Thug", kind="monster", max_hp=30,
                                  armor_class=12)["id"]
    server.cast_spell(cid, cleric, "Bless", target_id=ally)
    _rig(monkeypatch, d20_natural=10, d4=3)
    r = server.attack(cid, attacker_id=ally, target_id=foe,
                      attack_bonus=0, damage_dice="1d6")
    # d20(10) + 0 + blessed d4(3) = 13 vs AC 12 -> the d4 turned a miss into a hit
    assert r["attack_roll"]["total"] == 13
    assert r["hit"] is True
    bd = r["attack_roll"]["bonus_dice"]
    assert bd == [{"source": "Bless", "dice": "1d4", "rolled": 3,
                   "detail": "1d4[3] = 3"}]


def test_blessed_save_includes_d4(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BlessSave")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Sword", kind="player", max_hp=30)["id"]
    server.cast_spell(cid, cleric, "Bless", target_id=ally)
    _rig(monkeypatch, d20_natural=10, d4=3)
    out = server.saving_throw(cid, ally, "wis", dc=12)
    assert out["roll"] == 13
    assert out["success"] is True
    assert out["bonus_dice"][0]["source"] == "Bless"
    assert out["bonus_dice"][0]["rolled"] == 3


def test_baned_target_save_subtracts_d4(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Bane")["id"]
    caster = server.create_character(cid, "Hex", kind="player", max_hp=10)["id"]
    server.update_character(cid, caster, patch={
        "spell_slots": {"1": {"maximum": 2, "used": 0}}})
    foe = server.create_character(cid, "Thug", kind="monster", max_hp=20)["id"]
    server.cast_spell(cid, caster, "Bane", target_id=foe)
    _rig(monkeypatch, d20_natural=10, d4=3)
    out = server.saving_throw(cid, foe, "wis", dc=10)
    # d20(10) - bane d4(3) = 7 vs DC 10 -> the engine-applied penalty flipped it
    assert out["roll"] == 7
    assert out["success"] is False
    assert out["bonus_dice"][0]["source"] == "Bane"
    assert out["bonus_dice"][0]["rolled"] == -3


def test_blessed_concentration_save_includes_d4(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BlessConc")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Mage", kind="player", max_hp=20)["id"]
    server.cast_spell(cid, cleric, "Bless", target_id=ally)
    _rig(monkeypatch, d20_natural=10, d4=3)
    out = server.concentration_save(cid, ally, dc=12)
    assert out["roll"] == 13
    assert out["maintained"] is True
    assert out["bonus_dice"][0]["source"] == "Bless"


# --- concentration break releases the linked children (BOTH paths) ------------

def test_drop_concentration_frees_linked_children(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DropConc")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Sword", kind="player", max_hp=30)["id"]
    server.cast_spell(cid, cleric, "Bless", target_id=ally)
    assert server.get_character(cid, ally)["active_effects"]  # child present
    out = server.drop_concentration(cid, cleric)
    assert out["ended"] is True
    assert {"character_id": ally, "name": "Bless"} in out["freed_targets"]
    assert server.get_character(cid, ally)["active_effects"] == []


def test_failed_concentration_save_frees_linked_children_immediately(tmp_path, monkeypatch):
    # F3-6: a failed concentration save now releases the blessed ally's linked child in the
    # SAME call (surfaced in freed_targets), not deferred to the next next_turn sweep. The
    # sweep remains a clean no-op backstop afterward.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("SweepConc")["id"]
    caster = server.create_character(cid, "Priest", kind="monster", max_hp=20)["id"]
    server.update_character(cid, caster, patch={
        "spell_slots": {"1": {"maximum": 2, "used": 0}}})
    ally = server.create_character(cid, "Brute", kind="monster", max_hp=20)["id"]
    server.cast_spell(cid, caster, "Bless", target_id=ally)
    server.start_combat(cid, [caster, ally])
    # break the caster's concentration the non-voluntary way (impossible DC)
    cs = server.concentration_save(cid, caster, dc=100)
    assert cs["maintained"] is False
    assert {"character_id": ally, "name": "Bless"} in cs["freed_targets"]
    assert server.get_character(cid, ally)["active_effects"] == []  # freed immediately
    # The next_turn sweep is a no-op backstop now (nothing left to reconcile).
    nt = server.next_turn(cid)
    assert {"character_id": ally, "name": "Bless"} not in nt["expired_effects"]


def test_cast_result_advertises_engine_applied_riders(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Advert")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Sword", kind="player", max_hp=30)["id"]
    r = server.cast_spell(cid, cleric, "Bless", target_id=ally)
    riders = r["effect_riders"]
    assert riders["holder_id"] == ally
    assert riders["attack_bonus_dice"] == "1d4"
    assert riders["save_bonus_dice"] == "1d4"
