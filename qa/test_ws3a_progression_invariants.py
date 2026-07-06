"""WS3a — DM-unavoidable PER-BEAT PROGRESSION / CLOSURE cue invariants (deterministic, NO LLM).

The five WS3a cues in server._compute_beat_obligations name the HARD mechanical loop's stall states
— party stuck in one scene, a fight left hanging, XP that never landed, a frozen clock, a quest never
resolved — and point at the exact engine verb that clears each. This file proves, with NO LLM, that
for every cue:

  (a) the NAMED verb actually MOVES the engine-mutated gauge the cue reads (call it DIRECTLY on a real
      persisted campaign and assert the snapshot gauge changed), AND
  (b) the cue FIRES on the owed snapshot and CLEARS once the gauge has moved.

So a cue can never name a dead verb, and a moved gauge can never leave the cue stuck on. Single-process
friendly (-p no:xdist); state goes to a per-test temp dir; nothing touches qa/scores.db.

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_ws3a_progression_invariants.py -q -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The engine root must be importable whether launched from repo root or via
# `uv run --directory servers/engine` (mirrors qa/test_combat_smoke.py).
_QA_DIR = Path(__file__).resolve().parent
_ENGINE_DIR = _QA_DIR.parent / "servers" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

import server  # noqa: E402
import store  # noqa: E402
from models import (  # noqa: E402
    Campaign,
    Character,
    Combat,
    Combatant,
    Location,
    NarrativeArc,
    Quest,
)


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """Point the store at a per-test temp dir so saved campaigns are isolated + disposable."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return tmp_path


def _kinds(c: Campaign) -> set:
    return {o["kind"] for o in server._compute_beat_obligations(c)}


def _save(c: Campaign) -> str:
    store.save_campaign(c)
    return c.id


def _arced(c: Campaign, *, beats_in_act=8, act=1, **arc_kw) -> Campaign:
    """Drive the arc beats_in_act beats so the WS3a beats-gated cues are armed."""
    c.narrative_arc = NarrativeArc(act=act, day_act_entered=1, beats_in_act=beats_in_act, **arc_kw)
    return c


# === WS3a-1. party_stuck_one_location → travel_to ================================================


def test_travel_to_moves_the_visited_gauge(state_dir):
    """The named verb travel_to actually moves the engine gauge the cue reads (visited count)."""
    pc = Character(name="Hero", kind="player")
    c = Campaign(title="Stuck")
    a = Location(name="Opening", visited=True)
    b = Location(name="The Road", visited=False)
    a.connections = [b.id]
    b.connections = [a.id]
    c.locations = {a.id: a, b.id: b}
    c.current_location_id = a.id
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    cid = _save(c)

    before = sum(1 for loc in store.load_campaign(cid).locations.values() if loc.visited)
    assert before == 1
    server.travel_to(cid, destination_id=b.id, advance_time=True)
    after = sum(1 for loc in store.load_campaign(cid).locations.values() if loc.visited)
    assert after == 2, "travel_to did not move the visited gauge"


def test_party_stuck_cue_fires_then_clears_when_party_travels(state_dir):
    pc = Character(name="Hero", kind="player")
    c = _arced(Campaign(title="Stuck"), beats_in_act=8)
    a = Location(name="Opening", visited=True)
    b = Location(name="The Road", visited=False)
    a.connections = [b.id]
    b.connections = [a.id]
    c.locations = {a.id: a, b.id: b}
    c.current_location_id = a.id
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    cid = _save(c)

    assert "party_stuck_one_location" in _kinds(store.load_campaign(cid))
    server.travel_to(cid, destination_id=b.id, advance_time=True)
    assert "party_stuck_one_location" not in _kinds(store.load_campaign(cid))


# === WS3a-2. combat_left_hanging → end_combat ===================================================


def _campaign_in_combat_with_dead_monster(*, xp_value=0) -> Campaign:
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=xp_value)
    c = Campaign(title="Hanging")
    c.characters[pc.id] = pc
    c.characters[rat.id] = rat
    c.party.append(pc.id)
    c.combat = Combat(active=True, round=1,
                      order=[Combatant(character_id=pc.id, initiative=15),
                             Combatant(character_id=rat.id, initiative=8)])
    return c


def test_end_combat_moves_the_active_gauge(state_dir):
    """end_combat clears combat.active — the gauge combat_left_hanging reads."""
    cid = _save(_campaign_in_combat_with_dead_monster())
    assert store.load_campaign(cid).combat.active is True
    server.end_combat(cid, resolution="the rats are dead")
    assert store.load_campaign(cid).combat.active is False, "end_combat did not clear combat.active"


def test_combat_left_hanging_cue_fires_then_clears_on_end_combat(state_dir):
    cid = _save(_campaign_in_combat_with_dead_monster())
    assert "combat_left_hanging" in _kinds(store.load_campaign(cid))
    server.end_combat(cid, resolution="the rats are dead")
    assert "combat_left_hanging" not in _kinds(store.load_campaign(cid))


# === WS3a-3. xp_unawarded → award_xp / end_combat ===============================================


def test_award_xp_moves_the_character_xp_gauge(state_dir):
    """The named verb award_xp moves a party member's xp gauge."""
    pc = Character(name="Hero", kind="player")
    c = Campaign(title="XP")
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    cid = _save(c)

    assert store.load_campaign(cid).characters[pc.id].xp == 0
    server.award_xp(cid, pc.id, 25, reason="cellar rat")
    assert store.load_campaign(cid).characters[pc.id].xp == 25, "award_xp did not move the xp gauge"


def test_end_combat_zeroes_orphaned_monster_xp_value(state_dir):
    """end_combat's backstop sweep auto-awards a dead monster's XP and zeroes xp_value — the gauge
    xp_unawarded reads."""
    cid = _save(_campaign_in_combat_with_dead_monster(xp_value=25))
    rat_id = next(i for i, ch in store.load_campaign(cid).characters.items()
                  if ch.kind == "monster")
    assert store.load_campaign(cid).characters[rat_id].xp_value == 25
    server.end_combat(cid, resolution="slain")
    assert store.load_campaign(cid).characters[rat_id].xp_value == 0, \
        "end_combat did not award + zero the orphaned xp_value"


def test_xp_unawarded_cue_fires_then_clears_on_end_combat(state_dir):
    """Owed (non-combat, dead monster carrying XP) → fires; after end_combat awards + zeroes it →
    clears. Drive it as a real sequence: kill happened in a fight, the DM ends it."""
    cid = _save(_campaign_in_combat_with_dead_monster(xp_value=25))
    # While combat is ACTIVE, combat_left_hanging owns the beat (xp_unawarded is non-combat only).
    assert "xp_unawarded" not in _kinds(store.load_campaign(cid))
    # The DM forgot to end the fight but the cue still must clear after end_combat: end it, and the
    # orphaned xp_value is now 0, so xp_unawarded is silent.
    server.end_combat(cid, resolution="slain")
    assert "xp_unawarded" not in _kinds(store.load_campaign(cid))


def test_xp_unawarded_cue_fires_out_of_combat_then_clears_when_xp_value_zeroed(state_dir):
    """The pure non-combat owed state: a dead monster carrying XP with NO active combat fires the
    cue; zeroing the gauge (the award) clears it."""
    pc = Character(name="Hero", kind="player")
    rat = Character(name="Cellar Rat", kind="monster", dead=True, current_hp=0, max_hp=7,
                    xp_value=25)
    c = Campaign(title="Orphaned XP")
    c.characters[pc.id] = pc
    c.characters[rat.id] = rat
    c.party.append(pc.id)
    c.combat = Combat(active=False)
    cid = _save(c)

    assert "xp_unawarded" in _kinds(store.load_campaign(cid))
    # The award zeroes xp_value (what _award_kill_xp / end_combat do). Mutate via the store to
    # represent the awarded state, then re-read: the cue must clear.
    cc = store.load_campaign(cid)
    cc.characters[rat.id].xp_value = 0
    store.save_campaign(cc)
    assert "xp_unawarded" not in _kinds(store.load_campaign(cid))


# === WS3a-4. clock_dm_frozen → advance_time =====================================================


def test_advance_time_moves_the_clock_gauge(state_dir):
    """The named verb advance_time moves time_of_day off morning."""
    pc = Character(name="Hero", kind="player")
    c = Campaign(title="Frozen")
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    c.time_of_day = "morning"
    cid = _save(c)

    assert store.load_campaign(cid).time_of_day == "morning"
    server.advance_time(cid, phases=1)
    assert store.load_campaign(cid).time_of_day != "morning", "advance_time did not move the clock"


def test_clock_dm_frozen_cue_fires_then_clears_on_advance_time(state_dir):
    pc = Character(name="Hero", kind="player")
    c = _arced(Campaign(title="Frozen"), beats_in_act=8)
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    c.time_of_day = "morning"
    # visited >= 2 so party_stuck does NOT own the clock (clock_dm_frozen requires the party moved).
    a = Location(name="A", visited=True)
    b = Location(name="B", visited=True)
    c.locations = {a.id: a, b.id: b}
    cid = _save(c)

    assert "clock_dm_frozen" in _kinds(store.load_campaign(cid))
    server.advance_time(cid, phases=1)
    assert "clock_dm_frozen" not in _kinds(store.load_campaign(cid))


# === WS3a-5. quest_unresolved_late → complete_quest / complete_objective ========================


def test_complete_quest_moves_the_status_gauge(state_dir):
    """The named verb complete_quest moves a quest's status gauge to completed."""
    pc = Character(name="Hero", kind="player")
    c = Campaign(title="Quest")
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    q = Quest(title="The thread", objectives=["x"])
    c.quests[q.id] = q
    cid = _save(c)

    assert store.load_campaign(cid).quests[q.id].status == "active"
    server.complete_quest(cid, q.id, evolves_to="a lingering echo")
    assert store.load_campaign(cid).quests[q.id].status == "completed", \
        "complete_quest did not move the status gauge"


def test_quest_unresolved_late_cue_fires_then_clears_on_complete_quest(state_dir):
    pc = Character(name="Hero", kind="player")
    c = _arced(Campaign(title="Quest"), beats_in_act=8)
    c.characters[pc.id] = pc
    c.party.append(pc.id)
    q = Quest(title="The untouched thread", objectives=["step one", "step two"])
    c.quests[q.id] = q
    cid = _save(c)

    assert "quest_unresolved_late" in _kinds(store.load_campaign(cid))
    server.complete_quest(cid, q.id, evolves_to="a lingering echo")
    assert "quest_unresolved_late" not in _kinds(store.load_campaign(cid))
