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


def _authored_pack(tmp_path, monsters):
    import json as _json

    pack = tmp_path / "mythic-workshop"
    pack.mkdir()
    (pack / "pack.json").write_text(_json.dumps({
        "id": "mythic-workshop",
        "title": "Mythic Workshop Test Pack",
        "license": {"name": "CC-BY-4.0"},
        "source": {"title": "Unit test fixture"},
        "provenance": {"author": "ClawDnD tests", "method": "hand-authored"},
        "monsters": monsters,
    }))
    return tmp_path


def test_authored_monster_pack_requires_record_metadata(tmp_path, monkeypatch):
    root = _authored_pack(tmp_path, [{
        "name": "Lantern Mireling",
        "armor_class": 13,
        "hit_points": 19,
        "abilities": {"str": 8, "dex": 14, "con": 12, "int": 10, "wis": 13, "cha": 9},
        "license": {"name": "CC-BY-4.0"},
        "source": {"title": "Unit test fixture"},
        # missing provenance on the record: pack metadata alone is not enough
    }])
    monkeypatch.setattr(bestiary, "_AUTHORED_ROOT", root)
    bestiary._authored_entries.cache_clear()
    try:
        errors = bestiary.authored_validation_errors()
        assert any("Lantern Mireling" in e and "provenance" in e for e in errors)
        assert bestiary.stat_block("Lantern Mireling") is None
    finally:
        bestiary._authored_entries.cache_clear()
        bestiary._index.cache_clear()


def test_authored_monster_pack_adds_metadata_but_never_overrides_srd(tmp_path, monkeypatch):
    root = _authored_pack(tmp_path, [
        {
            "name": "Wolf",
            "armor_class": 99,
            "hit_points": 999,
            "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "license": {"name": "CC-BY-4.0"},
            "source": {"title": "Unit test fixture"},
            "provenance": {"author": "ClawDnD tests", "method": "hand-authored"},
        },
        {
            "name": "Lantern Mireling",
            "size": "Small",
            "type": "fey",
            "armor_class": 13,
            "hit_points": 19,
            "hit_dice": "3d6+9",
            "challenge_rating": "1/2",
            "experience_points": 100,
            "abilities": {"str": 8, "dex": 14, "con": 12, "int": 10, "wis": 13, "cha": 9},
            "actions": [{"name": "Glimmer Claw", "desc": "A player-safe action summary."}],
            "license": {"name": "CC-BY-4.0"},
            "source": {"title": "Unit test fixture"},
            "provenance": {"author": "ClawDnD tests", "method": "hand-authored"},
        },
    ])
    monkeypatch.setattr(bestiary, "_AUTHORED_ROOT", root)
    bestiary._authored_entries.cache_clear()
    bestiary._index.cache_clear()
    try:
        assert bestiary.stat_block("Wolf")["hp"] == 11
        errors = bestiary.authored_validation_errors()
        assert any("Wolf" in e and "overrides SRD" in e for e in errors)

        mireling = bestiary.stat_block("Lantern Mireling")
        assert mireling["content_origin"] == "authored"
        assert mireling["license"]["name"] == "CC-BY-4.0"
        assert mireling["source"]["title"] == "Unit test fixture"
        assert mireling["provenance"]["method"] == "hand-authored"
        assert "Lantern Mireling" in bestiary.find("mire")
    finally:
        bestiary._authored_entries.cache_clear()
        bestiary._index.cache_clear()


def test_player_bestiary_projection_is_safe_and_read_only(tmp_path, monkeypatch):
    root = _authored_pack(tmp_path, [{
        "name": "Lantern Mireling",
        "size": "Small",
        "type": "fey",
        "armor_class": 13,
        "hit_points": 19,
        "challenge_rating": "1/2",
        "abilities": {"str": 8, "dex": 14, "con": 12, "int": 10, "wis": 13, "cha": 9},
        "actions": [{"name": "Glimmer Claw", "desc": "Private tactics stay out of projection."}],
        "private_notes": "ambushes wounded PCs",
        "license": {"name": "CC-BY-4.0"},
        "source": {"title": "Unit test fixture"},
        "provenance": {"author": "ClawDnD tests", "method": "hand-authored"},
    }])
    monkeypatch.setattr(bestiary, "_AUTHORED_ROOT", root)
    bestiary._authored_entries.cache_clear()
    bestiary._index.cache_clear()
    try:
        preview = bestiary.player_bestiary_preview("Lantern Mireling")
        assert preview == {
            "name": "Lantern Mireling",
            "size": "Small",
            "type": "fey",
            "cr": "1/2",
            "content_origin": "authored",
            "source": {"title": "Unit test fixture"},
            "license": {"name": "CC-BY-4.0"},
            "provenance": {"author": "ClawDnD tests", "method": "hand-authored"},
            "known_actions": ["Glimmer Claw"],
        }
        assert "hp" not in preview and "ac" not in preview
        assert "private" not in repr(preview).lower()
    finally:
        bestiary._authored_entries.cache_clear()
        bestiary._index.cache_clear()


def test_server_list_bestiary_defaults_to_player_safe_projection():
    out = server.list_bestiary("wolf", limit=20)
    wolf = next(item for item in out["items"] if item["name"] == "Wolf")
    assert wolf["content_origin"] == "srd"
    assert "known_actions" in wolf
    assert "hp" not in wolf and "ac" not in wolf and "abilities" not in wolf


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
