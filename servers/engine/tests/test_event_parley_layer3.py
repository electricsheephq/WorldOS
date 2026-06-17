"""Tests for the Quest & Arc engine, Layer 3 — first-class Event / ParleyOption / Outcome.

The Kingmaker stumble-into decisional made a real engine fact: a content-authored ``Event``
whose ``ParleyOption``s carry a DETERMINISTIC ``Outcome`` that ripples through the EXISTING
engine vocabulary (``worldsim._apply_structured_effect`` + ``consequences.schedule``) AND can
STAGE the already-merged Layer-2 companion flip by setting a ``decision_flag``.

Coverage (per the verification plan):
  * ``present_events`` surfaces a trigger-met Event and hides a trigger-unmet / resolved one.
  * ``resolve_event`` applies each Outcome kind (flag set, faction reputation_delta, scheduled
    Consequence, narrate) and is IDEMPOTENT on re-resolve (a no-op).
  * the L2<->L3 SEAM: an option whose Outcome sets a ``decision_flag`` makes the matching
    ``attitude_below`` companion agenda's betrayal probability spike (reuses the L2 pattern).
  * ADDITIVE: no events == today's behavior, byte-for-byte; old snapshots round-trip.
  * the parley surface EXTENSION (engine generate_parley_options(event_id=) — reused, not new).
  * content seeding via ``seed_world`` / the ending overlay (degrade-not-abort).

Pure-module functions (events.py) are exercised directly (like test_companion_arc.py); the
MCP tools (server.py) are exercised against a real persisted campaign (like test_parley.py).
Single-process only (the host OOMs on parallel pytest).
"""

import random

import pytest

import companion_arc
import content as content_mod
import events as events_mod
import server
import store
from models import (
    Campaign,
    Character,
    CompanionAgenda,
    CompanionArc,
    Event,
    Faction,
    Outcome,
    ParleyOption,
)


# --- helpers -----------------------------------------------------------------


def _event(eid="event_x", trigger="manual", options=None, **kw) -> Event:
    return Event(
        id=eid,
        trigger=trigger,
        prompt=kw.pop("prompt", "A figure falls into step beside you."),
        options=options if options is not None else [ParleyOption(label="Listen")],
        **kw,
    )


def _campaign_with_events(*evs: Event, day: int = 1, flags=None, factions=None) -> Campaign:
    c = Campaign(title="L3")
    c.day = day
    if flags:
        c.flags.update(flags)
    for f in factions or []:
        c.factions[f.id] = f
    for ev in evs:
        c.events[ev.id] = ev
    return c


# =========================================================================
# ADDITIVE DEFAULT + round-trip (empty == today, old snapshots load)
# =========================================================================


def test_campaign_events_defaults_empty():
    assert Campaign(title="T").events == {}


def test_old_campaign_snapshot_without_events_deserializes_unchanged():
    """A snapshot authored before Layer 3 has no `events` key — it must load with events={}
    and round-trip identically (the additive-default contract)."""
    c = Campaign(title="Pre-L3")
    data = c.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "events"}
    assert "events" not in old
    reloaded = Campaign.model_validate(old)
    assert reloaded.events == {}
    # a full round-trip stays stable
    assert Campaign.model_validate(reloaded.model_dump(mode="json")).events == {}


def test_event_models_default_shapes():
    o = Outcome()
    assert o.flag == "" and o.decision_flag == "" and o.reputation_delta == 0
    assert o.schedule_in_days == 0 and o.schedule_text == "" and o.narrate == ""
    po = ParleyOption(label="Take the bribe")
    assert po.tag == "" and po.skill == "" and po.dc == 0
    assert isinstance(po.outcome, Outcome) and po.outcome.flag == ""
    ev = Event(prompt="p")
    assert ev.trigger == "manual" and ev.resolved is False and ev.options == []
    assert ev.id.startswith("event_")
    # SYN-04 (F07-3): first_presented_day defaults to None (never presented yet) — additive.
    assert ev.first_presented_day is None


def test_event_first_presented_day_additive_round_trip():
    """SYN-04: an old Event snapshot without first_presented_day loads with None and
    round-trips. Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-04 / F07-3)."""
    ev = _event(eid="event_fp")
    data = ev.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "first_presented_day"}
    assert "first_presented_day" not in old
    reloaded = Event.model_validate(old)
    assert reloaded.first_presented_day is None
    # a stamped value round-trips too
    ev.first_presented_day = 7
    assert Event.model_validate(ev.model_dump(mode="json")).first_presented_day == 7


def test_event_round_trips_with_full_outcome():
    ev = _event(options=[ParleyOption(
        label="Take the bribe", tag="CN", skill="deception", dc=15,
        outcome=Outcome(flag="took_money", faction_id="fac-x", reputation_delta=-10,
                        decision_flag="took_bribe", schedule_in_days=7, schedule_text="debt called",
                        narrate="You pocket the gold."),
    )])
    reloaded = Event.model_validate(ev.model_dump(mode="json"))
    assert reloaded == ev


# =========================================================================
# TRIGGERS (contract-safe: flags / reputation / day only) — events.trigger_holds + present
# =========================================================================


def test_manual_trigger_always_available_until_resolved():
    ev = _event(eid="event_m", trigger="manual")
    c = _campaign_with_events(ev)
    assert [e.id for e in events_mod.present(c)] == ["event_m"]
    ev.resolved = True
    assert events_mod.present(c) == []  # resolved -> hidden (idempotent)


def test_flag_set_trigger_gates_on_flag():
    ev = _event(eid="event_f", trigger="flag_set", trigger_value="door_open")
    c = _campaign_with_events(ev)
    assert events_mod.present(c) == []  # flag absent
    c.flags["door_open"] = True
    assert [e.id for e in events_mod.present(c)] == ["event_f"]
    c.flags["door_open"] = False
    assert events_mod.present(c) == []  # present but False -> not available


def test_day_reached_trigger_gates_on_day():
    ev = _event(eid="event_d", trigger="day_reached", trigger_threshold=5)
    c = _campaign_with_events(ev, day=4)
    assert events_mod.present(c) == []
    c.day = 5
    assert [e.id for e in events_mod.present(c)] == ["event_d"]


def test_reputation_at_positive_target_arms_on_rise():
    fac = Faction(id="fac-fist", name="Flaming Fist", reputation=0)
    ev = _event(eid="event_r", trigger="reputation_at", trigger_faction_id="fac-fist", trigger_threshold=10)
    c = _campaign_with_events(ev, factions=[fac])
    assert events_mod.present(c) == []  # rep 0 < 10
    fac.reputation = 10
    assert [e.id for e in events_mod.present(c)] == ["event_r"]


def test_reputation_at_negative_target_arms_on_fall():
    """A negative threshold arms when reputation has fallen TO/BELOW it (the sign picks
    direction) — 'when they hate you enough'."""
    fac = Faction(id="fac-fist", name="Flaming Fist", reputation=0)
    ev = _event(eid="event_rn", trigger="reputation_at", trigger_faction_id="fac-fist", trigger_threshold=-10)
    c = _campaign_with_events(ev, factions=[fac])
    assert events_mod.present(c) == []  # rep 0 not <= -10
    fac.reputation = -10
    assert [e.id for e in events_mod.present(c)] == ["event_rn"]


def test_reputation_at_unknown_faction_never_available():
    ev = _event(eid="event_ru", trigger="reputation_at", trigger_faction_id="nope", trigger_threshold=5)
    c = _campaign_with_events(ev)  # no such faction
    assert events_mod.present(c) == []


def test_present_skips_resolved_and_is_id_ordered():
    a = _event(eid="event_a", trigger="manual")
    b = _event(eid="event_b", trigger="manual")
    z = _event(eid="event_z", trigger="manual", prompt="resolved one")
    z.resolved = True
    c = _campaign_with_events(z, b, a)
    assert [e.id for e in events_mod.present(c)] == ["event_a", "event_b"]  # sorted, z skipped


def test_unknown_trigger_degrades_to_unavailable():
    ev = _event(eid="event_bad", trigger="manual")
    # force an out-of-vocabulary trigger past validation by mutating the instance
    object.__setattr__(ev, "trigger", "garbage")
    c = _campaign_with_events(ev)
    assert events_mod.trigger_holds(ev, c) is False  # never raises


# =========================================================================
# RESOLVE — each Outcome kind applies; idempotent on re-resolve (pure module)
# =========================================================================


def test_resolve_sets_flag():
    ev = _event(options=[ParleyOption(label="Open it", outcome=Outcome(flag="vault_open", narrate="It swings wide."))])
    c = _campaign_with_events(ev)
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Open it"))
    assert c.flags.get("vault_open") is True
    assert res["flags_set"] == ["vault_open"]
    assert res["narrated_line"] == "It swings wide."
    assert ev.resolved is True


def test_resolve_shifts_faction_reputation_clamped():
    fac = Faction(id="fac-x", name="X", reputation=95)
    ev = _event(options=[ParleyOption(label="Help them", outcome=Outcome(faction_id="fac-x", reputation_delta=20))])
    c = _campaign_with_events(ev, factions=[fac])
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Help them"))
    assert c.factions["fac-x"].reputation == 100  # clamped to +100
    assert res["rep_shift"] == {"faction_id": "fac-x", "reputation_delta": 20, "reputation": 100}


def test_resolve_schedules_consequence():
    ev = _event(options=[ParleyOption(
        label="Take the bribe",
        outcome=Outcome(schedule_in_days=7, schedule_text="Raphael calls in the debt"),
    )])
    c = _campaign_with_events(ev, day=3)
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Take the bribe"))
    due = [co for co in c.consequences if co.text == "Raphael calls in the debt"]
    assert len(due) == 1
    assert due[0].trigger_day == 3 + 7  # scheduled relative to today
    assert due[0].note == f"event:{ev.id}"
    assert res["scheduled"] == {"trigger_day": 10, "text": "Raphael calls in the debt"}


def test_resolve_half_specified_schedule_is_noop():
    """A schedule needs BOTH a positive day count and text — a half-spec never creates a
    0-day phantom consequence."""
    ev = _event(options=[
        ParleyOption(label="days only", outcome=Outcome(schedule_in_days=5)),  # no text
        ParleyOption(label="text only", outcome=Outcome(schedule_text="something")),  # no days
    ])
    c = _campaign_with_events(ev)
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "days only"))
    assert c.consequences == []
    assert res["scheduled"] is None


def test_resolve_narrate_only_is_pure_no_ripple():
    ev = _event(options=[ParleyOption(label="Walk away", outcome=Outcome(narrate="You turn your back."))])
    c = _campaign_with_events(ev)
    before = c.model_dump(mode="json")
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Walk away"))
    assert res["narrated_line"] == "You turn your back."
    assert res["flags_set"] == [] and res["rep_shift"] is None and res["scheduled"] is None
    # the ONLY change is the resolved latch and (nothing else moved)
    after = c.model_dump(mode="json")
    before["events"][ev.id]["resolved"] = True
    assert after == before


def test_resolve_empty_outcome_is_pure_noop_except_latch():
    ev = _event(options=[ParleyOption(label="Shrug")])  # default empty Outcome
    c = _campaign_with_events(ev)
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Shrug"))
    assert c.flags == {} and c.consequences == []
    assert res["flags_set"] == [] and res["decision_flag"] == ""
    assert ev.resolved is True


def test_resolve_applies_all_outcome_kinds_together():
    fac = Faction(id="fac-x", name="X", reputation=0)
    ev = _event(options=[ParleyOption(label="Take the bribe", outcome=Outcome(
        flag="took_money", faction_id="fac-x", reputation_delta=-15,
        decision_flag="took_bribe", schedule_in_days=7, schedule_text="debt called in",
        narrate="You pocket the gold.",
    ))])
    c = _campaign_with_events(ev, factions=[fac])
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, "Take the bribe"))
    assert c.flags.get("took_money") is True
    assert c.flags.get("took_bribe") is True  # the L2 decision flag
    assert c.factions["fac-x"].reputation == -15
    assert any(co.text == "debt called in" for co in c.consequences)
    assert set(res["flags_set"]) == {"took_money", "took_bribe"}
    assert res["decision_flag"] == "took_bribe"
    assert res["narrated_line"] == "You pocket the gold."


def test_find_option_case_insensitive_and_first_match():
    ev = _event(options=[ParleyOption(label="Take the Bribe"), ParleyOption(label="Refuse")])
    assert events_mod.find_option(ev, "take the bribe").label == "Take the Bribe"
    assert events_mod.find_option(ev, "  REFUSE  ").label == "Refuse"
    assert events_mod.find_option(ev, "nope") is None


# =========================================================================
# THE TOOLS (server.py) against a real persisted campaign — incl. idempotency
# =========================================================================


@pytest.fixture
def l3_campaign(tmp_path, monkeypatch):
    """A persisted campaign with a lead PC, a companion, and a faction. Returns (cid, pc, comp)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Layer 3 Test")["id"]
    pc = server.create_character(cid, "Vanya", kind="player", class_name="bard", level=3)["id"]
    comp = server.create_character(cid, "Dorn", kind="companion", class_name="fighter", level=3)["id"]
    c = store.load_campaign(cid)
    c.factions["fac-fist"] = Faction(id="fac-fist", name="Flaming Fist", reputation=0)
    store.save_campaign(c)
    return cid, pc, comp


def _inject_event(cid: str, ev: Event) -> None:
    c = store.load_campaign(cid)
    c.events[ev.id] = ev
    store.save_campaign(c)


def test_present_events_tool_surfaces_trigger_met_hides_unmet(l3_campaign):
    cid, _pc, _comp = l3_campaign
    met = _event(eid="event_met", trigger="manual",
                 options=[ParleyOption(label="Take the bribe", tag="CN", skill="deception", dc=15)])
    unmet = _event(eid="event_unmet", trigger="flag_set", trigger_value="never_set")
    _inject_event(cid, met)
    _inject_event(cid, unmet)
    out = server.present_events(cid)
    ids = [e["id"] for e in out["events"]]
    assert ids == ["event_met"]
    assert out["free_form"] is True  # the #141 guard: never a closed set
    surfaced = out["events"][0]
    assert surfaced["prompt"]
    assert surfaced["options"] == [{"label": "Take the bribe", "tag": "CN", "skill": "deception", "dc": 15}]


def test_present_events_tool_hides_resolved(l3_campaign):
    cid, _pc, _comp = l3_campaign
    ev = _event(eid="event_done", trigger="manual")
    ev.resolved = True
    _inject_event(cid, ev)
    assert server.present_events(cid)["events"] == []


def test_present_events_tool_is_read_only(l3_campaign):
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_ro", trigger="manual"))
    before = store.load_campaign(cid).model_dump(mode="json")
    server.present_events(cid)
    after = store.load_campaign(cid).model_dump(mode="json")
    assert before == after


def test_resolve_event_tool_applies_outcome_and_persists(l3_campaign):
    cid, _pc, _comp = l3_campaign
    ev = _event(eid="event_bribe", trigger="manual", options=[ParleyOption(
        label="Take the bribe",
        outcome=Outcome(flag="took_money", faction_id="fac-fist", reputation_delta=-20,
                        decision_flag="took_bribe", narrate="You pocket the gold."),
    )])
    _inject_event(cid, ev)
    res = server.resolve_event(cid, "event_bribe", "take the bribe")  # case-insensitive label
    assert res["resolved"] is True and res.get("noop") is None
    assert res["decision_flag"] == "took_bribe"
    # persisted
    c = store.load_campaign(cid)
    assert c.flags.get("took_money") is True
    assert c.flags.get("took_bribe") is True
    assert c.factions["fac-fist"].reputation == -20
    assert c.events["event_bribe"].resolved is True


def test_resolve_event_tool_idempotent_on_re_resolve(l3_campaign):
    """Re-resolving a resolved Event is a NO-OP — applies nothing, can't double-ripple."""
    cid, _pc, _comp = l3_campaign
    ev = _event(eid="event_once", trigger="manual", options=[ParleyOption(
        label="Help", outcome=Outcome(faction_id="fac-fist", reputation_delta=10),
    )])
    _inject_event(cid, ev)
    first = server.resolve_event(cid, "event_once", "Help")
    assert first["rep_shift"]["reputation"] == 10
    # second call: no-op, reputation does NOT move again
    second = server.resolve_event(cid, "event_once", "Help")
    assert second["noop"] is True and second["resolved"] is True
    assert store.load_campaign(cid).factions["fac-fist"].reputation == 10  # still 10, not 20


def test_resolve_event_tool_unknown_event_raises(l3_campaign):
    cid, _pc, _comp = l3_campaign
    with pytest.raises(ValueError):
        server.resolve_event(cid, "event_nope", "anything")


def test_resolve_event_tool_unknown_option_raises(l3_campaign):
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_o", trigger="manual", options=[ParleyOption(label="Yes")]))
    with pytest.raises(ValueError):
        server.resolve_event(cid, "event_o", "Maybe")


# =========================================================================
# THE L2<->L3 SEAM — resolve_event arming a decision_flag spikes the betrayal
# =========================================================================


def _knight_with_agenda(decision_flag: str, attitude: int = -30, threshold: int = 0) -> Character:
    agenda = CompanionAgenda(trigger="attitude_below", value=threshold, decision_flag=decision_flag)
    return Character(name="Knight", kind="companion", attitude_value=attitude, arc=CompanionArc(agenda=agenda))


def test_l2_l3_seam_resolve_arms_matching_agenda():
    """The whole integration: an Event option whose Outcome sets `decision_flag` flips the same
    flag a matching attitude_below agenda reads (Layer 2). They meet at Campaign.flags."""
    knight = _knight_with_agenda("took_bribe")
    c = Campaign(title="seam")
    c.characters[knight.id] = knight
    c.party.append(knight.id)
    ev = _event(eid="event_seam", trigger="manual", options=[ParleyOption(
        label="Take the bribe", outcome=Outcome(decision_flag="took_bribe"),
    )])
    c.events[ev.id] = ev

    agenda = knight.arc.agenda
    # before: the flag isn't set, the boost is NOT active
    assert companion_arc._decision_flag_active(agenda, c) is False
    p_off = companion_arc._attitude_below_snap_p(
        knight.attitude_value, agenda.value, vulnerable=False, decision_flag_active=False
    )

    # resolve the event -> sets the flag (identical to record_decision(sets_flag="took_bribe"))
    events_mod.resolve(c, ev, events_mod.find_option(ev, "Take the bribe"))
    assert c.flags.get("took_bribe") is True

    # after: the SAME agenda now reads the flag as active and the snap probability SPIKES
    assert companion_arc._decision_flag_active(agenda, c) is True
    p_on = companion_arc._attitude_below_snap_p(
        knight.attitude_value, agenda.value, vulnerable=False, decision_flag_active=True
    )
    assert p_on > p_off
    assert p_on == pytest.approx(p_off + companion_arc.ATTITUDE_SNAP_DECISION_BONUS)


def test_l2_l3_seam_fire_rate_spikes_through_resolve_event_tool(l3_campaign, monkeypatch):
    """End-to-end through the TOOLS: resolve_event arms the flag, then check_companion_arc
    fires the betrayal at a NOTABLY higher rate than the same agenda with no Event resolved.
    Mirrors the L2 statistical gate (test_companion_arc.test_decision_flag_fires_at_...)."""
    cid, _pc, _comp = l3_campaign

    # attach a knight with an attitude_below agenda gated on "took_bribe", below threshold
    c = store.load_campaign(cid)
    knight = _knight_with_agenda("took_bribe", attitude=-30, threshold=0)
    c.characters[knight.id] = knight
    c.party.append(knight.id)
    c.events["event_bribe"] = _event(eid="event_bribe", trigger="manual", options=[ParleyOption(
        label="Take the bribe", outcome=Outcome(decision_flag="took_bribe"),
    )])
    store.save_campaign(c)

    # a seeded rng makes evaluate deterministic; sample the fire rate with the flag OFF
    def fire_rate(seed: int) -> float:
        rng = random.Random(seed)
        hits = 0
        trials = 400
        for _ in range(trials):
            cc = store.load_campaign(cid)
            k = next(ch for ch in cc.characters.values() if ch.name == "Knight")
            k.arc.agenda.fired = False
            if companion_arc.evaluate(k, cc, rng=rng)["agenda_fired"]:
                hits += 1
        return hits / trials

    rate_off = fire_rate(seed=7)

    # now resolve the Event through the TOOL -> arms took_bribe
    server.resolve_event(cid, "event_bribe", "Take the bribe")
    assert store.load_campaign(cid).flags.get("took_bribe") is True

    rate_on = fire_rate(seed=8)

    # base ≈ 0.30, boosted ≈ 0.60 — demand a clearly higher rate (wide margin for variance)
    assert rate_on > rate_off + 0.15, f"on={rate_on:.3f} should be notably > off={rate_off:.3f}"


def test_l2_l3_seam_no_decision_flag_is_pure_world_ripple():
    """An Outcome with NO decision_flag is just a world ripple — it never arms a companion
    flip (fully additive)."""
    knight = _knight_with_agenda("took_bribe")
    c = Campaign(title="ripple")
    c.characters[knight.id] = knight
    c.party.append(knight.id)
    c.factions["fac-x"] = Faction(id="fac-x", name="X", reputation=0)
    ev = _event(eid="event_world", trigger="manual", options=[ParleyOption(
        label="Donate", outcome=Outcome(faction_id="fac-x", reputation_delta=10),  # no decision_flag
    )])
    c.events[ev.id] = ev
    events_mod.resolve(c, ev, events_mod.find_option(ev, "Donate"))
    assert c.factions["fac-x"].reputation == 10
    assert c.flags.get("took_bribe") is None  # the knight's flag was never touched
    assert companion_arc._decision_flag_active(knight.arc.agenda, c) is False


# =========================================================================
# PARLEY SURFACE EXTENSION — generate_parley_options(event_id=) (reused, not new)
# =========================================================================


def test_parley_surface_without_event_id_is_unchanged(l3_campaign):
    """No event_id -> no `event` block: today's freeform parley, byte-for-byte."""
    cid, _pc, _comp = l3_campaign
    out = server.generate_parley_options(cid)
    assert "event" not in out
    assert out["free_form"] is True
    assert out["skills"]  # still supplies the DC-tagged slots


def test_parley_surface_attaches_live_event_block(l3_campaign):
    cid, _pc, _comp = l3_campaign
    ev = _event(eid="event_live", trigger="manual", options=[
        ParleyOption(label="Take the bribe", tag="CN", skill="deception", dc=15),
        ParleyOption(label="Refuse", tag="LG"),
    ])
    _inject_event(cid, ev)
    out = server.generate_parley_options(cid, event_id="event_live")
    assert out["free_form"] is True  # free-form path STAYS (never a closed set)
    assert "event" in out
    block = out["event"]
    assert block["id"] == "event_live"
    assert block["resolve_with"] == "resolve_event"
    assert block["options"] == [
        {"label": "Take the bribe", "tag": "CN", "skill": "deception", "dc": 15},
        {"label": "Refuse", "tag": "LG", "skill": "", "dc": 0},
    ]


def test_parley_surface_omits_resolved_or_unknown_event(l3_campaign):
    cid, _pc, _comp = l3_campaign
    done = _event(eid="event_resolved", trigger="manual")
    done.resolved = True
    _inject_event(cid, done)
    assert "event" not in server.generate_parley_options(cid, event_id="event_resolved")
    assert "event" not in server.generate_parley_options(cid, event_id="event_missing")


def test_parley_surface_event_block_is_read_only(l3_campaign):
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_ro2", trigger="manual"))
    before = store.load_campaign(cid).model_dump(mode="json")
    server.generate_parley_options(cid, event_id="event_ro2")
    after = store.load_campaign(cid).model_dump(mode="json")
    assert before == after


# =========================================================================
# CONTENT SEEDING — seed_world / ending overlay (degrade-not-abort)
# =========================================================================


def _world_with_events(events_block) -> dict:
    return {
        "id": "testworld",
        "name": "Test World",
        "premise": "a place",
        "regions": [{"id": "loc-start", "name": "Start"}],
        "factions": [{"id": "fac-fist", "name": "Flaming Fist", "reputation": 0}],
        "events": events_block,
    }


def test_seed_world_seeds_events():
    world = _world_with_events([
        {
            "id": "event_raphael",
            "trigger": "manual",
            "prompt": "Raphael offers a deal.",
            "options": [
                {"label": "Take the bribe", "tag": "CN", "outcome": {"decision_flag": "took_bribe"}},
                {"label": "Refuse", "tag": "LG"},
            ],
        }
    ])
    c = content_mod.seed_world(world)
    assert "event_raphael" in c.events
    ev = c.events["event_raphael"]
    assert ev.prompt == "Raphael offers a deal."
    assert ev.options[0].outcome.decision_flag == "took_bribe"
    # and it surfaces via present (manual trigger)
    assert any(e.id == "event_raphael" for e in events_mod.present(c))


def test_seed_world_no_events_key_is_noop():
    world = {"id": "w", "name": "W", "regions": [{"id": "loc-a", "name": "A"}]}
    c = content_mod.seed_world(world)
    assert c.events == {}


def test_seed_world_degrades_on_malformed_event(capsys):
    """A malformed event entry is skipped (degrade-not-abort); valid siblings still seed."""
    world = _world_with_events([
        {"id": "event_good", "trigger": "manual", "prompt": "ok", "options": [{"label": "A"}]},
        {"id": "event_bad", "trigger": "manual", "bogus_key": True},  # forbidden extra -> skip
        "not even an object",  # skip
    ])
    c = content_mod.seed_world(world)
    assert "event_good" in c.events
    assert "event_bad" not in c.events
    assert "[content] skipping malformed event" in capsys.readouterr().out


def test_seed_world_skips_reputation_at_with_unknown_faction(capsys):
    world = _world_with_events([
        {"id": "event_rep", "trigger": "reputation_at", "trigger_faction_id": "ghost",
         "trigger_threshold": 5, "prompt": "p", "options": [{"label": "A"}]},
    ])
    c = content_mod.seed_world(world)
    assert "event_rep" not in c.events
    assert "unknown faction" in capsys.readouterr().out


def test_seed_world_events_block_accepts_dict_mapping():
    """The events block may be a dict id->event (like Campaign.events) OR a list."""
    world = _world_with_events({
        "event_a": {"id": "event_a", "trigger": "manual", "prompt": "p", "options": [{"label": "A"}]},
    })
    c = content_mod.seed_world(world)
    assert "event_a" in c.events


def test_ending_overlay_seeds_events_on_top_of_base():
    """An ending overlay may ALSO seed Events — the post-state shapes which decisionals stumble
    into the party. Folded onto whatever the base world already seeded."""
    c = Campaign(title="W")
    c.factions["fac-fist"] = Faction(id="fac-fist", name="Flaming Fist", reputation=0)
    c.events["event_base"] = _event(eid="event_base", trigger="manual")  # a pre-existing base event
    overlay = {
        "id": "tyranny", "name": "Tyranny",
        "events": [
            {"id": "event_overlay", "trigger": "manual", "prompt": "The Fist demands tribute.",
             "options": [{"label": "Pay", "outcome": {"decision_flag": "paid_tribute"}}]},
        ],
    }
    content_mod._apply_ending_overlay(c, overlay)  # must NOT raise
    assert "event_base" in c.events  # base survives
    assert "event_overlay" in c.events  # overlay folds on top
    assert c.events["event_overlay"].options[0].outcome.decision_flag == "paid_tribute"


def test_ending_overlay_events_degrade_not_abort():
    """A malformed overlay event is skipped; a valid sibling still applies; no `events` key is
    a no-op (today's behavior)."""
    c = Campaign(title="W")
    overlay = {
        "id": "ov", "name": "Ov",
        "events": [
            {"id": "event_ok", "trigger": "manual", "prompt": "ok", "options": [{"label": "A"}]},
            {"id": "event_bad", "trigger": "manual", "forbidden": True},  # extra key -> skip
        ],
    }
    content_mod._apply_ending_overlay(c, overlay)  # must NOT raise
    assert "event_ok" in c.events and "event_bad" not in c.events

    # no events key -> nothing touched
    c2 = Campaign(title="W2")
    content_mod._apply_ending_overlay(c2, {"id": "ov2", "name": "Ov2"})
    assert c2.events == {}


# =========================================================================
# CANON EXEMPLAR CONTENT — the baldurs-gate Raphael bribe Event + Minsc agenda
# (the DM-skill-wiring wave: prove the authored canon content LOADS and the
#  authored L3->L2 seam works end-to-end through the REAL world + ending overlay)
# =========================================================================

# Content-defined constants the exemplar authors (flag names live in CONTENT, never engine
# code — so the test asserts the CONTENT'S choices, the engine stays setting-agnostic).
BG_WORLD = "baldurs-gate"
BG_HOPEFUL_ENDING = "netherbrain-destroyed-heroes-live"
RAPHAEL_EVENT = "event-raphael-bargain"
BRIBE_OPTION = "Take the bargain"
TOOK_BRIBE_FLAG = "took_bribe"


def _seed_bg(ending: str = "") -> Campaign:
    """Seed the real baldurs-gate world (optionally with an ending overlay) from its shipped
    content. Skips cleanly if the world bible isn't reachable from the test's cwd."""
    try:
        world = content_mod.load_world_data(BG_WORLD)
    except (ValueError, FileNotFoundError, OSError):  # pragma: no cover - content not present
        pytest.skip("baldurs-gate world content not reachable from test cwd")
    return content_mod.seed_world(world, ending=ending)


def test_bg_raphael_event_seeds_and_surfaces():
    """The authored Raphael bribe Event loads from the base world and surfaces via present
    once its day_reached trigger arms (SYN-04: not at minute one), bound to the canon
    anchor NPC, with the bribe option carrying the took_bribe decision_flag + the negative
    Flaming-Fist ripple + the rule-of-three echo."""
    c = _seed_bg()
    assert RAPHAEL_EVENT in c.events, "the authored Raphael Event must seed from world.json"
    ev = c.events[RAPHAEL_EVENT]
    assert ev.prompt.strip(), "the Event must carry a prompt the DM voices"
    # SYN-04: a day_reached trigger so the devil's bargain doesn't open the campaign cold.
    assert ev.trigger == "day_reached" and ev.trigger_threshold >= 1
    assert not any(e.id == RAPHAEL_EVENT for e in events_mod.present(c)), \
        "must NOT surface on day 1 (the trigger hasn't armed) — SYN-04 fix"
    # bound to a canon roster NPC (the owner's anchoring priority) — Raphael is in the roster
    assert ev.anchor_npc_id == "npc-raphael"
    assert c.characters.get("npc-raphael") is not None, "anchor NPC must exist in the roster"
    # the bribe option's deterministic Outcome
    bribe = events_mod.find_option(ev, BRIBE_OPTION)
    assert bribe is not None, "the bribe ParleyOption must exist"
    assert bribe.outcome.decision_flag == TOOK_BRIBE_FLAG  # the L3->L2 seam flag
    assert bribe.outcome.faction_id == "fac-flaming-fist" and bribe.outcome.reputation_delta < 0
    assert bribe.outcome.schedule_in_days > 0 and bribe.outcome.schedule_text.strip()  # L1 echo
    # a refuse path exists (a choice the player can decline cleanly)
    assert events_mod.find_option(ev, "Refuse him") is not None
    # surfaces once the in-world day reaches the trigger (still unresolved)
    c.day = ev.trigger_threshold
    assert any(e.id == RAPHAEL_EVENT for e in events_mod.present(c))


def test_bg_hopeful_ending_seeds_minsc_bribe_agenda():
    """The hopeful-ending overlay pre-loads Minsc's attitude_below agenda gated on took_bribe
    (the principled companion whose bond the bribe can break) — the L3->L2 content seam."""
    c = _seed_bg(ending=BG_HOPEFUL_ENDING)
    minsc = c.characters.get("npc-minsc")
    assert minsc is not None and minsc.arc is not None, "Minsc must carry a seeded arc"
    agenda = minsc.arc.agenda
    assert agenda is not None and agenda.trigger == "attitude_below"
    assert agenda.decision_flag == TOOK_BRIBE_FLAG, "agenda must read the same flag the bribe sets"
    assert agenda.value is not None
    # the breaking point sits in/at the danger band so betrayal_warning can telegraph the fracture
    assert companion_arc.ATTITUDE_WARN_LOW <= agenda.value <= companion_arc.ATTITUDE_WARN_HIGH


def test_bg_exemplar_l3_to_l2_seam_end_to_end():
    """The whole authored loop on the REAL content: in the hopeful ending, resolving Raphael's
    bribe option arms took_bribe, which spikes Minsc's attitude_below betrayal probability and
    lights the advisory betrayal_warning. Mirrors the synthetic seam test against shipped canon."""
    c = _seed_bg(ending=BG_HOPEFUL_ENDING)
    minsc = c.characters["npc-minsc"]
    agenda = minsc.arc.agenda
    # put Minsc's bond in the danger band (a curdled friendship the bribe can finally break)
    minsc.attitude_value = (companion_arc.ATTITUDE_WARN_LOW + companion_arc.ATTITUDE_WARN_HIGH) // 2

    # before resolving: the flag is unset, no boost, the snap probability is the unescalated curve
    assert companion_arc._decision_flag_active(agenda, c) is False
    p_off = companion_arc._attitude_below_snap_p(
        minsc.attitude_value, agenda.value, vulnerable=False, decision_flag_active=False
    )

    # resolve the authored bribe option -> sets took_bribe (identical to record_decision(sets_flag=))
    ev = c.events[RAPHAEL_EVENT]
    res = events_mod.resolve(c, ev, events_mod.find_option(ev, BRIBE_OPTION))
    assert res["decision_flag"] == TOOK_BRIBE_FLAG
    assert c.flags.get(TOOK_BRIBE_FLAG) is True
    # the world rippled too: the Flaming Fist's regard fell, the cambion's return is scheduled
    assert c.factions["fac-flaming-fist"].reputation < 1  # base rep was 1; the bribe dropped it
    assert any(co.note == f"event:{ev.id}" for co in c.consequences)

    # after: the SAME agenda now reads the flag as active and the snap probability SPIKES
    assert companion_arc._decision_flag_active(agenda, c) is True
    p_on = companion_arc._attitude_below_snap_p(
        minsc.attitude_value, agenda.value, vulnerable=False, decision_flag_active=True
    )
    assert p_on == pytest.approx(p_off + companion_arc.ATTITUDE_SNAP_DECISION_BONUS)
    assert p_on > p_off

    # and the advisory telegraph fires so the DM can foreshadow the fracture before it snaps
    warn = companion_arc._betrayal_warning(minsc, c)
    assert warn is not None and warn["decision_flag_active"] is True


def test_bg_base_world_has_no_minsc_agenda_additive():
    """ADDITIVE check: the agenda is an ENDING-overlay seed. The base world (no ending) leaves
    Minsc without the betrayal agenda — taking the bribe there is a pure world ripple, no flip
    armed (the seam is opt-in via the post-state, exactly like Karlach's romance gate)."""
    c = _seed_bg()  # base world, no ending overlay
    minsc = c.characters.get("npc-minsc")
    assert minsc is not None
    # base roster seeds the dossier only (not an arc/agenda) — so no took_bribe-gated flip
    assert minsc.arc is None or minsc.arc.agenda is None
    # the Event still loads and resolving the bribe still ripples the world (just arms no companion)
    ev = c.events[RAPHAEL_EVENT]
    events_mod.resolve(c, ev, events_mod.find_option(ev, BRIBE_OPTION))
    assert c.flags.get(TOOK_BRIBE_FLAG) is True  # the flag is set regardless
    # ...but with no agenda reading it, nothing is armed (additive: no ending == today's behavior)


# =========================================================================
# CANON EXEMPLAR CONTENT — the living-story FILL: more stumble-into Events on
# canon BG factions/NPCs (Fist checkpoint, Guild offer, refugee crisis, patriar
# bargain) + two more L3->L2 companion-agenda seams (Astarion, Shadowheart).
# Same discipline as the Raphael/Minsc exemplar above: prove the authored canon
# content LOADS, surfaces, ripples, and arms the right companion end-to-end.
# Flag names are CONTENT'S choices (asserted here); the engine stays setting-agnostic.
# =========================================================================

# The four new stumble-into Events and the one CONTENT flag each "entangling" option sets.
BG_FILL_EVENTS = {
    "event-fist-checkpoint": ("fac-flaming-fist", "took_bribe"),
    "event-guild-offer": ("fac-guild", "owes_the_guild"),
    "event-refugee-crisis": ("fac-harpers", "abandoned_refugees"),
    "event-patriar-bargain": ("fac-zhentarim", "served_patriar_blackmail"),
}


def test_bg_fill_events_seed_and_surface():
    """All four authored fill Events load from the base world, bind to a canon roster anchor NPC,
    surface via present once their day_reached trigger arms (SYN-04: staggered, not all at
    minute one), and each offers a clean decline/walk-away path."""
    c = _seed_bg()
    # SYN-04: none ride beat 1 — they carry real (day_reached) triggers, not 'manual'.
    day1_ids = {e.id for e in events_mod.present(c)}
    for eid in BG_FILL_EVENTS:
        assert eid not in day1_ids, f"{eid} must NOT surface on day 1 (SYN-04 fix)"
    # advance the clock past the latest trigger so all four are now available.
    c.day = max(c.events[eid].trigger_threshold for eid in BG_FILL_EVENTS)
    present_ids = {e.id for e in events_mod.present(c)}
    for eid, (fac_id, _flag) in BG_FILL_EVENTS.items():
        assert eid in c.events, f"the authored fill Event {eid!r} must seed from world.json"
        ev = c.events[eid]
        assert ev.prompt.strip(), f"{eid} must carry a prompt the DM voices"
        assert ev.trigger == "day_reached", f"{eid} must use a real trigger, not manual (SYN-04)"
        # bound to a canon roster NPC (the owner's anchoring priority)
        assert ev.anchor_npc_id and c.characters.get(ev.anchor_npc_id) is not None, (
            f"{eid} must anchor to a real roster NPC"
        )
        # at least 2 options, and at least one carries the entangling decision_flag the content names
        assert len(ev.options) >= 2
        assert ev.id in present_ids, f"{eid} (day_reached trigger, unresolved) must surface via present"
        # the touched faction is a real seeded faction (so the rep ripple lands, not degrades)
        assert fac_id in c.factions


def test_bg_fill_events_entangling_option_ripples_and_arms_flag():
    """Each fill Event's 'entangling' option sets the CONTENT-defined decision_flag (the L3->L2
    seam), shifts the named faction's reputation, and schedules the rule-of-three echo — exactly
    the Raphael exemplar's shape, on more canon anchors. Resolving is idempotent."""
    for eid, (fac_id, flag) in BG_FILL_EVENTS.items():
        c = _seed_bg()
        ev = c.events[eid]
        # find the one option whose Outcome carries this content flag (decision_flag or flag)
        opt = next(
            (o for o in ev.options if flag in (o.outcome.decision_flag, o.outcome.flag)),
            None,
        )
        assert opt is not None, f"{eid} must have an option setting the {flag!r} flag"
        res = events_mod.resolve(c, ev, opt)
        assert c.flags.get(flag) is True, f"{eid}: resolving the entangling option must set {flag!r}"
        # the named faction's reputation moved (the shared ripple path), and the echo is scheduled
        assert res["rep_shift"] is not None and res["rep_shift"]["faction_id"] == fac_id
        assert res["scheduled"] is not None, f"{eid}: the entangling option schedules a callback"
        assert ev.resolved is True
        # idempotent: re-resolving a resolved Event applies nothing new
        flags_before = dict(c.flags)
        events_mod.resolve(c, ev, opt)
        assert c.flags == flags_before


def test_bg_fill_events_have_clean_refuse_path():
    """Every fill Event offers a 'refuse/walk away' option whose Outcome arms NO companion flip
    and schedules nothing — the player can always decline cleanly (the owner's requirement)."""
    c = _seed_bg()
    # the authored clean-out option label per Event (a walk-away with no decision_flag)
    clean = {
        "event-fist-checkpoint": "Not your bridge, not your problem — walk away",
        "event-guild-offer": "Slide the packet back unopened",
        "event-refugee-crisis": "Broker a delay — buy the clerk, shame the patriar's men",
        "event-patriar-bargain": "Decline the commission and leave",
    }
    for eid, label in clean.items():
        ev = c.events[eid]
        opt = events_mod.find_option(ev, label)
        assert opt is not None, f"{eid} must offer the clean path {label!r}"
        assert not opt.outcome.decision_flag, f"{eid} clean path must arm no companion flip"


BG_BHAAL_ENDING = "dark-urge-bhaal"


def test_bg_hopeful_ending_seeds_astarion_guild_agenda():
    """The hopeful-ending overlay pre-loads Astarion's attitude_below agenda gated on
    owes_the_guild (in-character: he refuses to be leashed to a creditor again) — a new L3->L2
    seam wired to the Guild-offer Event. Its breaking point sits in the warn band."""
    c = _seed_bg(ending=BG_HOPEFUL_ENDING)
    astarion = c.characters.get("npc-astarion")
    assert astarion is not None and astarion.arc is not None, "Astarion must carry a seeded arc"
    agenda = astarion.arc.agenda
    assert agenda is not None and agenda.trigger == "attitude_below"
    assert agenda.decision_flag == "owes_the_guild"
    assert agenda.value is not None
    assert companion_arc.ATTITUDE_WARN_LOW <= agenda.value <= companion_arc.ATTITUDE_WARN_HIGH


def test_bg_bhaal_ending_seeds_shadowheart_refugee_agenda():
    """The dark-urge/Bhaal-ending overlay pre-loads Shadowheart's attitude_below agenda gated on
    abandoned_refugees (in-character: a comrade's cruelty to the helpless vindicates Shar's
    bleak doctrine and pulls her back to the dark) — a new L3->L2 seam wired to the refugee Event."""
    c = _seed_bg(ending=BG_BHAAL_ENDING)
    sh = c.characters.get("npc-shadowheart")
    assert sh is not None and sh.arc is not None, "Shadowheart must carry a seeded arc in this ending"
    agenda = sh.arc.agenda
    assert agenda is not None and agenda.trigger == "attitude_below"
    assert agenda.decision_flag == "abandoned_refugees"
    assert agenda.value is not None
    assert companion_arc.ATTITUDE_WARN_LOW <= agenda.value <= companion_arc.ATTITUDE_WARN_HIGH


def test_bg_fill_astarion_seam_end_to_end():
    """The whole authored loop on REAL content: in the hopeful ending, resolving the Guild-offer's
    'take the job' option arms owes_the_guild, which spikes Astarion's attitude_below betrayal
    probability and lights the advisory betrayal_warning (mirrors the Minsc/Raphael seam test)."""
    c = _seed_bg(ending=BG_HOPEFUL_ENDING)
    astarion = c.characters["npc-astarion"]
    agenda = astarion.arc.agenda
    astarion.attitude_value = (companion_arc.ATTITUDE_WARN_LOW + companion_arc.ATTITUDE_WARN_HIGH) // 2

    assert companion_arc._decision_flag_active(agenda, c) is False
    p_off = companion_arc._attitude_below_snap_p(
        astarion.attitude_value, agenda.value, vulnerable=False, decision_flag_active=False
    )
    # resolve the authored Guild "take the job" option -> sets owes_the_guild
    ev = c.events["event-guild-offer"]
    opt = next(o for o in ev.options if o.outcome.decision_flag == "owes_the_guild")
    res = events_mod.resolve(c, ev, opt)
    assert res["decision_flag"] == "owes_the_guild" and c.flags.get("owes_the_guild") is True
    # the world rippled too: the Guild's regard rose, the follow-on favour is scheduled
    assert c.factions["fac-guild"].reputation > 0
    assert any(co.note == f"event:{ev.id}" for co in c.consequences)
    # after: the SAME agenda reads the flag active and the snap probability SPIKES
    assert companion_arc._decision_flag_active(agenda, c) is True
    p_on = companion_arc._attitude_below_snap_p(
        astarion.attitude_value, agenda.value, vulnerable=False, decision_flag_active=True
    )
    assert p_on == pytest.approx(p_off + companion_arc.ATTITUDE_SNAP_DECISION_BONUS)
    assert p_on > p_off
    # and the advisory telegraph fires so the DM can foreshadow the fracture
    warn = companion_arc._betrayal_warning(astarion, c)
    assert warn is not None and warn["decision_flag_active"] is True


# =========================================================================
# SYN-04 — scene_context throttles manual events to <=1 + stamps
# first_presented_day so an event presents ONCE then stops re-riding every beat.
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-04 / F05-3 + F07-3).
# present_events (standalone) stays the FULL, read-only payload; the throttle +
# write live in scene_context's events block.
# =========================================================================


def test_present_events_standalone_unchanged_and_full(l3_campaign):
    """The standalone present_events tool keeps the FULL payload AND stays read-only
    (the audit's invariant: only scene_context's events block throttles/stamps).
    Source: SYN-04 leg (b)/(c)."""
    cid, _pc, _comp = l3_campaign
    for i in range(3):
        _inject_event(cid, _event(eid=f"event_full_{i}", trigger="manual",
                                   prompt=f"Manual event {i} with full prose here."))
    before = store.load_campaign(cid).model_dump(mode="json")
    out = server.present_events(cid)
    after = store.load_campaign(cid).model_dump(mode="json")
    # all 3 surface, with prompt prose, and NOTHING was written (still read-only)
    assert len(out["events"]) == 3
    assert all(e["prompt"] for e in out["events"])
    assert before == after


def test_scene_context_throttles_to_one_manual_event_with_queue(l3_campaign):
    """scene_context surfaces at most ONE manual event in full + reports the rest as a
    manual_queued count (the audit's ~6.5KB-every-beat fix). Source: SYN-04 leg (b)."""
    cid, _pc, _comp = l3_campaign
    for i in range(3):
        _inject_event(cid, _event(eid=f"event_q_{i}", trigger="manual",
                                   prompt=f"Manual decisional {i}."))
    ev_block = server.scene_context(cid)["events"]
    # exactly one full event surfaced, the other two are queued (not full prose)
    assert len(ev_block["events"]) == 1
    assert ev_block["manual_queued"] == 2
    assert ev_block["events"][0]["prompt"]  # the surfaced one carries full prose


def test_scene_context_stamps_first_presented_day_and_stub_on_repeat(l3_campaign):
    """An event presented via scene_context is stamped first_presented_day; a LATER
    scene_context returns it as a compact stub (not full prose), so it stops re-riding
    every beat at ~1.6K tok. Source: SYN-04 leg (c)."""
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_once", trigger="manual",
                              prompt="A long stumble-into prompt that costs tokens every beat."))
    c = store.load_campaign(cid)
    day = c.day

    first = server.scene_context(cid)["events"]
    assert len(first["events"]) == 1
    # stamped under lock (engine = sole writer; scene_context now writes here)
    assert store.load_campaign(cid).events["event_once"].first_presented_day == day

    # next beat: already presented -> compact stub, NOT the full prompt prose again
    second = server.scene_context(cid)["events"]
    assert second["events"] == []  # not re-surfaced as a fresh full event
    stubs = second.get("presented", [])
    assert any(s["id"] == "event_once" for s in stubs)
    stub = next(s for s in stubs if s["id"] == "event_once")
    assert "prompt" not in stub or len(stub.get("prompt", "")) < 80  # head only, not full prose


def test_scene_context_queued_manual_event_stays_resolvable(l3_campaign):
    """A manual event throttled OUT of the surfaced slot is still resolvable by id
    (resolve_event looks it up directly in c.events). Source: SYN-04 leg (b)."""
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_aaa", trigger="manual", prompt="First."))
    _inject_event(cid, _event(eid="event_zzz", trigger="manual", options=[
        ParleyOption(label="Help", outcome=Outcome(faction_id="fac-fist", reputation_delta=5)),
    ], prompt="Second."))
    block = server.scene_context(cid)["events"]
    surfaced_ids = [e["id"] for e in block["events"]]
    assert "event_aaa" in surfaced_ids and block["manual_queued"] == 1
    # the QUEUED event (event_zzz) is still resolvable by id
    res = server.resolve_event(cid, "event_zzz", "Help")
    assert res["resolved"] is True
    assert store.load_campaign(cid).factions["fac-fist"].reputation == 5


def test_scene_context_triggered_event_surfaces_full_once(l3_campaign):
    """A non-manual (flag_set) event surfaces FULL exactly once the trigger arms, then
    becomes a stub. Source: SYN-04 (the content leg moves events off 'manual')."""
    cid, _pc, _comp = l3_campaign
    _inject_event(cid, _event(eid="event_flag", trigger="flag_set",
                              trigger_value="armed", prompt="The flag-gated moment."))
    # not armed: not surfaced
    assert server.scene_context(cid)["events"]["events"] == []
    # arm it
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        c.flags["armed"] = True
        store.save_campaign(c)
    first = server.scene_context(cid)["events"]
    assert [e["id"] for e in first["events"]] == ["event_flag"]
    # next beat: stub, not full again
    second = server.scene_context(cid)["events"]
    assert second["events"] == []
    assert any(s["id"] == "event_flag" for s in second.get("presented", []))
