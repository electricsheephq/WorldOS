"""Tests for the Event+Parley scaffold tools (Phases 1-2 of the Event+Parley design).

Two READ-ONLY tools, both structured-prompting scaffolds — the engine supplies the
structure (alignment, sheet-correct DCs, over-match math), the DM voices/judges:

  * `generate_parley_options` — the PC's alignment + sheet-correct skill modifiers +
    suggested DCs so the DM authors a tagged menu without hand-computing (P-B).
  * `encounter_outlook` — the SRD over-match ratio + a `must_offer_out` flag at low
    level so the balancing doctrine is followable; combat is NEVER altered (B-B).

Mirrors test_wander.py / test_encounter.py: pure helpers exercised directly, plus the
engine seams against a real persisted campaign. No model change here, so the additive
round-trip is covered by a trivial confirmation at the end.
"""

import pytest

import encounter
import server
import store
from models import Campaign


# =========================================================================
# PURE: the DC band helper (_suggested_dc) — situation x house difficulty
# =========================================================================


@pytest.mark.parametrize(
    "difficulty,base",
    [("easy", 10), ("medium", 14), ("hard", 18)],
)
def test_dc_band_standard_house(difficulty, base):
    # standard house difficulty -> the band value as-is
    assert server._suggested_dc(difficulty, "standard") == base


@pytest.mark.parametrize("difficulty,base", [("easy", 10), ("medium", 14), ("hard", 18)])
def test_dc_band_hard_house_adds_two(difficulty, base):
    assert server._suggested_dc(difficulty, "hard") == base + 2


@pytest.mark.parametrize("difficulty,base", [("easy", 10), ("medium", 14), ("hard", 18)])
def test_dc_band_easy_house_subtracts_two(difficulty, base):
    assert server._suggested_dc(difficulty, "easy") == base - 2


def test_dc_band_unknown_difficulty_defaults_medium():
    # an unrecognized situation difficulty falls back to the medium band, never errors
    assert server._suggested_dc("ferocious", "standard") == 14
    assert server._suggested_dc("", "standard") == 14


def test_dc_band_full_matrix():
    # the full easy/med/hard x easy/standard/hard grid, spelled out
    expected = {
        ("easy", "easy"): 8, ("easy", "standard"): 10, ("easy", "hard"): 12,
        ("medium", "easy"): 12, ("medium", "standard"): 14, ("medium", "hard"): 16,
        ("hard", "easy"): 16, ("hard", "standard"): 18, ("hard", "hard"): 20,
    }
    for (situation, house), dc in expected.items():
        assert server._suggested_dc(situation, house) == dc


# =========================================================================
# INTEGRATION: generate_parley_options against a real campaign
# =========================================================================


@pytest.fixture
def parley_campaign(tmp_path, monkeypatch):
    """A campaign with a lead PC (a CHA-forward face: persuasion proficiency, deception
    expertise) and a companion. Returns (cid, pc_id, comp_id)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Parley Test")["id"]
    pc = server.create_character(
        cid, "Vanya", kind="player", class_name="bard", level=3,
        abilities={"cha": 16, "int": 12, "wis": 10},
        skills=["persuasion", "history"],
    )["id"]
    comp = server.create_character(cid, "Dorn", kind="companion", class_name="fighter", level=3)["id"]
    # give the PC an alignment + expertise so the scaffold has something real to surface
    server.update_character(cid, pc, {"alignment": "chaotic good", "skill_expertise": ["deception"]})
    return cid, pc, comp


def test_parley_defaults_actor_to_lead_pc(parley_campaign):
    cid, pc, _comp = parley_campaign
    out = server.generate_parley_options(cid)  # no actor_id -> the lead PC
    assert out["actor"] == "Vanya"
    assert out["free_form"] is True
    assert out["alignment"] == "chaotic good"


def test_parley_default_skills_union_proficient_and_core(parley_campaign):
    cid, _pc, _comp = parley_campaign
    out = server.generate_parley_options(cid, difficulty="medium")
    skills = {row["skill"] for row in out["skills"]}
    # the four core social skills are ALWAYS present...
    assert {"persuasion", "deception", "intimidation", "insight"}.issubset(skills)
    # ...and so are the actor's own proficient/expertise skills (history from proficiency)
    assert "history" in skills
    # no duplicates: persuasion is both a proficiency AND a core skill, appears once
    skill_list = [row["skill"] for row in out["skills"]]
    assert len(skill_list) == len(set(skill_list))


def test_parley_modifier_sourced_from_sheet_not_recomputed(parley_campaign):
    cid, pc, _comp = parley_campaign
    out = server.generate_parley_options(cid, difficulty="medium")
    by_skill = {row["skill"]: row for row in out["skills"]}
    c = store.load_campaign(cid)
    actor = c.characters[pc]
    # every modifier matches the model's authoritative skill_bonus, never a hand value
    for skill, row in by_skill.items():
        assert row["modifier"] == actor.skill_bonus(skill)
    # spot-check the math the sheet implies: CHA 16 (+3), proficiency +2 at level 3.
    # persuasion (CHA, proficient) = 3 + 2 = 5; deception (CHA, EXPERTISE) = 3 + 2*2 = 7;
    # intimidation (CHA, neither) = 3; insight (WIS 10, neither) = 0.
    assert by_skill["persuasion"]["modifier"] == 5
    assert by_skill["deception"]["modifier"] == 7
    assert by_skill["intimidation"]["modifier"] == 3
    assert by_skill["insight"]["modifier"] == 0


def test_parley_suggested_dc_keyed_off_difficulty(parley_campaign):
    cid, _pc, _comp = parley_campaign
    for difficulty, dc in (("easy", 10), ("medium", 14), ("hard", 18)):
        out = server.generate_parley_options(cid, difficulty=difficulty)
        assert all(row["suggested_dc"] == dc for row in out["skills"])


def test_parley_dc_shifts_with_house_difficulty(parley_campaign):
    cid, _pc, _comp = parley_campaign
    c = store.load_campaign(cid)
    c.house_rules.difficulty = "hard"
    store.save_campaign(c)
    out = server.generate_parley_options(cid, difficulty="medium")
    assert all(row["suggested_dc"] == 16 for row in out["skills"])  # 14 + 2


def test_parley_explicit_skills_override_default(parley_campaign):
    cid, _pc, _comp = parley_campaign
    out = server.generate_parley_options(cid, skills=["Athletics", "stealth"])
    skills = [row["skill"] for row in out["skills"]]
    # explicit list is honored verbatim (normalized), NOT unioned with the core four
    assert skills == ["athletics", "stealth"]


def test_parley_include_alignment_false_omits_it(parley_campaign):
    cid, _pc, _comp = parley_campaign
    out = server.generate_parley_options(cid, include_alignment=False)
    assert "alignment" not in out


def test_parley_explicit_actor(parley_campaign):
    cid, _pc, comp = parley_campaign
    out = server.generate_parley_options(cid, actor_id=comp)
    assert out["actor"] == "Dorn"


def test_parley_unknown_skill_raises(parley_campaign):
    cid, _pc, _comp = parley_campaign
    with pytest.raises(ValueError):
        server.generate_parley_options(cid, skills=["jibberish"])


def test_parley_is_read_only(parley_campaign):
    cid, _pc, _comp = parley_campaign
    before = store.load_campaign(cid).model_dump(mode="json")
    server.generate_parley_options(cid, difficulty="hard")
    after = store.load_campaign(cid).model_dump(mode="json")
    assert before == after  # a scaffold never mutates state


def test_parley_guidance_mandates_free_form_and_routing(parley_campaign):
    cid, _pc, _comp = parley_campaign
    out = server.generate_parley_options(cid)
    g = out["guidance"].lower()
    assert "free-form" in g
    assert "skill_check" in g and "social_check" in g and "start_combat" in g


# =========================================================================
# PURE: the overmatch ratio + must_offer_out boundary (B-B)
#
# The decision doc's empirical line: a ~1.12x L3 troll -> False; a 6.25x L3 dragon
# -> True; the SAME dragon at L6 -> False. Verified directly against encounter.py math.
# =========================================================================


def _ratio(party_levels, monster_xps):
    deadly = encounter.xp_thresholds(party_levels)["deadly"]
    return encounter.adjusted_xp(monster_xps) / deadly


def test_overmatch_troll_at_l3_is_below_threshold():
    # CR5 troll (1800 XP) solo vs 4x L3 (deadly budget 1600): 1.12x -> deadly-but-fair.
    party = [3, 3, 3, 3]
    troll = encounter.xp_for_cr("5")
    ratio = _ratio(party, [troll])
    assert round(ratio, 2) == 1.12
    avg = sum(party) / len(party)
    assert not (avg <= 5 and ratio >= 2.0)  # must_offer_out -> False


def test_overmatch_dragon_at_l3_trips_threshold():
    # CR13 dragon (10000 XP) solo vs 4x L3 (deadly 1600): 6.25x -> the unwinnable zone.
    party = [3, 3, 3, 3]
    dragon = encounter.xp_for_cr("13")
    ratio = _ratio(party, [dragon])
    assert round(ratio, 2) == 6.25
    avg = sum(party) / len(party)
    assert avg <= 5 and ratio >= 2.0  # must_offer_out -> True


def test_overmatch_same_dragon_at_l6_does_not_trip():
    # the SAME CR13 dragon vs 4x L6 (deadly 5600): 1.79x AND level > 5 -> no out mandated.
    party = [6, 6, 6, 6]
    dragon = encounter.xp_for_cr("13")
    ratio = _ratio(party, [dragon])
    assert round(ratio, 2) == 1.79
    avg = sum(party) / len(party)
    assert not (avg <= 5 and ratio >= 2.0)  # must_offer_out -> False (level gate AND ratio)


def test_overmatch_exactly_2x_at_low_level_trips():
    # the 2.0 line is inclusive: a fight at exactly 2.0x and level <= 5 must fire.
    # 4x L3 deadly = 1600; we need adjusted == 3200. A single monster (x1) of 3200 XP.
    party = [3, 3, 3, 3]
    ratio = _ratio(party, [3200])
    assert ratio == 2.0
    assert (sum(party) / len(party)) <= 5 and ratio >= 2.0


# =========================================================================
# INTEGRATION: encounter_outlook against a real campaign
# =========================================================================


@pytest.fixture
def outlook_campaign(tmp_path, monkeypatch):
    """A 4x level-3 party so the deadly budget is the canonical 1600 XP. Returns cid."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Outlook Test")["id"]
    for i in range(4):
        server.create_character(cid, f"PC{i}", kind="player", class_name="fighter", level=3)
    return cid


def test_outlook_troll_l3_no_out_but_deadly(outlook_campaign):
    cid = outlook_campaign
    troll = encounter.xp_for_cr("5")
    out = server.encounter_outlook(cid, monster_xps=[troll])
    assert out["overmatch_ratio"] == 1.12
    assert out["band"] == "deadly"
    assert out["must_offer_out"] is False
    assert out["avg_party_level"] == 3.0
    assert "winnable" in out["guidance"].lower()


def test_outlook_dragon_l3_must_offer_out(outlook_campaign):
    cid = outlook_campaign
    dragon = encounter.xp_for_cr("13")
    out = server.encounter_outlook(cid, monster_xps=[dragon])
    assert out["overmatch_ratio"] == 6.25
    assert out["must_offer_out"] is True
    g = out["guidance"].lower()
    # the doctrine prose: do-not-soften, do-not-TPK, surface a costed out via the parley tool
    assert "do not auto-soften" in g
    assert "generate_parley_options" in out["guidance"]


def test_outlook_same_dragon_at_l6_no_out(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("L6")["id"]
    for i in range(4):
        server.create_character(cid, f"PC{i}", kind="player", class_name="fighter", level=6)
    dragon = encounter.xp_for_cr("13")
    out = server.encounter_outlook(cid, monster_xps=[dragon])
    assert out["overmatch_ratio"] == 1.79
    assert out["avg_party_level"] == 6.0
    assert out["must_offer_out"] is False  # level > 5 AND ratio < 2.0


def test_outlook_resolves_monster_ids_from_staged_characters(outlook_campaign):
    cid = outlook_campaign
    # spawn a real bestiary foe -> a monster Character carrying xp_value
    spawned = server.spawn_monster(cid, "Troll")
    assert "spawned" in spawned, spawned  # resolved, not an error
    mid = spawned["spawned"][0]["id"]
    out_ids = server.encounter_outlook(cid, monster_ids=[mid])
    out_xps = server.encounter_outlook(cid, monster_xps=[spawned["xp_each"]])
    # resolving by id (off the staged Character's xp_value) matches passing the XP directly
    assert out_ids["overmatch_ratio"] == out_xps["overmatch_ratio"]
    assert out_ids["overmatch_ratio"] > 0


def test_outlook_resolves_monster_ids_from_bestiary_name(outlook_campaign):
    cid = outlook_campaign
    # a bare bestiary name (not yet a Character) resolves via the stat block
    out = server.encounter_outlook(cid, monster_ids=["Troll"])
    assert out["overmatch_ratio"] == 1.12  # CR5 troll vs 4x L3


def test_outlook_unresolvable_monster_id_raises(outlook_campaign):
    cid = outlook_campaign
    with pytest.raises(ValueError):
        server.encounter_outlook(cid, monster_ids=["not-a-real-creature-xyz"])


def test_outlook_multiple_monsters_apply_encounter_multiplier(outlook_campaign):
    cid = outlook_campaign
    # 4 monsters trigger the x2 SRD multiplier -> ratio reflects adjusted_xp, not raw sum
    xps = [200, 200, 200, 200]
    out = server.encounter_outlook(cid, monster_xps=xps)
    expected = encounter.adjusted_xp(xps) / encounter.xp_thresholds([3, 3, 3, 3])["deadly"]
    assert out["overmatch_ratio"] == round(expected, 2)


def test_outlook_is_read_only(outlook_campaign):
    cid = outlook_campaign
    before = store.load_campaign(cid).model_dump(mode="json")
    server.encounter_outlook(cid, monster_xps=[10000])
    after = store.load_campaign(cid).model_dump(mode="json")
    assert before == after  # the engine NEVER alters combat from an outlook


# =========================================================================
# Both tools are registered in the FastMCP tool list, and old snapshots
# round-trip (no model change in Phases 1-2).
# =========================================================================


def test_both_tools_registered():
    import asyncio

    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "generate_parley_options" in tools
    assert "encounter_outlook" in tools


def test_no_model_change_old_snapshot_round_trips():
    # The Event+Parley SCAFFOLD (Phases 1-2) added no model fields; a minimal legacy snapshot
    # still validates unchanged (the additive-round-trip contract). NOTE: the Quest & Arc engine
    # Layer 3 later added `Campaign.events` (a dict, mirroring companion_quest_arcs) — still
    # additive, so a legacy snapshot deserializes with events == {} (see
    # test_event_parley_layer3.test_old_campaign_snapshot_without_events_deserializes_unchanged).
    payload = {"title": "Legacy", "house_rules": {"difficulty": "standard"}}
    c = Campaign.model_validate(payload)
    assert c.title == "Legacy"
    assert c.events == {}  # Layer 3's events dict defaults empty for a pre-L3 snapshot


# =========================================================================
# M2: encounter_outlook raises when no XP can be resolved (silent all-clear fix)
# =========================================================================


def test_encounter_outlook_raises_when_no_xps_passed(outlook_campaign):
    """encounter_outlook with neither monster_xps nor monster_ids must raise ValueError
    rather than silently returning a misleading all-clear outlook."""
    cid = outlook_campaign
    with pytest.raises(ValueError, match="no XP to evaluate"):
        server.encounter_outlook(cid)
