import content
from models import Campaign

SYNTH = {
    "title": "Test Delve",
    "premise": "A test.",
    "hook": "Go in.",
    "level_range": [1, 2],
    "locations": [
        {"id": "loc_a", "name": "Entrance", "description": "a door", "connections": ["loc_b"]},
        {"id": "loc_b", "name": "Hall", "description": "a hall", "connections": []},
    ],
    "npcs": [
        {"id": "npc_keeper", "name": "Old Keeper", "personality": "gruff", "voice_id": "npc-elder", "attitude": "wary"}
    ],
    "scenes": [{"id": "s1", "name": "Arrival", "type": "exploration"}],
}


def test_seed_campaign_synthetic():
    c = content.seed_campaign(SYNTH)
    assert isinstance(c, Campaign)
    assert c.title == "Test Delve"
    assert c.current_location_id == "loc_a"
    assert "loc_b" in c.locations
    keeper = c.characters["npc_keeper"]
    assert keeper.kind == "npc" and keeper.voice_id == "npc-elder"
    assert len(c.quests) == 1


def test_load_real_cellar_rats():
    adv = content.load_adventure_data("cellar-rats")
    assert adv["id"] == "cellar-rats"
    c = content.seed_campaign(adv)
    assert "quill" in c.characters
    assert c.characters["quill"].voice_id == "npc-rogue"
    assert c.characters["brakka"].kind == "npc"
    assert "loc-taproom" in c.locations
    assert c.current_location_id is not None
    assert len(c.quests) == 1
