"""Tool-arg-name robustness: the DM (Opus) calls engine tools with intuitive arg
names the strict MCP schema used to reject with a "Field required" validation error,
which flips the release_gate's `no_rejected_tool_calls` FATAL check RED even on an
otherwise-healthy run.

Each tool now accepts BOTH the canonical name AND an intuitive alias; the canonical
stays primary (wins if both are given) and behavior is IDENTICAL to using the
canonical name. social_check additionally treats a COMPANION as a legitimate social
target (it was a capability gap, not just naming — the guard rejected kind=companion
and produced WARNs in the run).

These are targeted unit tests (run with `-k alias` / `-k companion_social`), not the
full suite. They prove: alias == canonical for remember/record_decision/skill_check/
set_companion_arc, and that social_check succeeds against a companion (influence AND
read) while STILL rejecting a player.
"""

import pytest

import server
from models import CompanionArc


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("Alias")["id"]


# --- 1. remember: canonical `fact`, alias `text` --------------------------------

def test_remember_text_alias_equals_fact(campaign):
    npc = server.create_character(campaign, "Brakka", kind="npc")["id"]
    # canonical
    out_fact = server.remember(campaign, npc, fact="bought a round")
    assert "bought a round" in out_fact["memory"]
    # alias produces the identical effect (and de-dupes, like canonical would)
    out_text = server.remember(campaign, npc, text="owes the party a favor")
    assert "owes the party a favor" in out_text["memory"]
    # same return shape / same character
    assert set(out_fact) == set(out_text) == {"id", "name", "memory"}
    assert out_text["id"] == npc


def test_remember_canonical_wins_when_both_given(campaign):
    npc = server.create_character(campaign, "Brakka", kind="npc")["id"]
    out = server.remember(campaign, npc, fact="canonical fact", text="alias text")
    assert "canonical fact" in out["memory"]
    assert "alias text" not in out["memory"]


def test_remember_missing_both_raises_clear_error(campaign):
    npc = server.create_character(campaign, "Brakka", kind="npc")["id"]
    with pytest.raises(ValueError, match="fact"):
        server.remember(campaign, npc)


# --- 2. record_decision: canonical `summary`, alias `decision` ------------------

def test_record_decision_decision_alias_equals_summary(campaign):
    out_canon = server.record_decision(campaign, summary="spared the cultist")
    out_alias = server.record_decision(campaign, decision="spared the cultist")
    assert out_canon["summary"] == out_alias["summary"] == "spared the cultist"
    # identical return shape
    assert set(out_canon) == set(out_alias)
    # the alias call actually persisted a decision with that summary
    c = server._require(campaign)
    assert any(d.summary == "spared the cultist" for d in c.decisions)


def test_record_decision_canonical_wins_when_both_given(campaign):
    out = server.record_decision(campaign, summary="canonical", decision="alias")
    assert out["summary"] == "canonical"


def test_record_decision_missing_both_raises(campaign):
    with pytest.raises(ValueError, match="summary"):
        server.record_decision(campaign)


# --- 3. skill_check: canonical `skill`, aliases ability/skill_name/check --------

def test_skill_check_skill_aliases_equal_canonical(campaign):
    pc = server.create_character(
        campaign, "Scout", kind="player", abilities={"wisdom": 16}
    )["id"]
    canon = server.skill_check(campaign, pc, skill="perception", dc=0)
    for kw in ("ability", "skill_name", "check"):
        alias = server.skill_check(campaign, pc, **{kw: "perception"}, dc=0)
        # same resolved skill + same modifier (the load-bearing equality: alias maps
        # to the exact same sheet-derived bonus, not a hand-computed one)
        assert alias["skill"] == canon["skill"] == "perception"
        assert alias["modifier"] == canon["modifier"]
        assert set(alias) == set(canon)


def test_skill_check_canonical_wins_when_both_given(campaign):
    pc = server.create_character(campaign, "Scout", kind="player")["id"]
    out = server.skill_check(campaign, pc, skill="perception", ability="athletics")
    assert out["skill"] == "perception"


def test_skill_check_missing_all_names_raises(campaign):
    pc = server.create_character(campaign, "Scout", kind="player")["id"]
    with pytest.raises(ValueError, match="skill"):
        server.skill_check(campaign, pc)


# --- 4. set_companion_arc: canonical `companion_id`, aliases companion/character_id

def _arc_dict():
    return {
        "arc_gates": [{"kind": "loyalty", "threshold": 50}],
        "agenda": {"trigger": "day_reached", "value": 99},
    }


def test_set_companion_arc_aliases_equal_canonical(campaign):
    comp_a = server.create_character(campaign, "Aerie", kind="companion")["id"]
    comp_b = server.create_character(campaign, "Boo", kind="companion")["id"]
    comp_c = server.create_character(campaign, "Cael", kind="companion")["id"]

    canon = server.set_companion_arc(campaign, companion_id=comp_a, arc=_arc_dict())
    via_companion = server.set_companion_arc(campaign, companion=comp_b, arc=_arc_dict())
    via_charid = server.set_companion_arc(campaign, character_id=comp_c, arc=_arc_dict())

    # identical return shape and identical MEANINGFUL arc content (the gate `id` is
    # auto-generated per call, so compare the structural fields, not the random id)
    assert set(canon) == set(via_companion) == set(via_charid)

    def _shape(res):
        gate = res["arc"]["arc_gates"][0]
        return (gate["kind"], gate["threshold"], res["arc"]["agenda"]["trigger"],
                res["arc"]["agenda"]["value"])

    assert _shape(canon) == _shape(via_companion) == _shape(via_charid) == ("loyalty", 50, "day_reached", 99)
    # the alias calls actually attached the arc to the RIGHT companion
    assert server._require(campaign).characters[comp_b].arc is not None
    assert server._require(campaign).characters[comp_c].arc is not None
    assert via_companion["id"] == comp_b and via_charid["id"] == comp_c


def test_set_companion_arc_canonical_wins_when_both_given(campaign):
    comp = server.create_character(campaign, "Aerie", kind="companion")["id"]
    other = server.create_character(campaign, "Other", kind="companion")["id"]
    out = server.set_companion_arc(campaign, companion_id=comp, companion=other, arc=_arc_dict())
    assert out["id"] == comp  # canonical target won; the alias did not redirect


def test_set_companion_arc_missing_id_raises(campaign):
    with pytest.raises(ValueError, match="companion"):
        server.set_companion_arc(campaign, arc=_arc_dict())


# --- 5. social_check: a COMPANION is a valid social target ----------------------

def test_social_check_succeeds_against_companion_influence(campaign):
    pc = server.create_character(
        campaign, "Bard", kind="player", abilities={"charisma": 16}
    )["id"]
    comp = server.create_character(campaign, "Jaheira", kind="companion")["id"]

    # influence: persuading a companion moves the SAME attitude/approval track an NPC
    # uses (the gauge companion arcs evaluate) — no exception, real state change.
    win = server.social_check(campaign, pc, comp, "persuasion", dc=1)  # always succeeds
    assert win["success"] is True
    assert win["kind"] == "influence"
    assert win["new_attitude_value"] == 15  # +15, like an NPC
    assert server.get_character(campaign, comp)["attitude_value"] == 15

    loss = server.social_check(campaign, pc, comp, "persuasion", dc=100)  # always fails
    assert loss["success"] is False
    assert loss["new_attitude_value"] == 5  # -10 from 15, clamped scale
    assert "on_failure" in loss  # failed influence still hands the turn back


def test_social_check_read_against_companion_does_not_shift_attitude(campaign):
    pc = server.create_character(
        campaign, "Watcher", kind="player", abilities={"wisdom": 16}
    )["id"]
    comp = server.create_character(campaign, "Minsc", kind="companion")["id"]
    server.set_attitude(campaign, comp, "friendly")

    ok = server.social_check(campaign, pc, comp, "insight", dc=1)  # clear read
    assert ok["kind"] == "read" and ok["success"] is True
    # a read PERCEIVES, never influences — attitude (text + number) untouched
    assert ok["old_attitude"] == ok["new_attitude"] == "friendly"
    assert server.get_character(campaign, comp)["attitude_value"] == 0  # read didn't nudge


def test_social_check_still_rejects_a_player_target(campaign):
    # the relaxation only added companions; a PLAYER is still not a social target
    pc = server.create_character(campaign, "Bard", kind="player")["id"]
    pc2 = server.create_character(campaign, "Fighter", kind="player")["id"]
    with pytest.raises(ValueError):
        server.social_check(campaign, pc, pc2, "persuasion", dc=10)
