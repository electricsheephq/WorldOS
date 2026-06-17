"""Engine tests for the read-only multi-level LEVEL-UP ROADMAP (#882 build-optimizer).

`server.level_roadmap` is a PURE projection of the SRD progression tables from the PC's
current level + 1 through `through_level`. These assert it returns the right upcoming
levels (with the Fighter ASI levels flagged + features present), is byte-for-byte
non-mutating, and is GUARDED — a PC at the cap and a non-class entity both return [].
"""

import pytest

import server
import srd_tables
import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    yield


def _campaign_snapshot(campaign_id: str) -> dict:
    return store.load_campaign(campaign_id).model_dump(mode="json")


# --- pure helper (srd_tables.level_roadmap) ---------------------------------------


def test_pure_helper_projects_fighter_5_through_8_with_asi_flags():
    roadmap = srd_tables.level_roadmap("fighter", 5, through_level=8)
    levels = [row["level"] for row in roadmap]
    assert levels == [6, 7, 8]  # current 5 -> next is 6, capped at 8
    by_level = {row["level"]: row for row in roadmap}
    # Fighter bonus-ASI levels: 6 and 8 are ASI/feat; 7 is not (SRD).
    assert by_level[6]["is_asi_or_feat"] is True
    assert by_level[8]["is_asi_or_feat"] is True
    assert by_level[7]["is_asi_or_feat"] is False
    # Proficiency bonus tracks total level (still +3 at 6-8).
    assert all(row["prof_bonus"] == 3 for row in roadmap)
    # Every row carries its own class level and the projected class name.
    assert by_level[6]["class_level"] == 6
    assert by_level[6]["class_name"] == "fighter"


def test_pure_helper_features_match_srd_features_at():
    # Each row's features come STRAIGHT from features_at — never fabricated.
    roadmap = srd_tables.level_roadmap("wizard", 4, through_level=6)
    for row in roadmap:
        expected = {f["name"] for f in srd_tables.features_at("wizard", row["class_level"])}
        assert {f["name"] for f in row["features"]} == expected


def test_pure_helper_caster_spell_slot_note_present():
    # A wizard leveling 4 -> 5 opens L3 slots; the projection surfaces that note.
    roadmap = srd_tables.level_roadmap("wizard", 4, through_level=5)
    row = roadmap[0]
    assert row["level"] == 5
    assert "spell_slots_note" in row and "L3" in row["spell_slots_note"]


def test_pure_helper_unknown_class_returns_empty():
    assert srd_tables.level_roadmap("definitely-not-a-class", 3) == []


def test_pure_helper_at_cap_returns_empty():
    assert srd_tables.level_roadmap("fighter", 20, through_level=20) == []
    assert srd_tables.level_roadmap("fighter", 5, through_level=5) == []


def test_pure_helper_subclass_features_only_when_chosen():
    # Without a subclass, no subclass_features key appears on any row.
    plain = srd_tables.level_roadmap("cleric", 2, through_level=8)
    assert all("subclass_features" not in row for row in plain)
    # With a chosen subclass, the archetype features land on their SRD levels.
    chosen = srd_tables.level_roadmap("cleric", 2, subclass="Life Domain", through_level=8)
    assert any("subclass_features" in row for row in chosen)


# --- @mcp.tool() wrapper (server.level_roadmap) -----------------------------------


def test_level_roadmap_fighter_5_flags_asi_levels_and_lists_features_without_mutating():
    cid = server.create_campaign("Roadmap fighter")["id"]
    fid = server.create_character(
        cid,
        "Ren",
        kind="player",
        class_name="Fighter",
        level=5,
        apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 14, "constitution": 12},
    )["id"]
    before = _campaign_snapshot(cid)

    out = server.level_roadmap(cid, fid, through_level=20)

    assert out["character_id"] == fid
    assert out["primary_class"] == "fighter"
    assert out["multiclass"] is False
    assert out["from"] == {"total_level": 5, "class_level": 5}
    roadmap = out["roadmap"]
    # Projects 6..20 (15 upcoming levels).
    assert [row["level"] for row in roadmap] == list(range(6, 21))
    by_level = {row["level"]: row for row in roadmap}
    # Fighter ASI/feat levels include 6, 8, 12, 14, 16, 19 (the bonus martial ASIs).
    for asi_lvl in (6, 8, 12, 14, 16, 19):
        assert by_level[asi_lvl]["is_asi_or_feat"] is True, asi_lvl
    # And a non-ASI level is NOT flagged.
    assert by_level[7]["is_asi_or_feat"] is False
    # Proficiency bonus rises with total level: +3 at 6-8, +4 at 9-12, … +6 at 17-20.
    assert by_level[8]["prof_bonus"] == 3
    assert by_level[9]["prof_bonus"] == 4
    assert by_level[17]["prof_bonus"] == 6
    # A real class feature is projected (Extra Attack (2) lands at Fighter 11 in SRD).
    assert any(
        f["name"]
        for row in roadmap
        for f in row["features"]
    )
    # Read-only: the campaign snapshot is byte-identical.
    assert _campaign_snapshot(cid) == before


def test_level_roadmap_at_max_level_returns_empty_guarded():
    cid = server.create_campaign("Roadmap maxed")["id"]
    fid = server.create_character(
        cid,
        "Capstone",
        kind="player",
        class_name="Fighter",
        level=20,
        apply_srd_defaults=True,
        abilities={"strength": 18, "constitution": 14},
    )["id"]
    before = _campaign_snapshot(cid)

    out = server.level_roadmap(cid, fid, through_level=20)

    assert out["roadmap"] == []
    assert out["from"]["total_level"] == 20
    assert _campaign_snapshot(cid) == before


def test_level_roadmap_respects_through_level_window():
    cid = server.create_campaign("Roadmap window")["id"]
    fid = server.create_character(
        cid, "Ren", kind="player", class_name="Fighter", level=5, apply_srd_defaults=True,
    )["id"]

    out = server.level_roadmap(cid, fid, through_level=8)

    assert [row["level"] for row in out["roadmap"]] == [6, 7, 8]


def test_level_roadmap_multiclass_projects_primary_and_flags_multiclass():
    cid = server.create_campaign("Roadmap multiclass")["id"]
    server.set_house_rules(cid, {"multiclass_allowed": True})
    # Build a Fighter 4 then level it once into Wizard so the PC is Fighter 4 / Wizard 1.
    fid = server.create_character(
        cid,
        "Eldritch Knight Wannabe",
        kind="player",
        class_name="Fighter",
        level=4,
        apply_srd_defaults=True,
        abilities={"strength": 14, "intelligence": 16, "constitution": 12},
    )["id"]
    server.level_up(cid, fid, "Wizard")
    before = _campaign_snapshot(cid)

    out = server.level_roadmap(cid, fid, through_level=20)

    assert out["multiclass"] is True
    # The primary (most-levels) class is Fighter (4 vs Wizard 1) — projected from clvl 5.
    assert out["primary_class"] == "fighter"
    assert out["from"]["class_level"] == 4
    assert out["roadmap"][0]["class_level"] == 5
    # Total level continues from 5 (Fighter 4 + Wizard 1).
    assert out["roadmap"][0]["level"] == 6
    assert _campaign_snapshot(cid) == before


def test_level_roadmap_non_class_entity_returns_empty_guarded():
    cid = server.create_campaign("Roadmap monster")["id"]
    # A stat-block NPC created without a class track has no progression to project.
    nid = server.create_character(cid, "Goblin", kind="npc")["id"]
    out = server.level_roadmap(cid, nid, through_level=20)
    assert out["primary_class"] is None
    assert out["roadmap"] == []
