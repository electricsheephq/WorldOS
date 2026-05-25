"""S2.3 — spatial walk-time world, structured NPC identity, and auto-XP on end_combat.

All additive: empty fields = today's behavior. These guard the new fables-style context
shape (region / who's-here / walk-times to nearby places) and the #30 auto-XP feature.
"""
import pytest

import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("S2.3 Test")["id"]


def test_look_around_surfaces_region_walktimes_and_nearby_cast(cid):
    # two connected places + a region + a walk-time, with an NPC anchored at the far one
    tavern = server.add_location(cid, "The Cracked Flagon", "A dim tavern.", region="Lower City")["id"]
    street = server.add_location(cid, "Wyrm's Crossing", "A crowded span.", connections=[tavern])["id"]
    server.add_location(cid, "", location_id=tavern, travel_times={street: 5})  # walk-time FROM the current loc
    server.create_character(cid, "Rolph", kind="npc", location_id=street, add_to_party=False)

    la = server.look_around(cid)
    assert la["location"]["region"] == "Lower City"
    ex = {e["id"]: e for e in la["exits"]}
    assert street in ex and ex[street]["walk_minutes"] == 5
    assert "Rolph" in ex[street]["characters"]  # nearby, out of speaking distance


def test_character_has_structured_identity_fields(cid):
    nid = server.create_character(cid, "Shadowheart", kind="npc", add_to_party=False)["id"]
    server.update_character(cid, nid, {
        "appearance": "dark bob, silver-edged armor",
        "mannerisms": "touches her wolf pendant when uneasy",
        "backstory": "a cleric of a goddess she's begun to doubt",
    })
    sheet = server.get_character(cid, nid)
    assert sheet["appearance"].startswith("dark bob")
    assert "pendant" in sheet["mannerisms"]
    assert "doubt" in sheet["backstory"]


def test_spawn_monster_records_xp_value(cid):
    res = server.spawn_monster(cid, "Goblin Warrior")
    assert res.get("xp_each", 0) > 0
    mid = res["spawned"][0]["id"]
    assert server.get_character(cid, mid)["xp_value"] == res["xp_each"]


def test_end_combat_auto_awards_xp_in_xp_mode(cid):
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12)["id"]
    res = server.spawn_monster(cid, "Goblin Warrior")
    mid, xp = res["spawned"][0]["id"], res["xp_each"]
    server.start_combat(cid, [hero, mid])
    kill = server.apply_damage(cid, mid, 999)  # massive damage -> instant death
    # Kill-time award fires immediately at apply_damage (hardened behavior — robust to
    # DM sequencing). The XP lands in kill_xp, not deferred to end_combat.
    assert kill.get("kill_xp", {}).get("xp_awarded") == xp
    out = server.end_combat(cid)
    assert out["active"] is False
    # Backstop sweep: xp_value already zeroed by kill-time award → no double-award.
    assert out.get("xp_awarded", 0) == 0
    # The hero still has the XP (kill-time award did the work).
    assert server.get_character(cid, hero)["xp"] == xp


def test_load_canon_character_pulls_real_identity(cid):
    # the BG canon roster is ingested (content/worlds/baldurs-gate/characters/*.json, S2.5)
    c = store.load_campaign(cid); c.world_id = "baldurs-gate"; store.save_campaign(c)
    names = {a["name"] for a in server.list_canon_characters(cid)["available"]}
    assert {"Shadowheart", "Astarion"} <= names
    res = server.load_canon_character(cid, "Shadowheart", kind="companion", add_to_party=True)
    assert "error" not in res, res
    sheet = server.get_character(cid, res["id"])
    assert sheet["race"] == "Half-elf" and sheet["classes"][0]["name"] == "Cleric"
    assert sheet["appearance"] and sheet["backstory"]  # the prose the DM voices from
    assert res["id"] in store.load_campaign(cid).party
    again = server.load_canon_character(cid, "Shadowheart")  # already present -> idempotent success
    assert again.get("already_present") and again.get("id") == res["id"] and "error" not in again


def test_end_combat_milestone_mode_awards_nothing(cid):
    c = store.load_campaign(cid); c.leveling_mode = "milestone"; store.save_campaign(c)  # no tool yet
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12)["id"]
    res = server.spawn_monster(cid, "Goblin Warrior")
    mid = res["spawned"][0]["id"]
    server.start_combat(cid, [hero, mid])
    server.apply_damage(cid, mid, 999)
    out = server.end_combat(cid)
    assert out["active"] is False
    assert "xp_awarded" not in out
    assert server.get_character(cid, hero)["xp"] == 0
