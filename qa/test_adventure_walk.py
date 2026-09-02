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
        "camp_start", "to_tavern", "back_to_camp", "to_crypt", "to_throne", "return_to_camp"]
    assert [s.room for s in route] == [
        "camp_clearing", "tavern_snug", "camp_clearing", "crypt", "throne_hall", "camp_clearing"]
    assert [s.kind for s in route] == ["start", "approach", "return", "walk", "approach", "return"]
    # the two approach stages carry the quest-giver + the boss (proximity + actor-visible targets)
    assert route[1].actor == "Keeper Maera" and route[4].actor == "Goblin Boss"
    assert all(s.actor is None for s in (route[0], route[2], route[3], route[5]))


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
        return {"ok": True}   # /talk etc.


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
    report = A.run_walk("http://e", "http://q", tmp_path, _clean, settle=0.0, timeout=0.5)

    verdict, code = A.classify_walk_verdict(report)
    assert (verdict, code) == ("GREEN", 0)
    # provenance + shape
    assert report["schema_version"] == 1 and "T" in report["ts"]
    assert report["campaign"] == A.CAMPAIGN
    assert report["route"] == [s.id for s in A.build_route()]
    assert len(report["stages"]) == 6
    tot = report["totals"]
    assert tot["stages"] == 6 and tot["arrived"] == 6 and tot["stuck_stages"] == 0
    assert tot["dead_clicks"] == 0
    for s in report["stages"]:
        assert s["arrived"] and s["verdict"] == "GREEN"
        assert s["arrival_room"] == s["room"]
        assert isinstance(s["duration_s"], float)          # per-stage timing recorded
        assert s["vqa"]["frames_checked"] == 1 and s["vqa"]["passed"]
    # the two approach stages actually reached their actor + attempted the talk-equivalent
    approach = [s for s in report["stages"] if s["kind"] == "approach"]
    assert all(s["adjacent"] and s["talked"] is True for s in approach)
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
