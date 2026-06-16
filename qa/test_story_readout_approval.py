"""Self-tests for story_readout.analyze()'s APPROVAL-MOVED stamp detection.

The bug: the COVERAGE stamp marked `approval-moved` only from the adjust_attitude /
check_companion_arc / advance_companion_quest_arc tool-NAME counts (via
coverage_from_tool_counts). But in real play the DM most often moves a companion's regard by
PERSISTING the beat's decision — persist_beat / record_decision carrying `approval_tags`, with the
engine returning `approval_results` and stamping attitude_value. So the stamp read `approval ·`
while the engine state showed attitude moved (attitude_value=10). analyze() now ALSO detects
approval movement from (a) a persist_beat/record_decision INPUT with non-empty approval_tags, or
(b) a tool_RESULT carrying approval_results / an attitude-or-approval delta — without touching
coverage_from_tool_counts (the #961 behavioral assertion reuses that helper for camp/quest only).

Stdlib + pytest only; self-contained. Run single-process:
    uv run --directory servers/engine python -m pytest qa/test_story_readout_approval.py -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_readout as sr  # noqa: E402


# ── transcript builders (claude -p stream-json shape that story_readout._events parses) ──────────

def _assistant_tool_use(name: str, inp: dict) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": name, "input": inp}]},
    })


def _tool_result(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"role": "user",
                    "content": [{"type": "tool_result", "content": text}]},
    })


def _assistant_text(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def _write(tmp_path: Path, lines) -> str:
    p = tmp_path / "dm.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# ── the load-bearing case: persist_beat carrying approval_tags stamps approval ✓ ─────────────────

def test_persist_beat_approval_tags_stamps_approval(tmp_path):
    """A persist_beat carrying a decision with non-empty approval_tags (the engine returns
    approval_results) — no adjust_attitude / check_companion_arc anywhere — must stamp approval ✓.
    This is the exact real-play shape the old tool-name-count stamp missed (showed `approval ·`)."""
    path = _write(tmp_path, [
        _assistant_text("You meet Karlach at the forge; she sizes you up."),
        _assistant_tool_use("persist_beat", {
            "summary": "Player defended Karlach's honor",
            "decision": {"choice": "stood up for her", "approval_tags": ["karlach_approves"]},
        }),
        _tool_result(json.dumps({"ok": True, "approval_results": [
            {"companion": "Karlach", "attitude_value": 10, "delta": 10}]})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is True
    line = sr.stamp(cov)
    assert "approval-moved ✓" in line


def test_record_decision_approval_tags_stamps_approval(tmp_path):
    """record_decision carrying approval_tags at the INPUT top level (not nested) also fires."""
    path = _write(tmp_path, [
        _assistant_tool_use("record_decision", {
            "choice": "spared the cultist", "approval_tags": ["shadowheart_disapproves"]}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is True
    assert "approval-moved ✓" in sr.stamp(cov)


def test_approval_results_in_tool_result_stamps_approval(tmp_path):
    """Even if the input is opaque, an engine RESULT carrying approval_results proves the move."""
    path = _write(tmp_path, [
        _assistant_tool_use("persist_beat", {"summary": "a tense exchange"}),
        _tool_result(json.dumps({"approval_results": [{"companion": "Lae'zel", "attitude_value": -5}]})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is True


def test_attitude_delta_in_result_stamps_approval(tmp_path):
    """A bare attitude_delta field in a result (the older shape) also counts."""
    path = _write(tmp_path, [
        _assistant_tool_use("adjust_attitude", {"target": "Gale", "delta": 5}),
        _tool_result(json.dumps({"attitude_delta": 5, "attitude_value": 5})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is True


# ── negative cases: no false positives ───────────────────────────────────────────────────────────

def test_no_approval_signal_stamps_dot(tmp_path):
    """A run with NO approval movement (empty approval_tags, no approval_results, no attitude tool)
    keeps approval ·. The fix must not flip the stamp on unrelated persist_beat calls."""
    path = _write(tmp_path, [
        _assistant_text("The road stretches on under a grey sky."),
        _assistant_tool_use("persist_beat", {"summary": "travel beat", "decision": {"choice": "kept walking"}}),
        _tool_result(json.dumps({"ok": True, "day": 3})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is False
    assert "approval-moved ·" in sr.stamp(cov)


def test_empty_approval_tags_does_not_fire(tmp_path):
    """An explicit empty approval_tags list is NOT a move."""
    path = _write(tmp_path, [
        _assistant_tool_use("persist_beat", {"decision": {"choice": "x", "approval_tags": []}}),
        _tool_result(json.dumps({"ok": True})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is False


def test_prose_mention_of_approval_does_not_fire(tmp_path):
    """A result whose TEXT merely mentions 'approval' (no field) must NOT false-positive — the
    detector is field-shaped (a JSON key), never a bare word."""
    path = _write(tmp_path, [
        _assistant_tool_use("persist_beat", {"summary": "a chat about approval ratings"}),
        _tool_result("The crowd murmured their approval as the speech ended."),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is False


# ── the legacy path is preserved: adjust_attitude tool name still stamps ✓ ───────────────────────

def test_adjust_attitude_tool_name_still_stamps(tmp_path):
    """The pre-existing tool-NAME signal (adjust_attitude) must still fire — no regression."""
    path = _write(tmp_path, [
        _assistant_tool_use("adjust_attitude", {"target": "Astarion", "delta": 8}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["approval_moved"] is True
