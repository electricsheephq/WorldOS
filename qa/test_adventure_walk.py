#!/usr/bin/env python3
"""Offline proof for the A-series Lane G WALKED eval (qa/adventure_walk.py).

No live player: the PURE route-builder + VQA aggregation + tri-state helpers are exercised directly,
and the full stage drive runs against a MOCKED transport (monkeypatching walk_test._get/_post — the
qa/test_walk_test.py convention) so run_walk's arrival / stuck / dead-click accounting, per-stage
timing, and report shape are proven without booting the box.

Run: python3 -m pytest qa/test_adventure_walk.py -q -p no:xdist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adventure_walk as A  # noqa: E402
import walk_test as W  # noqa: E402


# ── PURE: the route + door graph ───────────────────────────────────────────────────────────────────
def test_build_route_is_the_section_4d_arc():
    route = A.build_route()
    assert [s.id for s in route] == [
        "camp_start", "to_tavern", "back_to_camp", "to_crypt", "to_throne", "return_to_camp",
        "return_to_giver"]
    assert [s.room for s in route] == [
        "camp_clearing", "tavern_snug", "camp_clearing", "crypt", "throne_hall", "camp_clearing",
        "tavern_snug"]
    assert [s.kind for s in route] == ["start", "approach", "return", "walk", "approach", "return",
                                       "return_to_giver"]
    # the two approach stages carry the quest-giver + the boss (proximity + actor-visible targets)
    assert route[1].actor == "Keeper Maera" and route[4].actor == "Goblin Boss"
    assert all(s.actor is None for s in (route[0], route[2], route[3], route[5]))


# ── the §9 RETURN-FOR-REWARD leg (the G3 route gap, #1709) ──────────────────────────────────────────
def test_route_data_covers_the_return_leg_to_the_giver():
    """The walked route must END at the giver: the final leg is throne_hall -> crypt -> camp ->
    tavern_snug, so the reward-return the §9 arc requires is actually driven."""
    route = A.build_route()
    last = route[-1]
    assert (last.id, last.room, last.kind, last.actor) == (
        "return_to_giver", "tavern_snug", "return_to_giver", "Keeper Maera")
    # the closing leg, stitched from the last two stages' door chains, is the full §9 return
    assert route[-2].hops == ["throne_hall", "crypt", "camp_clearing"]
    assert last.hops == ["camp_clearing", "tavern_snug"]


def test_route_is_data_not_code(tmp_path):
    """The route is DATA — the §9 default, inline JSON, or a @file — so a wider town graph extends
    it without touching the drive."""
    assert A.parse_route_spec(None) == A.DEFAULT_ROUTE
    assert A.parse_route_spec('[["a", "crypt", "walk"], ["b", "tavern_snug", "approach", "Keeper Maera"]]') == (
        ("a", "crypt", "walk", None), ("b", "tavern_snug", "approach", "Keeper Maera"))
    f = tmp_path / "route.json"
    f.write_text('[{"id": "only", "room": "crypt", "kind": "walk"}]')
    assert A.parse_route_spec(f"@{f}") == (("only", "crypt", "walk", None),)
    custom = A.build_route("camp_clearing", A.parse_route_spec('[["only", "crypt", "walk"]]'))
    assert [s.id for s in custom] == ["only"] and custom[0].hops == ["camp_clearing", "crypt"]


def test_every_stage_hop_chain_is_valid_adjacent_door_crosses():
    """A stage's hop chain must be a real door path — every consecutive pair adjacent in the seed graph
    — so the drive never tries to cross a door that does not exist."""
    for stage in A.build_route():
        for a, b in zip(stage.hops, stage.hops[1:]):
            assert b in A.ADJACENCY[a], f"{stage.id}: {a}->{b} is not an adjacent door"


def test_room_path_direct_multi_hop_and_edges():
    assert A.room_path("camp_clearing", "tavern_snug") == ["camp_clearing", "tavern_snug"]
    # the return-to-camp leg from the throne hall is a two-door path back through the crypt
    assert A.room_path("throne_hall", "camp_clearing") == ["throne_hall", "crypt", "camp_clearing"]
    assert A.room_path("crypt", "crypt") == ["crypt"]           # already there
    assert A.room_path("camp_clearing", "nowhere") == []        # unreachable → empty


# ── PURE: the per-stage VQA question set (journey_eval pattern, YES=defect) ──────────────────────────
def test_stage_questions_room_class_and_walkthrough_always_actor_conditionally():
    walk_stage = A.build_route()[3]      # to_crypt (no actor)
    approach = A.build_route()[1]        # to_tavern (Keeper Maera)
    walk_flags = {q["flag"] for q in A.stage_questions(walk_stage)}
    appr_flags = {q["flag"] for q in A.stage_questions(approach)}
    assert walk_flags == {"wrong_room_class", "walk_through_anomaly"}
    assert appr_flags == {"wrong_room_class", "walk_through_anomaly", "actor_missing"}
    # the actor question names the actual actor so the scorer knows who to look for
    assert any("Keeper Maera" in q["text"] for q in A.stage_questions(approach))


# ── VQA aggregation with a stub scorer (no LLM) ─────────────────────────────────────────────────────
def _clean(path, questions):
    return {q["flag"]: False for q in questions}


def test_score_stage_frame_clean_passes():
    stage = A.build_route()[1]
    res = A.score_stage_frame("/tmp/f.png", stage, _clean)
    assert res["passed"] and res["defects"] == [] and res["frames_checked"] == 1


def test_score_stage_frame_content_defect_fails():
    stage = A.build_route()[1]
    scorer = lambda p, qs: {**_clean(p, qs), "actor_missing": True}
    res = A.score_stage_frame("/tmp/f.png", stage, scorer)
    assert not res["passed"] and res["defects"] == ["actor_missing"]


def test_score_stage_frame_missing_frame_is_not_a_silent_pass():
    stage = A.build_route()[3]
    res = A.score_stage_frame(None, stage, _clean)
    assert not res["passed"] and res["defects"] == ["vqa_no_frame"] and res["frames_checked"] == 0


def test_score_stage_frame_incomplete_scorer_is_flagged():
    """A scorer that skips a requested flag must never read as clean."""
    stage = A.build_route()[1]
    partial = lambda p, qs: {"wrong_room_class": False}   # omits walk_through_anomaly + actor_missing
    res = A.score_stage_frame("/tmp/f.png", stage, partial)
    assert not res["passed"] and "vqa_incomplete" in res["defects"]


# ── PURE: tri-state stage + overall verdict (walk_test discipline) ──────────────────────────────────
def _stage(**over):
    rec = {"arrived": True, "stuck": False, "vqa": {"defects": []}, "harness_errors": []}
    rec.update(over)
    return rec


def test_classify_stage_clean_is_green():
    assert A.classify_stage_verdict(_stage()) == "GREEN"


def test_classify_stage_content_defect_is_red():
    assert A.classify_stage_verdict(_stage(vqa={"defects": ["wrong_room_class"]})) == "RED"


def test_classify_stage_stuck_is_red():
    # `stuck` is a CLEAN arc failure (a door that won't cross) → RED, even when the party never arrived.
    assert A.classify_stage_verdict(_stage(stuck=True, arrived=False)) == "RED"


def test_classify_stage_harness_only_is_error():
    assert A.classify_stage_verdict(_stage(harness_errors=["cross->crypt: click:timeout"])) == "ERROR"


def test_classify_stage_capture_failure_is_error_not_green():
    """A VQA capture/scorer-infra defect on an otherwise-clean stage is HARNESS → ERROR, never a silent
    GREEN on missing evidence."""
    assert A.classify_stage_verdict(_stage(vqa={"defects": ["vqa_no_frame"]})) == "ERROR"


def test_classify_stage_real_fail_wins_over_harness():
    # a CLEAN arc failure (stuck) alongside harness noise still classifies RED — the real fail wins.
    assert A.classify_stage_verdict(_stage(stuck=True, arrived=False, harness_errors=["boom"])) == "RED"


def test_classify_stage_unreachable_engine_is_error_not_red():
    # not arrived, but NOT a clean stuck (the engine was unreachable) → ERROR, never a false walk RED.
    assert A.classify_stage_verdict(
        _stage(arrived=False, stuck=False, harness_errors=["cross->crypt: surface:down"])) == "ERROR"


def test_classify_walk_overall_tristate():
    green = {"stages": [{"verdict": "GREEN"}, {"verdict": "GREEN"}], "harness_errors": []}
    red = {"stages": [{"verdict": "GREEN"}, {"verdict": "RED"}, {"verdict": "ERROR"}], "harness_errors": []}
    err = {"stages": [{"verdict": "GREEN"}, {"verdict": "ERROR"}], "harness_errors": []}
    top = {"stages": [{"verdict": "GREEN"}], "harness_errors": ["engine unreachable"]}
    assert A.classify_walk_verdict(green) == ("GREEN", 0)
    assert A.classify_walk_verdict(red) == ("RED", 1)       # a RED stage wins over an ERROR one
    assert A.classify_walk_verdict(err) == ("ERROR", 2)
    assert A.classify_walk_verdict(top) == ("ERROR", 2)     # top-level harness with clean stages


# ── the full drive over a MOCKED transport (monkeypatch walk_test._get/_post) ───────────────────────
# Deterministic door cells (mirror seed_adventure_demo's wiring) for the fake world's surfaces.
_DOORS = {
    ("camp_clearing", "tavern_snug"): [8, 0], ("tavern_snug", "camp_clearing"): [5, 0],
    ("camp_clearing", "crypt"): [0, 6], ("crypt", "camp_clearing"): [7, 0],
    ("crypt", "throne_hall"): [15, 5], ("throne_hall", "crypt"): [8, 11],
    ("tavern_snug", "shop"): [11, 4], ("shop", "tavern_snug"): [6, 0],
}
_NPCS = {"tavern_snug": [("Keeper Maera", (5, 5))],
         "throne_hall": [("Goblin Boss", (8, 8))],
         "crypt": [("Goblin", (6, 6))]}


class _FakeWorld:
    """A minimal player+engine fake: /combat-surface reports the current room + its doors + a full
    walkable grid + the player token (tokens[0]) and any NPCs; /click either crosses a door (its cell)
    or walks the token to a floor cell. `drop` removes a door to simulate a broken/unwired arc leg."""
    def __init__(self, drop=()):
        self.loc = "camp_clearing"
        self.player = (3, 3)
        self.drop = set(drop)
        self.talk_ok = True

    def _doors(self):
        return [{"cell": _DOORS[(self.loc, to)], "to": to}
                for to in A.ADJACENCY[self.loc] if (self.loc, to) not in self.drop]

    def get(self, url, timeout=5.0):
        toks = [{"x": self.player[0], "y": self.player[1], "name": "Aidan"}]
        toks += [{"x": x, "y": y, "name": n} for n, (x, y) in _NPCS.get(self.loc, [])]
        return {"location": self.loc, "doors": self._doors(), "tokens": toks,
                "grid": {"cols": 16, "rows": 16, "cellDefault": {"walkable": True}, "cells": [],
                         "sceneId": f"scene_{self.loc}"}}

    def post(self, url, body=None, timeout=5.0):
        if url.endswith("/click"):
            c, r = body["c"], body["r"]
            for d in self._doors():
                if list(d["cell"]) == [c, r]:
                    self.loc, self.player = d["to"], (3, 3)
                    return {"ok": True}
            self.player = (c, r)
            return {"ok": True}
        if url.endswith("/talk"):
            # mirrors CombatSurfaceClient's QA listener: an ACCEPTED verb answers ok:true (the fake
            # world grants the talk); a path it does not serve answers 200 + {"ok": false}.
            return {"ok": self.talk_ok}
        return {"ok": True}


def _wire(monkeypatch, fw):
    monkeypatch.setattr(W, "_get", fw.get)
    monkeypatch.setattr(W, "_post", fw.post)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    # never touch the filesystem for a shot in a unit test — return a stub frame path
    monkeypatch.setattr(W, "_capture_shot", lambda qa, out, label, timeout=6.0: f"/tmp/shot_{label}.png")


def test_full_walk_happy_path_is_green_and_well_shaped(monkeypatch, tmp_path):
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.5,
                        quest_reader=_paid_reader)

    verdict, code = A.classify_walk_verdict(report)
    assert (verdict, code) == ("GREEN", 0)
    # provenance + shape
    assert report["schema_version"] == 1 and "T" in report["ts"]
    assert report["campaign"] == A.CAMPAIGN
    assert report["route"] == [s.id for s in A.build_route()]
    assert len(report["stages"]) == 7
    tot = report["totals"]
    assert tot["stages"] == 7 and tot["arrived"] == 7 and tot["stuck_stages"] == 0
    assert tot["dead_clicks"] == 0
    for s in report["stages"]:
        assert s["arrived"] and s["verdict"] == "GREEN"
        assert s["arrival_room"] == s["room"]
        assert isinstance(s["duration_s"], float)          # per-stage timing recorded
        assert s["vqa"]["frames_checked"] == 1 and s["vqa"]["passed"]
    # every actor stage (both approaches + the giver return) reached its actor + talked
    approach = [s for s in report["stages"] if s["kind"] in ("approach", "return_to_giver")]
    assert len(approach) == 3
    assert all(s["adjacent"] and s["talked"] is True for s in approach)
    # the walk FINISHED the §9 arc: back at the giver, reward read, route_complete
    giver = report["stages"][-1]
    assert giver["room"] == "tavern_snug" and giver["reward_leg"]["verdict"] == "GREEN"
    assert report["route_complete"] is True
    # the report was persisted-shaped correctly (round-trips through JSON without loss)
    import json
    assert json.loads(json.dumps(report))["verdict"] == "GREEN"


def test_broken_arc_leg_makes_the_stage_stuck_and_red(monkeypatch, tmp_path):
    """Drop the camp->crypt door: the to_crypt stage can't cross, records `stuck`, and classifies RED —
    a real arc failure, not a harness ERROR."""
    fw = _FakeWorld(drop={("camp_clearing", "crypt")})
    _wire(monkeypatch, fw)
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.2)

    crypt = next(s for s in report["stages"] if s["id"] == "to_crypt")
    assert crypt["stuck"] and not crypt["arrived"] and crypt["verdict"] == "RED"
    assert A.classify_walk_verdict(report) == ("RED", 1)


def test_drive_error_is_harness_not_a_walk_red(monkeypatch, tmp_path):
    """An engine that is unreachable for the whole run yields HARNESS errors (ERROR), never a false
    walk RED — the tri-state discipline: infra defects are not room verdicts."""
    def _boom(url, timeout=5.0):
        raise ConnectionError("engine down")
    monkeypatch.setattr(W, "_get", _boom)
    monkeypatch.setattr(W, "_post", lambda url, body=None, timeout=5.0: {"ok": True})
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    monkeypatch.setattr(W, "_capture_shot", lambda qa, out, label, timeout=6.0: None)
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.2)
    # the start stage never crosses a door (it begins in camp) and captures no frame → ERROR, not RED;
    # later stages can't cross (surface unreachable) → harness. No stage should be a false RED here.
    assert report["harness_errors"]
    assert A.classify_walk_verdict(report)[0] == "ERROR"


def test_scorer_crash_is_stage_harness_error_never_a_run_killer():
    """A VQA scorer that RAISES (subprocess dead, non-JSON output) must degrade to a per-stage
    vqa_scorer_error harness defect — stage verdict ERROR, never RED/GREEN, never an exception."""
    def _boom(_frame, _questions):
        raise RuntimeError("scorer subprocess exploded")

    stage = A.build_route("camp_clearing")[0]
    vqa = A.score_stage_frame("frame.png", stage, _boom)
    assert vqa["defects"] == ["vqa_scorer_error"] and vqa["passed"] is False
    assert "vqa_scorer_error" in A.VQA_HARNESS_FLAGS
    rec = {"arrived": True, "stuck": False, "harness_errors": [], "vqa": vqa}
    assert A.classify_stage_verdict(rec) == "ERROR"


def test_incomplete_vqa_missing_names_never_become_content_defects():
    """A scorer that SKIPS a question is infra: the missing flag names must not appear in `defects`
    (they would classify as content → false RED); the stage reads ERROR via vqa_incomplete."""
    stage = A.build_route("camp_clearing")[0]
    vqa = A.score_stage_frame("f.png", stage, lambda _f, _q: {})  # answers nothing
    assert vqa["defects"] == ["vqa_incomplete"] and vqa.get("missing")
    rec = {"arrived": True, "stuck": False, "harness_errors": [], "vqa": vqa}
    assert A.classify_stage_verdict(rec) == "ERROR"


def test_clean_action_failure_is_red():
    """A stage that ARRIVED but cleanly failed its per-kind action (majority-dead floor / unreached
    known actor cell) must be RED — never a silent GREEN on clean VQA."""
    rec = {"arrived": True, "stuck": False, "harness_errors": [],
           "action_failed": "walk_floor: 2/3 sampled cells dead",
           "vqa": {"frames_checked": 1, "flags": {}, "defects": [], "passed": True}}
    assert A.classify_stage_verdict(rec) == "RED"


# ── the §9 REWARD leg verdict (read from get_quests / quest_trace; PURE) ─────────────────────────────
_ACTIVE_QUEST = {"quests": [{"id": "q1", "title": "Clear the crypt", "status": "active",
                             "objectives": ["Speak with Keeper Maera", "Clear the crypt",
                                            "Return to Keeper Maera for the reward"],
                             "completed_objectives": ["Speak with Keeper Maera", "Clear the crypt"]}]}


def _paid_reader():
    """A quest source where the reward LANDED (the return objective completed)."""
    q = dict(_ACTIVE_QUEST["quests"][0])
    q["completed_objectives"] = list(q["objectives"])
    return {"quests": [q]}


def test_reward_leg_green_on_a_completed_return_objective():
    res = A.classify_reward_leg(_paid_reader())
    assert res["verdict"] == "GREEN" and "reward_received" in res["signals"]


def test_reward_leg_green_on_a_quest_trace_stamp():
    """The A-T lane's quest_trace stamps are an equally valid source of the reward signal."""
    res = A.classify_reward_leg({"stamps": [{"stage": "reached_giver"}, {"stage": "quest_completed"}]})
    assert res["verdict"] == "GREEN" and res["signals"] == ["quest_completed"]


def test_reward_leg_red_when_the_quest_never_completes():
    """A fake engine that never marks the quest complete must yield RED — with the outstanding
    objective list — NEVER a silent GREEN just because the party stood next to the giver."""
    res = A.read_reward_leg(lambda: _ACTIVE_QUEST)
    assert res["verdict"] == "RED" and res["signals"] == []
    assert res["outstanding_objectives"] == ["Return to Keeper Maera for the reward"]
    assert res["quest_status"] == "active"


def test_reward_leg_rpc_unreachable_is_error_never_red():
    """HARNESS defects are never arc verdicts: an unreadable RPC (and a missing reader) is ERROR."""
    def _boom():
        raise ConnectionError("engine import failed")
    assert A.read_reward_leg(_boom)["verdict"] == "ERROR"
    assert A.read_reward_leg(None)["verdict"] == "ERROR"
    # an empty payload is missing evidence, not proof of a paid reward
    assert A.classify_reward_leg({})["verdict"] == "ERROR"
    assert A.classify_reward_leg({"quests": []})["verdict"] == "ERROR"


def test_reward_leg_error_classifies_the_stage_error_not_red(monkeypatch, tmp_path):
    """A giver stage that ARRIVED but could not read the quest is ERROR (harness), never a walk RED,
    and the route is NOT complete on unread evidence."""
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    def _boom():
        raise ConnectionError("engine down")
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.5,
                        quest_reader=_boom)
    giver = report["stages"][-1]
    assert giver["arrived"] and giver["reward_leg"]["verdict"] == "ERROR"
    assert giver["verdict"] == "ERROR"
    assert report["route_complete"] is False
    assert A.classify_walk_verdict(report) == ("ERROR", 2)


def test_unpaid_reward_makes_the_giver_stage_red_and_the_route_incomplete(monkeypatch, tmp_path):
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.5,
                        quest_reader=lambda: _ACTIVE_QUEST)
    giver = report["stages"][-1]
    assert giver["verdict"] == "RED" and "reward_leg" in giver["action_failed"]
    assert report["route_complete"] is False
    assert A.classify_walk_verdict(report) == ("RED", 1)


def test_route_complete_needs_an_arrived_giver_stage():
    """route_complete is not a route-shape claim: a walk that never reached the giver, or a route
    without the leg at all, must read False."""
    assert A.is_route_complete({"stages": [{"kind": "return", "arrived": True}]}) is False
    assert A.is_route_complete({"stages": [
        {"kind": "return_to_giver", "verdict": "GREEN", "arrived": False, "adjacent": True,
         "reward_leg": {"verdict": "GREEN"}}]}) is False
    assert A.is_route_complete({"stages": [
        {"kind": "return_to_giver", "verdict": "GREEN", "arrived": True, "adjacent": True,
         "reward_leg": {"verdict": "GREEN"}}]}) is True


# ── review round 1: the fail-closed / never-false-GREEN regressions ─────────────────────────────────
def test_route_override_naming_an_unlinked_room_fails_closed():
    """A --route override may only name rooms the seeded door graph links. An unreachable room used
    to yield hops=[], which walk_stage read as an arrival it never walked to (and could then set
    route_complete from the wrong room) — build_route must reject it instead."""
    import pytest
    with pytest.raises(ValueError, match="not reachable"):
        A.build_route("camp_clearing", A.parse_route_spec('[["ghost", "atlantis", "walk"]]'))
    # a room that EXISTS but is not linked from the previous stage's room is rejected too
    with pytest.raises(ValueError, match="not reachable"):
        A.build_route("nowhere", A.parse_route_spec('[["a", "crypt", "walk"]]'))


def test_a_hopless_stage_never_reads_as_arrived(monkeypatch, tmp_path):
    """Belt-and-braces for a hand-built Stage: no hop chain → HARNESS + not arrived (ERROR), never a
    silent arrival."""
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    stage = A.Stage(id="orphan", room="atlantis", kind="walk", expected_desc="nowhere", hops=[])
    rec = A.walk_stage("http://q", "http://e", stage, tmp_path, _clean, settle=0.0, timeout=0.2)
    assert rec["arrived"] is False and rec["arrival_room"] is None
    assert rec["harness_errors"] and rec["verdict"] == "ERROR"


def test_a_failed_quest_is_never_a_green_reward_leg():
    """A quest that ended FAILED (the party went down) did NOT pay out — it must not read GREEN just
    because its status left `active`, and it must not set route_complete."""
    failed = {"quests": [{**_ACTIVE_QUEST["quests"][0], "status": "failed"}]}
    res = A.classify_reward_leg(failed)
    assert res["verdict"] == "RED" and res["signals"] == [] and res["quest_status"] == "failed"
    # even an arc-end quest_completed STAMP cannot rescue a failed quest
    res2 = A.classify_reward_leg({**failed, "stamps": [{"stage": "quest_completed"}]})
    assert res2["verdict"] == "RED"
    assert A.is_route_complete({"stages": [
        {"kind": "return_to_giver", "arrived": True, "reward_leg": res}]}) is False
    # ...but an independently EARNED reward still reads GREEN even on a failed quest
    paid_but_failed = {"quests": [{**_ACTIVE_QUEST["quests"][0], "status": "failed",
                                   "completed_objectives": list(_ACTIVE_QUEST["quests"][0]["objectives"])}]}
    assert A.classify_reward_leg(paid_but_failed)["verdict"] == "GREEN"


def test_completed_status_is_green_once_nothing_is_outstanding():
    """A `completed` status certifies the leg when the quest carries no unmet return/reward objective
    (the stricter outstanding-objective rule lives in
    test_completed_status_with_an_outstanding_return_objective_is_not_green)."""
    q = {**_ACTIVE_QUEST["quests"][0], "status": "completed",
         "completed_objectives": list(_ACTIVE_QUEST["quests"][0]["objectives"])}
    res = A.classify_reward_leg({"quests": [q]})
    assert res["verdict"] == "GREEN" and res["signals"] == ["quest_completed", "reward_received"]


def test_reward_leg_records_whether_the_giver_talk_landed(monkeypatch, tmp_path):
    """The QA channel's /talk is best-effort (the seeded player serves only /click,/shot,/health,
    /debug — #1709). The leg must record whether the verb landed so a RED is not misread as 'we
    talked and the arc refused to pay'."""
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    fw.talk_ok = False    # the real listener: HTTP 200 + {"ok": false} for a path it does not serve
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.5,
                        quest_reader=lambda: _ACTIVE_QUEST)
    giver = report["stages"][-1]
    assert giver["talked"] is None and giver["reward_leg"]["talk_landed"] is None
    assert giver["reward_leg"]["verdict"] == "RED"     # the quest state is still the honest reading


# ── review round 2: no false GREEN through the trace, the talk echo, or an empty route ──────────────
def test_a_failed_quest_trace_stamp_is_not_a_paid_reward():
    """When the live get_quests read is down the stamps are the ONLY evidence — and quest_progress
    stamps `quest_completed` for ANY non-active status, recording which in `signal`. A failed arc
    must still read RED, never GREEN off a bare arc-end stamp."""
    failed_stamp = {"stamps": [{"stage": "quest_completed", "signal": "status:failed"}]}
    assert A.classify_reward_leg(failed_stamp)["verdict"] == "RED"
    # the trace's own quest_status is honoured too (a stamp with no signal recorded)
    assert A.classify_reward_leg(
        {"stamps": [{"stage": "quest_completed"}], "quest_status": "failed"})["verdict"] == "RED"
    # a genuinely completed arc still reads GREEN through the same trace-only path
    assert A.classify_reward_leg(
        {"stamps": [{"stage": "quest_completed", "signal": "status:completed"}]})["verdict"] == "GREEN"
    # ...as does an independently earned reward beside a failed status
    assert A.classify_reward_leg(
        {"stamps": [{"stage": "reward_received"}], "quest_status": "failed"})["verdict"] == "GREEN"


def test_talk_landed_needs_an_accepted_verb_not_just_a_200(monkeypatch, tmp_path):
    """The player's QA listener answers EVERY path with HTTP 200 and `{"ok": false}` for one it does
    not serve, so a 200 alone must not record a landed talk."""
    fw = _FakeWorld()
    fw.loc = "tavern_snug"        # stand in the giver's room so the approach has an actor to reach
    fw.talk_ok = False
    _wire(monkeypatch, fw)
    ap = A._approach_actor("http://q", "http://e", "Keeper Maera", 0.0, 0.2)
    assert ap["talked"] is None and "did not accept" in ap["talk_error"]
    fw.talk_ok = True
    assert A._approach_actor("http://q", "http://e", "Keeper Maera", 0.0, 0.2)["talked"] is True


def test_an_empty_route_is_never_a_vacuous_green(tmp_path):
    """`--route '[]'` used to run zero stages and print GREEN/exit 0 — automation could read that as
    a finished arc. An empty override is rejected, and a stage-less report is ERROR (no evidence)."""
    import pytest
    with pytest.raises(ValueError, match="empty"):
        A.parse_route_spec("[]")
    assert A.classify_walk_verdict({"stages": [], "harness_errors": []}) == ("ERROR", 2)


# ── review round 3: the trace is a FALLBACK, never a second opinion ─────────────────────────────────
def test_a_stale_completion_stamp_never_outvotes_a_live_active_quest():
    """A reused sandbox run keeps its state dir and the seeder rewrites the campaign without clearing
    quest_trace.json, so a STALE quest_completed stamp can sit beside a freshly ACTIVE quest. The
    live read must win — otherwise route_complete goes true on last run's reward."""
    stale = {**_ACTIVE_QUEST, "stamps": [{"stage": "quest_completed", "signal": "status:completed"},
                                         {"stage": "reward_received"}]}
    res = A.classify_reward_leg(stale)
    assert res["verdict"] == "RED" and res["signals"] == []
    assert res["outstanding_objectives"] == ["Return to Keeper Maera for the reward"]
    # with NO live quest the same stamps are the only evidence, and still count
    assert A.classify_reward_leg({"stamps": stale["stamps"]})["verdict"] == "GREEN"


def test_live_quest_reader_reads_a_trace_bound_to_the_current_state(tmp_path):
    """A state-local trace with the current campaign is valid fallback evidence."""
    state = tmp_path / "state"
    state.mkdir()
    trace = state / "quest_trace.json"
    trace.write_text('{"campaign_id": "adventure_demo_v1", "quest_status": "completed", '
                     '"stamps": [{"stage": "quest_completed", '
                     '"signal": "status:completed"}]}')
    # the engine import fails under a bogus state dir → the trace is the only source
    data = A._live_quest_reader(str(state), trace_path=str(trace))()
    assert data["stamps"] and data["quest_status"] == "completed"
    assert data["trace_provenance"]["campaign_id"] == A.CAMPAIGN
    assert A.classify_reward_leg(data)["verdict"] == "GREEN"
    # and with NEITHER source readable the leg is ERROR, never a silent GREEN
    reader = A._live_quest_reader(str(tmp_path / "no-such-state"))
    assert A.read_reward_leg(reader)["verdict"] == "ERROR"


# ── review round 4: no false certification of the reward leg ────────────────────────────────────────
def test_completed_status_with_an_outstanding_return_objective_is_not_green():
    """The DM can resolve a quest via complete_quest/set_quest_status while the return objective is
    still unmet (qa/test_quest_progress.py covers that state). A bare `completed` status must not
    certify the reward leg it is supposed to be checking."""
    unpaid = {"quests": [{**_ACTIVE_QUEST["quests"][0], "status": "completed"}]}
    res = A.classify_reward_leg(unpaid)
    assert res["verdict"] == "RED" and res["signals"] == []
    assert res["outstanding_objectives"] == ["Return to Keeper Maera for the reward"]
    # a completed quest with NOTHING outstanding still reads GREEN off the status alone
    clean = {"quests": [{**_ACTIVE_QUEST["quests"][0], "status": "completed",
                         "completed_objectives": list(_ACTIVE_QUEST["quests"][0]["objectives"])}]}
    assert A.classify_reward_leg(clean)["verdict"] == "GREEN"


def test_a_route_override_must_close_on_the_giver_unless_explicitly_partial():
    """An override of ordinary stages walks, scores GREEN/0, and only whispers its incompleteness
    through route_complete — automation can miss that. Fail closed instead, with an explicit
    opt-out for a deliberate partial walk."""
    import pytest
    partial = A.parse_route_spec('[["only", "crypt", "walk"]]')
    with pytest.raises(ValueError, match="return_to_giver"):
        A.assert_route_returns_to_giver(partial)
    assert A.assert_route_returns_to_giver(A.DEFAULT_ROUTE) == A.DEFAULT_ROUTE


# ── review round 5: the giver stage must CLOSE the route, and harness never becomes RED ─────────────
def test_route_must_end_on_a_valid_giver_stage_not_merely_contain_one():
    """`any()` would accept a giver stage followed by further stages — the party then ends the walk
    somewhere else (and those later stages may fail), yet an earlier paid leg would read complete.
    A giver stage with no actor reads the reward without ever approaching the giver."""
    import pytest
    trailing = A.parse_route_spec(
        '[["giver", "tavern_snug", "return_to_giver", "Keeper Maera"], ["after", "camp_clearing", "return"]]')
    with pytest.raises(ValueError, match="END on a"):
        A.assert_route_returns_to_giver(trailing)
    no_actor = A.parse_route_spec('[["giver", "tavern_snug", "return_to_giver"]]')
    with pytest.raises(ValueError, match="needs an `actor`"):
        A.assert_route_returns_to_giver(no_actor)
    assert A.assert_route_returns_to_giver(A.DEFAULT_ROUTE) == A.DEFAULT_ROUTE


def test_route_complete_reads_the_FINAL_stage_only():
    paid = {"kind": "return_to_giver", "verdict": "GREEN", "arrived": True, "adjacent": True,
            "reward_leg": {"verdict": "GREEN"}}
    assert A.is_route_complete({"stages": [paid]}) is True
    # a paid giver stage that is not last cannot certify a walk that ended elsewhere
    assert A.is_route_complete({"stages": [paid, {"kind": "return", "arrived": True}]}) is False
    assert A.is_route_complete({"stages": []}) is False


def test_a_successful_but_empty_live_read_never_falls_back_to_a_stale_trace():
    """`get_quests` succeeding with no quest (an unseeded / state-skewed campaign) is MISSING evidence
    for this campaign — a retained stamp from an earlier run must not answer for it."""
    stale = {"quests": [], "live_read_ok": True, "stamps": [{"stage": "reward_received"}]}
    assert A.classify_reward_leg(stale)["verdict"] == "ERROR"
    # without a successful live read the same stamps ARE the evidence (the documented fallback)
    assert A.classify_reward_leg({"stamps": [{"stage": "reward_received"}]})["verdict"] == "GREEN"


def test_a_harness_failure_in_the_giver_approach_stays_error_not_red(monkeypatch, tmp_path):
    """If the approach to Maera hits a drive-error the quest is unreadable-by-consequence: the unpaid
    reward is downstream of a HARNESS failure and must not be promoted to a clean arc RED."""
    stage = A.Stage(id="return_to_giver", room="tavern_snug", kind="return_to_giver",
                    expected_desc="tavern", actor="Keeper Maera", hops=["tavern_snug"])
    # drive the same branch walk_stage takes, via a run over the mocked world
    fw = _FakeWorld()
    fw.loc = "tavern_snug"
    _wire(monkeypatch, fw)
    def _boom_move(qa, engine, c, r, settle, timeout, expect_move=True):
        return False, "drive-error:ConnectionError('player down')", []   # walk_test's harness sentinel
    monkeypatch.setattr(W, "_drive_and_check", _boom_move)
    out = A.walk_stage("http://q", "http://e", stage, tmp_path, _clean, settle=0.0, timeout=0.2,
                       quest_reader=lambda: _ACTIVE_QUEST)
    assert out["harness_errors"] and "action_failed" not in out
    assert out["reward_leg"]["verdict"] == "RED" and out["reward_leg"]["blocked_by_harness"] is True
    assert out["verdict"] == "ERROR"          # harness, never a false product RED
    assert A.is_route_complete({"stages": [out]}) is False


# ── review round 6: the trace fallback and the override validator close the last GREEN holes ────────
def test_trace_fallback_applies_the_same_outstanding_reward_rule():
    """quest_progress stamps `quest_completed` for a quest the DM ended before the return objective
    (and deliberately does not stamp reward_received). It refreshes `objectives` on the trace, so the
    fallback applies the SAME rule as the live read instead of trusting the terminal stamp."""
    qd = _ACTIVE_QUEST["quests"][0]
    ended_early = {"stamps": [{"stage": "quest_completed", "signal": "status:completed"}],
                   "quest_status": "completed", "objectives": qd["objectives"],
                   "completed_objectives": qd["completed_objectives"]}
    res = A.classify_reward_leg(ended_early)
    assert res["verdict"] == "RED" and res["signals"] == []
    assert res["outstanding_objectives"] == ["Return to Keeper Maera for the reward"]
    # the genuinely paid trace still reads GREEN
    paid = {**ended_early, "completed_objectives": list(qd["objectives"])}
    assert A.classify_reward_leg(paid)["verdict"] == "GREEN"


def test_a_route_closing_on_the_wrong_actor_is_rejected():
    """Ending at SOME actor is not ending at the GIVER — a route closing on the Goblin Boss would
    approach him, pass VQA, read an already-paid quest and certify a return that never happened."""
    import pytest
    wrong = A.parse_route_spec(
        '[["to_throne", "throne_hall", "return_to_giver", "Goblin Boss"]]')
    with pytest.raises(ValueError, match="tracked"):
        A.assert_route_returns_to_giver(wrong)


def test_a_non_partial_override_may_insert_stages_but_never_drop_a_mandatory_leg():
    """`--route` exists for a wider town graph: inserting stages is fine, dropping the crypt / throne
    / boss is not — that would certify G3 off a camp-to-tavern stroll."""
    import pytest
    giver_only = A.parse_route_spec('[["only", "tavern_snug", "return_to_giver", "Keeper Maera"]]')
    with pytest.raises(ValueError, match="mandatory"):
        A.assert_route_returns_to_giver(giver_only)
    assert A.missing_mandatory_legs(A.DEFAULT_ROUTE) == []
    # a SUPERSET (the town-graph case) passes: the arc's own legs are all still walked, in order
    wider = list(A.DEFAULT_ROUTE)
    wider.insert(3, ("to_shop", "shop", "walk", None))
    assert A.assert_route_returns_to_giver(tuple(wider)) == tuple(wider)


# ── final custody delta: every current thread, one batch ───────────────────────────────────────────
def test_trace_fallback_rejects_an_unrelated_state_or_campaign(monkeypatch, tmp_path):
    """A completed trace from another run/campaign must never certify the current sandbox walk."""
    import json
    import pytest
    import quest_progress as Q

    monkeypatch.setattr(Q, "_import_server", lambda _state: (_ for _ in ()).throw(RuntimeError("down")))
    state = tmp_path / "sandbox" / "state"
    state.mkdir(parents=True)
    unrelated = tmp_path / "other.quest_trace.json"
    unrelated.write_text(json.dumps({"campaign_id": A.CAMPAIGN, "quest_status": "completed",
                                     "stamps": [{"stage": "quest_completed"}]}))
    with pytest.raises(RuntimeError, match="not bound to current state"):
        A._live_quest_reader(str(state), trace_path=str(unrelated))()

    local = state / "quest_trace.json"
    local.write_text(json.dumps({"campaign_id": "another_campaign", "quest_status": "completed",
                                 "stamps": [{"stage": "quest_completed"}]}))
    with pytest.raises(RuntimeError, match="does not match current campaign"):
        A._live_quest_reader(str(state), trace_path=str(local))()


def test_mandatory_route_signature_retains_the_approach_actors():
    """Keeping the room/kind while dropping Maera or the boss must not satisfy the §9 route."""
    import pytest

    no_boss = list(A.DEFAULT_ROUTE)
    no_boss[4] = (*no_boss[4][:3], None)
    assert any("Goblin Boss" in leg for leg in A.missing_mandatory_legs(tuple(no_boss)))
    with pytest.raises(ValueError, match="mandatory"):
        A.assert_route_returns_to_giver(tuple(no_boss))


def test_route_complete_requires_a_clean_successful_giver_approach():
    paid = {"kind": "return_to_giver", "arrived": True, "adjacent": True, "verdict": "GREEN",
            "reward_leg": {"verdict": "GREEN"}}
    assert A.is_route_complete({"stages": [paid]}) is True
    assert A.is_route_complete({"stages": [{**paid, "adjacent": False}]}) is False
    assert A.is_route_complete({"stages": [{**paid, "verdict": "RED",
                                              "action_failed": "approach failed"}]}) is False


def test_invalid_route_spec_is_a_cli_harness_error(capsys, tmp_path):
    assert A.main(["--route", "[]", "--out", str(tmp_path)]) == 2
    assert "ERROR: invalid route" in capsys.readouterr().err
    assert A.main(["--route", "not-json", "--out", str(tmp_path)]) == 2
    assert "ERROR: invalid route" in capsys.readouterr().err


def test_partial_route_never_reports_completion_and_run_walk_validates_by_default(monkeypatch, tmp_path):
    import pytest

    partial = '[["only", "camp_clearing", "start"]]'
    fw = _FakeWorld()
    _wire(monkeypatch, fw)
    with pytest.raises(ValueError, match="END on a"):
        A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.2,
                   route_spec=partial)
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.2,
                        route_spec=partial, allow_partial_route=True)
    assert report["partial_route"] is True
    assert report["verdict"] == "GREEN"
    assert report["route_complete"] is False


def test_route_override_rejects_duplicate_stage_ids():
    import pytest

    with pytest.raises(ValueError, match="duplicate stage ids"):
        A.parse_route_spec('[['
                           '"same", "camp_clearing", "start"], '
                           '["same", "tavern_snug", "approach", "Keeper Maera"]]')
