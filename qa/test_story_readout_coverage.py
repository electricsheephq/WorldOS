"""Self-tests for story_readout.analyze()'s COVERAGE-fidelity signals: distinct-location counting
and pre-seeded-companion engagement.

Two real false-negatives, both found on the gs-ember-deep authored-spine run (story-craft 4.8, 54
beats, 4 locations, a deeply-engaged companion) whose stamp read `recruit · locs=1`:

  1. travel_to carries its destination in `to` (not name/location/adventure_id), so a multi-location
     arc logged ZERO new locations → locs=1 on a 4-location run.
  2. An AUTHORED adventure PRE-SEEDS the companion (recruit_companion is never called), so the
     literal tool-count read `recruit ·` even though the session camped with the companion and moved
     their regard +27 — contradicting its own `camp ✓ approval ✓`.

analyze() now reads the travel_to destination field and derives `companion_engaged` from the
companion-specific tools + approval movement (matching the snapshot-coverage path, which already
marks a party companion `recruited`). coverage_from_tool_counts is untouched (the #961 behavioral
assertion reuses it).

Stdlib + pytest only; self-contained. Run single-process:
    uv run --directory servers/engine python -m pytest qa/test_story_readout_coverage.py -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_readout as sr  # noqa: E402


# ── transcript builders (claude -p stream-json shape that story_readout._events parses) ──────────

def _tool_use(name: str, inp: dict) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": name, "input": inp}]},
    })


def _tool_result(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
    })


def _text(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def _write(tmp_path: Path, lines) -> str:
    p = tmp_path / "dm.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# ── distinct-location counting (the travel_to `to` field) ────────────────────────────────────────

def test_travel_to_destination_counts_distinct_locations(tmp_path):
    """Occupied locations = the START (from a look_around result's current_location_id) + each
    travel_to destination (canonical `destination_id`, per server.py travel_to). Mirrors the EXACT
    gs-ember-deep arc the old extractor flattened to locs=1: cinderhollow + mill/barrow/crypt = 4.
    The adventure_id ("embergloom-pact") is NOT a place and must not be counted (else this is 5)."""
    path = _write(tmp_path, [
        _tool_use("start_adventure", {"adventure_id": "embergloom-pact"}),
        _tool_use("look_around", {}),
        _tool_result(json.dumps({"current_location_id": "loc-cinderhollow", "exits": []})),
        _tool_use("travel_to", {"campaign_id": "c1", "destination_id": "loc-hollowmere-mill", "advance_time": True}),
        _tool_use("travel_to", {"campaign_id": "c1", "destination_id": "loc-ashen-barrow", "advance_time": True}),
        _tool_use("travel_to", {"campaign_id": "c1", "destination_id": "loc-embergloom-crypt"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["distinct_locations"] == 4, cov["distinct_locations"]


def test_current_location_id_in_result_counts_single_occupied_location(tmp_path):
    """A pure single-location session (no travel) still reports its place via the current_location_id
    the engine stamps in look_around / scene_context results — so locs reflects the one place
    occupied (1), not 0, and repeats of the same place dedup."""
    path = _write(tmp_path, [
        _tool_use("start_adventure", {"adventure_id": "embergloom-pact"}),
        _tool_use("scene_context", {}),
        _tool_result(json.dumps({"current_location_id": "loc-the-hearth", "state": {}})),
        _tool_use("scene_context", {}),
        _tool_result(json.dumps({"current_location_id": "loc-the-hearth"})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["distinct_locations"] == 1, cov["distinct_locations"]


def test_travel_to_dedupes_revisited_location(tmp_path):
    """Revisiting a location does not double-count."""
    path = _write(tmp_path, [
        _tool_use("travel_to", {"destination_id": "loc-a"}),
        _tool_use("travel_to", {"destination_id": "loc-b"}),
        _tool_use("travel_to", {"destination_id": "loc-a"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["distinct_locations"] == 2


def test_travel_to_destination_alias_fields(tmp_path):
    """The engine's travel_to aliases (destination / to / location_id — see its signature) are also
    accepted, so a transcript that used an alias instead of destination_id still counts."""
    path = _write(tmp_path, [
        _tool_use("travel_to", {"destination": "loc-a"}),
        _tool_use("travel_to", {"to": "loc-b"}),
        _tool_use("travel_to", {"location_id": "loc-c"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["distinct_locations"] == 3


def test_advance_time_to_field_does_not_pollute_locations(tmp_path):
    """advance_time/advance_clock also carry a `to` field (to="night") — but they are NOT location
    tools, so the extractor (keyed on the 4 travel/world tool NAMES) must never pick them up."""
    path = _write(tmp_path, [
        _tool_use("travel_to", {"destination_id": "loc-x"}),
        _tool_use("advance_time", {"to": "night"}),
        _tool_use("advance_clock", {"to": "afternoon"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["distinct_locations"] == 1  # only loc-x; the time `to`s are ignored


# ── pre-seeded companion engagement ──────────────────────────────────────────────────────────────

def test_preseeded_companion_engaged_via_camp_stamps_recruit(tmp_path):
    """An AUTHORED adventure pre-seeds the companion (no recruit_companion call). camp_scene + moved
    approval prove engagement → companion_engaged True and the stamp marks `recruit ✓` — matching the
    snapshot path, and resolving the `recruit ·` beside `camp ✓ approval ✓` self-contradiction."""
    path = _write(tmp_path, [
        _tool_use("start_adventure", {"adventure_id": "embergloom-pact"}),
        _tool_use("camp_scene", {"summary": "Brother Toll confesses by the fire"}),
        _tool_use("record_decision", {"choice": "kept his secret", "approval_tags": ["toll_approves"]}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["recruit"] == 0                # the raw tool count is honestly 0 (pre-seeded)
    assert cov["companion_engaged"] is True
    assert "recruit ✓" in sr.stamp(cov)


def test_companion_engaged_via_arc_tool(tmp_path):
    """check_companion_arc / advance_companion_quest_arc are companion-specific — either implies an
    engaged companion even with no recruit and no approval delta."""
    path = _write(tmp_path, [
        _tool_use("check_companion_arc", {"companion_id": "npc-toll"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["companion_engaged"] is True
    assert "recruit ✓" in sr.stamp(cov)


def test_no_companion_signal_stamps_recruit_dot(tmp_path):
    """A solo run — no recruit, no camp/arc tool, no approval move — keeps companion_engaged False
    and `recruit ·` (no false positive)."""
    path = _write(tmp_path, [
        _tool_use("start_adventure", {"adventure_id": "solo-run"}),
        _tool_use("travel_to", {"to": "loc-x"}),
        _text("You walk the empty road alone."),
    ])
    _render, cov = sr.analyze(path)
    assert cov["companion_engaged"] is False
    assert "recruit ·" in sr.stamp(cov)


def test_solo_long_rest_is_not_companion_engagement(tmp_path):
    """long_rest fills the camp BUCKET but can be a solo rest — it must NOT imply a companion.
    companion_engaged derives from the companion-specific tools + approval, not the camp bucket."""
    path = _write(tmp_path, [
        _tool_use("long_rest", {}),
        _tool_result(json.dumps({"ok": True, "day": 2})),
    ])
    _render, cov = sr.analyze(path)
    assert cov["camp"] >= 1                   # long_rest still fills the camp bucket
    assert cov["companion_engaged"] is False  # ...but it is NOT companion engagement
    assert "recruit ·" in sr.stamp(cov)


def test_explicit_recruit_companion_still_stamps_recruit(tmp_path):
    """A GENERATED world that calls recruit_companion on-screen still stamps recruit ✓ (the literal
    path is preserved — no regression)."""
    path = _write(tmp_path, [
        _tool_use("recruit_companion", {"npc_id": "npc-karlach"}),
    ])
    _render, cov = sr.analyze(path)
    assert cov["recruit"] == 1
    assert cov["companion_engaged"] is True
    assert "recruit ✓" in sr.stamp(cov)
