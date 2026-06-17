"""End-to-end mechanical playthrough of the 'Cellar Rats' adventure.

Drives the real engine tools through the plan's Definition-of-Done beats:
load the adventure -> create the player + voiced companion -> exploration check
-> social attitude shift -> combat 1 (wolves) -> downed-and-healed beat -> short
rest -> combat 2 (goblins) -> loot -> persist + reload across sessions.

This proves the engine + content loader compose into a runnable session. Voice
is validated separately (it lives in a different server/venv).
"""

import pytest

import server
import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    yield


def test_cellar_rats_full_playthrough():
    # 1. Load the adventure -> a campaign seeded with NPCs + locations + the hook quest
    start = server.start_adventure("cellar-rats")
    cid = start["campaign_id"]
    assert start["scene_count"] == 5
    assert any(n["id"] == "quill" for n in start["npcs"])

    # 2. The companion (Vesper) is auto-seeded into the party by start_adventure;
    #    create the player hero alongside her.
    party0 = server.get_state(cid)["party"]
    comp = next(p["id"] for p in party0 if p["kind"] == "companion")
    assert any(p["name"] == "Vesper" for p in party0)
    hero = server.create_character(
        cid, "Aldric", kind="player", class_name="Fighter", max_hp=12, armor_class=16,
        voice_id="narrator-dm", abilities={"strength": 16, "dexterity": 14, "constitution": 14},
    )["id"]
    assert len(server.get_state(cid)["party"]) == 2  # Vesper + the hero

    # 3. EXPLORATION: a Perception check for the goblin alarm-cord (deterministic via tool)
    perception = server.roll("1d20+2", reason="Perception vs alarm-cord (DC 13)")
    assert 3 <= perception["total"] <= 22

    # 4. SOCIAL: befriend Quill -> her attitude shifts hostile -> helpful
    server.update_character(cid, "quill", {"attitude": "helpful"})
    assert server.get_character(cid, "quill")["attitude"] == "helpful"

    # 5. COMBAT 1: two scavenger-hounds (Wolves AC 13 / HP 11)
    w1 = server.create_character(cid, "Scavenger-hound", kind="monster", max_hp=11, armor_class=13,
                                 abilities={"dexterity": 15})["id"]
    w2 = server.create_character(cid, "Scavenger-hound II", kind="monster", max_hp=11, armor_class=13,
                                 abilities={"dexterity": 15})["id"]
    view = server.start_combat(cid, [hero, comp, w1, w2])
    assert view["active"] and len(view["order"]) == 4 and view["round"] == 1
    atk = server.attack(cid, hero, w1, attack_bonus=5, damage_dice="1d8+3", damage_type="slashing")
    assert isinstance(atk["hit"], bool)
    # the party fells both hounds; "defeated" = dropped to 0 HP (dead or downed,
    # depending on whether the prior attack already bloodied w1 — both end at 0)
    server.apply_damage(cid, w1, 100)
    server.apply_damage(cid, w2, 100)
    assert server.get_character(cid, w1)["current_hp"] == 0
    assert server.get_character(cid, w2)["current_hp"] == 0

    # downed-and-healed beat: hero drops to exactly 0 (dying, not dead), companion revives
    downed = server.apply_damage(cid, hero, 12)
    assert downed["dying"] is True and downed["dead"] is False
    revived = server.apply_healing(cid, hero, 8)
    assert revived["revived"] is True and server.get_character(cid, hero)["current_hp"] == 8
    server.end_combat(cid)

    # 6. SHORT REST: top the hero back up
    server.set_hp(cid, hero, 12)
    assert server.get_character(cid, hero)["current_hp"] == 12

    # 7. COMBAT 2: Grett + crew (Goblins AC 15 / HP 7)
    goblins = [
        server.create_character(cid, f"Goblin {i}", kind="monster", max_hp=7, armor_class=15,
                                abilities={"dexterity": 14})["id"]
        for i in range(3)
    ]
    server.start_combat(cid, [hero, comp] + goblins)
    for g in goblins:
        server.apply_damage(cid, g, 50)
    assert all(server.get_character(cid, g)["current_hp"] == 0 for g in goblins)
    server.end_combat(cid)

    # 8. LOOT: reward gp + a Potion of Healing
    server.update_character(
        cid, hero, {"currency": {"gp": 35}, "inventory": [{"name": "Potion of Healing", "quantity": 1}]}
    )
    sheet = server.get_character(cid, hero)
    assert sheet["currency"]["gp"] == 35
    assert any(i["name"] == "Potion of Healing" for i in sheet["inventory"])

    # 9. PERSIST + RECAP: reload from disk; state intact across sessions
    reloaded = store.load_campaign(cid)
    assert reloaded is not None
    assert reloaded.characters[hero].currency.gp == 35
    assert reloaded.characters["quill"].attitude == "helpful"
    assert reloaded.combat.active is False
