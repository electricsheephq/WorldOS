"""WS0 behavior tests for qa/feature_engagement.engagement_coverage.

The keystone is INERT-in-scope: a run that was OWED a system (precondition true) but never
engaged it (detector false) must surface in `inert`. The CONDITIONAL N/A logic is the other
half: a short run / a solo party / a factionless world / a combat-sprint must read those
systems N/A — never inert — so the loop never false-flags a run that legitimately had no
occasion to engage a system.

Reuses the test_structural_coverage.py fixture SHAPE (engine-snapshot dicts) + the
sys.path.insert(qa) import. Stdlib + pytest only; single-process:
    uv run --directory servers/engine python -m pytest qa/test_feature_engagement.py -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_engagement as fe  # noqa: E402


# ── fixtures (engine-snapshot shape, mirroring test_structural_coverage.py) ───────────────────

def _inert_state() -> dict:
    """A run OWED several systems that never fired: a companion in the party stuck at attitude 0
    (with an authored agenda that never fired), nobody ever long-rested, a multi-location quest
    left `active`, a joinable faction seeded but never joined, a companion-quest-arc seeded but
    locked, a multi-day arc — the owner's dead-system failure shape."""
    return {
        "day": 6,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": -1},
            "comp1": {"name": "Brother Toll", "kind": "companion",
                      "attitude_value": 0, "last_long_rest_day": -1,
                      "approval_log": [],
                      "arc": {"arc_gates": [],
                              "agenda": {"trigger": "attitude_below", "value": -20,
                                         "fired": False}}},
        },
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "active",
                          "objectives": ["x"], "completed_objectives": []}},
        "locations": {"a": {"name": "The Grove", "visited": True},
                      "b": {"name": "The Road", "visited": True}},
        "factions": {"f1": {"name": "The Order", "joined": False, "standing": 0,
                            "questline_arc_id": "farc1"}},
        "faction_arcs": {"farc1": {"faction_id": "f1", "title": "Rise", "status": "locked",
                                   "stages": [{"title": "join", "status": "locked"}]}},
        "companion_quest_arcs": {"cq1": {"companion_id": "comp1", "title": "Toll's Vow",
                                         "status": "locked",
                                         "stages": [{"title": "s1", "status": "locked"}]}},
        "decisions": [],
        "consequences": [],
    }


def _complete_state() -> dict:
    """A COMPLETE run: companion approval moved + camped, quest completed, faction joined with
    standing + its arc active, companion-quest-arc active, the agenda fired, a decision recorded,
    a consequence that came due (trigger_day 10 < final day 20) actually fired, the engine arc
    at act 3."""
    return {
        "day": 20,
        "party": ["pc1", "comp1"],
        "narrative_arc": {"act": 3},
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": 5},
            "comp1": {"name": "Karlach", "kind": "companion",
                      "attitude_value": 40, "last_long_rest_day": 5,
                      "approval_log": [{"day": 4, "cause": "mercy", "delta": 10, "new_value": 10}],
                      "arc": {"arc_gates": [],
                              "agenda": {"trigger": "attitude_below", "value": -20,
                                         "fired": True}}},
        },
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "completed",
                          "objectives": ["x"], "completed_objectives": ["x"],
                          "evolves_to": "the cult regroups"}},
        "locations": {
            "a": {"name": "The Emerald Grove (Act 1)", "visited": True},
            "b": {"name": "Moonrise Towers (Act 2)", "visited": True},
            "c": {"name": "The Lower City (Act 3)", "visited": True},
        },
        "factions": {"f1": {"name": "The Order", "joined": True, "standing": 5,
                            "questline_arc_id": "farc1"}},
        "faction_arcs": {"farc1": {"faction_id": "f1", "title": "Rise", "status": "active",
                                   "stages": [{"title": "join", "status": "resolved"}]}},
        "companion_quest_arcs": {"cq1": {"companion_id": "comp1", "title": "Karlach's Heart",
                                         "status": "active",
                                         "stages": [{"title": "s1", "status": "active"}]}},
        "decisions": [{"day": 10, "summary": "spare the cultist", "approval_tags": ["mercy"]}],
        "consequences": [{"trigger_day": 10, "fired": True, "text": "the cult regroups"}],
    }


def _solo_state() -> dict:
    """A solo run (no companion): companion-keyed systems must be N/A, not inert."""
    return {
        "day": 6,
        "party": ["pc1"],
        "characters": {"pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": -1}},
        "quests": {},
        "locations": {"a": {"name": "The Grove", "visited": True}},
        "factions": {},
        "decisions": [],
        "consequences": [],
    }


def _ids(block: dict, key: str) -> set:
    if key == "inert":
        return {x["id"] for x in block["inert"]}
    return set(block[key])


# ── (keystone) INERT-in-scope ─────────────────────────────────────────────────────────────────

def test_inert_in_scope_systems_are_listed_inert():
    """The keystone: a substantial run that was OWED systems but never engaged them lists them
    inert (not engaged, not N/A). companion_approval / camp_downtime / quests_objectives /
    factions_membership / companion_agenda / decisions_recorded are all owed and dead here."""
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    inert = _ids(block, "inert")
    for sid in ("companion_approval", "camp_downtime", "quests_objectives",
                "factions_membership", "companion_agenda", "decisions_recorded"):
        assert sid in inert, f"{sid} should be INERT in scope; block={block}"
    # None of those leaked into engaged.
    assert not (inert & _ids(block, "engaged"))
    # coverage denominator = engaged + inert (N/A excluded).
    eng, exp = block["coverage"].split("/")
    assert int(exp) == len(block["engaged"]) + len(block["inert"])


def test_inert_carries_why_and_severity_warn():
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    for item in block["inert"]:
        assert item["severity"] == "warn", item  # ALL-WARN this PR
        assert isinstance(item["why"], str) and item["why"]


def test_blocked_systems_inert_in_scope_but_warn():
    """faction_arc + companion_quest_arc CAN be inert (here the faction is unjoined so faction_arc
    is N/A, but companion_quest_arc is seeded+beats-owed and locked ⇒ inert) — and stay WARN."""
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    inert = {x["id"]: x for x in block["inert"]}
    # companion_quest_arc is seeded + beats>=10 ⇒ owed; locked ⇒ inert.
    assert "companion_quest_arc" in inert
    assert inert["companion_quest_arc"]["severity"] == "warn"
    # faction_arc precondition needs a JOINED faction; here unjoined ⇒ N/A (not inert).
    assert "faction_arc" in _ids(block, "na")


# ── COMPLETE → engaged ────────────────────────────────────────────────────────────────────────

def test_complete_run_engages_every_owed_system():
    block = fe.engagement_coverage(
        _complete_state(),
        tool_counts={"start_combat": 1, "check_companion_arc": 2},
        session_beats=30)
    engaged = _ids(block, "engaged")
    for sid in ("companion_approval", "camp_downtime", "quests_objectives", "acts_advance",
                "consequences_fired", "factions_membership", "faction_arc",
                "companion_quest_arc", "companion_agenda", "decisions_recorded"):
        assert sid in engaged, f"{sid} should be ENGAGED; block={block}"
    assert not block["inert"], block["inert"]
    assert block["coverage"] == f"{len(engaged)}/{len(engaged)}"


# ── CONDITIONAL N/A: short run (beats below threshold) ────────────────────────────────────────

def test_short_run_beats_keyed_systems_are_na_not_inert():
    """A 4-beat run is below every beats threshold (>=6/>=10/>=24): the beats-keyed systems must
    be N/A, never inert — a smoke test had no occasion to engage them."""
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=4)
    na = _ids(block, "na")
    for sid in ("companion_approval", "camp_downtime", "quests_objectives", "acts_advance",
                "factions_membership", "companion_quest_arc", "companion_agenda",
                "decisions_recorded"):
        assert sid in na, f"{sid} should be N/A on a 4-beat run; block={block}"
        assert sid not in _ids(block, "inert")


# ── CONDITIONAL N/A: session_beats=None ⇒ beats-keyed systems N/A (the inject callsite) ───────

def test_session_beats_none_makes_beats_keyed_systems_na():
    """The inject callsite passes session_beats=None (beats live in the transcript, not the
    snapshot). Every beats-keyed precondition must read N/A then — safe under-detect, never a
    false-RED. consequences_fired (snapshot-only precondition) is unaffected."""
    block = fe.engagement_coverage(_inert_state(), tool_counts=None, session_beats=None)
    na = _ids(block, "na")
    inert = _ids(block, "inert")
    for sid in ("companion_approval", "camp_downtime", "quests_objectives", "acts_advance",
                "factions_membership", "companion_quest_arc", "companion_agenda",
                "decisions_recorded"):
        assert sid in na, f"{sid} should be N/A when session_beats is None; block={block}"
        assert sid not in inert
    # consequences_fired has NO beats key — here there are no owed consequences ⇒ N/A.
    assert "consequences_fired" in na


# ── CONDITIONAL N/A: solo party (no companion) ────────────────────────────────────────────────

def test_solo_party_companion_systems_na():
    """A solo run: companion_approval / camp_downtime / companion_agenda have no companion to
    engage ⇒ N/A. decisions_recorded is still owed (beats>=10) ⇒ inert."""
    block = fe.engagement_coverage(_solo_state(), tool_counts={}, session_beats=12)
    na = _ids(block, "na")
    assert "companion_approval" in na
    assert "camp_downtime" in na
    assert "companion_agenda" in na
    assert "companion_quest_arc" in na  # none seeded
    assert "decisions_recorded" in _ids(block, "inert")


# ── CONDITIONAL N/A: factionless world ────────────────────────────────────────────────────────

def test_factionless_world_faction_systems_na():
    """No factions seeded ⇒ factions_membership + faction_arc N/A (never inert)."""
    block = fe.engagement_coverage(_solo_state(), tool_counts={}, session_beats=12)
    na = _ids(block, "na")
    assert "factions_membership" in na
    assert "faction_arc" in na


def test_faction_seeded_but_not_joinable_is_na():
    """A faction with an EMPTY questline_arc_id is not joinable ⇒ factions_membership N/A."""
    state = _inert_state()
    state["factions"] = {"f1": {"name": "Townsfolk", "joined": False, "standing": 0,
                                "questline_arc_id": ""}}
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "factions_membership" in _ids(block, "na")


# ── CONDITIONAL N/A: combat-sprint (WORLDOS_GATE_COMBAT_SPRINT) ────────────────────────────────

def test_combat_sprint_skips_fatal_systems(monkeypatch):
    """Under WORLDOS_GATE_COMBAT_SPRINT, FATAL systems are skipped (→ N/A). With every system
    WARN this PR it is a no-op for coverage, but the wiring must hold for safe graduation:
    monkeypatch one system FATAL and confirm it goes N/A under the env, inert without it."""
    spec = fe.SystemSpec("companion_approval", fe._pc_companion_approval,
                         fe._dt_companion_approval, "fatal")
    others = tuple(s for s in fe.SYSTEMS if s.id != "companion_approval")
    monkeypatch.setattr(fe, "SYSTEMS", (spec, *others))

    # Without the env, the FATAL system is owed-and-dead ⇒ inert.
    monkeypatch.delenv("WORLDOS_GATE_COMBAT_SPRINT", raising=False)
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    assert "companion_approval" in _ids(block, "inert")

    # Under the env, the FATAL system is SKIPPED ⇒ N/A (never inert).
    monkeypatch.setenv("WORLDOS_GATE_COMBAT_SPRINT", "1")
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    assert "companion_approval" in _ids(block, "na")
    assert "companion_approval" not in _ids(block, "inert")


def test_combat_sprint_keeps_warn_systems_reporting(monkeypatch):
    """WARN systems still report under the env (they can't false-RED) — matching
    assert_behavioral's fatal/warn split. The all-WARN manifest is unchanged by the env."""
    monkeypatch.setenv("WORLDOS_GATE_COMBAT_SPRINT", "1")
    block = fe.engagement_coverage(_inert_state(), tool_counts={}, session_beats=12)
    # decisions_recorded is WARN + owed + dead ⇒ still inert under the env.
    assert "decisions_recorded" in _ids(block, "inert")


# ── consequences: due-vs-future (STRICT < final day) ──────────────────────────────────────────

def test_consequence_due_before_final_day_and_unfired_is_inert():
    """A consequence whose trigger_day is STRICTLY < the final day but fired=False is OWED ⇒
    inert."""
    state = _inert_state()
    state["day"] = 10
    state["consequences"] = [{"trigger_day": 5, "fired": False, "text": "the cult regroups"}]
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "consequences_fired" in _ids(block, "inert")


def test_consequence_on_final_day_is_warn_not_owed_so_na():
    """trigger_day == final_day is WARN-not-owed (it may legitimately fire on the unseen last
    beat) ⇒ the precondition is False ⇒ N/A, never inert."""
    state = _inert_state()
    state["day"] = 5
    state["consequences"] = [{"trigger_day": 5, "fired": False}]
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "consequences_fired" in _ids(block, "na")
    assert "consequences_fired" not in _ids(block, "inert")


def test_future_dated_consequence_is_na():
    """A future-dated consequence (trigger_day > final day) is not yet due ⇒ N/A."""
    state = _inert_state()
    state["day"] = 5
    state["consequences"] = [{"trigger_day": 30, "fired": False}]
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "consequences_fired" in _ids(block, "na")


def test_consequence_due_and_fired_is_engaged():
    state = _inert_state()
    state["day"] = 10
    state["consequences"] = [{"trigger_day": 5, "fired": True}]
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "consequences_fired" in _ids(block, "engaged")


def test_threadtagged_past_due_consequence_is_na_not_inert():
    """A re-armed worldsim standing-thread consequence (thread_id set) sits past-due with fired=False
    FOREVER by design — worldsim.tick rolls its trigger_day forward IN PLACE — so the engine's own
    due/overdue contract excludes threads (consequences.due `not c.thread_id`; scene_debt thread skip).
    It must be N/A, never inert, else a perfectly healthy living world false-INERTs consequences_fired."""
    state = _inert_state()
    state["day"] = 10
    state["consequences"] = [
        {"trigger_day": 5, "fired": False, "thread_id": "thr_cult", "text": "the cult regroups"},
    ]
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=12)
    assert "consequences_fired" in _ids(block, "na")
    assert "consequences_fired" not in _ids(block, "inert")


# ── narrative_arc absent / None null-guard ────────────────────────────────────────────────────

def test_narrative_arc_absent_does_not_crash_acts_advance():
    """No narrative_arc key at all: acts_advance falls back to act-tags (none here on the inert
    fixture's untagged world) and never raises. With a long run + no arc + <2 tag-acts ⇒ N/A."""
    state = _inert_state()
    state.pop("narrative_arc", None)
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=30)
    # untagged world, no engine arc ⇒ no occasion ⇒ N/A (never inert, never a crash).
    assert "acts_advance" in _ids(block, "na")


def test_narrative_arc_explicit_none_null_guarded():
    """narrative_arc explicitly None must degrade to the empty cursor (felt_shape_from_state's
    guard), never raise."""
    state = _inert_state()
    state["narrative_arc"] = None
    block = fe.engagement_coverage(state, tool_counts={}, session_beats=30)
    assert isinstance(block["coverage"], str)  # computed without raising


def test_empty_state_is_all_na_not_crash():
    """An empty/legacy snapshot yields an all-N/A (or harmlessly-inert snapshot-only) block and
    never raises — old snapshots round-trip."""
    block = fe.engagement_coverage({}, tool_counts=None, session_beats=None)
    assert isinstance(block["coverage"], str)
    assert not block["engaged"]
    assert not block["inert"]  # nothing owed in an empty snapshot with no beats


# ── tool-count-only engagement (the detector reads counts when state is silent) ───────────────

def test_tool_counts_alone_can_engage_a_system():
    """A run whose snapshot is silent on a system but whose DM CALLED the engaging tool reads
    engaged (the detector reads tool-counts as a fallback signal)."""
    state = _inert_state()
    # The DM joined the faction via the tool but the snapshot latch wasn't captured here.
    block = fe.engagement_coverage(state, tool_counts={"join_faction": 1}, session_beats=12)
    assert "factions_membership" in _ids(block, "engaged")
