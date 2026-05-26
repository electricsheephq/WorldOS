"""Generativity spike — deliverable B: The Tidal Commonwealth.

Proves that a SECOND original world seed (content/worlds/tidal-commonwealth/world.json)
seeds and runs with ZERO engine code changes: all living-story surfaces (events,
faction_arcs, quest_variants, world_graph, settlements, npc_roster / dossier) exercise
the existing engine byte-for-byte, no new machinery required.

Assertions:
  1. seed_world returns a Campaign with NO skip-diagnostics ([content] skipping ...).
  2. present_events surfaces the authored event (manual trigger).
  3. Resolving an Outcome sets the decision_flag, shifts faction reputation, and schedules
     a follow-on Consequence.
  4. The authored faction arc gates (stage locked below unlock_at, available at/above).
  5. The quest_variant resolves (at least one outcome stored in c.quest_outcomes).
  6. NPC roster + companion dossier seeded correctly (4 NPCs, dossier on the companion).

Single-process only (the host OOMs on parallel pytest; never -n / xdist).
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

import content as content_mod
import events as events_mod
import faction_arc as fa
from models import Campaign, Faction, FactionArc, FactionArcStage

# ---------------------------------------------------------------------------
# constants — world + authored ids live in CONTENT, not engine code
# ---------------------------------------------------------------------------

WORLD_ID = "tidal-commonwealth"
EVENT_ID = "event-league-cache"
FACTION_ARC_ID = "arc-salvagers-rise"
FACTION_ID = "fac-salvagers-league"
QUEST_VARIANT_ID = "the-first-surfacing"
NPC_COMPANION_ID = "npc-captain-drev"
COMPACT_FACTION_ID = "fac-compact"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_world() -> dict:
    """Load the world dict; skip cleanly if content dir isn't reachable."""
    try:
        return content_mod.load_world_data(WORLD_ID)
    except (ValueError, FileNotFoundError, OSError):  # pragma: no cover
        pytest.skip(f"{WORLD_ID!r} world content not reachable from test cwd")


def _seed_world(world: dict) -> tuple[Campaign, str]:
    """Seed the world, capturing stdout to detect [content] skipping lines."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        c = content_mod.seed_world(world)
    return c, buf.getvalue()


# ---------------------------------------------------------------------------
# 1. zero skip-diagnostics
# ---------------------------------------------------------------------------


def test_seed_world_zero_skip_diagnostics():
    """seed_world must return a Campaign with NO [content] skipping lines.

    A skip-line means a dangling ref or malformed block — the author's first
    indicator that their world.json has an authoring error to fix.
    """
    world = _load_world()
    c, stdout = _seed_world(world)
    skip_lines = [ln for ln in stdout.splitlines() if "[content] skipping" in ln]
    assert skip_lines == [], (
        "seed_world emitted skip-diagnostics — dangling refs or malformed blocks:\n"
        + "\n".join(skip_lines)
    )
    assert isinstance(c, Campaign)
    assert c.world_id == WORLD_ID


# ---------------------------------------------------------------------------
# 2. events: present_events surfaces the authored event
# ---------------------------------------------------------------------------


def test_event_surfaces_via_present_events():
    """The authored manual-trigger event must surface via events_mod.present."""
    world = _load_world()
    c, _ = _seed_world(world)
    assert EVENT_ID in c.events, f"event {EVENT_ID!r} not seeded"
    ev = c.events[EVENT_ID]
    present_ids = [e.id for e in events_mod.present(c)]
    assert EVENT_ID in present_ids, (
        f"{EVENT_ID!r} not in present_events; present={present_ids}"
    )
    assert ev.anchor_npc_id == NPC_COMPANION_ID


def test_event_has_three_options_including_refuse_path():
    """The authored event must carry >= 2 options including a clean 'not your problem' path."""
    world = _load_world()
    c, _ = _seed_world(world)
    ev = c.events[EVENT_ID]
    assert len(ev.options) >= 2
    # at least one option has no faction_id or reputation_delta (the refuse/walk-away path)
    no_ripple = [o for o in ev.options if not o.outcome.faction_id and o.outcome.reputation_delta == 0]
    assert no_ripple, "event must have at least one option with no faction ripple (the refuse path)"


# ---------------------------------------------------------------------------
# 3. resolve_event: flag + reputation + consequence schedule
# ---------------------------------------------------------------------------


def test_resolve_event_sets_flag_shifts_faction_schedules_consequence():
    """Resolving the 'help the captain' option sets the decision_flag, shifts
    fac-salvagers-league reputation, and schedules a follow-on consequence.
    Mirrors test_event_parley_layer3.py assertions."""
    world = _load_world()
    c, _ = _seed_world(world)
    ev = c.events[EVENT_ID]

    # pick the 'help' option — the first option (trust_over_procedure)
    # find_option is exact case-insensitive; the full label includes the em-dash clause
    help_option = next(
        (o for o in ev.options if o.label.casefold().startswith("help him move it")),
        None,
    )
    assert help_option is not None, (
        "could not find the 'Help him move it' option; labels="
        + str([o.label for o in ev.options])
    )

    pre_rep = c.factions[FACTION_ID].reputation
    res = events_mod.resolve(c, ev, help_option)

    # flag set
    assert c.flags.get("trust_over_procedure") is True, "decision_flag not set"
    # faction reputation shifted
    post_rep = c.factions[FACTION_ID].reputation
    assert post_rep < pre_rep, f"expected reputation to fall; pre={pre_rep} post={post_rep}"
    # consequence scheduled
    followups = [co for co in c.consequences if "League" in co.text or "invoice" in co.text.lower() or "factors" in co.text.lower()]
    assert followups, "follow-on consequence not scheduled"
    # event marked resolved → idempotent (the pure module sets the latch)
    assert ev.resolved is True


# ---------------------------------------------------------------------------
# 4. faction arc gates
# ---------------------------------------------------------------------------


def test_faction_arc_seeded_and_gates_correctly():
    """The authored faction arc for fac-salvagers-league must seed and gate:
    - stage 1 (reputation gate at 12) is locked below 12 and available at 12.
    - stage 2 (standing gate at 20) is locked when standing < 20.
    Mirrors test_faction_arcs.py gate assertions."""
    world = _load_world()
    c, _ = _seed_world(world)

    assert FACTION_ARC_ID in c.faction_arcs, f"arc {FACTION_ARC_ID!r} not seeded"
    arc = c.faction_arcs[FACTION_ARC_ID]
    fac = c.factions[FACTION_ID]

    # The faction must link back to its arc
    assert fac.questline_arc_id == FACTION_ARC_ID

    # Stage 1: reputation gate at 12
    stage1 = arc.stages[0]
    assert stage1.gauge == "reputation"
    assert stage1.unlock_at == 12

    fac.reputation = 11
    assert fa.stage_gate_holds(stage1, fac) is False, "reputation 11 < 12: should be locked"
    fac.reputation = 12
    assert fa.stage_gate_holds(stage1, fac) is True, "reputation 12 >= 12: should be available"

    # Stage 2: standing gate — locked when standing < 20 even at max reputation
    stage2 = arc.stages[1]
    assert stage2.gauge == "standing"
    assert stage2.unlock_at == 20

    fac.reputation = 100  # reputation high
    fac.standing = 19
    assert fa.stage_gate_holds(stage2, fac) is False, "standing 19 < 20: should be locked"
    fac.standing = 20
    assert fa.stage_gate_holds(stage2, fac) is True, "standing 20 >= 20: should be available"


def test_faction_arc_requires_join_before_advancing():
    """The requires_joined arc must not advance before join_faction."""
    world = _load_world()
    c, _ = _seed_world(world)
    arc = c.faction_arcs[FACTION_ARC_ID]
    fac = c.factions[FACTION_ID]

    # give plenty of reputation but don't join
    fac.reputation = 100
    fac.joined = False
    res = fa.evaluate(arc, c)
    assert res["newly_available"] == [], "arc must stay locked until faction is joined"


# ---------------------------------------------------------------------------
# 5. quest_variants: at least one outcome is resolved
# ---------------------------------------------------------------------------


def test_quest_variant_resolves():
    """The quest_variant 'the-first-surfacing' must resolve to one of its authored outcomes."""
    world = _load_world()
    c, _ = _seed_world(world)
    assert QUEST_VARIANT_ID in c.quest_outcomes, (
        f"quest_variant {QUEST_VARIANT_ID!r} not resolved; quest_outcomes={c.quest_outcomes}"
    )
    outcome_id = c.quest_outcomes[QUEST_VARIANT_ID]
    valid_ids = {"cache-under-study", "cache-in-vethis-hands", "cache-lost-overboard"}
    assert outcome_id in valid_ids, f"unexpected outcome id {outcome_id!r}"


# ---------------------------------------------------------------------------
# 6. NPC roster + companion dossier seeded correctly
# ---------------------------------------------------------------------------


def test_npc_roster_seeded_with_dossier():
    """All 4 authored roster NPCs must be seeded; the companion (npc-captain-drev) must carry
    its companion_dossier (wound, wants, fears, values, banter_tags, camp_prompts, relationships)."""
    world = _load_world()
    c, _ = _seed_world(world)

    roster_ids = ["npc-warden-estris", "npc-captain-drev", "npc-mira-scroll", "npc-tribune-kessal"]
    for nid in roster_ids:
        assert nid in c.characters, f"roster NPC {nid!r} not seeded"

    companion = c.characters[NPC_COMPANION_ID]
    assert companion.companion_dossier is not None, "companion dossier not seeded"
    d = companion.companion_dossier
    assert d.wound, "dossier wound must not be empty"
    assert d.wants, "dossier wants must not be empty"
    assert d.fears, "dossier fears must not be empty"
    assert d.values, "dossier values must not be empty"
    assert d.banter_tags, "dossier banter_tags must not be empty"
    assert d.camp_prompts, "dossier camp_prompts must not be empty"
    assert d.relationships, "dossier relationships must not be empty"


# ---------------------------------------------------------------------------
# 7. starting_options respected (current_location_id is the first starting option)
# ---------------------------------------------------------------------------


def test_starting_location_set():
    """The world's first starting_option (loc-saltmere) must be the current_location_id."""
    world = _load_world()
    c, _ = _seed_world(world)
    assert c.current_location_id == "loc-saltmere"
    assert c.locations["loc-saltmere"].visited is True


# ---------------------------------------------------------------------------
# 8. ADDITIVE: no engine changes (belt-and-suspenders round-trip)
# ---------------------------------------------------------------------------


def test_campaign_round_trips_after_second_seed():
    """A campaign seeded from the second world must survive a model_dump / model_validate
    round-trip — proving the additive-default contract (no new required fields)."""
    world = _load_world()
    c, _ = _seed_world(world)
    data = c.model_dump(mode="json")
    reloaded = Campaign.model_validate(data)
    assert reloaded.world_id == WORLD_ID
    assert EVENT_ID in reloaded.events
    assert FACTION_ARC_ID in reloaded.faction_arcs


# ---------------------------------------------------------------------------
# 9. #221: a companion arc/agenda arms from the BASE world.json (no ending overlay)
# ---------------------------------------------------------------------------


def test_base_world_companion_arc_arms_without_an_ending():
    """The generativity boundary the spike surfaced: a CompanionAgenda (the sealed flip)
    used to require an ending overlay's companion_seeds. #221 lets the base npc_roster carry
    an `arc`, so a world with NO endings can still have a companion who turns. The Tidal
    Commonwealth's Captain Drev must arm his decision-gated flip from the seed alone."""
    world = _load_world()
    c, _ = _seed_world(world)
    drev = c.characters[NPC_COMPANION_ID]
    assert drev.arc is not None, "base-world npc_roster `arc` did not seed onto the companion"
    ag = drev.arc.agenda
    assert ag is not None and ag.trigger == "attitude_below"
    assert ag.value == -25, "breaking point must sit inside the [-40,-20] warn band so it telegraphs"
    # the L3->L2 seam: the agenda is decision-gated, and the gating flag is set by an event option
    assert ag.decision_flag == "left_drev_exposed"
    flag_setters = [
        o.outcome.decision_flag
        for ev in c.events.values()
        for o in ev.options
        if o.outcome.decision_flag
    ]
    assert "left_drev_exposed" in flag_setters, (
        "the agenda's decision_flag must be reachable from an authored event option (the L3->L2 seam)"
    )
    # the relationship gates (loyalty + personal-quest) seeded too — a full companion, not just a flip
    gate_kinds = {g.kind for g in drev.arc.arc_gates}
    assert {"loyalty", "personal_quest"} <= gate_kinds
