"""The replayability layer (S6): each MAJOR world quest's outcome is resolved once at
world-gen — ending-tied where the chosen ending's world-state matches, else a seeded
random roll — and surfaced as recallable [Outcome]/[Hook] lore under the canon header.

These tests mirror the world_state contract the layer copies: ending-tied subset-match,
seeded determinism, additive-default (byte-identical with no quest_variants), and
degrade-not-abort on a malformed entry. The shipped baldurs-gate exemplars exercise the
real integration; small synthetic worlds pin the engine behavior in isolation."""

import content
import server


# A small synthetic world (no quest_variants) — the additive-default baseline.
BASE_WORLD = {
    "id": "qv-test-world",
    "name": "Quest-Variant Test World",
    "era": "the testing age",
    "regions": [{"id": "r1", "name": "Region One"}],
    "history": ["a base history fact"],
    "standing_threads": ["a base standing thread"],
}

# A synthetic world WITH quest_variants — full control over ending-tied + random shapes,
# independent of the shipped content (so these stay true even if the exemplars change).
QV_WORLD = {
    "id": "qv-test-world",
    "name": "Quest-Variant Test World",
    "era": "the testing age",
    "regions": [{"id": "r1", "name": "Region One"}],
    "quest_variants": [
        {
            "id": "the-pure-roll",
            "name": "A Purely Rolled Thread",
            "outcomes": [
                {"id": "roll-a", "random": 1, "lore": "Outcome A came to pass.", "hook": "A hook from A."},
                {"id": "roll-b", "random": 1, "lore": "Outcome B came to pass.", "hook": "A hook from B."},
            ],
        },
        {
            "id": "the-tied-thread",
            "name": "An Ending-Tied Thread",
            "outcomes": [
                {"id": "tied-bright", "when": {"world_tenor": "hopeful"}, "lore": "The bright resolution holds.", "hook": "A bright follow-up."},
                {"id": "tied-fallback", "random": 1, "lore": "The fallback resolution holds."},
            ],
        },
    ],
}


def test_ending_tied_outcome_resolves_by_world_state_subset_match():
    # The shipped hopeful ending populates world_state (tenor=hopeful, baldurs_gate=
    # rebuilding); an outcome whose `when` is a SUBSET of that view wins (first match).
    # Per the spec exemplar: under a hopeful ending the grove resolves "saved".
    w = content.load_world_data("baldurs-gate")
    hopeful = content.seed_world(w, ending="netherbrain-destroyed-heroes-live")
    assert hopeful.world_state is not None and hopeful.world_state.world_tenor == "hopeful"
    # world_tenor-keyed ending-tie (grove-saved when:{world_tenor:hopeful})
    assert hopeful.quest_outcomes["the-emerald-grove"] == "grove-saved"
    # a facts-keyed ending-tie (council-restored when:{baldurs_gate:rebuilding})
    assert hopeful.quest_outcomes["who-rules-the-gate"] == "council-restored"

    # A grim ending does NOT match the hopeful `when`, so the grove falls to its random
    # pool (one of the non-saved outcomes) — the same quest, a different world.
    grim = content.seed_world(w, ending="gortash-tyranny")
    assert grim.world_state.world_tenor == "grim"
    assert grim.quest_outcomes["the-emerald-grove"] in {"grove-massacred", "grove-druids-locked"}
    # a fact unique to the tyranny ending pins its own thread (tyrant-holds when:{gortash:archduke})
    assert grim.quest_outcomes["who-rules-the-gate"] == "tyrant-holds"
    # a fact unique to the dark-urge ending pins the murder-cult (cult-ascendant when:{bhaal:ascendant})
    bhaal = content.seed_world(w, ending="dark-urge-bhaal")
    assert bhaal.quest_outcomes["the-bhaal-murder-cult"] == "cult-ascendant"

    # REGRESSION (adversarial Finding 1): who-rules-the-gate must NOT fall to the random
    # "vacuum-contested" (empty seat) under the OCCUPIED endings — that flatly contradicts
    # the canon "a crowned Bhaalspawn rules the Gate" / "an unseen illithid power holds the
    # reins". Both occupied endings are now pinned to a ruled-by-the-occupier outcome.
    assert bhaal.quest_outcomes["who-rules-the-gate"] == "bhaal-throne"
    illithid = content.seed_world(w, ending="illithid-ascension")
    assert illithid.quest_outcomes["who-rules-the-gate"] == "shadow-overlord"
    for occupied in (bhaal, illithid):
        assert occupied.quest_outcomes["who-rules-the-gate"] != "vacuum-contested"

    # REGRESSION (adversarial Finding 2): the-steel-watch-foundry's random outcomes all
    # describe the foundry as WRECKED ("automatons dead in the streets"). Under the Gortash
    # tyranny the Steel Watch is the live engine of the occupation — so it must pin the
    # ending-tied "watch-operational" (when:{gortash:archduke}), never a "the foundry fell"
    # random that contradicts the occupied-under-Gortash canon header.
    assert grim.quest_outcomes["the-steel-watch-foundry"] == "watch-operational"
    # the non-archduke endings (Gortash dead / city rebuilding) correctly let the foundry
    # FALL to its random pool — there the wrecked-foundry prose is canon-consistent.
    for fell in (bhaal, illithid, hopeful):
        assert fell.quest_outcomes["the-steel-watch-foundry"] in {
            "gondians-survived", "ironhands-survived", "factions-at-peace"}


def test_pure_random_quest_resolves_to_some_valid_outcome_seeded_deterministic():
    # A purely-random quest (no `when` matched, or no world_state at all) resolves to ONE
    # of its declared random outcomes — never invents an id, never stays unresolved.
    c = content.seed_world(QV_WORLD)
    assert c.quest_outcomes["the-pure-roll"] in {"roll-a", "roll-b"}
    # base/no-ending path leaves world_state None -> the ending-tied `when` can't match,
    # so the ending-tied thread ALSO falls through to its random fallback.
    assert c.world_state is None
    assert c.quest_outcomes["the-tied-thread"] == "tied-fallback"

    # Seeded off the campaign id -> a GIVEN campaign id rolls deterministically (re-seeding
    # the same id reproduces the same outcome; the rng is random.Random(c.id)).
    from models import Campaign
    import random as _random

    rolls = set()
    for _ in range(5):
        c2 = Campaign(id="camp-fixed-seed", title="T")
        content._resolve_quest_variants(c2, QV_WORLD, _random.Random(c2.id))
        rolls.add(c2.quest_outcomes["the-pure-roll"])
    assert len(rolls) == 1  # same id -> same roll every time (reproducible)

    # Two different campaign ids generally explore both outcomes (the pool is live, not
    # pinned to one value) — sample enough ids that both appear.
    seen = set()
    for i in range(40):
        c3 = Campaign(id=f"camp-{i}", title="T")
        content._resolve_quest_variants(c3, QV_WORLD, _random.Random(c3.id))
        seen.add(c3.quest_outcomes["the-pure-roll"])
    assert seen == {"roll-a", "roll-b"}  # both reachable -> it's a real roll


def test_resolved_outcomes_land_in_quest_outcomes_and_as_recallable_lore(tmp_path, monkeypatch):
    # The resolved outcome is BOTH a structured record (quest_outcomes) AND prose appended
    # to c.lore as [Outcome]/[Hook] lines — so recall surfaces it under the canon header.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("baldurs-gate", ending="netherbrain-destroyed-heroes-live")
    cid = out["campaign_id"]
    from models import Campaign  # noqa: F401  (load via server)
    c = server._require(cid)

    # structured map populated
    assert c.quest_outcomes.get("the-emerald-grove") == "grove-saved"
    # the prose landed as [Outcome] and [Hook] lore lines (the grove-saved outcome ships both)
    assert any(l.startswith("[Outcome] The Fate of the Emerald Grove:") for l in c.lore)
    assert any(l.startswith("[Hook] The Fate of the Emerald Grove:") for l in c.lore)

    # recall surfaces those lines as kind=lore, led by the world_state canon header
    hits = server.recall(cid, "Emerald Grove druid grove refugees")["hits"]
    assert hits and hits[0]["kind"] == "world_state"  # canon header leads
    assert hits[0]["text"].startswith("CURRENT WORLD (authoritative): tenor=hopeful")
    # the resolved [Outcome]/[Hook] lines come back as kind=lore under that header
    lore_hits = [h["text"] for h in hits if h["kind"] == "lore"]
    assert any(t.startswith("[Outcome] The Fate of the Emerald Grove:") for t in lore_hits)
    assert any(t.startswith("[Hook] The Fate of the Emerald Grove:") for t in lore_hits)
    # and the resolved prose itself is in the recalled text (a contiguous phrase from it)
    assert any("the goblin warband that besieged it was broken" in t for t in lore_hits)


def test_get_quest_outcomes_tool_returns_the_resolved_map(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("baldurs-gate", ending="dark-urge-bhaal")
    cid = out["campaign_id"]
    got = server.get_quest_outcomes(cid)
    assert got["count"] == len(got["quest_outcomes"]) >= 7  # the shipped exemplars resolved
    assert got["quest_outcomes"]["the-bhaal-murder-cult"] == "cult-ascendant"  # ending-tied
    # start_world echoes the resolved outcomes so the DM sees them at session open
    assert out["quest_outcomes"]["the-bhaal-murder-cult"] == "cult-ascendant"
    assert out["quest_outcomes_count"] == got["count"]
    assert out["quest_outcomes_sample"] and len(out["quest_outcomes_sample"]) <= 4


def test_additive_default_no_quest_variants_is_byte_identical():
    # A world with NO quest_variants resolves nothing: quest_outcomes == {}, and the seed
    # is byte-identical to today (no extra lore, same fields). Mirrors the world_state
    # additive-default contract.
    c = content.seed_world(BASE_WORLD)
    assert c.quest_outcomes == {}
    # lore is exactly the base history + threads — no [Outcome]/[Hook] lines appended
    expected_lore = [str(x) for x in (BASE_WORLD["history"] + BASE_WORLD["standing_threads"]) if str(x).strip()]
    assert c.lore == expected_lore
    assert not any(l.startswith("[Outcome]") or l.startswith("[Hook]") for l in c.lore)

    # the shipped sundered-reach world ships no quest_variants -> empty map, untouched
    w = content.load_world_data("sundered-reach")
    sr = content.seed_world(w)
    assert sr.quest_outcomes == {}
    assert not any(l.startswith("[Outcome]") or l.startswith("[Hook]") for l in sr.lore)

    # get_quest_outcomes on such a world is an empty, safe read (no crash)
    # (exercised via a freshly-seeded synthetic campaign through the tool path)


def test_get_quest_outcomes_empty_for_world_without_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("sundered-reach")["campaign_id"]
    got = server.get_quest_outcomes(cid)
    assert got == {"quest_outcomes": {}, "count": 0}
    # start_world omits the quest-outcome echo entirely when nothing resolved
    out = server.start_world("sundered-reach")
    assert "quest_outcomes" not in out and "quest_outcomes_sample" not in out


def test_degrade_not_abort_on_malformed_quest_entry():
    # A malformed quest/outcome entry is SKIPPED (never aborts seed_world); a valid sibling
    # still resolves. Mirrors the world_state / companion_seeds degrade-not-abort guard.
    world = {
        "id": "qv-bad",
        "name": "Bad QV World",
        "regions": [{"id": "r1", "name": "R1"}],
        "quest_variants": [
            "not a dict",                                   # malformed: not an object
            {"name": "missing-id"},                          # malformed: no id -> skipped
            {"id": "no-outcomes", "outcomes": "not a list"},  # malformed outcomes -> no resolution
            {"id": "bad-outcome-shapes", "name": "Bad Shapes", "outcomes": [
                "garbage",                                    # non-dict outcome -> skipped
                {"random": "not-a-number", "lore": "weird-weight outcome"},  # no id -> skipped
                {"id": "valid", "random": 1, "lore": "The valid outcome resolved."},
            ]},
            {"id": "good-quest", "name": "Good Quest", "outcomes": [
                {"id": "good-outcome", "random": 5, "lore": "All good here.", "hook": "A good hook."},
            ]},
        ],
    }
    c = content.seed_world(world)  # must not raise
    # the wholly-malformed / id-less / outcome-less quests resolved to nothing
    assert "missing-id" not in c.quest_outcomes
    assert "no-outcomes" not in c.quest_outcomes
    # an entry with some garbage outcomes still resolves its one VALID outcome
    assert c.quest_outcomes.get("bad-outcome-shapes") == "valid"
    # a fully-valid sibling resolves normally and lands its lore + hook
    assert c.quest_outcomes.get("good-quest") == "good-outcome"
    assert any(l.startswith("[Outcome] Good Quest:") for l in c.lore)
    assert any(l.startswith("[Hook] Good Quest:") for l in c.lore)
