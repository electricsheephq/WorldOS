"""Finesse weapons + Sneak Attack at the combat-numbers surface (audit F01-4 / F01-5).

F01-4 (#774): `_combat_numbers` hardcoded melee = prof + STR, so every DEX-martial
(the engine's own default rogue, seeded with a finesse Shortsword) was handed wrong
melee attack/damage numbers all campaign — under a "never invent" instruction that
forbids the DM from correcting them. Finesse weapons must use max(STR, DEX) on
attack AND damage.

F01-5 (#166 cluster): `Character.sneak_attack_dice` was written by chargen/level-up
and read by NOTHING in the combat path — ~half a rogue's damage invisible at the
attack trigger. Surface it in `_combat_numbers` / `turn_brief` (the proven adherence
channel) as a ready-to-pass `damage_rolls` component prompt.
"""

import pytest

import server
import srd_tables


# --- the pure finesse lookup ------------------------------------------------

def test_finesse_weapon_names_from_srd_data():
    names = srd_tables.finesse_weapon_names()
    # exactly the 6 srd524 WeaponPropertyAssignment finesse rows
    assert set(names) == {"dagger", "dart", "rapier", "scimitar", "shortsword", "whip"}


@pytest.mark.parametrize(
    "item,expected",
    [
        ("Rapier", True),
        ("rapier", True),
        ("Rapier +1", True),        # substring-tolerant (magic-item naming)
        ("Shortsword", True),
        ("Daggers", True),          # plural still names the weapon
        ("Greatsword", False),
        ("Longsword", False),
        ("Explorer's Pack", False),
        ("", False),
    ],
)
def test_is_finesse_weapon_matching(item, expected):
    assert srd_tables.is_finesse_weapon(item) is expected


# --- _combat_numbers: finesse uses max(STR, DEX) on attack AND damage --------

def test_rogue_with_finesse_weapon_uses_dex_on_melee(tmp_path, monkeypatch):
    # Red-first F01-4: DEX-16/STR-10 rogue (engine seeds an equipped Shortsword —
    # finesse). Today melee_attack_bonus is prof + STR = +2; it must be prof + DEX = +5.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Finesse")["id"]
    r = server.create_character(
        cid, "Sly", kind="player", class_name="Rogue", level=3,
        apply_srd_defaults=True,
        abilities={"strength": 10, "dexterity": 16},
    )
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    prof = cn["proficiency_bonus"]
    assert cn["melee_attack_bonus"] == prof + 3, cn
    assert cn["melee_damage_mod"] == 3
    # the surfaced cue names the weapon so the DM can narrate it
    assert "Shortsword" in cn["finesse"]["weapon"]
    assert cn["finesse"]["ability"] == "dex"


def test_str_fighter_without_finesse_weapon_unchanged(tmp_path, monkeypatch):
    # Greataxe barbarian: STR-only loadout — numbers byte-identical, no finesse key.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("NoFinesse")["id"]
    r = server.create_character(
        cid, "Krug", kind="player", class_name="Barbarian", level=1,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 14},
    )
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    assert cn["melee_attack_bonus"] == cn["proficiency_bonus"] + 3
    assert cn["melee_damage_mod"] == 3
    assert "finesse" not in cn


def test_magic_finesse_weapon_name_still_matches(tmp_path, monkeypatch):
    # "Rapier +1" must still register as finesse (substring-tolerant matching).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Magic")["id"]
    r = server.create_character(
        cid, "Duelist", kind="player", class_name="Fighter", level=1,
        apply_srd_defaults=True,
        abilities={"strength": 10, "dexterity": 18},
    )
    server.update_character(cid, r["id"], patch={
        "inventory": [{"name": "Rapier +1", "equipped": True}],
    })
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    assert cn["melee_attack_bonus"] == cn["proficiency_bonus"] + 4
    assert cn["finesse"]["weapon"] == "Rapier +1"


def test_carried_but_unequipped_finesse_weapon_still_counts(tmp_path, monkeypatch):
    # Equipped-first, else any carried (the spec's fallback): a dagger in the pack
    # still lets a DEX-martial fight with it.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Carried")["id"]
    r = server.create_character(
        cid, "Pock", kind="player", class_name="Fighter", level=1,
        apply_srd_defaults=True,
        abilities={"strength": 8, "dexterity": 16},
    )
    server.update_character(cid, r["id"], patch={
        "inventory": [{"name": "Dagger", "equipped": False}],
    })
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    assert cn["melee_attack_bonus"] == cn["proficiency_bonus"] + 3
    assert cn["finesse"]["weapon"] == "Dagger"


def test_str_winner_with_finesse_weapon_keeps_str_numbers(tmp_path, monkeypatch):
    # max(STR, DEX): a STR-16/DEX-10 fighter holding a dagger keeps the STR numbers
    # (finesse is "may use DEX", never a penalty).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("StrWins")["id"]
    r = server.create_character(
        cid, "Bron", kind="player", class_name="Fighter", level=1,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 10},
    )
    server.update_character(cid, r["id"], patch={
        "inventory": [{"name": "Dagger", "equipped": True}],
    })
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    assert cn["melee_attack_bonus"] == cn["proficiency_bonus"] + 3
    assert cn["melee_damage_mod"] == 3
    assert cn["finesse"]["ability"] == "str"


# --- sneak attack surfaced at the attack trigger ------------------------------

def test_sneak_attack_surfaced_in_combat_numbers(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Sneak")["id"]
    r = server.start_character(cid, name="Vex", origin="veteran_l5", class_name="Rogue",
                               abilities={"dex": 16, "str": 10})
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    sa = cn["sneak_attack"]
    assert sa["dice"] == "3d6"  # level-5 rogue
    # The note must teach the once-per-turn condition AND the damage_rolls shape so
    # the engine rolls it (crit-doubling rides the existing multi-component path).
    assert "once per turn" in sa["note"].lower()
    assert "damage_rolls" in sa["note"]
    assert "3d6" in sa["note"]


def test_non_rogue_has_no_sneak_attack_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("NoSneak")["id"]
    r = server.create_character(
        cid, "Pious", kind="player", class_name="Cleric", level=3,
        apply_srd_defaults=True,
    )
    cn = server.get_character(cid, r["id"])["combat_numbers"]
    assert "sneak_attack" not in cn


def test_turn_brief_carries_finesse_and_sneak(tmp_path, monkeypatch):
    # The per-turn surface (the one the DM actually reads each turn, #166) must carry
    # both cues, not just get_character.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Brief")["id"]
    r = server.create_character(
        cid, "Shade", kind="player", class_name="Rogue", level=5,
        apply_srd_defaults=True,
        abilities={"strength": 10, "dexterity": 16},
    )
    ch = server._require(cid).characters[r["id"]]
    brief = server._turn_brief(ch, server._require(cid))
    atk = brief["attack"]
    assert atk["melee_attack_bonus"] == ch.proficiency_bonus + 3
    assert atk["finesse"]["ability"] == "dex"
    assert atk["sneak_attack"]["dice"] == ch.sneak_attack_dice
