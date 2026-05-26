"""Unit tests for qa/collect_findings.py — the parse helpers + the merge/idempotency contract.

Stdlib + pytest only. Self-contained: the fixtures mirror the real score-artifact schema
(score_schema_angry_dm.json + the gate.txt format the harness writes) so the test runs in a
fresh checkout where qa/transcripts/ (gitignored runtime data) is empty.

Run with the engine venv (which has pytest):
    uv run --directory servers/engine python -m pytest qa/test_collect_findings.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# collect_findings lives next to this test (qa/); make it importable regardless of pytest rootdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_findings as cf  # noqa: E402


# A realistic Angry-DM scorecard (shape per score_schema_angry_dm.json), with defects out of
# severity order so we can assert the worst-first sort.
_ANGRYDM = {
    "scores": {
        "rules_as_written": 3, "mechanical_completeness": 3, "tool_fidelity": 4,
        "action_economy": 3, "combat_resolution": 4, "conditions_and_effects": 4, "coverage": 2,
    },
    "overall": 3.3,
    "defects": [
        {"severity": "low", "kind": "commission", "rule": "Attack Rolls", "area": "combat_resolution",
         "evidence": "Natural 1 narrated as a fumble with extra consequence.", "five_e_says": "A nat 1 is just a miss.",
         "suggested_fix": "Drop fumble language."},
        {"severity": "high", "kind": "omission", "rule": "The Order of Combat", "area": "action_economy",
         "evidence": "Maren (init 19) skipped at top of round 1 — zero action-class tool calls.", "five_e_says": "Each combatant takes a full turn.",
         "suggested_fix": "Assert an action before next_turn."},
        {"severity": "medium", "kind": "omission", "rule": "Experience Points", "area": "hp_death_rests",
         "evidence": "end_combat with all enemies alive; 0 XP awarded.", "five_e_says": "XP is awarded when creatures are overcome.",
         "suggested_fix": "Fight to at least one kill."},
    ],
    "coverage": {"exercised": ["attack_rolls_melee"], "gaps": ["saving_throws"], "had_caster": True, "fights": 1, "notes": "short sprint"},
    "verdict": "Mostly clean dice but a skipped top-of-initiative turn.",
}


# ---------------------------------------------------------------------------
# parse_angrydm
# ---------------------------------------------------------------------------

def test_parse_angrydm_overall_and_top_defects():
    overall, top = cf.parse_angrydm(_ANGRYDM)
    assert overall == 3.3
    assert len(top) == 3
    # Worst-first: high, then medium, then low.
    assert top[0].startswith("[high/omission] action_economy")
    assert top[1].startswith("[medium/omission] hp_death_rests")
    assert top[2].startswith("[low/commission] combat_resolution")
    # The summary weaves in the cited rule + a (truncated) evidence clause.
    assert "The Order of Combat" in top[0]
    assert "Maren" in top[0]


def test_parse_angrydm_caps_at_three_defects():
    card = dict(_ANGRYDM)
    card["defects"] = _ANGRYDM["defects"] + [
        {"severity": "critical", "area": "x", "kind": "omission", "rule": "r", "evidence": "e"},
        {"severity": "high", "area": "y", "kind": "omission", "rule": "r", "evidence": "e"},
    ]
    _, top = cf.parse_angrydm(card)
    assert len(top) == cf.TOP_DEFECTS == 3
    # The injected critical sorts to the very top.
    assert top[0].startswith("[critical/omission] x")


def test_parse_angrydm_tolerates_missing_and_malformed():
    assert cf.parse_angrydm(None) == (None, [])
    assert cf.parse_angrydm({}) == (None, [])
    assert cf.parse_angrydm({"overall": 2.0}) == (2.0, [])  # no defects key
    assert cf.parse_angrydm({"overall": "bad", "defects": "notalist"}) == (None, [])


def test_defect_summary_truncates_long_evidence():
    long_ev = "x" * 500
    s = cf._defect_summary({"severity": "high", "kind": "omission", "area": "a", "rule": "r", "evidence": long_ev})
    assert s.endswith("...")
    assert len(s) < 260  # head + truncated evidence, not the full 500


# ---------------------------------------------------------------------------
# parse_gate — both the trailing-summary form AND the derive-from-markers form
# ---------------------------------------------------------------------------

def test_parse_gate_trailing_green_summary(tmp_path):
    p = tmp_path / "r.gate.txt"
    p.write_text("=== behavioral assertions ===\n  [PASS] dm_produced_output\nGREEN\n", encoding="utf-8")
    assert cf.parse_gate(p) == "GREEN"


def test_parse_gate_trailing_green_with_warning_count(tmp_path):
    p = tmp_path / "r.gate.txt"
    p.write_text("  [WARN] world_peopled — only 1 NPC\nGREEN (1 warning(s))\n", encoding="utf-8")
    assert cf.parse_gate(p) == "GREEN (1 warning(s))"


def test_parse_gate_derives_red_from_fail_when_no_summary(tmp_path):
    # Some gate files end at the last assertion with NO summary footer (real: ocwiz-claude).
    # A [FAIL] anywhere must yield RED — not the leading token of the last [PASS] line.
    p = tmp_path / "r.gate.txt"
    p.write_text(
        "=== behavioral assertions ===\n"
        "  [PASS] dm_produced_output\n"
        "  [FAIL] dm_resolved_player_moves — 1 [attack] but DM attack=0\n"
        "  [PASS] world_peopled\n",
        encoding="utf-8",
    )
    assert cf.parse_gate(p) == "RED"


def test_parse_gate_derives_green_with_warns_when_no_summary(tmp_path):
    p = tmp_path / "r.gate.txt"
    p.write_text("  [PASS] a\n  [WARN] b\n  [WARN] c\n", encoding="utf-8")
    assert cf.parse_gate(p) == "GREEN (2 warning(s))"


def test_parse_gate_absent_is_none(tmp_path):
    assert cf.parse_gate(tmp_path / "missing.gate.txt") is None


# ---------------------------------------------------------------------------
# collect — end-to-end against a synthesized transcripts dir, incl. missing artifacts
# ---------------------------------------------------------------------------

def _write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")


def test_collect_full_and_partial_runs(tmp_path):
    tx = tmp_path / "transcripts"
    tx.mkdir()
    # A duo run with all three lenses + a gate.
    _write(tx / "duoX.angrydm.json", {**_ANGRYDM, "overall": 2.9})
    _write(tx / "duoX.tolkien.json", {"overall": 4.0, "scores": {}})
    _write(tx / "duoX.score.json", {"overall": 3.6, "scores": {}})
    _write(tx / "duoX.gate.txt", "  [PASS] a\nGREEN\n")
    # A combat sprint: angry-DM ONLY (no story/mechanical artifacts) — must not crash.
    _write(tx / "sprintY.angrydm.json", {**_ANGRYDM, "overall": 3.3})
    _write(tx / "sprintY.gate.txt", "  [PASS] a\nGREEN\n")

    rows = cf.collect(tx)
    by_run = {r["run"]: r for r in rows}
    assert set(by_run) == {"duoX", "sprintY"}

    duo = by_run["duoX"]
    assert duo["scores"] == {"story": 4.0, "mechanical": 3.6, "angry_dm": 2.9}
    assert duo["angry_overall"] == 2.9
    assert duo["gate"] == "GREEN"
    assert len(duo["top_defects"]) == 3

    sprint = by_run["sprintY"]
    # Missing lenses report null, not a crash.
    assert sprint["scores"] == {"story": None, "mechanical": None, "angry_dm": 3.3}
    assert sprint["angry_overall"] == 3.3


def test_collect_missing_dir_returns_empty(tmp_path):
    assert cf.collect(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# merge_rows — idempotency + append-only ledger semantics
# ---------------------------------------------------------------------------

def test_merge_dedupes_by_run_and_fresh_wins():
    existing = [{"run": "a", "angry_overall": 1.0}, {"run": "b", "angry_overall": 2.0}]
    fresh = [{"run": "a", "angry_overall": 9.9}]  # 'a' re-scored
    merged = cf.merge_rows(existing, fresh)
    by_run = {r["run"]: r for r in merged}
    assert by_run["a"]["angry_overall"] == 9.9   # fresh wins
    assert by_run["b"]["angry_overall"] == 2.0   # stale row preserved (append-only ledger)
    assert [r["run"] for r in merged] == ["a", "b"]  # sorted, deduped


def test_merge_is_idempotent():
    fresh = [{"run": "a", "angry_overall": 1.0}, {"run": "b", "angry_overall": 2.0}]
    once = cf.merge_rows([], fresh)
    twice = cf.merge_rows(once, fresh)
    assert once == twice
