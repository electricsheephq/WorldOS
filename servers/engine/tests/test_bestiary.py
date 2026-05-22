"""Bestiary + spawn_monster + damage resistance/immunity/vulnerability (P2.1)."""

import pytest

import bestiary
import combat
import server
from models import Character


# --- bestiary stat blocks --------------------------------------------------


def test_bestiary_loads_full_creature_set():
    assert bestiary.count() >= 300


def test_stat_block_known_creature():
    sb = bestiary.stat_block("wolf")  # case-insensitive
    assert sb is not None
    assert sb["ac"] == 12 and sb["hp"] == 11 and sb["cr"] == "1/4"
    assert sb["abilities"]["dex"] == 15
    assert sb["xp"] == 50  # derived from CR 1/4 (the 2024 dump omits XP)
    assert len(sb["actions"]) >= 1


def test_stat_block_unknown_returns_none():
    assert bestiary.stat_block("nonexistent beast") is None


def test_find_substring():
    assert "Goblin Warrior" in bestiary.find("goblin")


# --- damage resistance / immunity / vulnerability --------------------------


def _mob(**kw) -> Character:
    return Character(name="M", kind="monster", max_hp=20, current_hp=20, **kw)


def test_resistance_halves_damage():
    out = combat.apply_damage(_mob(damage_resistances=["fire"]), 10, damage_type="fire")
    assert out["current_hp"] == 15  # 10 -> 5


def test_immunity_zeroes_damage():
    out = combat.apply_damage(_mob(damage_immunities=["poison"]), 10, damage_type="poison")
    assert out["current_hp"] == 20


def test_vulnerability_doubles_damage():
    out = combat.apply_damage(_mob(damage_vulnerabilities=["cold"]), 5, damage_type="cold")
    assert out["current_hp"] == 10  # 5 -> 10


def test_unmatched_type_takes_full_damage():
    out = combat.apply_damage(_mob(damage_resistances=["fire"]), 10, damage_type="slashing")
    assert out["current_hp"] == 10


def test_save_halving_then_resistance_order():
    # SRD: resistance applies after other modifiers. 12 -(save)-> 6 -(resist)-> 3.
    out = combat.apply_damage(_mob(damage_resistances=["fire"]), 12, half=True, damage_type="fire")
    assert out["current_hp"] == 17


# --- spawn_monster + NPC stat-block seeding --------------------------------


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def test_spawn_monster_creates_combat_ready(cid):
    out = server.spawn_monster(cid, "Wolf")
    assert len(out["spawned"]) == 1 and out["ac"] == 12 and out["hp"] == 11
    sheet = server.get_character(cid, out["spawned"][0]["id"])
    assert sheet["kind"] == "monster" and sheet["armor_class"] == 12 and sheet["current_hp"] == 11


def test_spawn_monster_count_numbered(cid):
    out = server.spawn_monster(cid, "Goblin Warrior", count=3)
    assert [s["name"] for s in out["spawned"]] == [
        "Goblin Warrior 1", "Goblin Warrior 2", "Goblin Warrior 3"
    ]


def test_spawn_monster_fuzzy_resolves_to_warrior(cid):
    out = server.spawn_monster(cid, "Goblin")  # 2024 SRD baseline -> 'Goblin Warrior'
    assert "spawned" in out and out["name"] == "Goblin Warrior"


def test_spawn_monster_truly_unknown_suggests(cid):
    out = server.spawn_monster(cid, "Florble the Nonexistent")
    assert "error" in out


def test_adventure_npcs_seeded_battle_ready(cid):
    """The [critical] fix: Grett/Quill carry real stats, so the DM fights THIS
    record instead of spawning a duplicate monster."""
    grett = server.get_character(cid, "grett")
    assert grett["max_hp"] == 21 and grett["armor_class"] == 17 and grett["current_hp"] == 21
    quill = server.get_character(cid, "quill")
    assert quill["max_hp"] == 7 and quill["armor_class"] == 15
