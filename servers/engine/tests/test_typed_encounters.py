"""Tests for TYPED multi-resolution wandering encounters + the folded-in outlook.

A wandering encounter is no longer ALWAYS a fight: `wander.pick_typed_encounter`
picks a TYPE (combat / skill / social / hazard / boon — most of which are NOT
combat) so travel/camp feels varied, and a COMBAT pick carries the SRD over-match
`outlook` (the same math as `encounter_outlook`) so the DM gets the must-offer-an-out
signal automatically, no new tool to remember (the Wave-12 fix).

Two layers, mirroring test_wander.py:
  * PURE — `wander.pick_typed_encounter` exercised directly (no campaign, no I/O):
    the weight table, per-type required fields, DC banding, determinism, region skew;
  * INTEGRATION — the engine seam (`roll_wandering_encounter`, a forced-hit
    `travel_to`) staging the right payload per type: combat spawns sized foes +
    `outlook`; non-combat spawns NO foes and hands over the descriptor.
"""

import collections
import random

import pytest

import encounter
import server
import store
import wander
from models import Campaign, HouseRules, SKILL_ABILITIES


# =========================================================================
# PURE: pick_typed_encounter — the weight table + region skew
# =========================================================================

_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}


def _type_mix(region, n=6000, weights=None, party=(3, 3, 3, 3)):
    """The empirical type distribution over `n` seeds for `region`."""
    counts = collections.Counter()
    for s in range(n):
        d = wander.pick_typed_encounter(list(party), region, rng=random.Random(s), weights=weights)
        counts[d["type"]] += 1
    return {k: v / n for k, v in counts.items()}


def test_typed_pick_honors_weights_not_all_combat():
    # over many seeds in a baseline-ish region the mix spreads across types, with
    # combat the PLURALITY but the MINORITY (the whole point: most aren't fights).
    mix = _type_mix("Nowhere-In-Particular")  # unknown region -> default weights
    assert set(mix) == set(wander.ENCOUNTER_TYPES)  # every type appears
    # ~40% combat (the DEFAULT_TYPE_WEIGHTS target), well under half
    assert 0.34 <= mix["combat"] <= 0.46
    assert mix["combat"] < 0.5
    # the non-combat majority really is the majority
    assert sum(v for t, v in mix.items() if t != "combat") > 0.5


def test_typed_pick_matches_default_weight_proportions():
    # with no region bias (wilderness fallback), each type's share tracks its default
    # weight within sampling tolerance.
    mix = _type_mix("Nowhere-In-Particular", n=8000)
    for t, w in wander.DEFAULT_TYPE_WEIGHTS.items():
        assert mix.get(t, 0.0) == pytest.approx(w, abs=0.04), f"{t}: {mix.get(t)} vs {w}"


def test_typed_pick_is_deterministic_under_seed():
    a = wander.pick_typed_encounter([4, 4, 4], "the Black Bog", rng=random.Random(123))
    b = wander.pick_typed_encounter([4, 4, 4], "the Black Bog", rng=random.Random(123))
    assert a == b


def test_typed_pick_respects_caller_weight_override():
    # a caller can force a single type by zeroing the others (partial table -> missing
    # types treated as 0 weight).
    only_social = {"social": 1.0}
    for s in range(50):
        d = wander.pick_typed_encounter([3, 3], "forest", rng=random.Random(s), weights=only_social)
        assert d["type"] == "social"


# =========================================================================
# PURE: each type returns its required fields (+ valid skills / DCs)
# =========================================================================


def test_combat_type_returns_sized_foe_specs():
    # draw seeds until we hit a combat type, then check the foe-spec shape (reuses
    # pick_encounter sizing, so a spec is {name, count, xp_each, cr}).
    for s in range(200):
        d = wander.pick_typed_encounter([3, 3, 3, 3], "the Cursed Barrow", rng=random.Random(s))
        if d["type"] == "combat":
            assert d["foes"] and isinstance(d["foes"], list)
            spec = d["foes"][0]
            assert spec["name"] and spec["count"] >= 1 and spec["xp_each"] > 0
            return
    pytest.fail("no combat type drawn in 200 seeds for a combat-heavy region")


def test_skill_type_returns_challenge_skill_dc():
    for s in range(400):
        d = wander.pick_typed_encounter([3, 3], "the Greenwood", rng=random.Random(s))
        if d["type"] == "skill":
            assert d["challenge"] and isinstance(d["challenge"], str)
            assert d["skill"] in SKILL_ABILITIES  # routable straight into skill_check
            assert d["dc"] in (10, 14, 18)  # standard house difficulty -> unshifted band
            return
    pytest.fail("no skill type drawn")


def test_social_type_returns_who_stance_skill_dc():
    for s in range(400):
        d = wander.pick_typed_encounter([3, 3], "the toll road", rng=random.Random(s))
        if d["type"] == "social":
            assert d["who"] and isinstance(d["who"], str)
            assert d["stance"] in ("wary", "desperate", "hostile-but-talkable")
            assert d["skill"] in SKILL_ABILITIES  # routable into social_check
            assert d["dc"] in (10, 14, 18)
            return
    pytest.fail("no social type drawn")


def test_hazard_type_returns_peril_save_or_skill_dc():
    for s in range(400):
        d = wander.pick_typed_encounter([3, 3], "the Black Bog", rng=random.Random(s))
        if d["type"] == "hazard":
            assert d["peril"] and isinstance(d["peril"], str)
            # an ability SAVE or a skill the DM can route to a saving_throw / skill_check
            assert d["save_or_skill"] in _ABILITIES or d["save_or_skill"] in SKILL_ABILITIES
            assert d["dc"] in (10, 14, 18)
            return
    pytest.fail("no hazard type drawn")


def test_boon_type_returns_find_and_needs_no_resolution():
    for s in range(400):
        d = wander.pick_typed_encounter([3, 3], "the toll road", rng=random.Random(s))
        if d["type"] == "boon":
            assert d["find"] and isinstance(d["find"], str)
            # a boon is pure narration — it carries NO skill / dc / foes to resolve
            assert "skill" not in d and "dc" not in d and "foes" not in d
            return
    pytest.fail("no boon type drawn")


def test_dc_band_shifts_with_house_difficulty():
    # the same obstacle is +2 DC under 'hard', -2 under 'easy' (mirrors _suggested_dc).
    only_skill = {"skill": 1.0}
    std = wander.pick_typed_encounter([3, 3], "forest", rng=random.Random(5), weights=only_skill)
    hard = wander.pick_typed_encounter(
        [3, 3], "forest", rng=random.Random(5), weights=only_skill, house_difficulty="hard"
    )
    easy = wander.pick_typed_encounter(
        [3, 3], "forest", rng=random.Random(5), weights=only_skill, house_difficulty="easy"
    )
    assert std["challenge"] == hard["challenge"] == easy["challenge"]  # same draw
    assert hard["dc"] == std["dc"] + 2
    assert easy["dc"] == std["dc"] - 2


def test_combat_degrades_to_boon_when_party_empty():
    # an empty party can't size a fight -> a combat draw must degrade to a guaranteed
    # boon, never return an empty/foeless combat.
    only_combat = {"combat": 1.0}
    for s in range(30):
        d = wander.pick_typed_encounter([], "forest", rng=random.Random(s), weights=only_combat)
        assert d["type"] == "boon" and d["find"]


# =========================================================================
# PURE: region weighting shifts the mix
# =========================================================================


def test_region_weighting_shifts_combat_share():
    # civilized country -> fewer fights, more social; hostile country -> more combat.
    civ = _type_mix("a sleepy market town")
    undead = _type_mix("the Cursed Barrow")
    # a patrolled town meets PEOPLE, an undead waste meets MONSTERS
    assert civ["combat"] < undead["combat"]
    assert civ["social"] > undead["social"]
    # and the town's combat share is well below the dangerous region's
    assert civ["combat"] < 0.3 < undead["combat"]


def test_dangerous_region_raises_hazard_share():
    civ = _type_mix("a sleepy market town")
    swamp = _type_mix("the Black Bog")
    assert swamp["hazard"] > civ["hazard"]


def test_typed_pick_combat_draws_from_region_creature_pool():
    # a combat type in an undead region still pulls foes from that region's palette.
    undead_pool = set(wander.REGION_CREATURES["undead"])
    seen = set()
    for s in range(300):
        d = wander.pick_typed_encounter([5, 5, 5], "the Haunted Barrow", rng=random.Random(s))
        if d["type"] == "combat":
            seen.add(d["foes"][0]["name"])
    assert seen and seen.issubset(undead_pool)


# =========================================================================
# INTEGRATION: the engine seam stages per type + folds in the outlook
# =========================================================================


@pytest.fixture
def party_camp(tmp_path, monkeypatch):
    """A campaign with a start->forest graph and a level-3 PC + companion in the
    party (a real XP budget). Returns (cid, start_id, forest_id)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Typed Wander Test")["id"]
    start = server.add_location(cid, "Trailhead", region="the Greenwood")["id"]
    forest = server.add_location(cid, "Mirkwood Edge", connections=[start], region="the Greenwood")["id"]
    pc = server.create_character(cid, "Renn", kind="player")["id"]
    comp = server.create_character(cid, "Cinder", kind="companion")["id"]
    for who in (pc, comp):
        server.update_character(cid, who, {"classes": [{"name": "fighter", "level": 3}]})
    return cid, start, forest


def _monster_ids(c: Campaign) -> list[str]:
    return [i for i, ch in c.characters.items() if ch.kind == "monster"]


def _force_pick(monkeypatch, payload):
    """Force `pick_typed_encounter` to return a fixed typed payload (so each branch of
    the staging seam is deterministic in test)."""
    monkeypatch.setattr(wander, "pick_typed_encounter", lambda *a, **k: dict(payload))


def test_seam_combat_spawns_foes_and_folds_in_outlook(party_camp, monkeypatch):
    cid, start, _forest = party_camp
    _force_pick(monkeypatch, {"type": "combat", "foes": [{"name": "Wolf", "count": 2, "xp_each": 50, "cr": "1/4"}]})
    out = server.roll_wandering_encounter(cid)
    assert out["staged"] is True
    assert out["type"] == "combat"
    # the typed combat payload keeps the old combat fields...
    assert out["foes"] and out["encounter_xp"] > 0 and isinstance(out["surprise"], bool)
    # ...AND folds in the over-match outlook (the encounter_outlook math)
    o = out["outlook"]
    assert set(o) == {"band", "overmatch_ratio", "avg_party_level", "must_offer_out", "guidance"}
    # the foes are REAL monster Characters anchored at the party's location
    c = store.load_campaign(cid)
    foe_ids = out["foes"][0]["ids"]
    assert set(foe_ids).issubset(set(_monster_ids(c)))
    for fid in foe_ids:
        assert c.characters[fid].location_id == start
    # and combat never auto-starts (still the DM's start_combat to call)
    assert server.get_state(cid)["in_combat"] is False


def test_seam_combat_outlook_must_offer_out_at_troll_dragon_boundary(tmp_path, monkeypatch):
    # The brief's canonical boundary (troll ~1.12x / dragon ~6.25x) is computed against a
    # FOUR-PC level-3 deadly budget, so build a 4-PC party here (the shared fixture is 2).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Boundary")["id"]
    server.add_location(cid, "Trailhead", region="the Greenwood")
    for nm in ("Renn", "Cinder", "Brask", "Vell"):
        pid = server.create_character(cid, nm, kind="player")["id"]
        server.update_character(cid, pid, {"classes": [{"name": "fighter", "level": 3}]})
    levels = [3, 3, 3, 3]
    deadly = encounter.xp_thresholds(levels)["deadly"]

    # A Troll (1800 XP, ~1.12x of the 4-PC deadly budget) is deadly-but-WINNABLE: no out.
    troll_xp = 1800
    _force_pick(monkeypatch, {"type": "combat", "foes": [{"name": "Troll", "count": 1, "xp_each": troll_xp, "cr": "5"}]})
    troll = server.roll_wandering_encounter(cid)["outlook"]
    assert troll["band"] == "deadly"
    assert troll["overmatch_ratio"] == round(troll_xp / deadly, 2) == 1.12
    assert troll["must_offer_out"] is False

    # An Adult Red Dragon (18000 XP, well over 2x) at level <= 5 MUST offer a cost-bearing out.
    _force_pick(monkeypatch, {"type": "combat", "foes": [{"name": "Adult Red Dragon", "count": 1, "xp_each": 18000, "cr": "17"}]})
    dragon = server.roll_wandering_encounter(cid)["outlook"]
    assert dragon["band"] == "deadly"
    assert dragon["overmatch_ratio"] >= 2.0
    assert dragon["must_offer_out"] is True


def test_seam_outlook_matches_encounter_outlook_tool(party_camp, monkeypatch):
    # the folded-in outlook is BYTE-FOR-BYTE the encounter_outlook tool's result for the
    # same foes (shared _outlook_for_xps) — the must-offer-an-out signal can't drift.
    cid, _start, _forest = party_camp
    _force_pick(monkeypatch, {"type": "combat", "foes": [{"name": "Ogre", "count": 3, "xp_each": 450, "cr": "2"}]})
    staged = server.roll_wandering_encounter(cid)
    foe_xps = [f["xp_each"] for f in staged["foes"] for _ in range(f["count"])]
    tool = server.encounter_outlook(cid, monster_xps=foe_xps)
    assert staged["outlook"] == tool


@pytest.mark.parametrize(
    "payload,required",
    [
        ({"type": "skill", "challenge": "a washed-out ford", "skill": "athletics", "dc": 14},
         {"challenge", "skill", "dc"}),
        ({"type": "social", "who": "a wary patrol", "stance": "wary", "skill": "persuasion", "dc": 14},
         {"who", "stance", "skill", "dc"}),
        ({"type": "hazard", "peril": "a rockfall", "save_or_skill": "dex", "dc": 18},
         {"peril", "save_or_skill", "dc"}),
        ({"type": "boon", "find": "a hunter's cache"}, {"find"}),
    ],
)
def test_seam_non_combat_types_spawn_no_foes(party_camp, monkeypatch, payload, required):
    cid, _start, _forest = party_camp
    _force_pick(monkeypatch, payload)
    out = server.roll_wandering_encounter(cid)
    assert out["staged"] is True
    assert out["type"] == payload["type"]
    assert out["region"] == "the Greenwood"
    # the typed descriptor fields are carried through for the DM to run
    assert required <= out.keys()
    # a non-combat encounter NEVER spawns foes and NEVER carries an outlook
    assert "foes" not in out and "outlook" not in out
    assert _monster_ids(store.load_campaign(cid)) == []
    assert server.get_state(cid)["in_combat"] is False


def test_forced_travel_leg_stages_typed_encounter(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    # always hit the per-region roll, and force a non-combat type so we assert the
    # typed payload rides the travel seam without spawning foes.
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    _force_pick(monkeypatch, {"type": "social", "who": "a ranger", "stance": "wary", "skill": "persuasion", "dc": 14})
    out = server.travel_to(cid, forest, advance_time=True)
    we = out["wandering_encounter"]
    assert we["staged"] is True and we["type"] == "social" and we["who"] == "a ranger"
    assert _monster_ids(store.load_campaign(cid)) == []  # social staged no monsters


def test_forced_travel_combat_anchors_foes_at_destination(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    _force_pick(monkeypatch, {"type": "combat", "foes": [{"name": "Wolf", "count": 2, "xp_each": 50, "cr": "1/4"}]})
    out = server.travel_to(cid, forest, advance_time=True)
    we = out["wandering_encounter"]
    assert we["type"] == "combat" and "outlook" in we
    c = store.load_campaign(cid)
    for fid in we["foes"][0]["ids"]:
        assert c.characters[fid].kind == "monster"
        assert c.characters[fid].location_id == forest  # anchored at the DESTINATION


def test_house_rule_off_disables_typed_auto_stage(party_camp, monkeypatch):
    cid, _start, forest = party_camp
    c = store.load_campaign(cid)
    c.house_rules.wandering_encounters = False
    store.save_campaign(c)
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    _force_pick(monkeypatch, {"type": "social", "who": "a ranger", "stance": "wary", "skill": "persuasion", "dc": 14})
    out = server.travel_to(cid, forest, advance_time=True)
    assert "wandering_encounter" not in out  # flag off -> no auto-stage, any type


# =========================================================================
# ADDITIVITY: old snapshots round-trip; no required new persisted field
# =========================================================================


def test_old_snapshot_round_trips_no_new_required_field():
    # the typed `type` lives in the RUNTIME payload, not the persisted model — a
    # pre-typed-wave Campaign/HouseRules snapshot must still load unchanged.
    payload = {"title": "Legacy", "house_rules": {"difficulty": "standard"}}
    c = Campaign.model_validate(payload)
    assert c.house_rules.wandering_encounters is True
    # round-trip is lossless (no new field forced into the dump)
    again = Campaign.model_validate(c.model_dump(mode="json"))
    assert again.house_rules.wandering_encounters is True


def test_house_rules_default_unchanged():
    assert HouseRules().wandering_encounters is True
