"""Tests for the Kingmaker-style wandering-encounter system.

Two layers, mirroring the repo's split:
  * PURE — `wander.encounter_chance` / `roll_encounter` / `pick_encounter` exercised
    directly (no campaign, no I/O), like test_encounter.py;
  * INTEGRATION — the engine seams (`roll_wandering_encounter`, a forced-hit
    `travel_to`, a camp `long_rest`) staging real monster Characters sized to the
    party and returning the `wandering_encounter` payload, like test_travel.py.
"""

import random

import pytest

import encounter
import server
import store
import wander
from models import Campaign, HouseRules, Location


# =========================================================================
# PURE: encounter_chance
# =========================================================================


def test_chance_default_for_empty_and_unknown_region():
    assert wander.encounter_chance("") == wander.BASE_RATE
    # an unmatched region keyword falls back to the base rate, never errors
    assert wander.encounter_chance("Floating Sky-Citadel of Nowhere") == wander.BASE_RATE


def test_chance_region_keyword_substring_match():
    # keyword is matched as a case-insensitive SUBSTRING of the region string
    assert wander.encounter_chance("a sleepy Town square") == wander.REGION_RATES["town"]
    assert wander.encounter_chance("The Black Bog") == wander.REGION_RATES["bog"]


def test_chance_dangerous_region_higher_than_safe_region():
    safe = wander.encounter_chance("the warded Sanctuary")
    wild = wander.encounter_chance("the Cursed Fen")
    assert safe < wander.BASE_RATE < wild


def test_chance_camouflage_modifier_lowers_rate():
    base = wander.encounter_chance("bog")
    hidden = wander.encounter_chance("bog", {"camouflage": True})
    assert hidden < base
    assert hidden == pytest.approx(base + wander._MODIFIER_DELTAS["camouflage"])


def test_chance_dangerous_modifier_raises_rate():
    base = wander.encounter_chance("town")
    hot = wander.encounter_chance("town", {"dangerous": True})
    assert hot > base


def test_chance_numeric_modifier_applies_raw_delta():
    base = wander.encounter_chance("road")
    assert wander.encounter_chance("road", {"bonus": -0.1}) == pytest.approx(base - 0.1)


def test_chance_unknown_string_flag_is_noop():
    base = wander.encounter_chance("road")
    assert wander.encounter_chance("road", {"totally_made_up": True}) == base


def test_chance_clamped_to_unit_interval():
    assert wander.encounter_chance("safe", {"well_hidden": True, "safe_haven": True}) == 0.0
    assert wander.encounter_chance("cursed", {"dangerous": True, "bonus": 0.9}) == 1.0


# =========================================================================
# PURE: roll_encounter
# =========================================================================


def test_roll_is_deterministic_under_seeded_rng():
    seq_a = [wander.roll_encounter("swamp", rng=random.Random(42)) for _ in "xxxxx"]
    seq_b = [wander.roll_encounter("swamp", rng=random.Random(42)) for _ in "xxxxx"]
    # same seed each time -> identical first draw
    assert seq_a == seq_b
    # and a single advancing rng is reproducible across two equal seeds
    r1, r2 = random.Random(7), random.Random(7)
    assert [wander.roll_encounter("forest", rng=r1) for _ in range(10)] == [
        wander.roll_encounter("forest", rng=r2) for _ in range(10)
    ]


def test_roll_zero_chance_never_fires_and_full_chance_always_fires():
    assert wander.roll_encounter("", {"bonus": -1.0}, rng=random.Random(0)) is False
    assert wander.roll_encounter("", {"bonus": 1.0}, rng=random.Random(0)) is True


# =========================================================================
# PURE: pick_encounter
# =========================================================================


_DIFF_ORDER = {d: i for i, d in enumerate(("trivial",) + encounter.DIFFICULTIES)}


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard", "deadly"])
def test_pick_sizes_group_to_target_difficulty(difficulty):
    party = [3, 3, 3, 3]
    specs = wander.pick_encounter(party, "forest", target_difficulty=difficulty, rng=random.Random(7))
    assert len(specs) == 1
    spec = specs[0]
    # the chosen group's SRD difficulty band meets (or, only at the count cap, approaches)
    # the requested target — sized via encounter.py, not hand-waved
    band = encounter.encounter_difficulty(party, [spec["xp_each"]] * spec["count"])
    assert _DIFF_ORDER[band] >= _DIFF_ORDER[difficulty] or spec["count"] == 12
    assert spec["count"] >= 1
    assert spec["xp_each"] > 0


def test_pick_harder_target_needs_at_least_as_many_foes():
    party = [4, 4, 4]
    easy = wander.pick_encounter(party, "forest", target_difficulty="easy", rng=random.Random(1))[0]
    deadly = wander.pick_encounter(party, "forest", target_difficulty="deadly", rng=random.Random(1))[0]
    # same seed -> same creature kind; a deadlier target needs more of them
    assert easy["name"] == deadly["name"]
    assert deadly["count"] >= easy["count"]


def test_pick_draws_from_region_pool():
    # an undead region only ever yields creatures from the undead pool
    undead_names = set()
    for seed in range(40):
        specs = wander.pick_encounter([5, 5, 5], "the Haunted Barrow", rng=random.Random(seed))
        undead_names.add(specs[0]["name"])
    # every drawn name resolves and belongs to the region's tier palette
    pool = {n for n in wander.REGION_CREATURES["undead"]}
    assert undead_names  # non-empty
    assert undead_names.issubset(pool)


def test_pick_is_deterministic_under_seed():
    a = wander.pick_encounter([5, 5, 5], "swamp", rng=random.Random(99))
    b = wander.pick_encounter([5, 5, 5], "swamp", rng=random.Random(99))
    assert a == b


def test_pick_empty_party_returns_empty():
    assert wander.pick_encounter([], "forest") == []


def test_pick_unknown_region_uses_wilderness_pool():
    names = {
        wander.pick_encounter([2, 2], "Nowhere-In-Particular", rng=random.Random(s))[0]["name"]
        for s in range(30)
    }
    assert names.issubset(set(wander.REGION_CREATURES["wilderness"]))


def test_region_creature_pools_all_resolve_in_bestiary():
    # every name in every pool must resolve with positive XP, or the picker can't size it
    import bestiary

    for tier, names in wander.REGION_CREATURES.items():
        for name in names:
            canonical = bestiary.resolve(name)
            assert canonical is not None, f"{name!r} in pool {tier!r} does not resolve"
            sb = bestiary.stat_block(canonical)
            assert sb and int(sb.get("xp") or 0) > 0, f"{name!r} ({tier}) has no XP"


# =========================================================================
# INTEGRATION: engine seams stage real monster Characters + the payload
# =========================================================================


@pytest.fixture
def party_camp(tmp_path, monkeypatch):
    """A campaign with a connected start->forest graph, a level-3 PC + companion in
    the party, both anchored at the start. Returns (cid, start_id, forest_id)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Wander Test")["id"]
    # both locations carry a recognized region ("greenwood" -> wood/forest pool)
    start = server.add_location(cid, "Trailhead", region="the Greenwood")["id"]  # first -> current
    forest = server.add_location(cid, "Mirkwood Edge", connections=[start], region="the Greenwood")["id"]
    pc = server.create_character(cid, "Renn", kind="player")["id"]
    comp = server.create_character(cid, "Cinder", kind="companion")["id"]
    # level them up so the party has a real XP budget (level 3 each)
    for who in (pc, comp):
        server.update_character(cid, who, {"classes": [{"name": "fighter", "level": 3}]})
    return cid, start, forest


def _monster_ids(c: Campaign) -> list[str]:
    return [i for i, ch in c.characters.items() if ch.kind == "monster"]


def test_roll_wandering_encounter_stages_sized_foes_and_payload(party_camp, monkeypatch):
    cid, start, _forest = party_camp
    # pin the TYPE to combat (a wandering encounter is now typed — most aren't fights),
    # but let the REAL foe-sizing path run so the band assertion below is genuine.
    monkeypatch.setattr(wander, "_weighted_choice", lambda *a, **k: "combat")
    out = server.roll_wandering_encounter(cid, difficulty="medium")
    # payload shape mirrors world_beats: a staged dict the DM narrates from
    assert out["staged"] is True
    assert out["region"] == "the Greenwood"
    assert out["difficulty"] == "medium"
    assert isinstance(out["surprise"], bool)
    assert out["foes"] and out["encounter_xp"] > 0
    foe = out["foes"][0]
    assert foe["count"] >= 1 and len(foe["ids"]) == foe["count"]

    # the foes are REAL monster Characters now IN the campaign, ready to start_combat...
    c = store.load_campaign(cid)
    monster_ids = _monster_ids(c)
    assert set(foe["ids"]).issubset(set(monster_ids))
    # ...anchored at the party's current location (so the local cast shows them)
    for mid in foe["ids"]:
        assert c.characters[mid].location_id == start
        assert c.characters[mid].max_hp >= 1 and c.characters[mid].xp_value > 0

    # and the staged group actually hits the requested band for the party's levels
    party_levels = [3, 3]
    xps = [foe["xp_each"]] * foe["count"]
    band = encounter.encounter_difficulty(party_levels, xps)
    assert _DIFF_ORDER[band] >= _DIFF_ORDER["medium"] or foe["count"] == 12


def test_roll_wandering_encounter_does_not_start_combat(party_camp):
    cid, _start, _forest = party_camp
    server.roll_wandering_encounter(cid)
    # staging never auto-fights — the DM opens combat
    assert server.get_state(cid)["in_combat"] is False


def test_forced_travel_leg_stages_encounter(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    # force the per-region roll to always hit, AND pin the type to combat (a wandering
    # encounter is now typed — most aren't fights), so the foe-anchoring assertion is
    # deterministic; the typed-pick variety is exercised in test_typed_encounters.py.
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    monkeypatch.setattr(wander, "_weighted_choice", lambda *a, **k: "combat")
    out = server.travel_to(cid, forest, advance_time=True)
    assert out["to"] == forest
    we = out["wandering_encounter"]
    assert we["staged"] is True and we["region"] == "the Greenwood"
    # foes anchored at the DESTINATION, present in the campaign
    c = store.load_campaign(cid)
    foe_ids = [i for f in we["foes"] for i in f["ids"]]
    assert foe_ids
    for fid in foe_ids:
        assert c.characters[fid].kind == "monster"
        assert c.characters[fid].location_id == forest


def test_travel_without_advance_time_never_stages(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    out = server.travel_to(cid, forest, advance_time=False)  # short hop -> no clock, no roll
    assert "wandering_encounter" not in out
    assert _monster_ids(store.load_campaign(cid)) == []


def test_missed_roll_leaves_travel_result_unchanged(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: False)  # always miss
    out = server.travel_to(cid, forest, advance_time=True)
    assert "wandering_encounter" not in out  # additive key only present on a hit
    assert _monster_ids(store.load_campaign(cid)) == []


def test_house_rule_off_disables_auto_stage_but_not_explicit(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    # disable the auto-roll for this campaign
    c = store.load_campaign(cid)
    c.house_rules.wandering_encounters = False
    store.save_campaign(c)
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)  # would hit if allowed
    out = server.travel_to(cid, forest, advance_time=True)
    assert "wandering_encounter" not in out  # flag off -> no auto-stage
    assert _monster_ids(store.load_campaign(cid)) == []
    # the EXPLICIT tool still works (force=True bypasses the auto-roll, not the flag-as-veto)
    explicit = server.roll_wandering_encounter(cid)
    assert explicit["staged"] is True


def test_camp_long_rest_rolls_once_per_overnight(party_camp, monkeypatch):
    cid, _start, _forest = party_camp
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    c = store.load_campaign(cid)
    pcs = list(c.party)
    # put the clock at evening so the first long_rest rolls over to morning (steps > 0)
    c.time_of_day = "evening"
    store.save_campaign(c)
    first = server.long_rest(cid, pcs[0])
    assert "wandering_encounter" in first  # the night's single camp-watch check fired
    n_after_first = len(_monster_ids(store.load_campaign(cid)))
    # the SECOND member resting the same night is a clock no-op -> no second ambush roll
    second = server.long_rest(cid, pcs[1])
    assert "wandering_encounter" not in second
    assert len(_monster_ids(store.load_campaign(cid))) == n_after_first


def test_camp_watch_modifier_lowers_chance_via_seam(party_camp, monkeypatch):
    cid, _start, _forest = party_camp
    seen = {}

    def _capture(region, modifiers=None, rng=None):
        seen["modifiers"] = modifiers
        return False  # miss is fine; we only assert the modifier was threaded through

    monkeypatch.setattr(wander, "roll_encounter", _capture)
    c = store.load_campaign(cid)
    c.time_of_day = "evening"
    store.save_campaign(c)
    server.long_rest(cid, c.party[0], watch="careful")
    assert seen["modifiers"] == {"camouflage": True}


# =========================================================================
# ADDITIVE: old snapshots round-trip (no new fields present)
# =========================================================================


def test_old_snapshot_without_flag_defaults_on():
    # a Campaign/HouseRules serialized before the flag existed must load with it ON
    payload = {"title": "Legacy", "house_rules": {"difficulty": "standard"}}
    c = Campaign.model_validate(payload)
    assert c.house_rules.wandering_encounters is True


def test_house_rules_default_has_flag_on():
    assert HouseRules().wandering_encounters is True
