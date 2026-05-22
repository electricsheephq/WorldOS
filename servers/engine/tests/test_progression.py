import pytest

import server
import srd_tables


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


# --- SRD tables ---
def test_proficiency_bonus():
    assert srd_tables.proficiency_bonus(1) == 2
    assert srd_tables.proficiency_bonus(5) == 3
    assert srd_tables.proficiency_bonus(20) == 6


def test_level_for_xp():
    assert srd_tables.level_for_xp(0) == 1
    assert srd_tables.level_for_xp(299) == 1
    assert srd_tables.level_for_xp(300) == 2
    assert srd_tables.level_for_xp(900) == 3
    assert srd_tables.level_for_xp(2700) == 4
    assert srd_tables.level_for_xp(355000) == 20


def test_is_asi_level():
    assert srd_tables.is_asi_level("fighter", 6) is True  # fighter bonus ASI
    assert srd_tables.is_asi_level("wizard", 6) is False
    assert srd_tables.is_asi_level("wizard", 4) is True
    assert srd_tables.is_asi_level("rogue", 10) is True


@pytest.mark.parametrize(
    "class_levels,expected",
    [
        ([("Wizard", 5)], {1: 4, 2: 3, 3: 2}),
        ([("Cleric", 1), ("Wizard", 1)], {1: 3}),  # effective caster level 2
        ([("Paladin", 1)], {}),  # half-caster level 1 -> CL 0
        ([("Paladin", 2)], {1: 2}),  # CL 1
        ([("Paladin", 6), ("Sorcerer", 1)], {1: 4, 2: 3}),  # 3 + 1 = CL 4
    ],
)
def test_multiclass_slots(class_levels, expected):
    assert srd_tables.multiclass_slots(class_levels) == expected


def test_warlock_pact():
    assert srd_tables.warlock_pact_slots(1) == {"slots": 1, "level": 1}
    assert srd_tables.warlock_pact_slots(5) == {"slots": 2, "level": 3}
    assert srd_tables.warlock_pact_slots(20) == {"slots": 4, "level": 5}


# --- generate_ability_scores ---
def test_standard_array():
    assert server.generate_ability_scores("standard_array")["array"] == [15, 14, 13, 12, 10, 8]


def test_point_buy_valid():
    out = server.generate_ability_scores(
        "point_buy",
        point_buy={"strength": 15, "dexterity": 15, "constitution": 13,
                   "intelligence": 8, "wisdom": 10, "charisma": 10},
    )
    assert out["points_spent"] == 27 and out["points_remaining"] == 0


def test_point_buy_over_budget():
    with pytest.raises(Exception):
        server.generate_ability_scores(
            "point_buy",
            point_buy={k: 15 for k in
                       ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]},
        )


def test_point_buy_out_of_range():
    with pytest.raises(Exception):
        server.generate_ability_scores("point_buy", point_buy={"strength": 16})


def test_roll_method_deterministic():
    out = server.generate_ability_scores("roll", seed=1)
    assert len(out["totals"]) == 6 and all(3 <= t <= 18 for t in out["totals"])


# --- create_character with SRD defaults ---
def test_create_with_srd_defaults():
    cid = server.create_campaign("Chars")["id"]
    fid = server.create_character(cid, "Bruenor", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True, abilities={"constitution": 14})["id"]
    sheet = server.get_character(cid, fid)
    assert sheet["max_hp"] == 12  # d10 + CON +2
    assert set(sheet["saving_throw_proficiencies"]) == {"str", "con"}
    assert sheet["proficiency_bonus"] == 2

    wid = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                  apply_srd_defaults=True, abilities={"constitution": 12})["id"]
    wsheet = server.get_character(cid, wid)
    assert wsheet["spell_slots"]["1"]["maximum"] == 2  # Wizard L1 -> 2 first-level slots


# --- award_xp + level_up (HP-on-level-up, ASI, multiclass prereq) ---
def test_level_up_hp_and_prof():
    cid = server.create_campaign("Level")["id"]
    fid = server.create_character(cid, "Thorin", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True,
                                  abilities={"constitution": 14, "strength": 16})["id"]
    before = server.get_character(cid, fid)["max_hp"]  # 12
    res = server.level_up(cid, fid, "Fighter", hp_method="average")
    assert res["_hp_gained"] == 8  # average d10 (6) + CON +2
    assert res["max_hp"] == before + 8
    assert res["proficiency_bonus"] == 2


def test_level_up_asi_at_4():
    cid = server.create_campaign("ASI")["id"]
    fid = server.create_character(cid, "Aria", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True,
                                  abilities={"strength": 16, "constitution": 12})["id"]
    server.level_up(cid, fid, "Fighter")  # L2
    server.level_up(cid, fid, "Fighter")  # L3
    res = server.level_up(cid, fid, "Fighter", asi={"strength": 2})  # L4 -> ASI
    assert res["abilities"]["strength"] == 18


def test_award_xp_reports_level_available():
    cid = server.create_campaign("XP")["id"]
    fid = server.create_character(cid, "Hero", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True)["id"]
    out = server.award_xp(cid, fid, 300)
    assert out["level_available"] == 2 and out["can_level_up"] is True


def test_multiclass_prereq_enforced():
    cid = server.create_campaign("MC")["id"]
    frail = server.create_character(cid, "Frail", kind="player", class_name="Wizard",
                                    apply_srd_defaults=True,
                                    abilities={"intelligence": 16, "strength": 10, "dexterity": 10})["id"]
    with pytest.raises(Exception):
        server.level_up(cid, frail, "Fighter")  # needs STR 13 or DEX 13

    nimble = server.create_character(cid, "Nimble", kind="player", class_name="Wizard",
                                     apply_srd_defaults=True,
                                     abilities={"intelligence": 16, "dexterity": 14})["id"]
    res = server.level_up(cid, nimble, "Fighter")
    assert any(cl["name"].lower() == "fighter" for cl in res["classes"])
