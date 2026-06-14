import pytest

import server
import store
import srd_tables
import store


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
        # Half-casters round UP (SRD 5.2 / audit F02-2) — the old 2014 round-DOWN gave a
        # L1 paladin/ranger ZERO slots and under-slotted every odd level.
        ([("Paladin", 1)], {1: 2}),  # CL 1 (was {} under round-down)
        ([("Paladin", 2)], {1: 2}),  # CL 1
        ([("Paladin", 3)], {1: 3}),  # CL 2 (was {1: 2})
        ([("Ranger", 5)], {1: 4, 2: 2}),  # CL 3 (was {1: 3})
        ([("Paladin", 6), ("Sorcerer", 1)], {1: 4, 2: 3}),  # 3 + 1 = CL 4 (unchanged)
    ],
)
def test_multiclass_slots(class_levels, expected):
    assert srd_tables.multiclass_slots(class_levels) == expected


@pytest.mark.parametrize(
    "level,expected",
    [
        # With ceil rounding, the multiclass table reproduces the PHB half-caster
        # progression column EXACTLY at every single-class paladin/ranger level.
        (1, {1: 2}),
        (2, {1: 2}),
        (3, {1: 3}),
        (5, {1: 4, 2: 2}),
        (9, {1: 4, 2: 3, 3: 2}),
        (13, {1: 4, 2: 3, 3: 3, 4: 1}),
        (17, {1: 4, 2: 3, 3: 3, 4: 3, 5: 1}),
        (19, {1: 4, 2: 3, 3: 3, 4: 3, 5: 2}),
    ],
)
def test_half_caster_single_class_matches_phb_column(level, expected):
    assert srd_tables.multiclass_slots([("Paladin", level)]) == expected
    assert srd_tables.multiclass_slots([("Ranger", level)]) == expected


def test_l1_half_caster_creation_gets_slots_and_spells():
    # F02-2 compounding seed gate: a L1 paladin/ranger could not cast AT ALL — zero
    # slots from the round-down table AND a `level < 2` gate in _seed_starting_spells.
    cid = server.create_campaign("HalfCaster")["id"]
    for cls, spell in (("paladin", "Cure Wounds"), ("ranger", "Hunter's Mark")):
        out = server.create_character(cid, f"L1 {cls}", kind="player", class_name=cls,
                                      level=1, apply_srd_defaults=True)
        sheet = server.get_character(cid, out["id"])
        assert sheet["spell_slots"], f"L1 {cls} must have spell slots"
        assert sheet["spell_slots"]["1"]["maximum"] == 2, cls
        assert sheet["spells_known"], f"L1 {cls} must seed a starting loadout"
        assert spell in sheet["spells_prepared"], cls


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


def _campaign_snapshot(campaign_id: str) -> dict:
    return store.load_campaign(campaign_id).model_dump(mode="json")


def test_preview_level_up_reports_features_and_hp_without_mutating():
    cid = server.create_campaign("Preview")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        level=4,
        apply_srd_defaults=True,
        abilities={"constitution": 12, "strength": 16},
    )["id"]
    before = _campaign_snapshot(cid)

    out = server.preview_level_up(cid, fid, "Fighter", hp_method="average")

    assert out["ok"] is True
    assert out["character_id"] == fid
    assert out["from"]["total_level"] == 4
    assert out["to"]["total_level"] == 5
    assert out["from"]["class_level"] == 4
    assert out["to"]["class_level"] == 5
    assert out["hp_gain"] == 7  # average d10 (6) + CON +1
    assert any(f["name"] == "Extra Attack" for f in out["features_gained"])
    assert out["choice_requirements"] == []
    assert out["errors"] == []
    assert _campaign_snapshot(cid) == before


def test_preview_level_up_reports_spell_slot_and_resource_deltas_without_mutating():
    cid = server.create_campaign("Preview deltas")["id"]
    wid = server.create_character(
        cid,
        "Gale",
        kind="player",
        class_name="Wizard",
        level=2,
        apply_srd_defaults=True,
        abilities={"intelligence": 16, "constitution": 12},
    )["id"]
    mid = server.create_character(
        cid,
        "Kira",
        kind="player",
        class_name="Monk",
        level=1,
        apply_srd_defaults=True,
        abilities={"dexterity": 16, "wisdom": 14, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    wizard = server.preview_level_up(cid, wid, "Wizard")
    monk = server.preview_level_up(cid, mid, "Monk")

    assert wizard["spell_slot_deltas"]["2"] == {"from_max": 0, "to_max": 2, "delta": 2}
    assert monk["resource_deltas"]["ki"] == {
        "from_max": 0,
        "to_max": 2,
        "delta": 2,
        "recharge": "short",
    }
    assert _campaign_snapshot(cid) == before


def test_preview_level_up_reports_asi_or_feat_requirement_and_feat_house_rule_error():
    cid = server.create_campaign("Preview choices")["id"]
    fid = server.create_character(
        cid,
        "Aria",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "constitution": 12},
    )["id"]
    server.set_house_rules(cid, {"feats_allowed": False})
    before = _campaign_snapshot(cid)

    out = server.preview_level_up(cid, fid, "Fighter", feat="Lucky")

    assert out["ok"] is False
    assert out["choice_requirements"] == [
        {"type": "asi_or_feat", "class_name": "fighter", "class_level": 4}
    ]
    assert "feats are disabled by campaign house rules" in out["errors"]
    assert _campaign_snapshot(cid) == before


def test_preview_level_up_rejects_invalid_asi_payloads_without_mutating():
    cid = server.create_campaign("Preview invalid ASI")["id"]
    fid = server.create_character(
        cid,
        "Aria",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 12, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    unknown = server.preview_level_up(cid, fid, "Fighter", asi={"strength": 1, "luck": 1})
    over_budget = server.preview_level_up(cid, fid, "Fighter", asi={"strength": 2, "dexterity": 2})

    assert unknown["ok"] is False
    assert "unknown ability 'luck' in asi" in unknown["errors"]
    assert unknown["applied_choice"] is None
    assert over_budget["ok"] is False
    assert "asi must be +2 to one ability or +1 to two abilities" in over_budget["errors"]
    assert over_budget["applied_choice"] is None
    assert _campaign_snapshot(cid) == before


def test_preview_level_up_reports_multiclass_house_rule_error_without_mutating():
    cid = server.create_campaign("Preview multiclass")["id"]
    wid = server.create_character(
        cid,
        "Gale",
        kind="player",
        class_name="Wizard",
        apply_srd_defaults=True,
        abilities={"intelligence": 16, "strength": 10, "dexterity": 14},
    )["id"]
    server.set_house_rules(cid, {"multiclass_allowed": False})
    before = _campaign_snapshot(cid)

    out = server.preview_level_up(cid, wid, "Fighter")

    assert out["ok"] is False
    assert "multiclassing is disabled by campaign house rules" in out["errors"]
    assert _campaign_snapshot(cid) == before


def test_build_options_reports_legal_level_up_paths_without_mutating():
    cid = server.create_campaign("Build options")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 14, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    out = server.build_options(cid, fid)

    assert out["character_id"] == fid
    assert out["from"]["level"] == 3
    assert out["from"]["classes"] == [{"name": "fighter", "level": 3, "subclass": None}]
    assert out["choices"] == {"asi_required": True, "feat_allowed": True, "multiclass_allowed": True}
    assert out["errors"] == []
    fighter = next(option for option in out["options"] if option["class_name"] == "fighter")
    assert fighter["legal"] is True
    assert fighter["to"] == {"level": 4, "class": "fighter"}
    assert fighter["choices"]["asi_required"] is True
    assert fighter["choices"]["feat_allowed"] is True
    assert fighter["preview"]["choice_requirements"] == [
        {"type": "asi_or_feat", "class_name": "fighter", "class_level": 4}
    ]
    assert _campaign_snapshot(cid) == before


def test_build_options_omits_illegal_multiclass_paths_without_mutating():
    cid = server.create_campaign("Build options house rules")["id"]
    wid = server.create_character(
        cid,
        "Gale",
        kind="player",
        class_name="Wizard",
        apply_srd_defaults=True,
        abilities={"intelligence": 16, "strength": 10, "dexterity": 14},
    )["id"]
    server.set_house_rules(cid, {"multiclass_allowed": False})
    before = _campaign_snapshot(cid)

    out = server.build_options(cid, wid)

    assert out["choices"]["multiclass_allowed"] is False
    assert [option["class_name"] for option in out["options"]] == ["wizard"]
    assert out["blocked_options"]
    assert all(option["class_name"] != "fighter" for option in out["options"])
    assert any(
        option["class_name"] == "fighter"
        and "multiclassing is disabled by campaign house rules" in option["errors"]
        for option in out["blocked_options"]
    )
    assert _campaign_snapshot(cid) == before


def test_level_up_rejects_disabled_feat_without_mutating():
    cid = server.create_campaign("Commit feat rules")["id"]
    fid = server.create_character(
        cid,
        "Aria",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "constitution": 12},
    )["id"]
    server.set_house_rules(cid, {"feats_allowed": False})
    before = _campaign_snapshot(cid)

    with pytest.raises(Exception, match="feats are disabled by campaign house rules"):
        server.level_up(cid, fid, "Fighter", feat="Lucky")

    assert _campaign_snapshot(cid) == before


def test_level_up_rejects_invalid_asi_payloads_without_mutating():
    cid = server.create_campaign("Commit ASI rules")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 12, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    with pytest.raises(Exception, match="unknown ability"):
        server.level_up(cid, fid, "Fighter", asi={"strength": 1, "luck": 1})
    assert _campaign_snapshot(cid) == before

    with pytest.raises(Exception, match="asi must be"):
        server.level_up(cid, fid, "Fighter", asi={"strength": 2, "dexterity": 2})
    assert _campaign_snapshot(cid) == before


def test_level_up_rejects_asi_and_feat_together_without_mutating():
    cid = server.create_campaign("Commit ASI feat exclusivity")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        level=3,
        apply_srd_defaults=True,
        abilities={"strength": 16, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    with pytest.raises(Exception, match="choose either asi or feat"):
        server.level_up(cid, fid, "Fighter", asi={"strength": 2}, feat="Lucky")

    assert _campaign_snapshot(cid) == before


def test_level_up_rejects_asi_or_feat_on_non_asi_level_without_mutating():
    cid = server.create_campaign("Commit non-ASI choice")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        apply_srd_defaults=True,
        abilities={"strength": 16, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    with pytest.raises(Exception, match="does not grant an ASI or feat choice"):
        server.level_up(cid, fid, "Fighter", asi={"strength": 2})
    assert _campaign_snapshot(cid) == before

    with pytest.raises(Exception, match="does not grant an ASI or feat choice"):
        server.level_up(cid, fid, "Fighter", feat="Lucky")
    assert _campaign_snapshot(cid) == before


def test_level_up_rejects_disabled_multiclass_without_mutating():
    cid = server.create_campaign("Commit multiclass rules")["id"]
    wid = server.create_character(
        cid,
        "Gale",
        kind="player",
        class_name="Wizard",
        apply_srd_defaults=True,
        abilities={"intelligence": 16, "strength": 10, "dexterity": 14},
    )["id"]
    server.set_house_rules(cid, {"multiclass_allowed": False})
    before = _campaign_snapshot(cid)

    with pytest.raises(Exception, match="multiclassing is disabled by campaign house rules"):
        server.level_up(cid, wid, "Fighter")

    assert _campaign_snapshot(cid) == before
# --- Defect 2: milestone XP auto-awarded on quest completion (xp leveling_mode) ---


def _xp_party_of_two(title: str):
    """A fresh xp-mode campaign with two level-1 PCs at 0 XP. Returns (cid, a_id, b_id)."""
    cid = server.create_campaign(title)["id"]  # leveling_mode defaults to "xp"
    a = server.create_character(cid, "Ada", kind="player", class_name="Fighter",
                                apply_srd_defaults=True)["id"]
    b = server.create_character(cid, "Bek", kind="player", class_name="Rogue",
                                apply_srd_defaults=True)["id"]
    assert server.get_character(cid, a)["xp"] == 0
    assert server.get_character(cid, b)["xp"] == 0
    return cid, a, b


def test_complete_quest_awards_milestone_xp_in_xp_mode():
    cid, a, b = _xp_party_of_two("Milestone")
    qid = server.add_quest(cid, "Break the Siege")["id"]
    out = server.complete_quest(cid, qid, status="completed")
    assert out["status"] == "completed"
    assert out["xp_awarded"] == 150 * 1  # 150 x party level (1)
    # Both living party members gained >0 XP; the total equals the even split (+ remainder).
    xa = server.get_character(cid, a)["xp"]
    xb = server.get_character(cid, b)["xp"]
    assert xa > 0 and xb > 0
    assert xa + xb == out["xp_awarded"]
    assert {g["id"] for g in out["grants"]} == {a, b}


def test_complete_quest_milestone_idempotent():
    cid, a, b = _xp_party_of_two("Idemp")
    qid = server.add_quest(cid, "Recover the Relic")["id"]
    first = server.complete_quest(cid, qid, status="completed")
    xa_after_first = server.get_character(cid, a)["xp"]
    # Re-completing (or flipping status back to completed) must NOT award again.
    second = server.complete_quest(cid, qid, status="completed")
    assert "xp_awarded" not in second  # the milestone_awarded guard suppressed a re-award
    assert server.get_character(cid, a)["xp"] == xa_after_first
    # ...and routing the SAME completion through set_quest_status is also a no-op.
    third = server.set_quest_status(cid, qid, "completed")
    assert "xp_awarded" not in third
    assert server.get_character(cid, a)["xp"] == xa_after_first
    assert first["xp_awarded"] == 150


def test_set_quest_status_completed_awards_milestone_xp():
    """set_quest_status is the DM's equivalent verb for a tracked quest — it must award
    the same milestone XP as complete_quest when the quest reaches 'completed'."""
    cid, a, b = _xp_party_of_two("SQS")
    qid = server.add_quest(cid, "Clear the Road")["id"]
    out = server.set_quest_status(cid, qid, "completed")
    assert out["status"] == "completed"
    assert out["xp_awarded"] == 150
    assert server.get_character(cid, a)["xp"] > 0


def test_milestone_xp_skipped_in_milestone_mode():
    cid, a, b = _xp_party_of_two("MilestoneMode")
    c = store.load_campaign(cid)
    c.leveling_mode = "milestone"
    store.save_campaign(c)
    qid = server.add_quest(cid, "Win the Duel")["id"]
    out = server.complete_quest(cid, qid, status="completed")
    assert out["status"] == "completed"
    assert "xp_awarded" not in out                       # no auto-XP in milestone mode
    assert server.get_character(cid, a)["xp"] == 0       # unchanged
    # The idempotency guard is NOT consumed (no award happened), so switching to xp-mode
    # later and completing still pays out once.
    c = store.load_campaign(cid)
    assert c.quests[qid].milestone_awarded is False


def test_end_session_backstop_awards_when_advanced_and_zero_xp():
    cid, a, b = _xp_party_of_two("Backstop")
    server.start_session(cid)
    # The session genuinely advanced the clock (past the opening morning phase)...
    server.advance_time(cid, to="evening")
    # ...but no quest was completed, so the party is still at 0 XP. end_session backstops it.
    out = server.end_session(cid)
    assert out["xp_awarded"] == 100 * 1                  # 100 x party level (1)
    assert server.get_character(cid, a)["xp"] > 0
    assert server.get_character(cid, b)["xp"] > 0


def test_end_session_tops_up_zero_xp_member_to_party_parity():
    # PER-MEMBER backstop (mech2 #3A): when the DM awards XP to ONE member only (the common
    # single-target award_xp on the PC), a companion who fought all session must not close at
    # 0 while the other banked XP. The advanced-session backstop tops the 0-XP member up to the
    # party's max XP (levels them together) — it never stacks onto a member who already earned,
    # and never lowers anyone. (Was: the all-zero guard could not rescue an uneven party.)
    cid, a, b = _xp_party_of_two("Parity")
    server.start_session(cid)
    server.advance_time(cid, to="evening")               # session advanced...
    server.award_xp(cid, a, 50)                          # ...and only `a` earned XP
    out = server.end_session(cid)
    assert out["xp_awarded"] == 50                       # the 0-XP member is topped up by 50
    assert server.get_character(cid, a)["xp"] == 50      # `a` is NOT stacked (already had it)
    assert server.get_character(cid, b)["xp"] == 50      # `b` raised to party parity


def test_end_session_no_backstop_when_not_advanced():
    """The backstop only fires on a session that ADVANCED — a frozen session (no clock
    movement, ≤1 location) at 0 XP is not retroactively paid (nothing happened to reward)."""
    cid, a, b = _xp_party_of_two("Frozen")
    server.start_session(cid)
    out = server.end_session(cid)                        # clock never left morning, 1 location
    assert "xp_awarded" not in out
    assert server.get_character(cid, a)["xp"] == 0
