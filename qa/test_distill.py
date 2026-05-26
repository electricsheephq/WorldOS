"""Unit tests for qa/distill.py — the _audit_fields helper that surfaces engine-auto-fired
mechanics the 240-char tool_result preview would otherwise truncate (so the Angry-DM scorer
can audit them tool-sourced, not only in DM prose).

Stdlib + pytest only. Self-contained.

Run with the engine venv (which has pytest):
    uv run --directory servers/engine python -m pytest qa/test_distill.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# distill lives next to this test (qa/); make it importable regardless of pytest rootdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import distill  # noqa: E402


def test_audit_fields_surfaces_repeat_saves_with_roll_detail():
    # A next_turn return carrying two Hold Person end-of-turn escape saves (#209): one held,
    # one that frees the target. The exact roll values must survive (the scorer flagged them
    # "irrecoverable from the truncated next_turn output").
    res = json.dumps(
        {
            "round": 2,
            "repeat_saves": [
                {"character_id": "char_cap", "name": "Hold Person", "ability": "wis",
                 "dc": 14, "roll": 5, "natural": 5, "success": False, "ended": False},
                {"character_id": "char_cap", "name": "Hold Person", "ability": "wis",
                 "dc": 14, "roll": 18, "natural": 18, "success": True, "ended": True,
                 "cleared_condition": "paralyzed"},
            ],
        }
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 2
    assert "Hold Person on char_cap" in lines[0]
    assert "WIS 5 (nat 5) vs DC 14 → held" in lines[0]
    assert "WIS 18 (nat 18) vs DC 14 → ENDS" in lines[1]


def test_audit_fields_surfaces_maneuver_damage():
    # An attack/use_resource return carrying the Battle Master superiority die (#213).
    res = json.dumps(
        {"ok": True, "maneuver_damage": {"maneuver": "Trip Attack", "die": "1d8",
                                         "rolled": 6, "applied": True}}
    )
    lines = distill._audit_fields(res)
    assert len(lines) == 1
    assert "maneuver-damage: Trip Attack 1d8=6 applied=True" in lines[0]


def test_audit_fields_is_safe_on_non_json_non_dict_and_irrelevant():
    assert distill._audit_fields("not json at all") == []
    assert distill._audit_fields(json.dumps([1, 2, 3])) == []
    assert distill._audit_fields(json.dumps({"unrelated": "result"})) == []
    # An empty/null repeat_saves is a no-op (the common case: most turns fire none).
    assert distill._audit_fields(json.dumps({"repeat_saves": []})) == []
    assert distill._audit_fields(json.dumps({"repeat_saves": None})) == []


def test_audit_fields_tolerates_partial_repeat_save_entries():
    # A malformed entry (missing keys, or not a dict) must not crash — it degrades to "?".
    res = json.dumps({"repeat_saves": ["bogus", {"name": "Hold Monster"}]})
    lines = distill._audit_fields(res)
    assert len(lines) == 1  # the string entry is skipped; the partial dict still renders
    assert "Hold Monster" in lines[0]
