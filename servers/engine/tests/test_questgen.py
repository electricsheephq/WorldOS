"""S7 — the quest-generation layer: assemble lore-derived quest hooks + a guaranteed 4-beat
cold-open PRELUDE the DM weaves. The engine assembles STRUCTURE (a SHAPE tag bound to lore nouns
+ a grievance, with prereq/arc_back LABELS); it does NOT own win-conditions/monitors (proven
hollow — world_state is immutable in play). These tests pin: the prelude always emits its 4 ordered
beats with bound nouns; hooks derive from the resolved quest_outcomes; the spine is the most
world-central hook and ribs arc back to it; apophenia binds related nouns; determinism off the seed;
and the additive default (no variants → no hooks; no locations → no prelude; byte-identical otherwise).
"""

import random

import content
import questgen
from models import Campaign, Faction, Location, QuestHook


def _gen(ending: str = "gortash-tyranny") -> Campaign:
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w, ending=ending)
    questgen.generate(c, w, random.Random(c.id))
    return c


def test_prelude_emits_four_ordered_beats_with_bound_nouns():
    c = _gen()
    kinds = [b.kind for b in c.prelude]
    assert kinds == ["arrival", "meeting", "inciting_incident", "threshold"]  # fixed order, all four
    beats = {b.kind: b for b in c.prelude}
    # arrival grounds the PC in a real place; meeting binds a companion; inciting binds the spine hook
    assert beats["arrival"].ref_id and beats["arrival"].ref_id in c.locations
    assert beats["meeting"].ref_id in c.characters  # a real roster companion to meet
    assert beats["meeting"].note  # carries a shared-stake frame, not empty
    spine = next(h for h in c.quest_hooks if h.spine)
    assert beats["inciting_incident"].ref_id == spine.id  # the inciting wrong IS the spine grievance
    assert beats["threshold"].ref_id == spine.id


def test_hooks_derive_from_resolved_quest_outcomes():
    c = _gen()
    # every resolved quest_outcome whose chosen outcome ships a follow-on `hook` becomes a typed
    # hook (not every random outcome carries one, so this is a subset — never more than resolved).
    assert c.quest_hooks, "the seeded BG world resolves quest_variants -> hooks expected"
    assert 1 <= len(c.quest_hooks) <= len(c.quest_outcomes)
    for h in c.quest_hooks:
        assert h.grievance and h.note  # a wrong + its follow-on
        assert h.shape in {
            "fetch_plus", "investigation", "hunt", "rescue", "heist",
            "escort", "faction_war", "dilemma",
            "false_accusation", "sacrifice_choice", "revelation", "tragedy_unfolding",
        }
        assert h.status == "open"


def test_exactly_one_spine_and_ribs_arc_back_to_it():
    c = _gen()
    spines = [h for h in c.quest_hooks if h.spine]
    assert len(spines) == 1
    spine = spines[0]
    # under the tyranny ending the central wrong IS who-rules-the-gate (max overlap w/ facts)
    assert "Who Rules" in spine.grievance
    assert spine.arc_back == ""  # the spine arcs back to nothing
    for h in c.quest_hooks:
        if not h.spine:
            assert spine.grievance in h.arc_back  # every rib feeds the spine


def test_apophenia_binds_related_nouns():
    # the shadow-cursed-lands hook should bind a place whose name overlaps it (Reithwin/Shadow),
    # not an arbitrary location — connected nouns read as authored intent.
    c = _gen()
    sc = next((h for h in c.quest_hooks if "Shadow-Cursed" in h.grievance), None)
    assert sc is not None and sc.place_id
    place = c.locations.get(sc.place_id)
    assert place is not None
    assert any(w in place.name.lower() for w in ("shadow", "reithwin", "moonrise", "last light"))


def test_determinism_same_seed_same_graph():
    # The contract: given the SAME campaign state + SAME rng seed, generate() is reproducible.
    # (seed_world rolls different random quest_outcomes per fresh campaign id, so two seedings
    # would have different INPUTS — deep-copy one seeded campaign to hold the inputs fixed.)
    import copy
    w = content.load_world_data("baldurs-gate")
    c1 = content.seed_world(w, ending="gortash-tyranny")
    c2 = copy.deepcopy(c1)
    questgen.generate(c1, w, random.Random("fixed-seed"))
    questgen.generate(c2, w, random.Random("fixed-seed"))
    sig1 = [(h.shape, h.grievance, h.giver_id, h.place_id, h.spine) for h in c1.quest_hooks]
    sig2 = [(h.shape, h.grievance, h.giver_id, h.place_id, h.spine) for h in c2.quest_hooks]
    assert sig1 == sig2  # identical structure from the same inputs + seed
    assert [b.note for b in c1.prelude] == [b.note for b in c2.prelude]


def test_additive_default_no_variants_no_hooks_no_prelude():
    # a synthetic world with NO quest_variants resolves no hooks; a world with no locations
    # opens no prelude — both == today's behavior.
    base = {"id": "qg-empty", "name": "Empty", "regions": []}
    c = content.seed_world(base)
    questgen.generate(c, base, random.Random(c.id))
    assert c.quest_hooks == []
    assert c.prelude == []  # no locations -> no cold-open


def test_no_world_state_first_hook_is_spine_and_degrades_clean():
    # a campaign with quest_outcomes but no world_state (base/no-ending) still builds a graph:
    # the first hook leads as spine; generation never raises.
    c = Campaign(title="No-WS")
    c.locations["loc-1"] = Location(id="loc-1", name="A Place")
    c.current_location_id = "loc-1"
    c.factions["fac-1"] = Faction(id="fac-1", name="The Ring")
    c.quest_outcomes = {"q-a": "o-a", "q-b": "o-b"}
    world = {"id": "w", "quest_variants": [
        {"id": "q-a", "name": "The First Wrong", "outcomes": [{"id": "o-a", "random": 1, "lore": "x", "hook": "a thread to pull"}]},
        {"id": "q-b", "name": "The Second Wrong", "outcomes": [{"id": "o-b", "random": 1, "lore": "y", "hook": "another thread"}]},
    ]}
    questgen.generate(c, world, random.Random("s"))
    assert len(c.quest_hooks) == 2
    assert sum(1 for h in c.quest_hooks if h.spine) == 1
    assert c.quest_hooks[0].spine  # no facts to rank by -> the first hook leads
    assert [b.kind for b in c.prelude] == ["arrival", "meeting", "inciting_incident", "threshold"]


# --- wiring into seed_world + the MCP tools (server path) ---------------------------------


def test_start_world_wires_s7_and_echoes_and_persists(tmp_path, monkeypatch):
    import server
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    out = server.start_world("baldurs-gate", ending="gortash-tyranny")
    cid = out["campaign_id"]
    # the echo surfaces the cold-open + the seeds at session open
    assert len(out.get("prelude", [])) == 4
    assert [b["kind"] for b in out["prelude"]] == ["arrival", "meeting", "inciting_incident", "threshold"]
    assert out.get("quest_hooks_count", 0) >= 1
    assert out.get("spine_grievance")
    # and it persisted (a re-load sees the generated graph)
    c = server._require(cid)
    assert c.quest_hooks and c.prelude


def test_s7_tools_read_filter_and_advance(tmp_path, monkeypatch):
    import pytest
    import server
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate", ending="illithid-ascension")["campaign_id"]

    # get_prelude resolves ref names (location for arrival, companion for meeting, spine grievance)
    pre = server.get_prelude(cid)
    assert pre["count"] == 4
    arrival = next(b for b in pre["prelude"] if b["kind"] == "arrival")
    assert arrival["ref_name"]  # a real starting place

    # get_quest_hooks: spine_only + status filters; the spine view resolves its bound nouns
    allh = server.get_quest_hooks(cid)
    assert allh["count"] >= 1
    spine = server.get_quest_hooks(cid, spine_only=True)["quest_hooks"]
    assert len(spine) == 1 and spine[0]["spine"] is True
    hid = spine[0]["id"]

    # set_quest_status advances + persists; the status filter reflects it
    n_open = server.get_quest_hooks(cid, status="open")["count"]
    server.set_quest_status(cid, hid, "active")
    assert server.get_quest_hooks(cid, status="open")["count"] == n_open - 1
    assert server.get_quest_hooks(cid, status="active")["count"] == 1
    assert server._require(cid).quest_hooks  # persisted

    # bad status + unknown hook id are rejected
    with pytest.raises(ValueError, match="open|active|resolved"):
        server.set_quest_status(cid, hid, "finished")
    with pytest.raises(ValueError, match="no quest hook"):
        server.set_quest_status(cid, "hook_nope", "active")


def test_easter_egg_npc_excluded_from_default_giver_pool():
    # Claudan is flagged easter_egg in the roster — a RARE opt-in chaos-engine, NOT a default
    # quest-giver. He must exist in the world (the DM can surface him) but never be auto-bound as
    # a giver/target or the cold-open companion, so one oddball can't dominate the quest system.
    c = _gen("gortash-tyranny")
    assert "npc-claudan" in c.characters  # he exists in the world
    for h in c.quest_hooks:
        assert h.giver_id != "npc-claudan" and h.target_id != "npc-claudan"
    meeting = next((b for b in c.prelude if b.kind == "meeting"), None)
    assert meeting is None or meeting.ref_id != "npc-claudan"


def test_sundered_reach_gets_cold_open_but_no_hooks(tmp_path, monkeypatch):
    # a world with locations but NO quest_variants: a generic cold-open prelude still generates
    # (every world deserves an opening), but there are no lore-derived hooks. And questgen never
    # touches c.lore, so the quest_variants additive-default (byte-identical lore) is preserved.
    import server
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    out = server.start_world("sundered-reach")
    cid = out["campaign_id"]
    c = server._require(cid)
    assert c.quest_hooks == []                      # no quest_variants -> no hooks
    assert "quest_hooks_count" not in out           # echo omitted when none
    assert len(c.prelude) == 4                       # but a cold-open still opens the world
    assert not any(l.startswith("[Outcome]") or l.startswith("[Hook]") for l in c.lore)


# --- gut-punch shapes: false_accusation, sacrifice_choice, revelation, tragedy_unfolding ----
# Each test drives the shape via keyword-hinted grievance/hook text so _pick_shape selects it,
# then checks: valid skeleton fields, determinism under a seeded rng, appears in _SHAPES.

_ALL_SHAPES = set(questgen._SHAPES)


def _make_shaped_campaign(grievance: str, hook_note: str) -> tuple:
    """Return (Campaign, world_dict) with exactly one quest_outcome whose hook text carries
    the given grievance and note — so _derive_hooks produces exactly one shaped hook."""
    from models import Campaign, Location, Faction
    c = Campaign(title="Shaped")
    c.locations["loc-1"] = Location(id="loc-1", name="The Gallows Quarter", description="where blame lands")
    c.current_location_id = "loc-1"
    c.factions["fac-1"] = Faction(id="fac-1", name="The Accusers", description="those with power to condemn")
    c.quest_outcomes = {"q-shaped": "o-1"}
    world = {"id": "w-shaped", "quest_variants": [
        {"id": "q-shaped", "name": grievance, "outcomes": [
            {"id": "o-1", "random": 1, "lore": "lore text", "hook": hook_note}
        ]},
    ]}
    return c, world


def test_false_accusation_shape_valid_skeleton_and_determinism():
    # keyword "framed" in grievance triggers false_accusation via _SHAPE_HINTS
    grievance = "The Healer Framed for Poisoning"
    hook_note = "an innocent ally is blamed and the accuser holds the power to exile them"
    c, world = _make_shaped_campaign(grievance, hook_note)
    questgen.generate(c, world, random.Random("fa-seed"))
    assert len(c.quest_hooks) == 1
    h = c.quest_hooks[0]
    assert h.shape == "false_accusation"
    assert h.grievance == grievance
    assert h.note == hook_note
    assert h.status == "open"
    assert h.giver_id or h.target_id or h.place_id  # at least one lore noun bound
    assert h.motivation == "reputation"
    assert h.shape in _ALL_SHAPES
    # determinism: same seed -> same output
    import copy
    c2 = copy.deepcopy(c)
    c2.quest_hooks = []; c2.prelude = []
    questgen.generate(c2, world, random.Random("fa-seed"))
    assert [(h2.shape, h2.grievance, h2.giver_id, h2.place_id) for h2 in c2.quest_hooks] == \
           [(h.shape, h.grievance, h.giver_id, h.place_id) for h in c.quest_hooks]


def test_sacrifice_choice_shape_valid_skeleton_and_determinism():
    # keyword "trade" in hook triggers sacrifice_choice (no earlier hint fires on this text)
    grievance = "The Price at the Temple Gate"
    hook_note = "there is no clean exit — the trade forces a real cost: one life given freely or all lost"
    c, world = _make_shaped_campaign(grievance, hook_note)
    questgen.generate(c, world, random.Random("sc-seed"))
    assert len(c.quest_hooks) == 1
    h = c.quest_hooks[0]
    assert h.shape == "sacrifice_choice"
    assert h.grievance == grievance
    assert h.note == hook_note
    assert h.status == "open"
    assert h.motivation == "serenity"
    assert h.shape in _ALL_SHAPES
    import copy
    c2 = copy.deepcopy(c)
    c2.quest_hooks = []; c2.prelude = []
    questgen.generate(c2, world, random.Random("sc-seed"))
    assert [(h2.shape, h2.grievance) for h2 in c2.quest_hooks] == \
           [(h.shape, h.grievance) for h in c.quest_hooks]


def test_revelation_shape_valid_skeleton_and_determinism():
    # keyword "lied" triggers revelation (no earlier hint fires on this text)
    grievance = "The Lie at the Root of the Order"
    hook_note = "the elder lied from the beginning — their identity reshapes everything the party trusted"
    c, world = _make_shaped_campaign(grievance, hook_note)
    questgen.generate(c, world, random.Random("rv-seed"))
    assert len(c.quest_hooks) == 1
    h = c.quest_hooks[0]
    assert h.shape == "revelation"
    assert h.grievance == grievance
    assert h.note == hook_note
    assert h.status == "open"
    assert h.motivation == "knowledge"
    assert h.shape in _ALL_SHAPES
    import copy
    c2 = copy.deepcopy(c)
    c2.quest_hooks = []; c2.prelude = []
    questgen.generate(c2, world, random.Random("rv-seed"))
    assert [(h2.shape, h2.grievance) for h2 in c2.quest_hooks] == \
           [(h.shape, h.grievance) for h in c.quest_hooks]


def test_tragedy_unfolding_shape_valid_skeleton_and_determinism():
    # keyword "doom" triggers tragedy_unfolding
    grievance = "The Doom Already in Motion"
    hook_note = "the curse is spreading — the party may witness and soften it but cannot stop what is already dying"
    c, world = _make_shaped_campaign(grievance, hook_note)
    questgen.generate(c, world, random.Random("tu-seed"))
    assert len(c.quest_hooks) == 1
    h = c.quest_hooks[0]
    assert h.shape == "tragedy_unfolding"
    assert h.grievance == grievance
    assert h.note == hook_note
    assert h.status == "open"
    assert h.motivation == "comfort"
    assert h.shape in _ALL_SHAPES
    import copy
    c2 = copy.deepcopy(c)
    c2.quest_hooks = []; c2.prelude = []
    questgen.generate(c2, world, random.Random("tu-seed"))
    assert [(h2.shape, h2.grievance) for h2 in c2.quest_hooks] == \
           [(h.shape, h.grievance) for h in c.quest_hooks]


def test_all_four_gut_punch_shapes_appear_in_shapes_list():
    for shape in ("false_accusation", "sacrifice_choice", "revelation", "tragedy_unfolding"):
        assert shape in _ALL_SHAPES


def test_gut_punch_shapes_selectable_via_seeded_rng():
    # When no keyword hints fire, _pick_shape falls through to rng.choice(_SHAPES).
    # The 4 new shapes must be reachable — seed until each appears (bounded loop).
    import random as _random
    found: set[str] = set()
    target = {"false_accusation", "sacrifice_choice", "revelation", "tragedy_unfolding"}
    for i in range(10_000):
        shape = questgen._pick_shape("neutral grievance text", "neutral hook note", _random.Random(i))
        if shape in target:
            found.add(shape)
        if found == target:
            break
    assert found == target, f"shapes not reached via rng.choice: {target - found}"
