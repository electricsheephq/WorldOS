import copy

import content
import generator


def test_real_cellar_rats_validates_clean():
    adv = content.load_adventure_data("cellar-rats")
    assert generator.validate_adventure(adv) == []


def test_broken_duplicate_location_id_reported():
    adv = content.load_adventure_data("cellar-rats")
    broken = copy.deepcopy(adv)
    # Two locations now share an id -> a problem must be reported.
    broken["locations"][1]["id"] = broken["locations"][0]["id"]
    problems = generator.validate_adventure(broken)
    assert problems
    assert any("duplicate location id" in p for p in problems)


def test_broken_bad_voice_id_reported():
    adv = content.load_adventure_data("cellar-rats")
    broken = copy.deepcopy(adv)
    broken["npcs"][0]["voice_id"] = "npc-not-a-real-voice"
    problems = generator.validate_adventure(broken)
    assert problems
    assert any("voice_id" in p for p in problems)


def test_broken_scene_points_at_missing_location_reported():
    adv = content.load_adventure_data("cellar-rats")
    broken = copy.deepcopy(adv)
    broken["scenes"][0]["location_id"] = "loc-does-not-exist"
    problems = generator.validate_adventure(broken)
    assert problems
    assert any("unknown location_id" in p for p in problems)


def test_missing_title_reported():
    problems = generator.validate_adventure({"locations": [], "npcs": [], "scenes": []})
    assert any("title" in p for p in problems)


def test_scaffold_output_is_valid():
    adv = generator.scaffold_adventure(
        "The Hollow Bell", premise="Something tolls beneath the abbey.", level_range=(2, 4)
    )
    assert adv["title"] == "The Hollow Bell"
    assert adv["level_range"] == [2, 4]
    assert generator.validate_adventure(adv) == []


def test_scaffold_defaults_are_valid():
    # Defaults (empty premise, level_range=(1,2)) must also produce a valid dict.
    adv = generator.scaffold_adventure("Bare Bones")
    assert adv["level_range"] == [1, 2]
    assert generator.validate_adventure(adv) == []


def test_scaffold_filled_in_round_trips():
    # A scaffold the DM has filled in with real content stays valid.
    adv = generator.scaffold_adventure("Filled In")
    adv["locations"].append({"id": "loc-gate", "name": "The Gate", "description": "An arch."})
    adv["npcs"].append(
        {"id": "npc-warden", "name": "Warden Mol", "voice_id": "npc-elder", "personality": "stern"}
    )
    adv["scenes"].append({"id": "s1", "name": "Arrival", "type": "social", "location_id": "loc-gate"})
    assert generator.validate_adventure(adv) == []
