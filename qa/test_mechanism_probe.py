#!/usr/bin/env python3
"""Tests for the Tier-1.5 mechanism probe: the fixture builder determinism + pre-check, and the
deterministic verdict parser (synthetic transcripts). Single-process (no xdist); no LLM, no
`claude -p` — the DM-driving beat is exercised by the live run, not here.

Run: uv run --directory servers/engine python -m pytest -q -p no:xdist ../../qa/test_mechanism_probe.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_ENGINE_DIR = _QA_DIR.parent / "servers" / "engine"
sys.path.insert(0, str(_QA_DIR))
sys.path.insert(0, str(_ENGINE_DIR))

import probe_verdict  # noqa: E402


def _load_fixture_module():
    """Import qa/probe_fixtures/wrap_window_active_quest.py as a module (it lives outside the qa
    package path, so load it by file)."""
    path = _QA_DIR / "probe_fixtures" / "wrap_window_active_quest.py"
    spec = importlib.util.spec_from_file_location("wrap_window_active_quest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Fields that MUST vary run-to-run — normalized out before a "same fixture → same snapshot"
# structural comparison. Determinism is about the STRUCTURE the PROBE seeds (party composition,
# quest objectives, arc state, the cue), NOT engine chrome the probe never reads: wall-clock
# timestamps, and the procedural `scene_grid` add_location auto-generates from a RANDOM `seed`
# (a battle-grid the cue mechanism doesn't touch — its dims/props differ every run by design).
_VOLATILE_KEYS = {"created_at", "updated_at", "engine_sha", "scene_grid", "seed"}

# Engine ids are ``<prefix>_<12 hex>`` (models._new_id) or ``session-<8 hex>`` — random by
# construction. They appear as VALUES, as list ENTRIES (party = [char ids]), AND as dict KEYS
# (quests/characters are keyed by id). Normalize all three so the structural comparison ignores
# them; a genuine structure change (an extra party member, a changed objective) still trips.
#
# The third alternative (``camp_[0-9a-z_]+``) is NOT a general engine-id shape — it exists
# specifically to match THIS fixture's pinned, human-readable campaign id
# (``wrap_window_active_quest.CID = "camp_probe_wrapwindow"``, deliberately NOT a random
# ``camp_<12 hex>`` so the fixture is reproducible by name). If a future fixture pins a
# DIFFERENT non-hex campaign id, or this CID is renamed to something the pattern no longer
# matches, that id would leak through un-normalized as a dict KEY and break this structural-
# equality assertion with a confusing diff rather than a clear "the id changed" failure — so
# keep this arm's pattern (or add a new one) in sync with every fixture's pinned CID constant.
_ID_RE = __import__("re").compile(r"^(?:[a-z]+_[0-9a-f]{12}|session-[0-9a-f]{8}|camp_[0-9a-z_]+)$")


def _norm_scalar(v):
    return "<id>" if isinstance(v, str) and _ID_RE.match(v) else v


def _normalize(obj):
    """Recursively replace volatile values / random ids (as values, list entries, or dict keys) with
    stable placeholders so two snapshots of the SAME seeded structure compare equal (uuids/timestamps
    differ by construction; the shape must not)."""
    if isinstance(obj, dict):
        return {
            ("<id>" if isinstance(k, str) and _ID_RE.match(k) else k):
                ("<volatile>" if k in _VOLATILE_KEYS else _normalize(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_normalize(_norm_scalar(v)) if not isinstance(v, (dict, list)) else _normalize(v) for v in obj]
    return _norm_scalar(obj)


def _build_into(tmp_dir: Path) -> dict:
    """Build the fixture into a fresh state dir and return the manifest. Reloads the engine against
    the given WORLDOS_STATE_DIR so each build is isolated."""
    os.environ["WORLDOS_STATE_DIR"] = str(tmp_dir)
    # server caches campaigns per-process keyed by state dir; import once, but each build writes a
    # fresh snapshot under its own state dir so the on-disk artifact is what we compare.
    mod = _load_fixture_module()
    return mod.build_and_precheck()


def _snapshot(manifest: dict, state_dir: Path) -> dict:
    snap = state_dir / "campaigns" / manifest["campaign_id"] / "snapshot.json"
    return json.loads(snap.read_text(encoding="utf-8"))


# ── Fixture builder: determinism + the pre-check assertion ─────────────────────────────────────

def test_fixture_precheck_yields_quest_endgame_next_action(tmp_path):
    """The FREE pre-check: the seeded wrap-window fixture yields quest_endgame_unresolved as the
    beat's next_action (the invariant the probe stands on)."""
    manifest = _build_into(tmp_path / "s1")
    assert manifest["cue"] == "quest_endgame_unresolved"
    assert manifest["next_action"] == "quest_endgame_unresolved"
    # The wrap-window HIGH cue must sort FIRST (ahead of the med/low camp/act/companion cues).
    assert manifest["obligation_kinds"][0] == "quest_endgame_unresolved"
    assert "quest_id" in manifest and manifest["quest_id"]


def test_fixture_determinism_same_structure_across_builds(tmp_path):
    """Same fixture → same snapshot STRUCTURE (volatile ids/timestamps normalized). Two independent
    builds into fresh state dirs must produce structurally identical snapshots."""
    m1 = _build_into(tmp_path / "a")
    snap1 = _snapshot(m1, tmp_path / "a")
    m2 = _build_into(tmp_path / "b")
    snap2 = _snapshot(m2, tmp_path / "b")
    assert _normalize(snap1) == _normalize(snap2), "fixture is not structurally deterministic"


def test_fixture_arc_and_quest_parked_in_wrap_window(tmp_path):
    """Structural spot-check: the arc is act=2 beats_in_act=8 and the quest is active with an
    incomplete objective — the exact parked state the cue depends on."""
    m = _build_into(tmp_path / "c")
    snap = _snapshot(m, tmp_path / "c")
    arc = snap["narrative_arc"]
    assert arc["act"] == 2 and arc["beats_in_act"] == 8
    q = snap["quests"][m["quest_id"]]
    assert q["status"] == "active"
    assert len(q["objectives"]) > len(q.get("completed_objectives") or [])


# ── Verdict parser: tool tally + engagement subset ──────────────────────────────────────────────

def _write_transcript(path: Path, tool_uses: list[tuple[str, dict]], texts: list[str] = ()) -> Path:
    """Write a synthetic stream-json DM transcript: one `assistant` event carrying the given
    tool_use blocks (name,input) + any text blocks, then a `result` event."""
    content = [{"type": "text", "text": t} for t in texts]
    content += [{"type": "tool_use", "name": n, "input": i} for n, i in tool_uses]
    lines = [
        json.dumps({"type": "assistant", "message": {"content": content}}),
        json.dumps({"type": "result", "result": "ok", "is_error": False, "num_turns": 2}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_tool_tally_counts_all_tool_uses(tmp_path):
    _write_transcript(tmp_path / "t.jsonl", [
        ("scene_context", {}), ("complete_objective", {"objective": "x"}),
        ("complete_quest", {"quest_id": "q"}), ("complete_objective", {"objective": "y"}),
    ], texts=["a scene"])
    tally = probe_verdict.tool_tally(tmp_path / "t.jsonl")
    assert tally["complete_objective"] == 2
    assert tally["complete_quest"] == 1
    assert tally["scene_context"] == 1


def test_engagement_tally_filters_to_cue_tools(tmp_path):
    _write_transcript(tmp_path / "t.jsonl", [
        ("scene_context", {}), ("say", {}), ("complete_quest", {"quest_id": "q"}),
        ("add_consequence", {"text": "echo"}),
    ])
    eng = probe_verdict.engagement_tally(tmp_path / "t.jsonl", "quest_endgame_unresolved")
    assert dict(eng) == {"complete_quest": 1, "add_consequence": 1}
    # scene_context / say are NOT engagement tools for this cue.
    assert "scene_context" not in eng


def test_tool_names_are_mcp_prefix_stripped(tmp_path):
    """Claude reports fully-qualified MCP tool names (mcp__worldos-engine__complete_quest); the
    tally must match the cue's bare-name table. (Regression from the first real probe run, where
    the un-stripped matcher tallied nothing despite the DM calling complete_quest.)"""
    t = _write_transcript(tmp_path / "t.jsonl", [
        ("mcp__worldos-engine__complete_quest", {"quest_id": "q"}),
        ("mcp__worldos-engine__complete_objective", {"objective": "x"}),
        ("mcp__worldos-engine__scene_context", {}),
    ])
    tally = probe_verdict.tool_tally(t)
    assert tally["complete_quest"] == 1 and tally["scene_context"] == 1
    eng = probe_verdict.engagement_tally(t, "quest_endgame_unresolved")
    assert dict(eng) == {"complete_quest": 1, "complete_objective": 1}


def test_iter_tool_uses_tolerates_bad_and_non_assistant_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "r"}]}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "attack", "input": {}}]}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": "a plain string content"}}) + "\n",
        encoding="utf-8",
    )
    names = [tu["name"] for tu in probe_verdict.iter_tool_uses(p)]
    assert names == ["attack"]  # the bad line, the user event, and the string-content event are skipped


# ── Verdict parser: quest movement (engine ground truth) ────────────────────────────────────────

def test_quest_moved_detects_status_flip():
    before = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": []}}}
    after = {"quests": {"q1": {"id": "q1", "status": "completed", "completed_objectives": []}}}
    assert probe_verdict.quest_moved(before, after) is True


def test_quest_moved_detects_objective_progress():
    before = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": []}}}
    after = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": ["step 1"]}}}
    assert probe_verdict.quest_moved(before, after) is True


def test_quest_moved_false_when_unchanged():
    same = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": ["a"]}}}
    assert probe_verdict.quest_moved(same, json.loads(json.dumps(same))) is False


# ── Verdict parser: the fold-into-verdict logic ─────────────────────────────────────────────────

def test_verdict_acted_when_tool_called_and_state_moved():
    v = probe_verdict.compute_verdict(
        "quest_endgame_unresolved", cue_present_at_start=True,
        engagement=Counter({"complete_quest": 1}), state_moved=True,
    )
    assert v == "ACTED"


def test_verdict_ignored_when_cue_fired_but_state_never_moved():
    v = probe_verdict.compute_verdict(
        "quest_endgame_unresolved", cue_present_at_start=True,
        engagement=Counter(), state_moved=False,
    )
    assert v == "IGNORED"


def test_verdict_ignored_when_tool_called_but_state_did_not_move():
    # A complete_quest that failed/aborted (state didn't move) is still IGNORED — ground truth is
    # engine movement, not the attempt.
    v = probe_verdict.compute_verdict(
        "quest_endgame_unresolved", cue_present_at_start=True,
        engagement=Counter({"complete_quest": 1}), state_moved=False,
    )
    assert v == "IGNORED"


def test_verdict_cue_absent_only_when_cue_missing_at_start():
    """CUE_ABSENT means the fixture never held the question (cue not present at beat 1) — NOT a cue
    that fired at start and then CLEARED because the DM acted (that is ACTED)."""
    v = probe_verdict.compute_verdict(
        "quest_endgame_unresolved", cue_present_at_start=False,
        engagement=Counter({"complete_quest": 1}), state_moved=True,
    )
    assert v == "CUE_ABSENT"


def test_build_report_end_to_end_acted(tmp_path):
    """A full synthetic ACTED case: cue held both beats, DM called complete_quest, engine moved."""
    _write_transcript(tmp_path / "t.jsonl", [
        ("scene_context", {}), ("complete_objective", {"objective": "Find the haunt"}),
        ("complete_quest", {"quest_id": "q1", "evolves_to": "the stone speaks again"}),
    ])
    before = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": []}}}
    after = {"quests": {"q1": {"id": "q1", "status": "completed", "completed_objectives": ["Find the haunt"]}}}
    report = probe_verdict.build_report("quest_endgame_unresolved", [True, True], tmp_path / "t.jsonl", before, after)
    assert report["verdict"] == "ACTED"
    assert report["cue_present_at_start"] is True
    assert report["quest_resolved_or_progressed"] is True
    assert report["dm_engagement_tools_called"] == {"complete_objective": 1, "complete_quest": 1}


def test_build_report_acted_when_dm_acts_immediately_and_cue_clears(tmp_path):
    """The case the FIRST real probe run exposed: the DM resolves the quest on beat 1, which CLEARS
    the wrap-window cue on beats 2–3. That cue-clearing is the SUCCESS signal (ACTED) — not
    CUE_ABSENT. Tool names arrive MCP-prefixed (as in a real transcript)."""
    _write_transcript(tmp_path / "t.jsonl", [
        ("mcp__worldos-engine__scene_context", {}),
        ("mcp__worldos-engine__complete_objective", {"objective": "Find the haunt"}),
        ("mcp__worldos-engine__complete_quest", {"quest_id": "q1", "evolves_to": "echo"}),
    ])
    before = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": []}}}
    after = {"quests": {"q1": {"id": "q1", "status": "completed", "completed_objectives": ["Find the haunt"]}}}
    report = probe_verdict.build_report("quest_endgame_unresolved", [True, False, False], tmp_path / "t.jsonl", before, after)
    assert report["verdict"] == "ACTED"
    assert report["cue_present_at_start"] is True
    assert report["cue_present_each_beat"] is False
    assert report["cue_cleared_after_action"] is True
    assert report["dm_engagement_tools_called"] == {"complete_objective": 1, "complete_quest": 1}


def test_build_report_end_to_end_ignored(tmp_path):
    """The IGNORED case the probe exists to catch: cue fired every beat, DM only narrated (no
    engagement tool), engine state never moved."""
    _write_transcript(tmp_path / "t.jsonl", [("say", {}), ("scene_context", {})],
                       texts=["The stone flickers... the party wonders who silenced it."])
    before = {"quests": {"q1": {"id": "q1", "status": "active", "completed_objectives": []}}}
    after = json.loads(json.dumps(before))
    report = probe_verdict.build_report("quest_endgame_unresolved", [True, True, True], tmp_path / "t.jsonl", before, after)
    assert report["verdict"] == "IGNORED"
    assert report["dm_engagement_tools_called"] == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:xdist"]))
