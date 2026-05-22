import pytest

import server
import spells


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


# --- effect resolution (pure) ---
def test_firebolt_cantrip_scaling():
    fb = spells.spell_data("Fire Bolt")
    assert spells.resolve_effect(fb, 0, 1, 3)["damage"] == "1d10"
    assert spells.resolve_effect(fb, 0, 5, 3)["damage"] == "2d10"
    assert spells.resolve_effect(fb, 0, 11, 3)["damage"] == "3d10"
    assert spells.resolve_effect(fb, 0, 17, 3)["damage"] == "4d10"


def test_magic_missile_darts_and_upcast():
    mm = spells.spell_data("Magic Missile")
    base = spells.resolve_effect(mm, 1, 5, 3)
    assert base["darts"] == 3 and base["damage"] == "3d4+3"
    up = spells.resolve_effect(mm, 3, 5, 3)
    assert up["darts"] == 5 and up["damage"] == "5d4+5"


def test_cure_wounds_upcast_and_mod():
    cw = spells.spell_data("Cure Wounds")
    assert spells.resolve_effect(cw, 1, 1, 3)["heal"] == "1d8+3"
    assert spells.resolve_effect(cw, 3, 1, 3)["heal"] == "3d8+3"


def test_burning_hands_save_and_upcast():
    bh = spells.spell_data("Burning Hands")
    e1 = spells.resolve_effect(bh, 1, 1, 3)
    assert e1["kind"] == "save" and e1["save_ability"] == "dex" and e1["damage"] == "3d6"
    assert spells.resolve_effect(bh, 2, 1, 3)["damage"] == "4d6"


# --- cast_spell tool ---
def test_cast_consumes_slot_and_dc():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True,
                                abilities={"intelligence": 16, "constitution": 12})["id"]
    out = server.cast_spell(cid, w, "Magic Missile")
    assert out["slot_used"] == 1 and out["slots_remaining"]["1"] == 1
    assert out["spell_save_dc"] == 13 and out["spell_attack_bonus"] == 5
    assert out["effect"]["damage"] == "3d4+3"
    server.cast_spell(cid, w, "Magic Missile")  # uses the second slot
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Magic Missile")  # no slots left


def test_cantrip_uses_no_slot():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    out = server.cast_spell(cid, w, "Fire Bolt")
    assert out["slot_used"] is None and out["effect"]["damage"] == "1d10"


def test_upcast_with_higher_slot():
    cid = server.create_campaign("S")["id"]
    # a level-3 wizard has a 2nd-level slot to upcast Magic Missile
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.level_up(cid, w, "Wizard")
    server.level_up(cid, w, "Wizard")  # level 3 -> 4/3/2 slots
    out = server.cast_spell(cid, w, "Magic Missile", slot_level=2)
    assert out["slot_used"] == 2 and out["effect"]["darts"] == 4  # 3 + 1 upcast


def test_concentration_set_on_cast():
    cid = server.create_campaign("S")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     apply_srd_defaults=True,
                                     abilities={"wisdom": 16, "constitution": 12})["id"]
    out = server.cast_spell(cid, cleric, "Bless")
    assert out["concentration"] == "Bless"
    assert server.get_character(cid, cleric)["concentration"] == "Bless"


def test_saving_throw():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"dexterity": 14})["id"]
    out = server.saving_throw(cid, w, "dex", 10)
    assert isinstance(out["success"], bool) and out["ability"] == "dex"


def test_learn_and_prepare():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True)["id"]
    server.learn_spells(cid, w, ["Fire Bolt", "Magic Missile"])
    server.prepare_spells(cid, w, ["Magic Missile"])
    sheet = server.get_character(cid, w)
    assert "Magic Missile" in sheet["spells_known"] and sheet["spells_prepared"] == ["Magic Missile"]


def test_unknown_spell_raises():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True)["id"]
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Wish")  # not bundled
