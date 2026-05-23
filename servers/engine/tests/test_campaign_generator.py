"""Multi-act campaign generator + arc/antagonist validation (P2.7)."""

import generator


def test_generate_campaign_is_schema_valid():
    adv = generator.generate_campaign(
        "The Sunless Pact", premise="A creeping dark", num_acts=3, level_range=(1, 5)
    )
    assert generator.validate_adventure(adv) == []  # generated skeleton validates clean
    assert len(adv["arcs"]) == 3
    assert adv["antagonist"]["hidden"] is True
    loc_ids = {loc["id"] for loc in adv["locations"]}
    assert "loc-hub" in loc_ids
    for arc in adv["arcs"]:  # every beat references a real location
        for beat in arc["beats"]:
            assert beat["location_id"] in loc_ids


def test_generate_campaign_escalating_level_bands():
    adv = generator.generate_campaign("X", num_acts=3, level_range=(1, 5))
    bands = [arc["level_range"] for arc in adv["arcs"]]
    assert bands[0][0] == 1 and bands[-1][1] == 5
    assert all(bands[i][0] <= bands[i + 1][0] for i in range(len(bands) - 1))


def test_validate_flags_bad_arc_and_antagonist():
    adv = {
        "title": "T",
        "locations": [{"id": "a", "name": "A"}],
        "arcs": [{"id": "arc1", "beats": [{"title": "b", "location_id": "ghost"}]}],
        "antagonist": {"voice_id": "not-a-voice"},
    }
    problems = generator.validate_adventure(adv)
    assert any("title" in p for p in problems)  # arc missing title
    assert any("ghost" in p for p in problems)  # beat -> dangling location
    assert any("antagonist is missing" in p for p in problems)  # no name
    assert any("unknown voice_id" in p for p in problems)


def test_existing_one_shot_scaffold_still_valid():
    assert generator.validate_adventure(generator.scaffold_adventure("S")) == []
