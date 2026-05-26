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


def test_ghoul_stat_block_has_multi_component_bite_and_two_bite_multiattack():
    """Ground-truth guard for #210/#211: the Ghoul Bite deals piercing PLUS necrotic in
    one strike, and its Multiattack text is 'two Bite attacks' (the Claw, with the
    paralysis rider, is a SEPARATE action). If the SRD data ever changes, the
    engine-side parse/compose tests would silently pass on different inputs — this
    pins the source data they rely on."""
    sb = bestiary.stat_block("Ghoul")
    assert sb is not None
    actions = {a["name"]: a for a in sb["actions"]}
    assert "Bite" in actions and "Claw" in actions and "Multiattack" in actions
    bite_desc = actions["Bite"]["desc"].lower()
    assert "piercing" in bite_desc and "necrotic" in bite_desc and "plus" in bite_desc
    assert "two bite attacks" in actions["Multiattack"]["desc"].lower()
    # The Claw is the paralysis-rider action and is NOT the Bite's necrotic component.
    claw_desc = actions["Claw"]["desc"].lower()
    assert "paralyzed" in claw_desc and "constitution saving throw" in claw_desc


def test_find_substring():
    assert "Goblin Warrior" in bestiary.find("goblin")


def test_pack_precedence_srd_wins_and_pack_adds(tmp_path, monkeypatch):
    """A content pack (e.g. ingested BFRPG) never overrides an SRD creature of the
    same name — srd524 is first-wins — but it DOES contribute its own new creatures,
    with actions pk-namespaced so a colliding fixture pk can't cross-attribute."""
    import json as _json
    pack = tmp_path / "fakepack"
    pack.mkdir()
    (pack / "Creature.json").write_text(_json.dumps([
        # COLLISION: a bogus Wolf (pk reused from SRD) — must lose to canonical SRD Wolf
        {"model": "x.creature", "pk": 1, "fields": {"name": "Wolf", "hit_points": 999, "armor_class": 99}},
        # a brand-new pack creature — must be added
        {"model": "x.creature", "pk": 2, "fields": {"name": "Fizzbin Horror", "hit_points": 42, "armor_class": 13}},
    ]))
    (pack / "CreatureAction.json").write_text(_json.dumps([
        {"model": "x.action", "pk": 1, "fields": {"parent": 2, "name": "Gnash", "desc": "bites"}},
    ]))

    monkeypatch.setattr(bestiary, "_dirs", lambda: [bestiary._PRIMARY, pack])
    bestiary._index.cache_clear()
    bestiary._actions_by_source_parent.cache_clear()
    try:
        # srd524 Wolf wins the name collision — not the pack's bogus 999 HP
        assert bestiary.stat_block("Wolf")["hp"] == 11
        # the pack's own new creature is available, with its own pk-namespaced action
        horror = bestiary.stat_block("Fizzbin Horror")
        assert horror is not None and horror["hp"] == 42
        assert any(a["name"] == "Gnash" for a in horror["actions"])
        # find() lists each name once (deduped against the first-wins index)
        assert bestiary.find("wolf", limit=100).count("Wolf") == 1
        # count includes the pack's net-new creature
        assert bestiary.count() >= 301
    finally:
        bestiary._index.cache_clear()
        bestiary._actions_by_source_parent.cache_clear()


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


def test_resolve_token_prefix_near_miss():
    """QA finding (illithid): spawn_monster('Cult Fanatic') returned no match though the SRD
    ships 'Cultist Fanatic'. resolve now falls back to a unique token-prefix match — but stays
    conservative (a genuine non-match still returns None)."""
    import bestiary
    assert bestiary.resolve("Cult Fanatic") == "Cultist Fanatic"
    assert bestiary.resolve("Xyzzy Nonsense") is None      # all tokens must land -> no false match
    # existing exact / <name> Warrior paths are unchanged
    assert bestiary.resolve("Aboleth") == "Aboleth"
    assert bestiary.resolve("Goblin") == "Goblin Warrior"
