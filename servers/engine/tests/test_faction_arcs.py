"""Tests for the Quest & Arc engine, faction arcs (#127) — the Skyrim/Kingmaker join->grow->lead loop.

A FactionArc GENERALIZES the proven companion stage-machine (CompanionQuestArc) onto a FACTION-owned
reputation/standing gauge — NOT a parallel system. Coverage (per the verification plan):
  * a stage is LOCKED below its `unlock_at` and AVAILABLE at/above (a deterministic gauge gate).
  * the join->advance flow: an arc only arms after join_faction; advance_faction_arc climbs it.
  * the finale ripples via worldsim._apply_structured_effect EXACTLY ONCE (idempotent re-advance).
  * the standing-vs-reputation distinction: a stage may gate on either engine-mutated gauge.
  * the advisory surface (check_faction_arcs nudges + the scene_debt detector) detects-not-acts.
  * the exemplar Flaming-Fist arc loads via seed_world (degrade-not-abort on malformed).
  * ADDITIVE: no faction arc == today's behavior byte-for-byte; old snapshots round-trip.

Pure-module functions (faction_arc.py) are exercised directly (like test_companion_arc.py); the MCP
tools (server.py) are exercised against a real persisted campaign (like test_event_parley_layer3.py).
Single-process only (the host OOMs on parallel pytest; never -n / xdist).
"""

import pytest

import content as content_mod
import faction_arc as fa
import scene_debt
import server
import store
from models import (
    Campaign,
    Faction,
    FactionArc,
    FactionArcStage,
    Outcome,
)


# --- helpers -----------------------------------------------------------------


def _stage(sid="s1", title="Stage", unlock_at=10, gauge="reputation", **kw) -> FactionArcStage:
    return FactionArcStage(id=sid, title=title, unlock_at=unlock_at, gauge=gauge, **kw)


def _arc(aid="arc-x", faction_id="fac-x", title="Arc", stages=None, **kw) -> FactionArc:
    return FactionArc(id=aid, faction_id=faction_id, title=title, stages=stages or [], **kw)


def _campaign_with_arc(arc: FactionArc, *, reputation=0, standing=0, joined=False) -> Campaign:
    c = Campaign(title="FA")
    c.factions[arc.faction_id] = Faction(
        id=arc.faction_id, name="X", reputation=reputation, standing=standing, joined=joined
    )
    c.faction_arcs[arc.id] = arc
    return c


# =========================================================================
# ADDITIVE DEFAULT + round-trip (empty == today, old snapshots load)
# =========================================================================


def test_campaign_faction_arcs_defaults_empty():
    assert Campaign(title="T").faction_arcs == {}


def test_faction_membership_fields_default_to_today():
    """Faction's new fields default to "behaves like today when unset" — the additive contract."""
    f = Faction(id="fac-a", name="A")
    assert f.rank == 0 and f.standing == 0 and f.joined is False and f.questline_arc_id == ""


def test_old_faction_snapshot_without_membership_round_trips():
    """A faction authored before #127 has only id/name/description/reputation — it must load with
    the membership fields at their defaults and round-trip identically."""
    old = {"id": "fac-a", "name": "A", "description": "d", "reputation": 5}
    f = Faction.model_validate(old)
    assert f.standing == 0 and f.joined is False and f.rank == 0 and f.reputation == 5
    assert Faction.model_validate(f.model_dump(mode="json")).reputation == 5


def test_old_campaign_snapshot_without_faction_arcs_deserializes_unchanged():
    c = Campaign(title="Pre-127")
    data = c.model_dump(mode="json")
    old = {k: v for k, v in data.items() if k != "faction_arcs"}
    assert "faction_arcs" not in old
    reloaded = Campaign.model_validate(old)
    assert reloaded.faction_arcs == {}
    assert Campaign.model_validate(reloaded.model_dump(mode="json")).faction_arcs == {}


def test_standing_is_monotonic_typed():
    """`standing` is a monotonic gauge — the model rejects a negative value (the type guard)."""
    with pytest.raises(Exception):
        Faction(id="z", name="Z", standing=-1)


def test_faction_arc_models_default_shapes():
    st = FactionArcStage(title="Finale")
    assert st.status == "locked" and st.unlock_at == 0 and st.gauge == "reputation"
    assert st.finale_effect is None and st.effect_applied is False
    arc = FactionArc(faction_id="fac-x", title="Rise")
    assert arc.status == "locked" and arc.stages == [] and arc.requires_joined is True
    assert arc.id.startswith("farc")


def test_faction_arc_round_trips_with_full_finale():
    arc = _arc(stages=[_stage(
        sid="s2", title="Lead", unlock_at=50, gauge="standing",
        finale_effect=Outcome(flag="led", faction_id="fac-x", reputation_delta=30,
                              decision_flag="seized_power", schedule_in_days=10,
                              schedule_text="cost comes due", narrate="You command."),
    )])
    reloaded = FactionArc.model_validate(arc.model_dump(mode="json"))
    assert reloaded == arc


def test_duplicate_stage_id_rejected():
    with pytest.raises(ValueError, match="duplicate faction arc stage id"):
        FactionArc(faction_id="f", title="t", stages=[_stage(sid="dup"), _stage(sid="dup")])


# =========================================================================
# THE GAUGE GATE (LOCKED below unlock_at, AVAILABLE at/above) — pure module
# =========================================================================


def test_stage_gate_locked_below_available_at_and_above_reputation():
    """The core deterministic gate on the bidirectional reputation gauge."""
    fac = Faction(id="fac-x", name="X", reputation=9)
    st = _stage(unlock_at=10, gauge="reputation")
    assert fa.stage_gate_holds(st, fac) is False  # 9 < 10 -> locked
    fac.reputation = 10
    assert fa.stage_gate_holds(st, fac) is True  # at threshold -> available
    fac.reputation = 25
    assert fa.stage_gate_holds(st, fac) is True  # above -> available


def test_stage_gate_on_standing_gauge():
    """The standing-vs-reputation distinction: a stage may gate on the MONOTONIC standing gauge
    instead — reputation at 0 doesn't satisfy a standing gate, and vice-versa."""
    fac = Faction(id="fac-x", name="X", reputation=100, standing=5)
    st = _stage(unlock_at=10, gauge="standing")
    assert fa.stage_gate_holds(st, fac) is False  # standing 5 < 10 even though reputation is 100
    fac.standing = 10
    assert fa.stage_gate_holds(st, fac) is True
    # a reputation-gauge stage at the same threshold reads reputation, not standing
    rep_stage = _stage(unlock_at=10, gauge="reputation")
    fac.reputation = 0
    fac.standing = 99
    assert fa.stage_gate_holds(rep_stage, fac) is False  # standing high, reputation 0 -> locked


def test_stage_gate_negative_threshold_arms_on_fall():
    """A negative reputation threshold arms on a FALL to/below it (the sign picks direction) —
    a "they've come to despise you" branch unlock."""
    fac = Faction(id="fac-x", name="X", reputation=0)
    st = _stage(unlock_at=-10, gauge="reputation")
    assert fa.stage_gate_holds(st, fac) is False  # 0 not <= -10
    fac.reputation = -10
    assert fa.stage_gate_holds(st, fac) is True
    fac.reputation = -30
    assert fa.stage_gate_holds(st, fac) is True


# =========================================================================
# evaluate(): join is the precondition; gate flips locked->available; idempotent
# =========================================================================


def test_evaluate_inert_until_joined():
    """A requires_joined arc never advances until the faction is joined — the gate is necessary
    but not sufficient (membership is the precondition)."""
    arc = _arc(stages=[_stage(unlock_at=10)])
    c = _campaign_with_arc(arc, reputation=50, joined=False)  # gate holds, but not joined
    res = fa.evaluate(arc, c)
    assert res["newly_available"] == []
    assert arc.status == "locked" and arc.stages[0].status == "locked"


def test_evaluate_arms_on_join_and_unlocks_held_gates():
    arc = _arc(stages=[_stage(sid="s1", unlock_at=10), _stage(sid="s2", unlock_at=30)])
    c = _campaign_with_arc(arc, reputation=20, joined=True)
    res = fa.evaluate(arc, c)
    assert arc.status == "available"  # the arc itself opens once armed
    assert res["newly_available"] == ["s1"]  # only s1's gate (10) holds at rep 20; s2 (30) doesn't
    assert arc.stages[0].status == "available" and arc.stages[1].status == "locked"


def test_evaluate_is_idempotent():
    arc = _arc(stages=[_stage(unlock_at=10)])
    c = _campaign_with_arc(arc, reputation=20, joined=True)
    assert fa.evaluate(arc, c)["newly_available"] == ["s1"]
    assert fa.evaluate(arc, c)["newly_available"] == []  # already unlocked -> not re-reported


def test_evaluate_requires_joined_false_arms_without_join():
    """A world MAY author an arc that doesn't require joining (requires_joined=False) — it arms on
    the gauge gate alone."""
    arc = _arc(stages=[_stage(unlock_at=10)], requires_joined=False)
    c = _campaign_with_arc(arc, reputation=20, joined=False)
    res = fa.evaluate(arc, c)
    assert arc.status == "available" and res["newly_available"] == ["s1"]


def test_evaluate_degrades_on_dangling_faction():
    """An arc whose faction was removed degrades to inert (never raises)."""
    arc = _arc(stages=[_stage(unlock_at=10)])
    c = Campaign(title="T")
    c.faction_arcs[arc.id] = arc  # no faction seeded
    assert fa.evaluate(arc, c) == {"newly_available": []}


# =========================================================================
# apply_finale(): ripples via _apply_structured_effect EXACTLY ONCE
# =========================================================================


def test_finale_ripples_through_structured_effect():
    """The finale moves the world through the SAME path the backlog/Events use: flag + reputation
    + control marker + a scheduled echo."""
    c = Campaign(title="T")
    c.factions["fac-x"] = Faction(id="fac-x", name="X", reputation=10)
    c.current_location_id = None
    st = _stage(finale_effect=Outcome(
        flag="world_changed", faction_id="fac-x", reputation_delta=25,
        controller_id="fac-x", location_id="loc-1",
        schedule_in_days=5, schedule_text="the reckoning", narrate="The world tilts."))
    res = fa.apply_finale(c, st)
    assert res is not None
    assert c.flags.get("world_changed") is True
    assert c.flags.get("control:loc-1=fac-x") is True
    assert c.factions["fac-x"].reputation == 35  # 10 + 25
    assert res["rep_shift"]["reputation"] == 35
    assert any(co.note == f"faction_arc:{st.id}" for co in c.consequences)  # the rule-of-three echo
    assert st.effect_applied is True


def test_finale_is_idempotent():
    """A re-advance to resolved never double-ripples (the effect_applied latch)."""
    c = Campaign(title="T")
    c.factions["fac-x"] = Faction(id="fac-x", name="X", reputation=10)
    st = _stage(finale_effect=Outcome(flag="f", faction_id="fac-x", reputation_delta=20, narrate="n"))
    fa.apply_finale(c, st)
    assert c.factions["fac-x"].reputation == 30
    again = fa.apply_finale(c, st)  # second call
    assert again is None
    assert c.factions["fac-x"].reputation == 30  # unchanged — no double ripple
    assert len(c.consequences) == 0


def test_finale_arms_companion_flip_via_decision_flag():
    """A finale may set a decision_flag — the L2<->L3 seam (e.g. seizing leadership turns a rival
    companion). It lands in Campaign.flags exactly like record_decision(sets_flag=)."""
    c = Campaign(title="T")
    c.factions["fac-x"] = Faction(id="fac-x", name="X")
    st = _stage(finale_effect=Outcome(decision_flag="seized_the_fist", narrate="You take command."))
    res = fa.apply_finale(c, st)
    assert c.flags.get("seized_the_fist") is True
    assert "seized_the_fist" in res["flags_set"]


def test_finale_none_is_noop():
    c = Campaign(title="T")
    st = _stage()  # no finale_effect
    assert fa.apply_finale(c, st) is None


# =========================================================================
# THE ADVISORY SURFACE — detect_rank_available + the scene_debt detector
# =========================================================================


def test_detect_rank_available_reports_available_stages_read_only():
    arc = _arc(stages=[_stage(sid="s1", unlock_at=10), _stage(sid="s2", unlock_at=30)])
    c = _campaign_with_arc(arc, reputation=20, joined=True)
    fa.evaluate(arc, c)  # flips s1 -> available
    before = c.model_dump(mode="json")
    nudges = fa.detect_rank_available(c)
    assert len(nudges) == 1
    assert nudges[0]["available_stage_ids"] == ["s1"]
    assert "rank-up available" in nudges[0]["nudge"]
    # read-only — detect_rank_available never mutates
    assert c.model_dump(mode="json") == before


def test_detect_rank_available_skips_unjoined_faction():
    arc = _arc(stages=[_stage(unlock_at=10)])
    c = _campaign_with_arc(arc, reputation=50, joined=False)
    assert fa.detect_rank_available(c) == []  # not a member -> no rank-up to nudge


def test_scene_debt_detector_surfaces_earned_rank_up():
    """The Director surfaces a faction_rank_available debt for a joined faction's earned-but-
    untaken rank-up — the established advise-not-act contract."""
    arc = _arc(stages=[_stage(sid="s1", title="Take the oath", unlock_at=10)])
    c = _campaign_with_arc(arc, reputation=20, joined=True)
    fa.evaluate(arc, c)
    debts = scene_debt.detect(c)
    rank_debts = [d for d in debts if d.kind == "faction_rank_available"]
    assert len(rank_debts) == 1
    assert rank_debts[0].subject == arc.id
    assert rank_debts[0].severity == "low"
    assert "s1" in rank_debts[0].evidence["available_stage_ids"]


def test_scene_debt_detector_silent_when_no_arc():
    """No faction arc == today's behavior: the new detector adds nothing."""
    c = Campaign(title="T")
    assert [d for d in scene_debt.detect(c) if d.kind == "faction_rank_available"] == []


# =========================================================================
# THE TOOLS (server.py) against a real persisted campaign — the join->advance flow
# =========================================================================


@pytest.fixture
def fa_campaign(tmp_path, monkeypatch):
    """A persisted campaign with a PC and a seeded faction (un-joined, rep 0). Returns cid."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Faction Arc Test")["id"]
    server.create_character(cid, "Vanya", kind="player", class_name="fighter", level=3)
    c = store.load_campaign(cid)
    c.factions["fac-fist"] = Faction(id="fac-fist", name="Flaming Fist", reputation=0, standing=0)
    store.save_campaign(c)
    return cid


def _arm_arc(cid: str) -> str:
    """Seed a 3-stage Flaming-Fist arc via the tool and return its id."""
    out = server.set_faction_arc(cid, {
        "id": "arc-fist",
        "faction_id": "fac-fist",
        "title": "The Banner of the Fist",
        "stages": [
            {"id": "st-oath", "title": "Take the oath", "unlock_at": 15, "gauge": "reputation"},
            {"id": "st-captain", "title": "Earn the captaincy", "unlock_at": 25, "gauge": "standing"},
            {"id": "st-command", "title": "Raise the banner", "unlock_at": 50, "gauge": "standing",
             "finale_effect": {"flag": "fist_under_party_banner", "faction_id": "fac-fist",
                               "reputation_delta": 30, "controller_id": "fac-fist",
                               "location_id": "loc-1", "narrate": "The banner flies high."}},
        ],
    })
    return out["faction_arc"]["id"]


def test_set_faction_arc_links_questline_to_faction(fa_campaign):
    cid = fa_campaign
    arc_id = _arm_arc(cid)
    c = store.load_campaign(cid)
    assert arc_id in c.faction_arcs
    assert c.factions["fac-fist"].questline_arc_id == arc_id  # the faction links its questline


def test_set_faction_arc_rejects_unknown_faction(fa_campaign):
    cid = fa_campaign
    with pytest.raises(ValueError, match="unknown faction"):
        server.set_faction_arc(cid, {"faction_id": "fac-nope", "title": "X", "stages": []})


def test_join_faction_arms_arc_and_unlocks_held_gate(fa_campaign):
    """join_faction sets joined + rank and unlocks any stage whose gauge gate already holds."""
    cid = fa_campaign
    _arm_arc(cid)
    # raise reputation to 20 (>= the oath stage's 15) BEFORE joining
    server.adjust_reputation(cid, "fac-fist", 20)
    out = server.join_faction(cid, "fac-fist")
    assert out["joined"] is True and out["rank"] == 1
    assert out["newly_available_stage_ids"] == ["st-oath"]  # the reputation gate held
    c = store.load_campaign(cid)
    assert c.faction_arcs["arc-fist"].stages[0].status == "available"


def test_join_faction_requires_existing_faction(fa_campaign):
    cid = fa_campaign
    with pytest.raises(ValueError, match="no faction"):
        server.join_faction(cid, "fac-ghost")


def test_grant_standing_is_monotonic_and_floors_at_zero(fa_campaign):
    cid = fa_campaign
    assert server.grant_standing(cid, "fac-fist", 10)["standing"] == 10
    assert server.grant_standing(cid, "fac-fist", 5)["standing"] == 15
    assert server.grant_standing(cid, "fac-fist", -100)["standing"] == 0  # floors, never negative


def test_join_grow_advance_full_flow(fa_campaign):
    """The whole join->grow->advance loop through the tools: join, grow standing, the
    standing-gated stages unlock via check_faction_arcs, advance each stage."""
    cid = fa_campaign
    _arm_arc(cid)
    server.adjust_reputation(cid, "fac-fist", 20)
    server.join_faction(cid, "fac-fist")  # unlocks st-oath (reputation gate)
    # take the oath: available -> active -> resolved
    server.advance_faction_arc(cid, "arc-fist", stage_id="st-oath", stage_status="active")
    server.advance_faction_arc(cid, "arc-fist", stage_id="st-oath", stage_status="resolved")
    # grow standing through service to 50 (>= both standing gates)
    server.grant_standing(cid, "fac-fist", 50)
    out = server.check_faction_arcs(cid, "fac-fist")
    avail = out["results"][0]["newly_available_stage_ids"]
    assert set(avail) == {"st-captain", "st-command"}
    # promote to captain
    server.advance_faction_arc(cid, "arc-fist", stage_id="st-captain", stage_status="resolved", rank=3)
    c = store.load_campaign(cid)
    assert c.factions["fac-fist"].rank == 3


def test_advance_rejects_ungated_stage(fa_campaign):
    """The engine ENFORCES earned trust: a locked stage can't be advanced toward active while its
    gauge gate is unmet (invariant #3 — a pure gauge check)."""
    cid = fa_campaign
    _arm_arc(cid)
    server.join_faction(cid, "fac-fist")  # rep 0 < 15, so st-oath stays locked
    with pytest.raises(ValueError, match="gated"):
        server.advance_faction_arc(cid, "arc-fist", stage_id="st-oath", stage_status="active")


def test_advance_finale_ripples_once_idempotent(fa_campaign):
    """Resolving the finale stage ripples its world-changing effect ONCE; re-resolving applies
    nothing further (the idempotency the verification plan calls out)."""
    cid = fa_campaign
    _arm_arc(cid)
    server.join_faction(cid, "fac-fist")
    server.grant_standing(cid, "fac-fist", 50)
    server.check_faction_arcs(cid, "fac-fist")  # unlock the standing-gated stages
    server.advance_faction_arc(cid, "arc-fist", stage_id="st-command", stage_status="active")
    first = server.advance_faction_arc(cid, "arc-fist", stage_id="st-command", stage_status="resolved", rank=5)
    assert first["finale"]["flags_set"] == ["fist_under_party_banner"]
    c = store.load_campaign(cid)
    rep_after_finale = c.factions["fac-fist"].reputation
    assert c.flags.get("fist_under_party_banner") is True
    assert c.flags.get("control:loc-1=fac-fist") is True
    assert c.factions["fac-fist"].rank == 5
    # re-resolve -> finale applies NOTHING further (no double ripple)
    second = server.advance_faction_arc(cid, "arc-fist", stage_id="st-command", stage_status="resolved")
    assert second.get("finale") is None
    assert store.load_campaign(cid).factions["fac-fist"].reputation == rep_after_finale


def test_advance_requires_an_argument(fa_campaign):
    cid = fa_campaign
    _arm_arc(cid)
    with pytest.raises(ValueError, match="requires stage_status, status, or rank"):
        server.advance_faction_arc(cid, "arc-fist")


def test_get_faction_arcs_resolves_gauge_context(fa_campaign):
    cid = fa_campaign
    _arm_arc(cid)
    server.adjust_reputation(cid, "fac-fist", 12)
    server.grant_standing(cid, "fac-fist", 7)
    out = server.get_faction_arcs(cid, "fac-fist")
    assert out["count"] == 1
    view = out["faction_arcs"][0]
    assert view["faction_name"] == "Flaming Fist"
    assert view["reputation"] == 12 and view["standing"] == 7  # current gauge context surfaced


def test_check_faction_arcs_returns_advisory_nudges(fa_campaign):
    cid = fa_campaign
    _arm_arc(cid)
    server.adjust_reputation(cid, "fac-fist", 20)
    server.join_faction(cid, "fac-fist")
    out = server.check_faction_arcs(cid, "fac-fist")
    assert any(n["faction_id"] == "fac-fist" for n in out["nudges"])


# =========================================================================
# THE EXEMPLAR — the authored Flaming-Fist arc on the REAL shipped content
# =========================================================================

BG_WORLD = "baldurs-gate"
FIST_ARC = "arc-fist-rise"


def _seed_bg(ending: str = "") -> Campaign:
    """Seed the real baldurs-gate world. Skips cleanly if the world bible isn't reachable."""
    try:
        world = content_mod.load_world_data(BG_WORLD)
    except (ValueError, FileNotFoundError, OSError):  # pragma: no cover - content not present
        pytest.skip("baldurs-gate world content not reachable from test cwd")
    return content_mod.seed_world(world, ending=ending)


def test_bg_flaming_fist_arc_seeds_and_links():
    """The authored Flaming-Fist questline loads from world.json, links to its faction, and has
    the 3-stage join->prove->lead shape with a world-changing finale on the terminal stage."""
    c = _seed_bg()
    assert FIST_ARC in c.faction_arcs, "the authored Flaming-Fist arc must seed from world.json"
    arc = c.faction_arcs[FIST_ARC]
    assert arc.faction_id == "fac-flaming-fist"
    assert c.factions["fac-flaming-fist"].questline_arc_id == FIST_ARC  # linked back
    assert len(arc.stages) == 3, "join -> prove -> lead"
    # stage 1 gates on reputation (trust to be let in); the later stages on standing (earned rank)
    assert arc.stages[0].gauge == "reputation"
    assert arc.stages[1].gauge == "standing" and arc.stages[2].gauge == "standing"
    # the terminal stage carries a world-changing finale that ripples a real effect
    finale = arc.stages[-1].finale_effect
    assert finale is not None
    assert finale.flag and finale.narrate.strip()
    assert finale.faction_id == "fac-flaming-fist" and finale.reputation_delta > 0
    # canon-grounded: the finale puts the Fist in control of a real location in this world
    assert finale.location_id in c.locations


def test_bg_flaming_fist_arc_locked_at_seed():
    """At world start the Fist isn't joined and reputation is the seed value (1) — all stages
    locked. The additive guarantee: the exemplar changes nothing until the player engages it."""
    c = _seed_bg()
    arc = c.faction_arcs[FIST_ARC]
    assert c.factions["fac-flaming-fist"].joined is False
    fa.evaluate(arc, c)
    assert all(s.status == "locked" for s in arc.stages)


def test_bg_flaming_fist_arc_end_to_end_on_real_content():
    """The whole authored loop on shipped canon: join the Fist, prove yourself (reputation),
    rise through service (standing), then the finale ripples the Gate under the Fist's banner."""
    c = _seed_bg()
    arc = c.faction_arcs[FIST_ARC]
    fac = c.factions["fac-flaming-fist"]
    # prove yourself: reputation up to the oath threshold, then join
    fac.reputation = arc.stages[0].unlock_at
    fac.joined = True
    fa.evaluate(arc, c)
    assert arc.stages[0].status == "available"  # the oath opens
    # rise through service: standing up to the command threshold
    fac.standing = arc.stages[2].unlock_at
    fa.evaluate(arc, c)
    assert arc.stages[2].status == "available"  # the command stage opens
    # the finale ripples the Gate under the Fist's banner, exactly once
    rep_before = fac.reputation
    res = fa.apply_finale(c, arc.stages[2])
    assert res is not None
    assert c.flags.get(arc.stages[2].finale_effect.flag) is True
    assert fac.reputation == min(100, rep_before + arc.stages[2].finale_effect.reputation_delta)
    assert fa.apply_finale(c, arc.stages[2]) is None  # idempotent


def test_bg_malformed_faction_arc_degrades_not_aborts():
    """A malformed faction arc in a world block is SKIPPED (degrade-not-abort), exactly the
    companion_seeds / events contract — a sibling valid arc still seeds, start_world never aborts."""
    c = Campaign(title="probe")
    c.factions["fac-real"] = Faction(id="fac-real", name="Real")
    seeded = content_mod._seed_faction_arcs_block(
        c,
        [
            {"id": "bad", "faction_id": "fac-missing", "title": "X", "stages": []},  # unknown faction
            {"id": "alsobad", "title": "no faction id", "stages": []},  # missing faction_id
            {"id": "good", "faction_id": "fac-real", "title": "OK", "stages": []},  # valid
        ],
        where="probe block",
    )
    assert seeded == 1
    assert "good" in c.faction_arcs and "bad" not in c.faction_arcs and "alsobad" not in c.faction_arcs
