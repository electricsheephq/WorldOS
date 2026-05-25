"""S4 — Companion relationship-arc + agenda engine.

A companion's loyalty/romance/personal-quest/betrayal is a REAL, engine-evaluated event
built on the EXISTING approval gauge (`Character.attitude_value`) — not a line that lives
only in an ephemeral QA prompt. These tests guard the pure `companion_arc.evaluate`
(gates unlock at threshold; each agenda trigger fires under its condition; idempotent;
empty/None arc is a no-op) AND that the new `arc` field is ADDITIVE — an old snapshot
with no `arc` deserializes unchanged.
"""

import pytest

import companion_arc
import server
import store
from models import ArcGate, Campaign, Character, CompanionAgenda, CompanionArc


# --- helpers ----------------------------------------------------------------

def _companion(attitude: int = 0, gates=None, agenda=None, **kw) -> Character:
    arc = None
    if gates is not None or agenda is not None:
        arc = CompanionArc(arc_gates=list(gates or []), agenda=agenda)
    return Character(name="Grok", kind="companion", attitude_value=attitude, arc=arc, **kw)


def _campaign_with(*members: Character, day: int = 1, flags=None) -> Campaign:
    c = Campaign(title="S4 Arc")
    for m in members:
        c.characters[m.id] = m
        c.party.append(m.id)
    c.day = day
    if flags:
        c.flags.update(flags)
    return c


# --- additive default: a Character with no arc -------------------------------

def test_character_arc_defaults_none():
    ch = Character(name="Hero")
    assert ch.arc is None


def test_old_snapshot_without_arc_field_deserializes_unchanged():
    """An existing snapshot predates the `arc` field — it must load with arc=None and
    round-trip identically (the additive-default contract)."""
    ch = Character(name="Hero", kind="companion", attitude_value=10)
    data = ch.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "arc"}  # simulate a pre-S4 snapshot
    assert "arc" not in old
    reloaded = Character.model_validate(old)
    assert reloaded.arc is None
    # full round-trip stays stable
    assert Character.model_validate(reloaded.model_dump(mode="json")).arc is None


def test_old_campaign_snapshot_without_flags_deserializes_unchanged():
    c = Campaign(title="Pre-S4")
    data = c.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "flags"}
    assert "flags" not in old
    assert Campaign.model_validate(old).flags == {}


def test_old_campaign_snapshot_without_companion_quest_arcs_deserializes_unchanged():
    c = Campaign(title="Pre-#70")
    data = c.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "companion_quest_arcs"}
    assert "companion_quest_arcs" not in old
    reloaded = Campaign.model_validate(old)
    assert reloaded.companion_quest_arcs == {}


def test_evaluate_no_arc_is_noop():
    ch = _companion(attitude=99)  # no gates, no agenda -> arc stays None
    assert ch.arc is None
    c = _campaign_with(ch)
    res = companion_arc.evaluate(ch, c)
    assert res == {"newly_unlocked": [], "agenda_fired": False, "agenda": None}


def test_evaluate_empty_arc_is_noop():
    ch = _companion(attitude=99, gates=[])  # arc present but empty
    assert ch.arc is not None and ch.arc.arc_gates == [] and ch.arc.agenda is None
    res = companion_arc.evaluate(ch, _campaign_with(ch))
    assert res == {"newly_unlocked": [], "agenda_fired": False, "agenda": None}


# --- gates unlock at threshold ----------------------------------------------

def test_gate_unlocks_when_attitude_crosses_threshold():
    gate = ArcGate(kind="loyalty", threshold=40, note="trust earned")
    ch = _companion(attitude=39, gates=[gate])
    c = _campaign_with(ch)

    # below threshold: locked, nothing reported
    res = companion_arc.evaluate(ch, c)
    assert res["newly_unlocked"] == []
    assert ch.arc.arc_gates[0].unlocked is False

    # raise approval to the threshold -> unlocks, reported once
    ch.attitude_value = 40
    res = companion_arc.evaluate(ch, c)
    assert [g["kind"] for g in res["newly_unlocked"]] == ["loyalty"]
    assert ch.arc.arc_gates[0].unlocked is True


def test_gate_unlock_is_idempotent():
    gate = ArcGate(kind="personal_quest", threshold=20)
    ch = _companion(attitude=50, gates=[gate])
    c = _campaign_with(ch)

    first = companion_arc.evaluate(ch, c)
    assert len(first["newly_unlocked"]) == 1
    # a second evaluate must NOT re-report the already-unlocked gate
    second = companion_arc.evaluate(ch, c)
    assert second["newly_unlocked"] == []
    assert ch.arc.arc_gates[0].unlocked is True


def test_multiple_gates_only_those_at_or_below_attitude_unlock():
    gates = [
        ArcGate(kind="loyalty", threshold=10, note="a"),
        ArcGate(kind="romance", threshold=30, note="b"),
        ArcGate(kind="personal_quest", threshold=60, note="c"),
    ]
    ch = _companion(attitude=30, gates=gates)
    res = companion_arc.evaluate(ch, _campaign_with(ch))
    unlocked = {g["kind"] for g in res["newly_unlocked"]}
    assert unlocked == {"loyalty", "romance"}  # threshold 60 stays locked
    assert ch.arc.arc_gates[2].unlocked is False


# --- agenda triggers --------------------------------------------------------

def test_agenda_attitude_below_fires_when_approval_drops():
    agenda = CompanionAgenda(trigger="attitude_below", value=-30, note="turns on the party")
    ch = _companion(attitude=-29, agenda=agenda)
    c = _campaign_with(ch)

    # -29 is NOT below -30 -> no fire
    assert companion_arc.evaluate(ch, c)["agenda_fired"] is False
    assert ch.arc.agenda.fired is False

    # drop to -31 (strictly below) -> fires
    ch.attitude_value = -31
    res = companion_arc.evaluate(ch, c)
    assert res["agenda_fired"] is True
    assert res["agenda"]["trigger"] == "attitude_below"
    assert ch.arc.agenda.fired is True


def test_agenda_day_reached_fires_on_or_after_the_day():
    agenda = CompanionAgenda(trigger="day_reached", value=7, note="the plan comes due")
    ch = _companion(attitude=0, agenda=agenda)

    # day 6 -> not yet
    assert companion_arc.evaluate(ch, _campaign_with(ch, day=6))["agenda_fired"] is False
    assert ch.arc.agenda.fired is False
    # day 7 -> fires (>= is inclusive)
    assert companion_arc.evaluate(ch, _campaign_with(ch, day=7))["agenda_fired"] is True
    assert ch.arc.agenda.fired is True


def test_agenda_party_vulnerable_fires_on_downed_member():
    agenda = CompanionAgenda(trigger="party_vulnerable", note="strikes when weakest")
    saboteur = _companion(attitude=0, agenda=agenda)
    hero = Character(name="Hero", kind="player", max_hp=20, current_hp=20)
    c = _campaign_with(saboteur, hero)

    # full-HP party -> no fire
    assert companion_arc.evaluate(saboteur, c)["agenda_fired"] is False
    # drop the hero to 0 HP (downed) -> fires
    hero.current_hp = 0
    assert companion_arc.evaluate(saboteur, c)["agenda_fired"] is True


def test_agenda_party_vulnerable_fires_at_quarter_hp_threshold():
    agenda = CompanionAgenda(trigger="party_vulnerable")
    saboteur = _companion(attitude=0, agenda=agenda)
    hero = Character(name="Hero", kind="player", max_hp=20, current_hp=6)  # 30% -> safe
    c = _campaign_with(saboteur, hero)
    assert companion_arc.evaluate(saboteur, c)["agenda_fired"] is False

    hero.current_hp = 5  # exactly 25% of 20 -> vulnerable (<=)
    assert companion_arc.evaluate(saboteur, c)["agenda_fired"] is True


def test_agenda_prize_seized_fires_on_flag():
    agenda = CompanionAgenda(trigger="prize_seized", note="the goal is in hand")
    ch = _companion(attitude=0, agenda=agenda)

    # no flag -> no fire
    assert companion_arc.evaluate(ch, _campaign_with(ch))["agenda_fired"] is False
    # flag set -> fires
    assert companion_arc.evaluate(ch, _campaign_with(ch, flags={"prize_seized": True}))["agenda_fired"] is True


def test_agenda_fire_is_idempotent():
    agenda = CompanionAgenda(trigger="day_reached", value=1)
    ch = _companion(attitude=0, agenda=agenda)
    c = _campaign_with(ch, day=5)

    assert companion_arc.evaluate(ch, c)["agenda_fired"] is True
    # already fired -> never re-reports
    again = companion_arc.evaluate(ch, c)
    assert again["agenda_fired"] is False
    assert again["agenda"] is None


def test_gate_and_agenda_evaluate_together():
    gate = ArcGate(kind="betrayal", threshold=10, note="cracks show")
    agenda = CompanionAgenda(trigger="attitude_below", value=5)
    ch = _companion(attitude=15, gates=[gate], agenda=agenda)
    c = _campaign_with(ch)

    # high approval: gate unlocks, agenda not yet
    res = companion_arc.evaluate(ch, c)
    assert len(res["newly_unlocked"]) == 1 and res["agenda_fired"] is False
    # approval collapses below 5: agenda fires, gate stays unlocked (not re-reported)
    ch.attitude_value = 2
    res = companion_arc.evaluate(ch, c)
    assert res["newly_unlocked"] == [] and res["agenda_fired"] is True


# --- MCP tool layer (mirrors check_consequences) ----------------------------

@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A persisted campaign with one companion in the party."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("S4 Arc Tools")["id"]
    comp = server.create_character(cid, "Seraphine", kind="companion", max_hp=18)["id"]
    return cid, comp


def test_set_companion_arc_attaches_and_persists(camp):
    cid, comp = camp
    out = server.set_companion_arc(cid, comp, {
        "arc_gates": [{"kind": "loyalty", "threshold": 25, "note": "stands with you"}],
        "agenda": {"trigger": "prize_seized", "note": "wants the relic"},
    })
    assert out["id"] == comp
    assert out["arc"]["arc_gates"][0]["kind"] == "loyalty"
    # persisted to the snapshot
    c = store.load_campaign(cid)
    assert c.characters[comp].arc.arc_gates[0].threshold == 25
    assert c.characters[comp].arc.agenda.trigger == "prize_seized"


def test_set_companion_arc_rejects_non_companion(camp):
    cid, _comp = camp
    hero = server.create_character(cid, "Kield", kind="player")["id"]
    with pytest.raises(ValueError, match="not a companion"):
        server.set_companion_arc(cid, hero, {"arc_gates": [{"kind": "loyalty", "threshold": 1}]})


def test_set_companion_arc_rejects_missing_character(camp):
    cid, _comp = camp
    with pytest.raises(ValueError, match="no character"):
        server.set_companion_arc(cid, "char_doesnotexist", {"arc_gates": []})


def test_check_companion_arc_named_companion_fires_gate(camp):
    cid, comp = camp
    server.set_companion_arc(cid, comp, {
        "arc_gates": [{"kind": "romance", "threshold": 10, "note": "a glance held too long"}],
    })
    server.adjust_attitude(cid, comp, 30)  # push approval over the threshold

    res = server.check_companion_arc(cid, comp)
    assert len(res["results"]) == 1
    r = res["results"][0]
    assert r["companion_id"] == comp
    assert [g["kind"] for g in r["newly_unlocked"]] == ["romance"]

    # idempotent: a second check reports nothing new
    assert server.check_companion_arc(cid, comp)["results"] == []


def test_check_companion_arc_all_companions_when_id_omitted(camp):
    cid, comp = camp
    other = server.create_character(cid, "Grok", kind="companion")["id"]
    server.set_companion_arc(cid, comp, {"arc_gates": [{"kind": "loyalty", "threshold": 0}]})
    server.set_companion_arc(cid, other, {"agenda": {"trigger": "day_reached", "value": 1}})

    res = server.check_companion_arc(cid)  # no id -> all companions with an arc
    ids = {r["companion_id"] for r in res["results"]}
    assert ids == {comp, other}


def test_check_companion_arc_companion_without_arc_is_silent(camp):
    cid, comp = camp  # no arc set on this companion
    assert server.check_companion_arc(cid, comp)["results"] == []
    assert server.check_companion_arc(cid)["results"] == []


def test_set_flag_arms_prize_seized_agenda(camp):
    cid, comp = camp
    server.set_companion_arc(cid, comp, {"agenda": {"trigger": "prize_seized", "note": "the knife comes out"}})

    # before the flag, nothing fires
    assert server.check_companion_arc(cid, comp)["results"] == []
    # set the flag, then the betrayal agenda fires
    assert server.set_flag(cid, "prize_seized")["flags"]["prize_seized"] is True
    res = server.check_companion_arc(cid, comp)
    assert len(res["results"]) == 1 and res["results"][0]["agenda_fired"] is True


def test_threshold_agenda_requires_explicit_value():
    # M2 regression: a day_reached/attitude_below agenda with `value` OMITTED used to
    # default to 0 and fire IMMEDIATELY (day>=0 on day 1). It's now a LOUD error at
    # author time, so an ending-seed (C2) or a DM can't silently arm an instant betrayal.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CompanionAgenda(trigger="day_reached")
    with pytest.raises(ValidationError):
        CompanionAgenda(trigger="attitude_below")
    # triggers that don't use a threshold stay valueless-OK
    CompanionAgenda(trigger="party_vulnerable")
    CompanionAgenda(trigger="prize_seized")
    # a properly-armed day_reached fires ON its day, never before
    comp = _companion(agenda=CompanionAgenda(trigger="day_reached", value=5))
    assert companion_arc._agenda_triggered(comp, _campaign_with(comp, day=1)) is False
    assert companion_arc._agenda_triggered(comp, _campaign_with(comp, day=5)) is True


# --- companion quest arcs (#70) ---------------------------------------------

def test_set_and_get_companion_quest_arc_by_companion_and_status(camp):
    cid, comp = camp
    out = server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "available",
        "stages": [{"id": "stage_oath", "title": "Name the broken oath", "status": "available"}],
    })
    assert out["companion_quest_arc"]["id"] == "cq_seraphine_vow"
    assert out["companion_quest_arc"]["companion_id"] == comp

    by_companion = server.get_companion_quest_arcs(cid, companion_id=comp)
    assert by_companion["count"] == 1
    assert by_companion["companion_quest_arcs"][0]["title"] == "Seraphine's Vow"

    by_status = server.get_companion_quest_arcs(cid, status="available")
    assert [a["id"] for a in by_status["companion_quest_arcs"]] == ["cq_seraphine_vow"]
    assert server.get_companion_quest_arcs(cid, status="resolved")["count"] == 0


def test_personal_quest_gate_makes_linked_companion_quest_arc_available_once(camp):
    cid, comp = camp
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "stages": [{"id": "stage_oath", "title": "Name the broken oath"}],
    })
    server.set_companion_arc(cid, comp, {
        "arc_gates": [{
            "kind": "personal_quest",
            "threshold": 0,
            "quest_arc_id": "cq_seraphine_vow",
            "stage_id": "stage_oath",
        }],
    })

    first = server.check_companion_arc(cid, comp)
    assert len(first["results"]) == 1
    unlocks = first["results"][0]["companion_quest_unlocks"]
    assert unlocks == [{
        "quest_arc_id": "cq_seraphine_vow",
        "stage_id": "stage_oath",
        "status": "available",
    }]
    arc = server.get_companion_quest_arcs(cid, companion_id=comp)["companion_quest_arcs"][0]
    assert arc["status"] == "available"
    assert arc["stages"][0]["status"] == "available"

    assert server.check_companion_arc(cid, comp)["results"] == []
    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "available"


def test_advance_companion_quest_arc_links_and_repairs_tracked_quest(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow", objectives=["Recover the oath-name"])["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "active",
        "stages": [{"id": "stage_oath", "title": "Recover the oath-name", "status": "active"}],
        "quest_ids": [qid],
    })
    # Simulate a drifted tracked Quest projection. The companion arc remains the owner.
    server.complete_quest(cid, qid, "failed")

    out = server.advance_companion_quest_arc(
        cid,
        "cq_seraphine_vow",
        status="resolved",
        stage_id="stage_oath",
        stage_status="resolved",
        quest_id=qid,
    )
    assert out["companion_quest_arc"]["status"] == "resolved"
    assert out["companion_quest_arc"]["stages"][0]["quest_id"] == qid
    assert out["quest_updates"] == [{
        "quest_id": qid,
        "previous_status": "failed",
        "status": "completed",
    }]
    persisted = store.load_campaign(cid)
    assert persisted.quests[qid].status == "completed"
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].quest_ids == [qid]


def test_advance_companion_quest_arc_rejects_inconsistent_quest_status_without_mutation(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "stages": [{"id": "stage_oath", "title": "Recover the oath-name"}],
        "quest_ids": [qid],
    })

    with pytest.raises(ValueError, match="inconsistent"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            status="resolved",
            quest_id=qid,
            quest_status="active",
        )

    persisted = store.load_campaign(cid)
    arc = persisted.companion_quest_arcs["cq_seraphine_vow"]
    assert arc.status == "locked"
    assert arc.quest_ids == [qid]
    assert persisted.quests[qid].status == "active"


def test_companion_quest_arc_apis_reject_bad_optional_links_without_partial_mutation(camp):
    cid, comp = camp
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "stages": [{"id": "stage_oath", "title": "Recover the oath-name"}],
    })

    with pytest.raises(ValueError, match="no tracked quest"):
        server.set_companion_quest_arc(cid, comp, {
            "id": "cq_bad",
            "title": "Bad Link",
            "quest_ids": ["quest_missing"],
        })
    assert "cq_bad" not in store.load_campaign(cid).companion_quest_arcs

    with pytest.raises(ValueError, match="no stage"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            stage_id="stage_missing",
            stage_status="active",
        )
    persisted = store.load_campaign(cid)
    arc = persisted.companion_quest_arcs["cq_seraphine_vow"]
    assert arc.status == "locked"
    assert arc.stages[0].status == "locked"
