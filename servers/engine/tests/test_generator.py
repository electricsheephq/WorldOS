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


# --- companion-arc coverage (the previously-uncovered pass) -----------------------

def _adv_with_companion(arc=None, dossier=None, quest_arcs=None):
    """A minimal valid adventure carrying one companion (with an optional arc / dossier / a
    top-level companion_quest_arcs registry) — the fixture each companion-pass test plants a
    single bug into so the new check is exercised in isolation."""
    adv = generator.scaffold_adventure("Companion Spine")
    comp = {"id": "companion-vael", "name": "Vael", "voice_id": "companion-default"}
    if arc is not None:
        comp["arc"] = arc
    if dossier is not None:
        comp["companion_dossier"] = dossier
    adv["companions"] = [comp]
    if quest_arcs is not None:
        adv["companion_quest_arcs"] = quest_arcs
    return adv


def test_real_authored_companion_spines_validate_clean():
    """Every shipped companion-bearing spine must pass the new companion pass — the invariant
    that the pass flags only the planted bugs below, never real content (the audit's whole point
    is to add coverage WITHOUT breaking the authored campaigns)."""
    for cid in ("hollow-mile", "embergloom-pact", "three-knives", "ashfall-reach",
                "the-ledger-of-mercy"):
        adv = content.load_adventure_data(cid)
        comp_problems = [
            p for p in generator.validate_adventure(adv)
            if "companion" in p.lower() and ("gate" in p or "agenda" in p or "approval" in p)
        ]
        assert comp_problems == [], (cid, comp_problems)


def test_positive_threshold_betrayal_gate_flagged():
    """A 'betrayal'-kind arc-gate with a POSITIVE threshold is the inversion bug (a betrayal
    that unlocks on HIGH approval) — it must be flagged so the spine can't ship undetected."""
    adv = _adv_with_companion(arc={
        "arc_gates": [{"id": "g1", "kind": "betrayal", "threshold": 15}],
    })
    problems = generator.validate_adventure(adv)
    assert any("betrayal gate" in p and "POSITIVE" in p for p in problems), problems


def test_negative_threshold_betrayal_gate_is_clean():
    """A correctly-authored betrayal gate (NEGATIVE threshold — unlocks as approval curdles)
    is clean."""
    adv = _adv_with_companion(arc={
        "arc_gates": [{"id": "g1", "kind": "betrayal", "threshold": -25}],
    })
    assert generator.validate_adventure(adv) == []


def test_positive_attitude_below_agenda_value_flagged():
    """An attitude_below agenda with a POSITIVE value arms the betrayal at HIGH approval — the
    same inversion bug; flagged. (Real spines use -20/-30.)"""
    adv = _adv_with_companion(arc={
        "arc_gates": [],
        "agenda": {"trigger": "attitude_below", "value": 20},
    })
    problems = generator.validate_adventure(adv)
    assert any("attitude_below agenda" in p and "POSITIVE" in p for p in problems), problems


def test_negative_attitude_below_agenda_value_is_clean():
    adv = _adv_with_companion(arc={
        "arc_gates": [],
        "agenda": {"trigger": "attitude_below", "value": -20},
    })
    assert generator.validate_adventure(adv) == []


def test_personal_quest_gate_dangling_quest_arc_id_flagged():
    """A personal_quest gate whose quest_arc_id has no authored companion_quest_arc is a dangling
    link — the gate can never make a real arc available — and must be flagged."""
    adv = _adv_with_companion(
        arc={"arc_gates": [{"id": "g1", "kind": "personal_quest", "threshold": 40,
                            "quest_arc_id": "cqarc-does-not-exist"}]},
        quest_arcs=[{"id": "cqarc-real", "companion_id": "companion-vael",
                     "title": "Real Arc", "stages": []}],
    )
    problems = generator.validate_adventure(adv)
    assert any("personal_quest gate links unknown" in p for p in problems), problems


def test_personal_quest_gate_resolving_quest_arc_id_is_clean():
    """When the quest_arc_id resolves to an authored companion_quest_arc, the link is clean."""
    adv = _adv_with_companion(
        arc={"arc_gates": [{"id": "g1", "kind": "personal_quest", "threshold": 40,
                            "quest_arc_id": "cqarc-real"}]},
        quest_arcs=[{"id": "cqarc-real", "companion_id": "companion-vael",
                     "title": "Real Arc", "stages": []}],
    )
    assert generator.validate_adventure(adv) == []


def test_space_separated_approval_key_flagged():
    """An approval key with whitespace can never match the engine's token-shaped approval_tags,
    so the regard move it gates is silently dead — flag it."""
    adv = _adv_with_companion(dossier={
        "approval_likes": ["tell_a_hard_truth", "trust the evidence"],  # second key has spaces
        "approval_dislikes": ["sell_the_grey_water"],
    })
    problems = generator.validate_adventure(adv)
    assert any("contains whitespace" in p and "trust the evidence" in p for p in problems), problems


def test_clean_companion_with_arc_and_dossier_validates():
    """A fully-correct companion (negative betrayal gate, resolving personal_quest link, negative
    agenda value, whitespace-free approval keys) validates clean — no false positives."""
    adv = _adv_with_companion(
        arc={
            "arc_gates": [
                {"id": "g1", "kind": "loyalty", "threshold": 20},
                {"id": "g2", "kind": "personal_quest", "threshold": 40,
                 "quest_arc_id": "cqarc-vael"},
                {"id": "g3", "kind": "betrayal", "threshold": -25},
            ],
            "agenda": {"trigger": "attitude_below", "value": -20,
                       "decision_flag": "broke_your_oath"},
        },
        dossier={"approval_likes": ["keep_your_word"], "approval_dislikes": ["abandon_an_ally"]},
        quest_arcs=[{"id": "cqarc-vael", "companion_id": "companion-vael",
                     "title": "Vael's Oath", "stages": []}],
    )
    assert generator.validate_adventure(adv) == []


def test_adventure_with_no_companions_is_clean():
    """The companion pass is fully optional — an adventure with no companions key is untouched."""
    adv = generator.scaffold_adventure("Solo")
    assert generator.validate_adventure(adv) == []
