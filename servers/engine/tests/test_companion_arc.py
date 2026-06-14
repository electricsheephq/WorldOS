"""S4 — Companion relationship-arc + agenda engine.

A companion's loyalty/romance/personal-quest/betrayal is a REAL, engine-evaluated event
built on the EXISTING approval gauge (`Character.attitude_value`) — not a line that lives
only in an ephemeral QA prompt. These tests guard the pure `companion_arc.evaluate`
(gates unlock at threshold; each agenda trigger fires under its condition; idempotent;
empty/None arc is a no-op) AND that the new `arc` field is ADDITIVE — an old snapshot
with no `arc` deserializes unchanged.

Issue #142 — attitude_below is now a RISING PROBABILITY ROLL:
  - At/above threshold: P = 0 (never fires).
  - Below threshold:    P rises linearly with depth below the threshold, capped at
    ATTITUDE_SNAP_MAX per beat. Vulnerable-party gives an additive bonus.
  - Deterministic under a seeded rng (passed to evaluate).
"""

import random

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

def test_agenda_attitude_below_never_fires_above_threshold():
    """At or above the threshold P=0 — the agenda must not fire regardless of rng."""
    agenda = CompanionAgenda(trigger="attitude_below", value=-30, note="turns on the party")
    ch = _companion(attitude=-29, agenda=agenda)
    c = _campaign_with(ch)

    # -29 is NOT below -30 -> P=0, must never fire across many calls
    rng = random.Random(42)
    for _ in range(50):
        assert companion_arc.evaluate(ch, c, rng=rng)["agenda_fired"] is False
    assert ch.arc.agenda.fired is False


def test_agenda_attitude_below_eventually_fires_below_threshold():
    """Below the threshold the agenda MUST eventually fire (P > 0)."""
    agenda = CompanionAgenda(trigger="attitude_below", value=-30, note="turns on the party")
    ch = _companion(attitude=-50, agenda=agenda)  # well below threshold
    c = _campaign_with(ch)

    rng = random.Random(0)
    fired = False
    for _ in range(200):  # 200 independent evaluate calls (agenda resets each iter)
        _ch = _companion(attitude=-50, agenda=CompanionAgenda(trigger="attitude_below", value=-30))
        if companion_arc.evaluate(_ch, c, rng=rng)["agenda_fired"]:
            fired = True
            break
    assert fired, "agenda should have fired within 200 beats at attitude -50 (threshold -30)"


def test_agenda_attitude_below_deeper_attitude_fires_more_often():
    """Deeper below the threshold = higher per-beat P — measured by fire rate across trials."""
    threshold = 0
    n_trials = 500

    def fire_rate(attitude: int, seed: int) -> float:
        rng = random.Random(seed)
        hits = 0
        for _ in range(n_trials):
            _ch = _companion(attitude=attitude, agenda=CompanionAgenda(trigger="attitude_below", value=threshold))
            c = _campaign_with(_ch)
            if companion_arc.evaluate(_ch, c, rng=rng)["agenda_fired"]:
                hits += 1
        return hits / n_trials

    rate_near = fire_rate(-1, seed=1)    # just 1 below threshold  -> very low P
    rate_mid = fire_rate(-50, seed=2)   # halfway down              -> mid P
    rate_deep = fire_rate(-90, seed=3)  # near the floor            -> near cap

    assert rate_near < rate_mid, f"near={rate_near:.3f} should be < mid={rate_mid:.3f}"
    assert rate_mid < rate_deep, f"mid={rate_mid:.3f} should be < deep={rate_deep:.3f}"


def test_agenda_attitude_below_at_exact_threshold_p_is_zero():
    """Exactly at the threshold (attitude == value) P must be 0."""
    p = companion_arc._attitude_below_snap_p(0, 0, False)
    assert p == 0.0


def test_agenda_attitude_below_p_capped_at_snap_max():
    """Even at the ATTITUDE_SNAP_FLOOR the probability is capped at ATTITUDE_SNAP_MAX
    (no vulnerable bonus) or ATTITUDE_SNAP_MAX + ATTITUDE_SNAP_VULNERABLE_BONUS."""
    p_normal = companion_arc._attitude_below_snap_p(companion_arc.ATTITUDE_SNAP_FLOOR, 0, False)
    assert p_normal <= companion_arc.ATTITUDE_SNAP_MAX + 1e-9

    p_vuln = companion_arc._attitude_below_snap_p(companion_arc.ATTITUDE_SNAP_FLOOR, 0, True)
    assert p_vuln <= companion_arc.ATTITUDE_SNAP_MAX + companion_arc.ATTITUDE_SNAP_VULNERABLE_BONUS + 1e-9


def test_agenda_attitude_below_vulnerable_party_raises_probability():
    """The vulnerable-party bonus pushes P up when _party_vulnerable is True."""
    # Use an attitude just barely below threshold so we can detect the small bonus.
    attitude, threshold = -10, 0
    p_safe = companion_arc._attitude_below_snap_p(attitude, threshold, False)
    p_vuln = companion_arc._attitude_below_snap_p(attitude, threshold, True)
    assert p_vuln > p_safe


def test_agenda_attitude_below_firing_once_preserved():
    """Once fired (fired=True), additional evaluate calls never re-fire — even with an
    rng seeded to always return 0 (which would always roll < p if P>0)."""
    agenda = CompanionAgenda(trigger="attitude_below", value=0, note="turns on the party")
    ch = _companion(attitude=-80, agenda=agenda)
    c = _campaign_with(ch)
    rng = random.Random(0)

    # Force the first fire
    forced = False
    for _ in range(500):
        res = companion_arc.evaluate(ch, c, rng=rng)
        if res["agenda_fired"]:
            forced = True
            break
    assert forced

    # Now agenda.fired=True — should never report again
    for _ in range(20):
        res = companion_arc.evaluate(ch, c, rng=rng)
        assert res["agenda_fired"] is False
        assert res["agenda"] is None


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

    # high approval: gate unlocks, agenda not yet (P=0 above threshold)
    rng = random.Random(42)
    res = companion_arc.evaluate(ch, c, rng=rng)
    assert len(res["newly_unlocked"]) == 1 and res["agenda_fired"] is False

    # approval collapses deep below 5: agenda eventually fires, gate stays unlocked
    ch.attitude_value = -90  # near the floor → near-cap P so it fires in few beats
    fired = False
    for _ in range(200):
        res = companion_arc.evaluate(ch, c, rng=rng)
        if res["agenda_fired"]:
            fired = True
            break
    assert fired
    assert res["newly_unlocked"] == []  # gate already unlocked, not re-reported


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
        "stage_status": "available",
        "changed": ["arc", "stage"],
    }]
    arc = server.get_companion_quest_arcs(cid, companion_id=comp)["companion_quest_arcs"][0]
    assert arc["status"] == "available"
    assert arc["stages"][0]["status"] == "available"

    assert server.check_companion_arc(cid, comp)["results"] == []
    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "available"


def test_personal_quest_gate_stage_unlock_reports_actual_arc_status(camp):
    cid, comp = camp
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "active",
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

    unlock = server.check_companion_arc(cid, comp)["results"][0]["companion_quest_unlocks"][0]
    assert unlock["status"] == "active"
    assert unlock["stage_status"] == "available"
    assert unlock["changed"] == ["stage"]


def test_set_companion_arc_rejects_missing_personal_quest_link_without_mutation(camp):
    cid, comp = camp
    # F06-1: a companion now carries a seeded DEFAULT arc at creation; the no-mutation
    # guarantee is that a REJECTED set_companion_arc leaves that arc UNCHANGED (the bad
    # personal_quest gate is never applied), not that arc is None.
    before = store.load_campaign(cid).characters[comp].arc.model_dump(mode="json")

    with pytest.raises(ValueError, match="no companion quest arc"):
        server.set_companion_arc(cid, comp, {
            "arc_gates": [{
                "kind": "personal_quest",
                "threshold": 0,
                "quest_arc_id": "cq_missing",
            }],
        })

    after = store.load_campaign(cid).characters[comp].arc.model_dump(mode="json")
    assert after == before  # the seeded default arc is intact; the bad arc was not applied
    assert not any(g["kind"] == "personal_quest" for g in after["arc_gates"])


def test_set_companion_arc_rejects_stage_without_quest_arc(camp):
    cid, comp = camp
    before = store.load_campaign(cid).characters[comp].arc.model_dump(mode="json")

    with pytest.raises(ValueError, match="stage_id requires quest_arc_id"):
        server.set_companion_arc(cid, comp, {
            "arc_gates": [{
                "kind": "personal_quest",
                "threshold": 0,
                "stage_id": "stage_oath",
            }],
        })

    after = store.load_campaign(cid).characters[comp].arc.model_dump(mode="json")
    assert after == before  # the seeded default arc is intact; the bad arc was not applied
    assert not any(g["kind"] == "personal_quest" for g in after["arc_gates"])


def test_set_companion_quest_arc_rejects_replacing_referenced_stage(camp):
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

    with pytest.raises(ValueError, match="references missing stage"):
        server.set_companion_quest_arc(cid, comp, {
            "id": "cq_seraphine_vow",
            "title": "Seraphine's Vow",
            "stages": [{"id": "stage_other", "title": "A different stage"}],
        })

    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].stages[0].id == "stage_oath"


def test_personal_quest_gate_bad_legacy_link_does_not_burn_unlock(camp):
    cid, comp = camp
    c = store.load_campaign(cid)
    c.characters[comp].arc = CompanionArc(arc_gates=[ArcGate(
        kind="personal_quest",
        threshold=0,
        quest_arc_id="cq_missing",
    )])
    store.save_campaign(c)

    out = server.check_companion_arc(cid, comp)
    unlock = out["results"][0]["companion_quest_unlocks"][0]
    assert "error" in unlock
    assert store.load_campaign(cid).characters[comp].arc.arc_gates[0].unlocked is False

    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_missing",
        "title": "Recovered Link",
    })
    retried = server.check_companion_arc(cid, comp)
    assert retried["results"][0]["companion_quest_unlocks"][0]["changed"] == ["arc"]
    persisted = store.load_campaign(cid)
    assert persisted.characters[comp].arc.arc_gates[0].unlocked is True
    assert persisted.companion_quest_arcs["cq_missing"].status == "available"


def test_personal_quest_gate_reports_no_transition_for_already_resolved_arc(camp):
    cid, comp = camp
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "resolved",
        "stages": [{"id": "stage_oath", "title": "Name the broken oath", "status": "resolved"}],
    })
    server.set_companion_arc(cid, comp, {
        "arc_gates": [{
            "kind": "personal_quest",
            "threshold": 0,
            "quest_arc_id": "cq_seraphine_vow",
            "stage_id": "stage_oath",
        }],
    })

    unlock = server.check_companion_arc(cid, comp)["results"][0]["companion_quest_unlocks"][0]
    assert unlock["no_transition"] is True
    assert unlock["status"] == "resolved"
    assert unlock["stage_status"] == "resolved"
    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "resolved"
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].stages[0].status == "resolved"


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


def test_advance_companion_quest_arc_rejects_quest_status_without_arc_transition(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "quest_ids": [qid],
    })

    with pytest.raises(ValueError, match="quest_status requires status or stage_status"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            quest_id=qid,
            quest_status="completed",
        )

    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "locked"
    assert persisted.quests[qid].status == "active"


def test_advance_companion_quest_arc_rejects_locked_status_quest_projection(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "quest_ids": [qid],
    })

    with pytest.raises(ValueError, match="cannot project"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            status="locked",
            quest_id=qid,
            quest_status="completed",
        )

    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "locked"
    assert persisted.quests[qid].status == "active"


def test_advance_companion_quest_arc_rejects_locked_arc_with_available_stage(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "stages": [{"id": "stage_oath", "title": "Recover the oath-name", "quest_id": qid}],
    })

    with pytest.raises(ValueError, match="cannot advance while companion quest status is 'locked'"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            status="locked",
            stage_id="stage_oath",
            stage_status="available",
        )

    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "locked"
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].stages[0].status == "locked"
    assert persisted.quests[qid].status == "active"


def test_advance_companion_quest_arc_rejects_conflicting_arc_and_stage_projection(camp):
    cid, comp = camp
    qid = server.add_quest(cid, "Seraphine's Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "active",
        "stages": [{"id": "stage_oath", "title": "Recover the oath-name", "status": "active"}],
        "quest_ids": [qid],
    })

    with pytest.raises(ValueError, match="conflicting quest projections"):
        server.advance_companion_quest_arc(
            cid,
            "cq_seraphine_vow",
            status="resolved",
            stage_id="stage_oath",
            stage_status="active",
            quest_id=qid,
        )

    persisted = store.load_campaign(cid)
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].status == "active"
    assert persisted.companion_quest_arcs["cq_seraphine_vow"].stages[0].status == "active"
    assert persisted.quests[qid].status == "active"


def test_advance_companion_quest_arc_stage_only_updates_only_linked_stage_quest(camp):
    cid, comp = camp
    first_qid = server.add_quest(cid, "Seraphine's First Vow")["id"]
    second_qid = server.add_quest(cid, "Seraphine's Second Vow")["id"]
    server.set_companion_quest_arc(cid, comp, {
        "id": "cq_seraphine_vow",
        "title": "Seraphine's Vow",
        "status": "active",
        "stages": [
            {"id": "stage_first", "title": "First oath", "status": "active", "quest_id": first_qid},
            {"id": "stage_second", "title": "Second oath", "status": "active", "quest_id": second_qid},
        ],
    })

    out = server.advance_companion_quest_arc(
        cid,
        "cq_seraphine_vow",
        stage_id="stage_first",
        stage_status="resolved",
    )

    assert out["quest_updates"] == [{
        "quest_id": first_qid,
        "previous_status": "active",
        "status": "completed",
    }]
    persisted = store.load_campaign(cid)
    assert persisted.quests[first_qid].status == "completed"
    assert persisted.quests[second_qid].status == "active"
    stages = {stage.id: stage for stage in persisted.companion_quest_arcs["cq_seraphine_vow"].stages}
    assert stages["stage_first"].status == "resolved"
    assert stages["stage_second"].status == "active"


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


# ════════════════════════════════════════════════════════════════════════════
# Quest & Arc engine — Layer 2: decision-gated companion flips
#
# A recorded player CHOICE sets a CONTENT-defined campaign flag that ESCALATES an
# `attitude_below` agenda's betrayal weight ("let the daughter die → the knight turns").
# Additive: empty `decision_flag` == today's #142/#158 behavior, byte-for-byte. The
# escalation reads ONLY engine-mutated values (flags + attitude_value), never fiction.
# A danger-band warning telegraphs the turn so it isn't a surprise-from-nowhere.
# ════════════════════════════════════════════════════════════════════════════


# --- additive default: the new field --------------------------------------------

def test_agenda_decision_flag_defaults_empty():
    agenda = CompanionAgenda(trigger="attitude_below", value=-20)
    assert agenda.decision_flag == ""


def test_old_agenda_snapshot_without_decision_flag_deserializes_unchanged():
    """An agenda authored before Layer 2 has no `decision_flag` key — it must load with
    decision_flag="" and round-trip identically (the additive-default contract)."""
    agenda = CompanionAgenda(trigger="attitude_below", value=-20, note="turns")
    data = agenda.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "decision_flag"}
    assert "decision_flag" not in old
    reloaded = CompanionAgenda.model_validate(old)
    assert reloaded.decision_flag == ""
    # full round-trip stays stable
    assert CompanionAgenda.model_validate(reloaded.model_dump(mode="json")).decision_flag == ""


def test_empty_decision_flag_snap_p_is_byte_identical_to_158():
    """With no decision_flag active, the snap probability is EXACTLY the #142/#158 curve —
    the Layer-2 param is purely additive."""
    for attitude in range(-100, 1, 7):
        for vuln in (False, True):
            base = companion_arc._attitude_below_snap_p(attitude, 0, vuln)
            with_param = companion_arc._attitude_below_snap_p(attitude, 0, vuln, decision_flag_active=False)
            assert base == with_param


# --- the probability boost math --------------------------------------------------

def test_decision_flag_boosts_snap_probability():
    """An active decision_flag ADDS ATTITUDE_SNAP_DECISION_BONUS on top of the rising
    chance (capped at ATTITUDE_SNAP_DECISION_MAX)."""
    attitude, threshold = -30, 0  # raw_p = 30/100 = 0.30 (below the 0.35 cap)
    p_off = companion_arc._attitude_below_snap_p(attitude, threshold, False, decision_flag_active=False)
    p_on = companion_arc._attitude_below_snap_p(attitude, threshold, False, decision_flag_active=True)
    assert p_off == pytest.approx(0.30)
    assert p_on == pytest.approx(0.30 + companion_arc.ATTITUDE_SNAP_DECISION_BONUS)
    assert p_on > p_off


def test_decision_flag_boost_capped_at_decision_max():
    """The boosted probability never exceeds ATTITUDE_SNAP_DECISION_MAX (0.90) — the
    betrayal stays a roll, never a certainty per beat. At the floor with the vulnerable
    bonus the base reaches 0.45, +0.30 decision = 0.75 (still under the 0.90 ceiling)."""
    p_floor = companion_arc._attitude_below_snap_p(
        companion_arc.ATTITUDE_SNAP_FLOOR, 0, True, decision_flag_active=True
    )
    assert p_floor <= companion_arc.ATTITUDE_SNAP_DECISION_MAX + 1e-9
    assert p_floor == pytest.approx(
        companion_arc.ATTITUDE_SNAP_MAX
        + companion_arc.ATTITUDE_SNAP_VULNERABLE_BONUS
        + companion_arc.ATTITUDE_SNAP_DECISION_BONUS
    )

    # And where the base curve is high enough, the decision boost is what the 0.90 ceiling
    # actually clamps: a threshold of 80 puts the raw curve near 1.0, so base hits the
    # 0.35 cap, +vuln 0.10 = 0.45, +decision 0.30 = 0.75 — still under. To exercise the
    # ceiling directly, confirm it never returns above DECISION_MAX across the whole range.
    for attitude in range(-100, 80):
        p = companion_arc._attitude_below_snap_p(attitude, 80, True, decision_flag_active=True)
        assert p <= companion_arc.ATTITUDE_SNAP_DECISION_MAX + 1e-9


def test_decision_flag_never_fires_above_threshold():
    """The decision boost must NOT override the breaking-point guard: at/above the
    threshold P stays 0 even with the flag active (no betrayal from nowhere)."""
    p = companion_arc._attitude_below_snap_p(5, 0, True, decision_flag_active=True)
    assert p == 0.0


# --- the with/without-flag FIRE-RATE comparison (the headline behavioral gate) ---

def test_decision_flag_fires_at_notably_higher_rate(monkeypatch):
    """STATISTICAL gate: an attitude_below agenda WITH a set decision_flag fires at a
    NOTABLY higher per-beat rate than the same agenda without it — over a seeded rng
    loop. This is the owner's "betrayal chance spikes" made measurable."""
    threshold, attitude = 0, -30  # base p = 0.30; boosted p = 0.60
    n_trials = 800

    def fire_rate(*, with_flag: bool, seed: int) -> float:
        rng = random.Random(seed)
        hits = 0
        for _ in range(n_trials):
            agenda = CompanionAgenda(
                trigger="attitude_below",
                value=threshold,
                decision_flag="let_daughter_die" if with_flag else "",
            )
            _ch = _companion(attitude=attitude, agenda=agenda)
            flags = {"let_daughter_die": True} if with_flag else None
            c = _campaign_with(_ch, flags=flags)
            if companion_arc.evaluate(_ch, c, rng=rng)["agenda_fired"]:
                hits += 1
        return hits / n_trials

    rate_off = fire_rate(with_flag=False, seed=11)
    rate_on = fire_rate(with_flag=True, seed=12)

    # base ≈ 0.30, boosted ≈ 0.60 — demand a clearly higher rate (wide margin for variance)
    assert rate_on > rate_off + 0.15, f"on={rate_on:.3f} should be notably > off={rate_off:.3f}"


def test_decision_flag_set_but_false_does_not_boost():
    """The flag must be present AND True to escalate — a flag set to False (or a different
    flag set) leaves the #158 curve unchanged."""
    threshold, attitude = 0, -30
    agenda = CompanionAgenda(trigger="attitude_below", value=threshold, decision_flag="took_bribe")
    ch = _companion(attitude=attitude, agenda=agenda)

    # flag present but False -> no boost
    c_false = _campaign_with(ch, flags={"took_bribe": False})
    assert companion_arc._decision_flag_active(ch.arc.agenda, c_false) is False
    # an UNRELATED flag set True -> no boost
    c_other = _campaign_with(ch, flags={"some_other_flag": True})
    assert companion_arc._decision_flag_active(ch.arc.agenda, c_other) is False
    # the named flag True -> boost active
    c_true = _campaign_with(ch, flags={"took_bribe": True})
    assert companion_arc._decision_flag_active(ch.arc.agenda, c_true) is True


def test_decision_flag_ignored_by_non_attitude_triggers():
    """decision_flag is scoped to attitude_below — it must not change a day_reached /
    prize_seized / party_vulnerable agenda's deterministic semantics."""
    # day_reached with a decision_flag set True still only fires on/after its day
    agenda = CompanionAgenda(trigger="day_reached", value=7, decision_flag="took_bribe")
    ch = _companion(attitude=-90, agenda=agenda)
    assert companion_arc.evaluate(ch, _campaign_with(ch, day=6, flags={"took_bribe": True}))["agenda_fired"] is False
    assert companion_arc.evaluate(ch, _campaign_with(ch, day=7, flags={"took_bribe": True}))["agenda_fired"] is True


# --- warning bands (telegraph) ---------------------------------------------------

def test_betrayal_warning_surfaces_in_danger_band():
    """A live attitude_below agenda whose companion sits in [-40, -20] surfaces an
    advisory betrayal_warning."""
    agenda = CompanionAgenda(trigger="attitude_below", value=0, note="turns on the party")
    ch = _companion(attitude=-30, agenda=agenda)  # in the band, below the threshold
    res = companion_arc.evaluate(ch, _campaign_with(ch), rng=random.Random(0))
    warn = res.get("betrayal_warning")
    assert warn is not None
    assert warn["companion_id"] == ch.id
    assert warn["attitude_value"] == -30
    assert warn["band"] == [companion_arc.ATTITUDE_WARN_LOW, companion_arc.ATTITUDE_WARN_HIGH]
    assert warn["decision_flag_active"] is False


def test_betrayal_warning_absent_above_the_band():
    """Above the danger band (attitude > -20) there is no warning — the bond hasn't
    soured far enough to telegraph."""
    agenda = CompanionAgenda(trigger="attitude_below", value=0)
    ch = _companion(attitude=-10, agenda=agenda)  # above the band
    res = companion_arc.evaluate(ch, _campaign_with(ch), rng=random.Random(0))
    assert "betrayal_warning" not in res


def test_betrayal_warning_persists_deep_red_below_old_lower_edge():
    """F06-4 (audit, CORRECTED): a LIVE attitude_below agenda whose companion has fallen
    DEEP into the red (below the old absolute lower edge of -40) MUST still telegraph —
    the deepest-red bond is the MOST dangerous, exactly when the DM most needs to
    foreshadow. The old absolute band [-40,-20] silently dropped this case."""
    agenda = CompanionAgenda(trigger="attitude_below", value=0)
    # use a fresh companion so a fire doesn't end the loop; force no-fire to inspect the warning.
    ch = _companion(attitude=-60, agenda=agenda)
    class _NoFire(random.Random):
        def random(self):  # always >= any p -> never fires this beat
            return 0.999999
    res = companion_arc.evaluate(ch, _campaign_with(ch), rng=_NoFire())
    assert res["agenda_fired"] is False
    warn = res.get("betrayal_warning")
    assert warn is not None
    assert warn["attitude_value"] == -60


def test_betrayal_warning_fires_for_low_threshold_agenda():
    """F06-4 (audit, CORRECTED): the never-warn DEAD ZONE — an agenda whose breaking-point
    threshold is itself <= -40. With the old absolute band [-40,-20] the conditions
    (av in band AND av < threshold<=-40) were disjoint, so a saboteur with a low threshold
    NEVER telegraphed at any attitude. Now it warns once the bond has crossed below its
    own breaking point and soured past the upper edge."""
    agenda = CompanionAgenda(trigger="attitude_below", value=-50, note="turns on the party")
    # below the -50 breaking point and past the upper edge -> a live, foreshadowable turn.
    ch = _companion(attitude=-55, agenda=agenda)
    class _NoFire(random.Random):
        def random(self):
            return 0.999999
    res = companion_arc.evaluate(ch, _campaign_with(ch), rng=_NoFire())
    assert res["agenda_fired"] is False
    warn = res.get("betrayal_warning")
    assert warn is not None
    assert warn["attitude_value"] == -55
    assert warn["threshold"] == -50


def test_betrayal_warning_absent_when_low_threshold_not_yet_crossed():
    """A low-threshold agenda (value=-50) whose companion sits in the souring band but
    has NOT yet crossed below the breaking point (av=-30 > -50) does NOT warn — the agenda
    isn't live yet. (Guards against over-warning when fixing the dead zone.)"""
    agenda = CompanionAgenda(trigger="attitude_below", value=-50)
    ch = _companion(attitude=-30, agenda=agenda)  # soured, but above the -50 threshold
    res = companion_arc.evaluate(ch, _campaign_with(ch), rng=random.Random(0))
    assert "betrayal_warning" not in res


def test_betrayal_warning_reflects_active_decision_flag():
    """When a recorded choice has armed the agenda's decision_flag, the warning flags it
    so the DM foreshadows harder."""
    agenda = CompanionAgenda(trigger="attitude_below", value=0, decision_flag="let_daughter_die")
    ch = _companion(attitude=-25, agenda=agenda)
    res = companion_arc.evaluate(ch, _campaign_with(ch, flags={"let_daughter_die": True}), rng=random.Random(0))
    assert res["betrayal_warning"]["decision_flag_active"] is True


def test_no_warning_for_non_attitude_or_fired_agenda():
    """The warning is only for a LIVE attitude_below agenda — not for other triggers, and
    not once it has fired."""
    # a prize_seized agenda in the same attitude band -> no warning
    ch_event = _companion(attitude=-30, agenda=CompanionAgenda(trigger="prize_seized"))
    assert "betrayal_warning" not in companion_arc.evaluate(ch_event, _campaign_with(ch_event))

    # a fired attitude_below agenda -> no warning (the fire was the event)
    agenda = CompanionAgenda(trigger="attitude_below", value=0, fired=True)
    ch_fired = _companion(attitude=-30, agenda=agenda)
    assert "betrayal_warning" not in companion_arc.evaluate(ch_fired, _campaign_with(ch_fired))


# --- F06-11: a broken personal_quest gate link reports its error EXACTLY ONCE ----

def test_broken_quest_arc_link_reports_error_exactly_once():
    """F06-11 (audit 2026-06-11, option b — one-shot `link_error` latch): a personal_quest
    gate whose `quest_arc_id` resolves to no companion quest arc used to RE-REPORT its error
    on EVERY evaluate forever (gate stayed locked, `continue` regenerated the same error),
    violating the module's EXACTLY-ONCE contract. Now the error is latched on the gate and
    reported exactly once; the gate stays LOCKED so a later set_companion_quest_arc can still
    recover the link (the deliberate author-the-gate-first recovery path)."""
    from models import ArcGate
    ch = _companion(attitude=50, gates=[
        ArcGate(kind="personal_quest", threshold=25, quest_arc_id="cqarc-missing")
    ])
    c = _campaign_with(ch)

    first = companion_arc.evaluate(ch, c)
    unlocks = first.get("companion_quest_unlocks") or []
    assert len(unlocks) == 1 and "error" in unlocks[0]
    # The gate stays LOCKED (recovery path preserved), but the error is now latched...
    assert ch.arc.arc_gates[0].unlocked is False
    assert ch.arc.arc_gates[0].link_error == unlocks[0]["error"]

    # ...so every later beat is SILENT — the error is reported exactly once.
    second = companion_arc.evaluate(ch, c)
    assert "companion_quest_unlocks" not in second
    third = companion_arc.evaluate(ch, c)
    assert "companion_quest_unlocks" not in third


def test_valid_quest_arc_link_still_unlocks_and_reports_once():
    """A WELL-FORMED personal_quest gate link still marks the arc available exactly once and
    flips the gate (regression guard that the F06-11 fix didn't break the happy path)."""
    from models import ArcGate, CompanionQuestArc, CompanionQuestStage
    ch = _companion(attitude=50, gates=[
        ArcGate(kind="personal_quest", threshold=25, quest_arc_id="cqarc-1", stage_id="s1")
    ])
    c = _campaign_with(ch)
    c.companion_quest_arcs["cqarc-1"] = CompanionQuestArc(
        id="cqarc-1", companion_id=ch.id, title="Reckoning",
        stages=[CompanionQuestStage(id="s1", title="confront the past")],
    )

    first = companion_arc.evaluate(ch, c)
    unlocks = first.get("companion_quest_unlocks") or []
    assert len(unlocks) == 1 and "error" not in unlocks[0]
    assert unlocks[0]["changed"] == ["arc", "stage"]
    assert ch.arc.arc_gates[0].unlocked is True
    assert c.companion_quest_arcs["cqarc-1"].status == "available"
    # idempotent thereafter
    assert "companion_quest_unlocks" not in companion_arc.evaluate(ch, c)


# --- the Decision -> flag path (MCP tool layer) ----------------------------------

def test_set_flag_arms_decision_gated_agenda(camp):
    """The existing set_flag path arms an attitude_below agenda's decision_flag: with the
    companion below threshold, setting the flag spikes the betrayal so it fires reliably."""
    cid, comp = camp
    server.set_companion_arc(cid, comp, {
        "agenda": {
            "trigger": "attitude_below",
            "value": 0,
            "decision_flag": "let_daughter_die",
            "note": "the knight turns",
        },
    })
    server.adjust_attitude(cid, comp, -30)  # push deep below the breaking point

    server.set_flag(cid, "let_daughter_die")
    # With p ≈ 0.60/beat, it fires within a handful of beats (idempotent check loop).
    fired = False
    for _ in range(60):
        res = server.check_companion_arc(cid, comp)["results"]
        if any(r.get("agenda_fired") for r in res):
            fired = True
            break
    assert fired, "decision-gated betrayal should fire once the flag is set and attitude is below threshold"


def test_record_decision_sets_flag_persists_and_arms_betrayal(camp):
    """record_decision(..., sets_flag=...) flips the content-defined flag in the same call
    that records the choice — the one-step decision->escalation path."""
    cid, comp = camp
    out = server.record_decision(
        cid,
        "Let the farmer's daughter die",
        options=["save her", "let her die"],
        chosen="let her die",
        sets_flag="let_daughter_die",
    )
    assert out["flag"] == "let_daughter_die"
    c = store.load_campaign(cid)
    assert c.flags["let_daughter_die"] is True
    assert len(c.decisions) == 1 and c.decisions[0].chosen == "let her die"


def test_record_decision_without_sets_flag_is_unchanged(camp):
    """Omitting sets_flag leaves flags untouched and the return shape free of `flag` —
    today's behavior, byte-for-byte."""
    cid, _comp = camp
    out = server.record_decision(cid, "A choice with no gated agenda", chosen="x")
    assert "flag" not in out
    assert store.load_campaign(cid).flags == {}
