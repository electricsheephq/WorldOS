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


def test_drop_concentration_frees_all_aoe_linked_children(tmp_path, monkeypatch):
    # Multi-target Bless via target_ids: dropping concentration must release the linked child on
    # EVERY blessed ally, not just one — the sweep is keyed on the caster's concentration, so all
    # children fall together.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DropConcAoE")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    a = server.create_character(cid, "AllyA", kind="player", max_hp=30)["id"]
    b = server.create_character(cid, "AllyB", kind="player", max_hp=30)["id"]
    server.cast_spell(cid, cleric, "Bless", target_ids=[a, b])
    assert server.get_character(cid, a)["active_effects"] and server.get_character(cid, b)["active_effects"]
    out = server.drop_concentration(cid, cleric)
    assert out["ended"] is True
    freed = {(f["character_id"], f["name"]) for f in out["freed_targets"]}
    assert (a, "Bless") in freed and (b, "Bless") in freed
    assert server.get_character(cid, a)["active_effects"] == []
    assert server.get_character(cid, b)["active_effects"] == []


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


# --- Bless / Bane via target_ids (multi-target / AoE) — the rider must reach EVERY beneficiary ---

def test_bless_via_target_ids_blesses_each_ally_not_the_caster(tmp_path, monkeypatch):
    # Bless cast on an explicit target_ids list (the multi-target path) must give the +1d4 rider to
    # EVERY named ally — not just one, and NOT the caster (who blessed others). The bug: the rider
    # logic only handled the singular target_id, so a target_ids cast landed the rider on the
    # caster-twin and left the named allies with nothing.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BlessAoE")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    a = server.create_character(cid, "AllyA", kind="player", max_hp=30)["id"]
    b = server.create_character(cid, "AllyB", kind="player", max_hp=30)["id"]
    server.cast_spell(cid, cleric, "Bless", target_ids=[a, b])
    for ally_id in (a, b):
        effs = [e for e in server.get_character(cid, ally_id)["active_effects"] if e["name"] == "Bless"]
        assert effs, f"{ally_id} got no Bless rider"
        assert effs[0]["attack_bonus_dice"] == "1d4"
        assert effs[0]["save_bonus_dice"] == "1d4"
        assert effs[0]["linked_to_concentration"] is True
    # the caster holds the concentration twin but carries NO rider numbers (it blessed others, not itself)
    caster_bless = [e for e in server.get_character(cid, cleric)["active_effects"] if e["name"] == "Bless"]
    assert caster_bless, "caster should still hold the concentration twin"
    assert all(e["attack_bonus_dice"] == "" and e["save_bonus_dice"] == "" for e in caster_bless), \
        "caster wrongly received the Bless rider when it blessed only others"
    # and the engine actually rolls the d4 on each ally's attack (not just one of them)
    _rig(monkeypatch, d20_natural=10, d4=3)
    foe = server.create_character(cid, "Thug", kind="monster", max_hp=30, armor_class=12)["id"]
    r = server.attack(cid, attacker_id=a, target_id=foe, attack_bonus=0, damage_dice="1d6")
    assert r["attack_roll"]["total"] == 13 and r["hit"] is True
    assert r["attack_roll"]["bonus_dice"][0]["source"] == "Bless"


def test_bless_via_target_ids_including_caster_blesses_both(tmp_path, monkeypatch):
    # The exact gs-ember-deep cast: Bless target_ids=[ally, caster]. BOTH must carry the rider — the
    # ally via a concentration-linked child, the caster via its own twin (it IS a beneficiary here).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BlessSelfAlly")["id"]
    cleric = server.create_character(cid, "Toll", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    ally = server.create_character(cid, "Kield", kind="player", max_hp=30)["id"]
    server.cast_spell(cid, cleric, "Bless", target_ids=[ally, cleric])
    for who in (ally, cleric):
        effs = [e for e in server.get_character(cid, who)["active_effects"] if e["name"] == "Bless"]
        assert effs and effs[0]["attack_bonus_dice"] == "1d4", f"{who} missing Bless rider"
    ally_bless = [e for e in server.get_character(cid, ally)["active_effects"] if e["name"] == "Bless"][0]
    assert ally_bless["linked_to_concentration"] is True  # the ally holds the linked child


def test_cast_result_advertises_all_rider_holders_for_multi_target(tmp_path, monkeypatch):
    # The cast result surfaces EVERY rider holder so the DM (and the GUI) can see who got blessed.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AdvertAoE")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    a = server.create_character(cid, "AllyA", kind="player", max_hp=30)["id"]
    b = server.create_character(cid, "AllyB", kind="player", max_hp=30)["id"]
    r = server.cast_spell(cid, cleric, "Bless", target_ids=[a, b])
    riders = r["effect_riders"]
    assert set(riders["holder_ids"]) == {a, b}
    assert riders["attack_bonus_dice"] == "1d4"


def test_shield_of_faith_via_target_ids_buffs_each_ally(tmp_path, monkeypatch):
    # The multi-target rider path is rider-AGNOSTIC (not Bless-specific): Shield of Faith
    # (ac_bonus, concentration) cast on a target_ids list gives EACH ally the +2 AC linked child.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("SoFAoE")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     level=1, apply_srd_defaults=True)["id"]
    a = server.create_character(cid, "AllyA", kind="player", max_hp=30, armor_class=14)["id"]
    b = server.create_character(cid, "AllyB", kind="player", max_hp=30, armor_class=12)["id"]
    server.cast_spell(cid, cleric, "Shield of Faith", target_ids=[a, b])
    for who in (a, b):
        effs = [e for e in server.get_character(cid, who)["active_effects"] if e["name"] == "Shield of Faith"]
        assert effs and effs[0]["ac_bonus"] == 2 and effs[0]["linked_to_concentration"] is True


# --- Bane via target_ids: a DEBUFF must apply its rider, NOT deal damage, and not crash ---

def test_bane_via_target_ids_applies_rider_to_each_foe_and_deals_no_damage(tmp_path, monkeypatch):
    # Bane is an SRD-only DEBUFF: its tracked effect is the -1d4 save/attack rider, NOT damage.
    # But its srd524 record carries a stray damage_roll='1d4' AND a full-word
    # saving_throw_ability='charisma'. A target_ids cast used to enter the AoE save-for-damage
    # path and hard-crash on Ability('charisma') (the enum code is 'cha') — AFTER the slot was
    # already spent. Bane must instead apply its -1d4 save rider to EACH foe and deal NO damage.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("BaneAoE")["id"]
    caster = server.create_character(cid, "Hex", kind="player", max_hp=10)["id"]
    server.update_character(cid, caster, patch={
        "spell_slots": {"1": {"maximum": 2, "used": 0}}})
    a = server.create_character(cid, "ThugA", kind="monster", max_hp=20)["id"]
    b = server.create_character(cid, "ThugB", kind="monster", max_hp=20)["id"]
    r = server.cast_spell(cid, caster, "Bane", target_ids=[a, b])  # must NOT raise
    for foe in (a, b):
        sheet = server.get_character(cid, foe)
        assert sheet["current_hp"] == 20, f"{foe} wrongly took Bane 'damage'"
        effs = [e for e in sheet["active_effects"] if e["name"] == "Bane"]
        assert effs, f"{foe} got no Bane rider"
        assert effs[0]["save_bonus_dice"] == "-1d4"
        assert effs[0]["linked_to_concentration"] is True
    # the engine rolled NO area damage for a pure debuff
    assert "aoe" not in r or r["aoe"].get("shared_damage") is None
    # ...and the -1d4 actually bites on a foe's save (engine-rolled, like the single-target case)
    _rig(monkeypatch, d20_natural=10, d4=3)
    out = server.saving_throw(cid, a, "wis", dc=10)
    assert out["roll"] == 7 and out["success"] is False
    assert out["bonus_dice"][0]["source"] == "Bane" and out["bonus_dice"][0]["rolled"] == -3


def test_srd_only_area_damage_spell_with_full_word_save_ability_resolves(tmp_path, monkeypatch):
    # The crash class Bane surfaced is general: ALL ~68 SRD-only save-for-damage spells spell
    # their save ability as the FULL WORD ('constitution', 'dexterity', …), but the AoE resolver
    # fed it straight to Ability(...) (which only knows the 3-letter codes). A real SRD-only area
    # damage spell (Cone of Cold: 8d8 cold, CON save) cast via target_ids must resolve the engine
    # save-for-half table — not crash AFTER the slot spend.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("ConeAoE")["id"]
    caster = server.create_character(cid, "Evoker", kind="player", max_hp=20)["id"]
    server.update_character(cid, caster, patch={
        "spell_slots": {"5": {"maximum": 1, "used": 0}}})
    a = server.create_character(cid, "FoeA", kind="monster", max_hp=100)["id"]
    b = server.create_character(cid, "FoeB", kind="monster", max_hp=100)["id"]
    r = server.cast_spell(cid, caster, "Cone of Cold", target_ids=[a, b])  # must NOT raise
    aoe = r["aoe"]
    assert aoe["save_ability"] == "con"  # the full word 'constitution' resolved to the enum code
    assert aoe["shared_damage"]["type"] == "cold"
    assert len(aoe["targets"]) == 2
    for foe in (a, b):  # each foe took the engine-resolved damage (full on fail / half on save)
        assert server.get_character(cid, foe)["current_hp"] < 100
