"""Unit tests for the journey-eval FACTUAL-VQA harness (qa/journey_eval.py).

Covers the PURE, box-free, LLM-free cores: the versioned question set parses with the YES=defect
contract, the scripted path is derived correctly from a room manifest (a step adjacent to every
impassable prop, transitions flagged to capture both sides), VQA aggregation runs against an injected
STUB scorer, and the verdict fails on ANY yes while naming the offending frame. The box capture +
live claude -p VQA are exercised on the box (the #1386 claim) — deliberately not unit-tested here.
"""
from __future__ import annotations

import sys
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import check_plate_drift as drift  # noqa: E402
import journey_eval as je  # noqa: E402

_CRYPT_MANIFEST = _QA_DIR / "room_manifests" / "crypt_dense_v1.cells.json"


def _crypt() -> dict:
    return drift.load_manifest(_CRYPT_MANIFEST)


# ── question set (versioned, YES=defect) ────────────────────────────────────────────────────────────
def test_questions_parse_and_are_defect_polarity():
    qs = je.load_questions()
    flags = {q["flag"] for q in qs}
    # the packet's factual set, phrased YES=defect
    assert {"on_prop", "t_pose", "floating", "missing_or_cloned"} <= flags
    assert all(q["applies_to"] in ("all", "transition", "transition_pair") for q in qs)
    # the swap check is a transition_pair question (harness-computed from both sides, not single-frame)
    assert any(q["applies_to"] == "transition_pair" for q in qs)


def test_transition_pair_questions_are_excluded_from_the_single_frame_scorer():
    """A `transition_pair` question needs both sides — it must NEVER be handed to the single-frame LLM
    scorer (which can only see one image and would guess)."""
    qs = je.load_questions()
    pair = {q["flag"] for q in qs if q["applies_to"] == "transition_pair"}
    for is_trans in (False, True):
        asked = {q["flag"] for q in je.questions_for_frame(qs, is_transition=is_trans)}
        assert not (pair & asked), f"transition_pair flags leaked to the LLM set: {pair & asked}"


# ── scripted path derivation ────────────────────────────────────────────────────────────────────────
def test_build_script_visits_every_prop_on_a_walkable_adjacent_cell():
    m = _crypt()
    script = je.build_script(m)
    approaches = [s for s in script.steps if s.kind == "prop_approach"]
    # every prop is EITHER approached or explicitly recorded unreachable — never silently dropped
    assert len(approaches) + len(script.unreachable) == len(m["props"]) == 3
    prop_cells = {(int(c), int(r)) for p in m["props"] for (c, r) in (p.get("footprint") or p["cells"])}
    cols, rows = m["grid"]["cols"], m["grid"]["rows"]
    for s in approaches:
        c, r = s.cell
        assert 0 <= c < cols and 0 <= r < rows, f"{s.id} target off-grid: {s.cell}"
        assert (c, r) not in prop_cells, f"{s.id} target sits ON a prop footprint cell {s.cell}"


def test_build_script_surfaces_unreachable_props():
    """A prop with no walkable orthogonal neighbour (wall-pinned scenery) is RECORDED as unreachable,
    never silently dropped — the forest_road derived manifest exercises this (many trees are pinned)."""
    forest = drift.load_manifest(_QA_DIR / "room_manifests" / "forest_road.cells.json")
    script = je.build_script(forest)
    approached = len(script.steps)
    assert script.unreachable, "wall-pinned forest props must be surfaced as unreachable"
    assert approached + len(script.unreachable) == len(forest["props"])
    assert all("reason" in u and "id" in u for u in script.unreachable)


def test_build_script_adds_transitions_from_plan():
    plan = {"start_cell": [7, 8], "parley_cell": [5, 5], "door_cell": [0, 5], "combat_cell": [9, 4]}
    script = je.build_script(_crypt(), plan)
    kinds = [s.kind for s in script.steps]
    assert kinds[0] == "start"
    assert "parley" in kinds and "door_cross" in kinds and "combat_entry" in kinds
    transitions = [s for s in script.steps if s.transition]
    assert {s.kind for s in transitions} == {"door_cross", "combat_entry"}


# ── VQA aggregation with a STUB scorer + STUB differ (no LLM, no box) ────────────────────────────────
_CLEAN = lambda p, q: {question["flag"]: False for question in q}  # noqa: E731
_NEVER_DIFF = lambda a, b: 1.0  # backdrops always "changed" (no false swap-failure)  # noqa: E731


def test_run_vqa_and_verdict_clean_journey_passes():
    frames = [{"path": "a.png", "step": "approach_x", "side": "step", "transition": False}]
    verdict = je.build_verdict(je.run_vqa(frames, je.load_questions(), _CLEAN, image_differ=_NEVER_DIFF))
    assert verdict["passed"] and verdict["frames_with_defects"] == 0


def test_verdict_fails_and_names_offending_frame_on_any_yes():
    frames = [
        {"path": "clean.png", "step": "approach_sarcophagus", "side": "step", "transition": False},
        {"path": "bad.png", "step": "approach_pillar_l", "side": "step", "transition": False},
    ]

    def stub(path, questions):
        flags = {q["flag"]: False for q in questions}
        if path == "bad.png":
            flags["on_prop"] = True  # character standing inside a painted prop — the sarcophagus class
        return flags

    verdict = je.build_verdict(je.run_vqa(frames, je.load_questions(), stub, image_differ=_NEVER_DIFF))
    assert not verdict["passed"] and verdict["frames_with_defects"] == 1
    off = verdict["defects"][0]
    assert off["frame"] == "bad.png" and off["defects"] == ["on_prop"]


def test_verdict_fails_when_no_frames_checked():
    """An empty/malformed capture must FAIL — zero frames is not evidence the loop was inspected."""
    verdict = je.build_verdict(je.run_vqa([], je.load_questions(), _CLEAN, image_differ=_NEVER_DIFF))
    assert not verdict["passed"] and verdict["frames_checked"] == 0 and verdict["reasons"]


def test_unreachable_props_surfaced_in_verdict():
    verdict = je.build_verdict([{"frame": "a.png", "step": "x", "side": "step", "flags": {}, "defects": []}],
                               unreachable=[{"id": "tree_9", "cells": [[0, 0]], "reason": "pinned"}])
    assert verdict["unreachable_props"][0]["id"] == "tree_9"


def test_missing_or_cloned_suppressed_on_establishing_start_frame():
    """An establishing 'start' shot may legitimately show only scenery, so missing_or_cloned must NOT be
    asked of it (else a clean journey false-reds) — but every gameplay frame still gets it."""
    qs = je.load_questions()
    seen = {}

    def stub(path, questions):
        seen[path] = {q["flag"] for q in questions}
        return {q["flag"]: False for q in questions}

    je.run_vqa([{"path": "start.png", "step": "start", "kind": "start", "side": "step",
                 "transition": False}], qs, stub, image_differ=_NEVER_DIFF)
    je.run_vqa([{"path": "game.png", "step": "approach_x", "kind": "prop_approach", "side": "step",
                 "transition": False}], qs, stub, image_differ=_NEVER_DIFF)
    assert "missing_or_cloned" not in seen["start.png"], "establishing shot must tolerate an empty scene"
    assert "missing_or_cloned" in seen["game.png"], "gameplay frames must still get missing_or_cloned"


def test_transition_swap_check_is_deterministic_from_both_frames():
    """The swap check is computed by the harness from the pre/post pair (NOT asked of the LLM). A tiny
    pre/post difference => backdrop unchanged => a failed plate swap is flagged; a large one => clean."""
    frames = [
        {"path": "door_pre.png", "step": "door_cross", "side": "pre", "transition": True},
        {"path": "door_post.png", "step": "door_cross", "side": "post", "transition": True},
    ]
    qs = je.load_questions()
    # backdrops nearly identical -> swap failed
    failed = je.run_vqa(frames, qs, _CLEAN, image_differ=lambda a, b: 0.0)
    post = next(r for r in failed if r["side"] == "post")
    assert post["flags"]["transition_backdrop_unchanged"] is True
    assert "transition_backdrop_unchanged" in post["defects"]
    # backdrops clearly changed -> clean swap
    ok = je.run_vqa(frames, qs, _CLEAN, image_differ=lambda a, b: 0.5)
    post_ok = next(r for r in ok if r["side"] == "post")
    assert post_ok["flags"]["transition_backdrop_unchanged"] is False
    assert not post_ok["defects"]
