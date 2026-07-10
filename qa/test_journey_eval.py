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
    assert all(q["applies_to"] in ("all", "transition") for q in qs)
    # at least one transition-scoped question (both-sides-of-a-transition check)
    assert any(q["applies_to"] == "transition" for q in qs)


def test_questions_for_frame_scopes_transition_only():
    qs = je.load_questions()
    all_frame = je.questions_for_frame(qs, is_transition=False)
    trans_frame = je.questions_for_frame(qs, is_transition=True)
    assert all(q["applies_to"] == "all" for q in all_frame)
    assert len(trans_frame) > len(all_frame)  # transition frames get the extra swap check


# ── scripted path derivation ────────────────────────────────────────────────────────────────────────
def test_build_script_visits_every_prop_on_a_walkable_adjacent_cell():
    m = _crypt()
    steps = je.build_script(m)
    approaches = [s for s in steps if s.kind == "prop_approach"]
    assert len(approaches) == len(m["props"]) == 3
    prop_cells = {(int(c), int(r)) for p in m["props"] for (c, r) in p["cells"]}
    cols, rows = m["grid"]["cols"], m["grid"]["rows"]
    for s in approaches:
        c, r = s.cell
        assert 0 <= c < cols and 0 <= r < rows, f"{s.id} target off-grid: {s.cell}"
        assert (c, r) not in prop_cells, f"{s.id} target sits ON a prop cell {s.cell}"


def test_build_script_adds_transitions_from_plan():
    plan = {"start_cell": [7, 8], "parley_cell": [5, 5], "door_cell": [0, 5], "combat_cell": [9, 4]}
    steps = je.build_script(_crypt(), plan)
    kinds = [s.kind for s in steps]
    assert kinds[0] == "start"
    assert "parley" in kinds and "door_cross" in kinds and "combat_entry" in kinds
    transitions = [s for s in steps if s.transition]
    assert {s.kind for s in transitions} == {"door_cross", "combat_entry"}


# ── VQA aggregation with a STUB scorer (no LLM, no box) ─────────────────────────────────────────────
def test_run_vqa_and_verdict_clean_journey_passes():
    frames = [{"path": "a.png", "step": "approach_x", "side": "step", "transition": False}]
    qs = je.load_questions()
    stub = lambda p, q: {question["flag"]: False for question in q}  # noqa: E731
    verdict = je.build_verdict(je.run_vqa(frames, qs, stub))
    assert verdict["passed"] and verdict["frames_with_defects"] == 0


def test_verdict_fails_and_names_offending_frame_on_any_yes():
    frames = [
        {"path": "clean.png", "step": "approach_sarcophagus", "side": "step", "transition": False},
        {"path": "bad.png", "step": "approach_pillar_l", "side": "step", "transition": False},
    ]
    qs = je.load_questions()

    def stub(path, questions):
        flags = {q["flag"]: False for q in questions}
        if path == "bad.png":
            flags["on_prop"] = True  # character standing inside a painted prop — the sarcophagus class
        return flags

    verdict = je.build_verdict(je.run_vqa(frames, qs, stub))
    assert not verdict["passed"]
    assert verdict["frames_with_defects"] == 1
    off = verdict["defects"][0]
    assert off["frame"] == "bad.png" and off["defects"] == ["on_prop"]


def test_transition_frame_gets_the_swap_question_scored():
    frames = [{"path": "door_post.png", "step": "door_cross", "side": "post", "transition": True}]
    qs = je.load_questions()
    seen = {}

    def stub(path, questions):
        seen[path] = {q["flag"] for q in questions}
        return {q["flag"]: False for q in questions}

    je.run_vqa(frames, qs, stub)
    assert any(q["applies_to"] == "transition" for q in qs)
    trans_flags = {q["flag"] for q in qs if q["applies_to"] == "transition"}
    assert trans_flags <= seen["door_post.png"], "transition frame must be asked the swap question"
