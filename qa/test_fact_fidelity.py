#!/usr/bin/env python3
"""Tests for qa/fact_fidelity.py — the differential fact-fidelity QA instrument.

The 1–5 LLM lens scorer is BLIND to content degradation (it reads plot-gist, not
arc-completeness): deleting the climax+resolution from a transcript moved the lens
score by ~0.0. This deterministic, grep-based instrument is the SENSITIVE measure —
it asserts a candidate transcript preserves a reference's discrete facts, and a
truncated/compressed candidate's fidelity drops monotonically.

Run (single-process):
    uv run --directory servers/engine python -m pytest qa/test_fact_fidelity.py -q -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import fact_fidelity as ff  # noqa: E402


def test_fidelity_is_fraction_of_facts_preserved():
    facts = [
        ff.Fact(id="present_one", desc="appears", patterns=[r"Dwarf"]),
        ff.Fact(id="absent_one", desc="does not appear", patterns=[r"Tiefling"]),
    ]
    report = ff.score_fidelity(facts, "Dal Lightspark is a Dwarf wizard, level 5.")
    assert report.fidelity == 0.5
    assert report.present == ["present_one"]
    assert report.missing == ["absent_one"]


def test_dropped_critical_fact_flags_critical_loss_and_weights_harder():
    facts = [
        ff.Fact(id="climax", desc="antagonist reveal", patterns=[r"Liora"], severity="critical"),
        ff.Fact(id="detail", desc="a minor detail", patterns=[r"candle"], severity="normal"),
    ]
    # the candidate keeps the minor detail but drops the CRITICAL climax fact
    report = ff.score_fidelity(facts, "The candle gutters low in the dark room.")
    assert report.critical_loss is True
    assert "climax" in report.missing
    # a heavy fact carries more weight than a normal one, so the weighted score punishes the
    # critical miss harder than the flat fraction (0.5) does
    assert report.weighted < report.fidelity


def test_load_inventory_parses_committed_json(tmp_path):
    p = tmp_path / "inv.json"
    p.write_text(json.dumps({
        "reference": "demo",
        "facts": [
            {"id": "a", "desc": "first", "patterns": ["foo", "f00"], "severity": "critical"},
            {"id": "b", "desc": "second", "patterns": ["bar"]},
        ],
    }))
    facts = ff.load_inventory(p)
    assert [f.id for f in facts] == ["a", "b"]
    assert facts[0].patterns == ["foo", "f00"]
    assert facts[0].severity == "critical"
    assert facts[1].severity == "normal"  # default when omitted


# Committed, CI-safe fixture (raw transcripts under /qa/transcripts/ are gitignored).
SAMPLE_REFERENCE = QA_DIR / "fact_inventories" / "sample_session.reference.md"
SAMPLE_INVENTORY = QA_DIR / "fact_inventories" / "sample_session.facts.json"


def _cut_before(text: str, marker: str) -> str:
    """Drop everything from ``marker`` onward — the deterministic analogue of the manual
    truncation that fooled the 1–5 lens (cut at 'CLIMAX —' deletes the climax+resolution;
    cut at 'MID —' keeps only the opening)."""
    idx = text.index(marker)
    return text[:idx]


def test_cutting_climax_resolution_drops_fidelity_and_flags_critical_loss():
    facts = ff.load_inventory(SAMPLE_INVENTORY)
    full = SAMPLE_REFERENCE.read_text(encoding="utf-8")
    through_mid = _cut_before(full, "CLIMAX — ")   # antagonist reveal, MacGuffin, end-session mechanics deleted
    opening_only = _cut_before(full, "MID — ")      # only the opening kept

    r_full = ff.score_fidelity(facts, full)
    r_mid = ff.score_fidelity(facts, through_mid)
    r_open = ff.score_fidelity(facts, opening_only)

    # the intact reference preserves ~all of its own facts (a faithful inventory)
    assert r_full.fidelity >= 0.97, r_full.missing
    assert not r_full.critical_loss
    # deleting the climax+resolution is a LARGE, visible drop — the exact loss the lens rated ~0.0
    assert r_mid.fidelity <= r_full.fidelity - 0.30
    # monotonic: keeping even less (only the opening) drops it further
    assert r_open.fidelity < r_mid.fidelity
    # the dropped facts include CRITICAL ones (the antagonist reveal, the MacGuffin, end_session)
    assert r_mid.critical_loss and r_open.critical_loss


@pytest.mark.skipif(
    not (QA_DIR / "transcripts" / "ow-combat-031717.md").exists(),
    reason="raw transcript is gitignored / local-only; reproduces the owner's exact evidence when present",
)
def test_real_combat_transcript_reproduces_finding():
    """Reproduce the owner's 2026-06-21 finding on the actual evidence transcript when it is
    present locally: line-fraction truncation (0.58 / 0.25, as in the original experiment) tanks
    fidelity monotonically and trips critical_loss — the loss the 1–5 lens scored ~flat (4.0/4.0/3.7)."""
    inv = QA_DIR / "fact_inventories" / "ow-combat-031717.facts.json"
    facts = ff.load_inventory(inv)
    full = (QA_DIR / "transcripts" / "ow-combat-031717.md").read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)

    def frac(f: float) -> str:
        return "".join(lines[: max(1, int(len(lines) * f))])

    r_full = ff.score_fidelity(facts, full)
    r_58 = ff.score_fidelity(facts, frac(0.58))   # climax+resolution deleted
    r_25 = ff.score_fidelity(facts, frac(0.25))   # only the first quarter kept

    assert r_full.fidelity >= 0.95, r_full.missing
    assert r_58.fidelity <= r_full.fidelity - 0.30
    assert r_25.fidelity < r_58.fidelity
    assert r_58.critical_loss and r_25.critical_loss


def test_passed_gates_on_min_fidelity_and_critical_loss():
    facts = [
        ff.Fact(id="c", desc="crit", patterns=[r"alpha"], severity="critical"),
        ff.Fact(id="n", desc="norm", patterns=[r"beta"], severity="normal"),
    ]
    # both present, above floor -> pass
    assert ff.passed(ff.score_fidelity(facts, "alpha beta"), min_fidelity=0.9) is True
    # a dropped CRITICAL fact fails even though fidelity (0.5) might clear a lax floor
    assert ff.passed(ff.score_fidelity(facts, "beta only"), min_fidelity=0.4) is False
    # below the fidelity floor fails even with no critical loss
    only_norm = [ff.Fact(id=f"n{i}", desc="", patterns=[r"zzz"]) for i in range(10)]
    assert ff.passed(ff.score_fidelity(only_norm, "nothing here"), min_fidelity=0.9) is False


def test_report_dict_enriches_missing_with_severity_and_desc():
    facts = [
        ff.Fact(id="kept", desc="present one", patterns=[r"alpha"], severity="normal"),
        ff.Fact(id="lost", desc="the antagonist reveal", patterns=[r"omega"], severity="critical"),
    ]
    d = ff.report_dict(ff.score_fidelity(facts, "alpha only"), facts)
    assert d["critical_loss"] is True
    assert d["present"] == ["kept"]
    assert d["missing"] == [{"id": "lost", "severity": "critical", "desc": "the antagonist reveal"}]


def test_cli_main_exit_codes(tmp_path, capsys):
    full = SAMPLE_REFERENCE.read_text(encoding="utf-8")
    # intact candidate passes
    intact = tmp_path / "intact.md"
    intact.write_text(full)
    assert ff.main([str(SAMPLE_INVENTORY), str(intact), "--min-fidelity", "0.9"]) == 0
    # climax-deleted candidate fails (critical loss)
    gutted = tmp_path / "gutted.md"
    gutted.write_text(full[: full.index("CLIMAX — ")])
    assert ff.main([str(SAMPLE_INVENTORY), str(gutted), "--min-fidelity", "0.9"]) == 1
