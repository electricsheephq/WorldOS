"""#1405 — quest AUTHORING & consequence-CAPTURE cues (the flywheel's binding constraint).

The measured failure (PR #1404 honest re-score): extraction is no longer the binding constraint
on the 3.9→4.0 quest-promotion gap — CONTENT AUTHORING is. Objectives empty in 2/3 sampled
quests and consequences empty in 3/3 AT THE SNAPSHOT LEVEL (no engine data to extract). The fix
is the same cue-stack family S1 proved works (#1313/#1286/#1334): ADVISORY return-payload nudges
that the DM may act on — never engine-judged fiction, never a block.

These guard the two seams:
  (a) add_quest emits an `authoring_cue` when a quest is created MISSING
      objectives / giver_id / location_id (and _compute_beat_obligations surfaces the
      load-bearing objectives gap as a per-beat quest_authoring_incomplete cue);
  (b) complete_quest / complete_objective / record_decision emit a `consequence_cue` when a
      resolved quest has no recorded branch outcome (empty evolves_to + no naming consequence).

THE ADDITIVE INVARIANT is load-bearing: a fully-authored / fully-captured quest returns
BYTE-IDENTICAL to today (the cue key is simply absent). Every test that asserts a happy-path
shape checks the EXACT dict, not just cue-absence.
"""

import pytest

import server
from models import Campaign, Consequence, Quest


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("Authoring Cues")["id"]


# ── (a) add_quest authoring cue ──────────────────────────────────────────────────────────────


def test_add_quest_bare_emits_authoring_cue_for_all_three_fields(cid):
    out = server.add_quest(cid, "A Word from the Docks")
    assert "authoring_cue" in out
    cue = out["authoring_cue"]
    assert cue["kind"] == "quest_authoring_incomplete"
    assert cue["severity"] == "med"
    assert cue["missing"] == ["objectives", "giver_id", "location_id"]
    assert "objectives" in cue["imperative"]


def test_add_quest_partial_missing_only_names_the_empty_fields(cid):
    # objectives supplied, giver + location omitted → only those two are flagged.
    out = server.add_quest(cid, "A Half-Authored Job", objectives=["ask around the docks"])
    assert out["authoring_cue"]["missing"] == ["giver_id", "location_id"]


def test_add_quest_complete_is_byte_identical_no_cue(cid):
    # A giver NPC + a location so add_quest can be fully authored.
    npc = server.create_character(campaign_id=cid, name="Longshoreman", kind="npc")
    loc = server.add_location(campaign_id=cid, name="The Docks")
    out = server.add_quest(
        cid, "A Fully Authored Job",
        giver_id=npc["id"], location_id=loc["id"], objectives=["meet the contact"],
    )
    # BYTE-IDENTICAL: the exact three keys, no authoring_cue.
    assert set(out.keys()) == {"id", "title", "status"}
    assert "authoring_cue" not in out


# ── (a) per-beat obligation: quest_authoring_incomplete ──────────────────────────────────────


def _kinds(obligations):
    return {o["kind"] for o in obligations}


def _obligations_at_authoring_beats(c):
    # The per-beat authoring cue is beats-gated (a fresh beat-0 quest is not nagged); park the arc
    # at the escalation threshold so the cue is live.
    c.narrative_arc.beats_in_act = server._QUEST_AUTHORING_BEATS
    return server._compute_beat_obligations(c)


def test_beat_obligation_fires_for_objectiveless_active_quest():
    c = Campaign(title="Obligations")
    q = Quest(title="A spine-less thread")  # no objectives
    c.quests[q.id] = q
    obligations = _obligations_at_authoring_beats(c)
    assert "quest_authoring_incomplete" in _kinds(obligations)
    cue = next(o for o in obligations if o["kind"] == "quest_authoring_incomplete")
    assert cue["quest_id"] == q.id
    assert "objectives" in cue["missing"]


def test_beat_obligation_not_fired_beat_zero_for_fresh_quest():
    # A freshly-introduced spine-less quest (arc at beat 0) is NOT nagged — the create-moment
    # nudge is add_quest's result cue; the per-beat cue only escalates after a stretch of play.
    c = Campaign(title="Obligations")
    q = Quest(title="Just introduced")  # no objectives, arc.beats_in_act == 0
    c.quests[q.id] = q
    assert "quest_authoring_incomplete" not in _kinds(server._compute_beat_obligations(c))


def test_beat_obligation_absent_for_authored_quest():
    c = Campaign(title="Obligations")
    q = Quest(title="An authored thread", objectives=["step one"])
    c.quests[q.id] = q
    assert "quest_authoring_incomplete" not in _kinds(_obligations_at_authoring_beats(c))


def test_beat_obligation_suppresses_stalled_on_same_spineless_quest():
    # A spine-less quest that is also stale must surface ONLY the authoring cue (it owns the quest),
    # not ALSO quest_stalled — the anti-double-nag `continue`.
    c = Campaign(title="Obligations", day=10)
    q = Quest(title="Old and empty", last_progress_day=1)  # no objectives, stale
    c.quests[q.id] = q
    kinds = _kinds(_obligations_at_authoring_beats(c))
    assert "quest_authoring_incomplete" in kinds
    assert "quest_stalled" not in kinds


# ── (b) complete_quest consequence cue ───────────────────────────────────────────────────────


def test_complete_quest_no_consequence_emits_capture_cue(cid):
    qid = server.add_quest(cid, "The Silenced Bell", objectives=["find the saboteur"])["id"]
    out = server.complete_quest(cid, qid)
    assert "consequence_cue" in out
    cue = out["consequence_cue"]
    assert cue["kind"] == "quest_consequence_uncaptured"
    assert cue["quest_id"] == qid


def test_complete_quest_with_evolves_to_has_no_cue(cid):
    qid = server.add_quest(cid, "The Silenced Bell", objectives=["find the saboteur"])["id"]
    out = server.complete_quest(cid, qid, evolves_to="the saboteur's patron seeks revenge")
    assert "consequence_cue" not in out


def test_complete_quest_failed_also_nudges_capture(cid):
    # A FAILED resolution is a branch outcome too — it should nudge for a recorded consequence.
    qid = server.add_quest(cid, "The Lost Caravan", objectives=["reach the caravan in time"])["id"]
    out = server.complete_quest(cid, qid, status="failed")
    assert out["consequence_cue"]["kind"] == "quest_consequence_uncaptured"


def test_complete_quest_with_naming_consequence_has_no_cue(cid):
    qid = server.add_quest(cid, "The Silenced Bell", objectives=["find the saboteur"])["id"]
    # A consequence whose text names the quest counts as a recorded branch outcome.
    server.add_consequence(cid, in_days=3, text="Fallout from The Silenced Bell reaches the temple.")
    out = server.complete_quest(cid, qid)
    assert "consequence_cue" not in out


# ── (b) complete_objective auto-resolve consequence cue ──────────────────────────────────────


def test_complete_objective_autoresolve_emits_capture_cue(cid):
    qid = server.add_quest(cid, "One-Step Quest", objectives=["do the thing"])["id"]
    out = server.complete_objective(cid, qid, "do the thing")  # last objective → auto-completes
    assert out["status"] == "completed"
    assert out["consequence_cue"]["kind"] == "quest_consequence_uncaptured"


def test_complete_objective_nonterminal_has_no_cue(cid):
    qid = server.add_quest(cid, "Two-Step Quest", objectives=["step one", "step two"])["id"]
    out = server.complete_objective(cid, qid, "step one")  # quest still active
    assert out["status"] == "active"
    assert "consequence_cue" not in out


# ── (b) record_decision consequence cue (campaign-scoped) ────────────────────────────────────


def test_record_decision_nudges_when_a_resolved_quest_is_uncaptured(cid):
    qid = server.add_quest(cid, "The Silenced Bell", objectives=["x"])["id"]
    server.complete_quest(cid, qid)  # resolved, no consequence
    out = server.record_decision(cid, summary="The party spared the saboteur")
    assert out["consequence_cue"]["quest_id"] == qid


def test_record_decision_byte_identical_when_all_captured(cid):
    # No resolved-but-uncaptured quest → record_decision returns its exact today shape.
    qid = server.add_quest(cid, "The Silenced Bell", objectives=["x"])["id"]
    server.complete_quest(cid, qid, evolves_to="a lingering echo")
    out = server.record_decision(cid, summary="A plain decision, nothing owed")
    assert set(out.keys()) == {"id", "summary", "chosen", "day"}
    assert "consequence_cue" not in out


# ── helper unit tests (the additive invariant, at the source) ────────────────────────────────


def test_quest_missing_fields_empty_for_full_quest():
    q = Quest(title="Full", objectives=["a"], giver_id="npc_1", location_id="loc_1")
    assert server._quest_missing_fields(q) == []


def test_quest_authoring_cue_none_for_full_quest():
    q = Quest(title="Full", objectives=["a"], giver_id="npc_1", location_id="loc_1")
    assert server._quest_authoring_cue(q) is None


def test_quest_consequence_cue_none_for_active_quest():
    # An ACTIVE (not-yet-resolved) quest is never nagged for a consequence.
    c = Campaign(title="C")
    q = Quest(title="Ongoing", objectives=["a"], status="active")
    assert server._quest_consequence_cue(c, q) is None


def test_quest_consequence_recorded_true_on_naming_consequence():
    c = Campaign(title="C")
    q = Quest(title="The Bell", status="completed", objectives=["x"], completed_objectives=["x"])
    c.quests[q.id] = q
    c.consequences.append(Consequence(text=f"echo of {q.id}", trigger_day=3))
    assert server._quest_consequence_recorded(c, q) is True
