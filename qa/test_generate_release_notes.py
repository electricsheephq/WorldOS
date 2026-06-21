#!/usr/bin/env python3
"""Tests for qa/generate_release_notes.py + the scores_db release_readiness_verdict emitter.

Every test seeds a TEMP scores.db (tmp_path) — NEVER the committed qa/scores.db (the additive,
read-only invariant). The two load-bearing cases:
  * a SKIPPED (or otherwise not-PASSED) gate ⇒ STATUS: DEVELOPMENT
  * all 11 gates PASSED ⇒ STATUS: RELEASE

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_generate_release_notes.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import generate_release_notes as grn  # noqa: E402

ALL_GATES = scores_db.RRI_CANONICAL_GATES


def _seed_row(db, **over):
    """Append one RRI-bearing ledger row to a TEMP db (never the committed one)."""
    fields = dict(
        surface="GUI-built-app", ts="2026-06-19T00:00:00+00:00", build_sha="abc1234",
        dm_model="opus", scorer_model="claude", rc_label="v1.0.5-rc1",
        story_overall=4.4, mech_overall=4.6, behavioral="GREEN", rri=10.0,
        cross_persona_sat=7.5, critical_bugs=0,
        scoring_config_version="sc_deadbeef0000", lens_config_version="lc_deadbeef0000",
    )
    fields.update(over)
    scores_db.add_run("rri-test", db_path=db, **fields)


def _write_rri_json(path: Path, *, failed=None, skipped=None, gates_total=11, build_sha="abc1234"):
    """Write a minimal release_readiness.py-shaped RRI.json."""
    payload = {
        "rri": round(10.0 * (gates_total - len(failed or [])) / gates_total, 1) if gates_total else 0.0,
        "status": "READY" if not (failed or skipped) else "NOT_READY",
        "release_ready": not (failed or skipped),
        "build_sha": build_sha,
        "gates_total": gates_total,
        "failed_gates": failed or [],
        "skipped_gates": skipped or [],
        "gate_detail": {g: f"{g} detail" for g in ALL_GATES},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# scores_db.release_readiness_verdict — the 11-gate emitter
# --------------------------------------------------------------------------- #
def test_verdict_all_pass_is_release(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json")
    out = tmp_path / "verdict.json"
    verdict = scores_db.release_readiness_verdict(rri, db_path=db, out_path=out)
    assert verdict["status"] == "RELEASE"
    assert verdict["gates_passed"] == 11
    assert verdict["gates_total"] == 11
    assert all(verdict["gates"][g]["status"] == "PASSED" for g in ALL_GATES)
    # ruler provenance flows from the matching ledger row (matched by SHA)
    assert verdict["scoring_config_version"] == "sc_deadbeef0000"
    assert verdict["ruler_source"] == "scores_ledger"
    # also written to disk
    assert json.loads(out.read_text())["status"] == "RELEASE"


def test_verdict_one_skipped_is_development(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json", skipped=["palette_live"])
    verdict = scores_db.release_readiness_verdict(rri, db_path=db)
    assert verdict["status"] == "DEVELOPMENT"
    assert verdict["gates"]["palette_live"]["status"] == "SKIPPED"
    assert "palette_live" in verdict["gates_skipped"]
    assert "palette_live" in verdict["gates_not_passed"]


def test_verdict_one_failed_is_development(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json", failed=["mechanical"])
    verdict = scores_db.release_readiness_verdict(rri, db_path=db)
    assert verdict["status"] == "DEVELOPMENT"
    assert verdict["gates"]["mechanical"]["status"] == "FAILED"


def test_verdict_does_not_mutate_committed_db(tmp_path):
    """release_readiness_verdict is READ-ONLY: it never writes the db it reads."""
    db = tmp_path / "t.db"
    _seed_row(db)
    before = db.read_bytes()
    rri = _write_rri_json(tmp_path / "RRI.json")
    scores_db.release_readiness_verdict(rri, db_path=db)
    assert db.read_bytes() == before


# --------------------------------------------------------------------------- #
# generate_release_notes — the Markdown + DEVELOPMENT/RELEASE flag
# --------------------------------------------------------------------------- #
class _Args:
    """Minimal argparse.Namespace stand-in for build_notes()."""
    def __init__(self, **kw):
        self.tag = None
        self.milestone = None
        self.rc_label = None
        self.rri_json = None
        self.verdict_json = None
        self.build_sha = None
        self.repo = None
        self.no_issues = True  # offline by default in tests
        self.out = None
        for k, v in kw.items():
            setattr(self, k, v)


def test_notes_release_when_all_gates_pass(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json")
    notes = grn.build_notes(_Args(db=str(db), rri_json=str(rri), tag="v1.0.5", milestone="v1.0.5"))
    assert "**STATUS: RELEASE**" in notes
    assert "all 11 RRI gates PASSED" in notes
    assert "DEVELOPMENT" not in notes.split("---")[0]  # not in the body header


def test_notes_development_when_a_gate_is_skipped(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json", skipped=["behavioral"])
    notes = grn.build_notes(_Args(db=str(db), rri_json=str(rri), tag="v1.0.5"))
    assert "**STATUS: DEVELOPMENT**" in notes
    assert "skipped: behavioral" in notes
    assert "**STATUS: RELEASE**" not in notes


def test_notes_via_verdict_json(tmp_path):
    """A pre-emitted release_readiness_verdict.json drives the per-gate statuses directly."""
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json", failed=["zero_critical"])
    verdict_path = tmp_path / "verdict.json"
    scores_db.release_readiness_verdict(rri, db_path=db, out_path=verdict_path)
    notes = grn.build_notes(_Args(db=str(db), verdict_json=str(verdict_path)))
    assert "**STATUS: DEVELOPMENT**" in notes
    assert "failed: zero_critical" in notes


def test_notes_inferred_without_artifact_is_never_release(tmp_path):
    """With NO per-gate artifact, the ledger row alone can prove only a few gates; the rest are
    UNKNOWN, so an inferred verdict can NEVER claim RELEASE (the honesty guard)."""
    db = tmp_path / "t.db"
    _seed_row(db)  # great scores, but no RRI artifact
    notes = grn.build_notes(_Args(db=str(db), tag="v1.0.5"))
    assert "**STATUS: DEVELOPMENT**" in notes
    assert "ledger-inferred" in notes
    # the three provable gates show PASSED; the rest UNKNOWN
    assert "| `story_craft` | PASSED |" in notes
    assert "| `native_gate` | UNKNOWN |" in notes


def test_notes_milestone_summary_and_ruler_present(tmp_path):
    db = tmp_path / "t.db"
    _seed_row(db)
    rri = _write_rri_json(tmp_path / "RRI.json")
    notes = grn.build_notes(_Args(db=str(db), rri_json=str(rri), milestone="v1.0.5"))
    assert "## Milestone summary" in notes
    assert "## Ruler versions" in notes
    assert "sc_deadbeef0000" in notes
    assert "lc_deadbeef0000" in notes
    assert "## RRI gate results (11 canonical gates)" in notes


def test_development_or_release_helper():
    all_pass = {g: "PASSED" for g in ALL_GATES}
    assert grn.development_or_release(all_pass) == ("RELEASE", [])
    one_skip = dict(all_pass, palette_live="SKIPPED")
    status, not_passed = grn.development_or_release(one_skip)
    assert status == "DEVELOPMENT"
    assert not_passed == ["palette_live"]
