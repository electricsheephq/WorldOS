import pytest

import content
import server
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


def test_duplicate_location_id_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "locations": [{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}]})


def test_duplicate_npc_id_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "npcs": [{"id": "n", "name": "A"}, {"id": "n", "name": "B"}]})


def test_malformed_shape_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "locations": "not a list"})


def test_scenes_persisted_on_seed():
    # authored scenes must survive seeding so the DM can read them at play time
    c = content.seed_campaign(SYNTH)
    assert len(c.scenes) == 1 and c.scenes[0]["name"] == "Arrival"
    # non-dict scene entries are dropped defensively, not crashed on
    c2 = content.seed_campaign({"title": "X", "scenes": [{"id": "ok"}, "garbage", 5]})
    assert len(c2.scenes) == 1 and c2.scenes[0]["id"] == "ok"


def test_get_scene_surfaces_authored_guidance(tmp_path, monkeypatch):
    # The DM was playing blind: scenes (read_aloud/dm_notes) were dropped at seed.
    # get_scene now surfaces them — incl. the previously-buried Maerith heartbreak cue.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("embergloom-pact")["campaign_id"]
    out = server.get_scene(cid)  # defaults to the current (hub) location
    assert out["count"] >= 1
    assert out["scenes"][0].get("read_aloud") and out["scenes"][0].get("dm_notes")
    assert "Maerith" in " ".join(s.get("dm_notes", "") for s in out["scenes"])
    assert server.get_scene(cid, "loc-nonexistent")["count"] == 0


def test_start_world_seeds_living_world_and_lore_is_recallable(tmp_path, monkeypatch):
    # The generative pivot: a persistent WORLD bible seeds a navigable map + factions +
    # pullable NPCs + lore, and the lore is recallable so a generated story stays
    # consistent with canon (the anti-mush guardrail at world scale).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("sundered-reach")
    cid = out["campaign_id"]
    assert out["world"] == "The Sundered Reach"
    assert len(out["regions"]) == 6 and len(out["factions"]) == 4 and len(out["npc_roster"]) == 6
    assert out["starting_at"]["id"] == "loc-brassmoor"  # first starting_option
    assert server.get_state(cid)["location"]["name"] == "Brassmoor"
    # the seeded map is navigable (Brassmoor -> Tideway is wired in the bible)
    assert server.travel_to(cid, "loc-tideway")["to_name"] == "The Tideway"
    # world lore is recallable, tagged kind=lore
    hits = server.recall(cid, "Hollow War seal Pale Choir")["hits"]
    assert hits and any(h["kind"] == "lore" for h in hits)


def test_seed_world_rejects_unknown_start(tmp_path, monkeypatch):
    w = content.load_world_data("sundered-reach")
    with pytest.raises(ValueError, match="not a region"):
        content.seed_world(w, start_at="loc-nope")


def test_start_world_resume_continues_instead_of_orphaning(tmp_path, monkeypatch):
    # adversarial review #4: re-running start_world must not silently orphan a live world.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    first = server.start_world("sundered-reach")
    cid = first["campaign_id"]
    server.add_location(cid, "A Generated Hamlet", connections=[first["starting_at"]["id"]])
    # a fresh start warns that a campaign already exists in this world
    second = server.start_world("sundered-reach")
    assert second["campaign_id"] != cid
    assert any(e["id"] == cid for e in second.get("existing_campaigns", []))
    assert "resume_hint" in second
    # resume returns the SAME campaign with its grown state (the hamlet persists)
    resumed = server.start_world("sundered-reach", resume=cid)
    assert resumed["campaign_id"] == cid and resumed.get("resumed") is True
    assert any(r["name"] == "A Generated Hamlet" for r in resumed["regions"])
    # a bogus resume id falls through to a fresh start, no crash
    fresh = server.start_world("sundered-reach", resume="camp_nonexistent")
    assert fresh["campaign_id"] != cid and "resumed" not in fresh


def test_lookup_lore_returns_world_canon(tmp_path, monkeypatch):
    # the DM's on-demand "wiki": lookup_lore pulls ranked canon from the world's
    # lore corpus + reports the chronology (era), and is empty/safe off-world.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("sundered-reach")["campaign_id"]
    out = server.lookup_lore(cid, "Brassmoor capital of the Concord")
    assert out["corpus_pages"] >= 1 and out["era"]  # chronology surfaced
    assert out["hits"] and any("brassmoor" in (h["title"] + h["excerpt"]).lower() for h in out["hits"])
    # a campaign not started from a world seed returns empty, no crash
    blank = server.create_campaign("blank")["id"]
    assert server.lookup_lore(blank, "anything")["hits"] == []
