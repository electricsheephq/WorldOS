#!/usr/bin/env python3
"""Tests for qa/detect_regression.py — the candidate-vs-canonical-baseline regression signal.

Run (single-process):
    uv run --directory servers/engine python -m pytest qa/test_detect_regression.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import detect_regression as dr  # noqa: E402
import scores_db  # noqa: E402

# A comparability key (surface, dm_model, methodology, lens_config_version) all four runs share.
KEY = dict(
    surface="engine-duo",
    dm_model="opus",
    methodology="duo-8beat",
    lens_config_version="lc_testfloor1",
)


def _seed_baseline(db, **overrides):
    """Add a behaviorally-GREEN baseline run and mark it canonical. Returns its run_id."""
    fields = dict(story_overall=4.0, mech_overall=3.6, angrydm_overall=3.4, behavioral="GREEN", **{"pass": 1})
    fields.update(overrides)
    scores_db.add_run("baseline-1", db_path=db, **KEY, **fields)
    scores_db.set_canonical_baseline("baseline-1", db_path=db)
    return "baseline-1"


def test_no_baseline_when_none_set(tmp_path):
    db = tmp_path / "s.db"
    scores_db.add_run("cand", db_path=db, **KEY, story_overall=4.0, behavioral="GREEN")
    out = dr.detect_regression({"run_id": "cand", **KEY, "story_overall": 4.0, "behavioral": "GREEN"}, db_path=db)
    assert out["verdict"] == "NO_BASELINE"
    assert out["baseline_run"] is None


def test_improved_beyond_floor(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)  # story 4.0
    cand = {"run_id": "c", **KEY, "story_overall": 4.6, "mech_overall": 3.6, "angrydm_overall": 3.4, "behavioral": "GREEN"}
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "IMPROVED"
    story = next(p for p in out["per_lens"] if p["lens"] == "story_overall")
    assert story["classification"] == "IMPROVED"
    assert story["delta"] == pytest.approx(0.6)


def test_regressed_beyond_floor(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)  # story 4.0
    cand = {"run_id": "c", **KEY, "story_overall": 3.4, "mech_overall": 3.6, "angrydm_overall": 3.4, "behavioral": "GREEN"}
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "REGRESSED"
    story = next(p for p in out["per_lens"] if p["lens"] == "story_overall")
    assert story["classification"] == "REGRESSED"


def test_within_noise_is_not_a_regression(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)  # story 4.0 mech 3.6 angry 3.4
    # All deltas inside the per-lens range floors (story 0.40 / mech 0.60 / angry 0.80).
    cand = {"run_id": "c", **KEY, "story_overall": 4.2, "mech_overall": 3.5, "angrydm_overall": 3.7, "behavioral": "GREEN"}
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "WITHIN_NOISE"
    assert all(p["classification"] == "WITHIN_NOISE" for p in out["per_lens"] if p["delta"] is not None)


def test_behavioral_red_is_regression_even_if_lenses_within_noise(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)
    cand = {"run_id": "c", **KEY, "story_overall": 4.1, "mech_overall": 3.6, "angrydm_overall": 3.4, "behavioral": "RED"}
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "REGRESSED"
    assert out["behavioral"]["regressed"] is True


def test_ruler_fencing_different_lens_config_is_not_comparable(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)  # lens_config_version lc_testfloor1
    # Candidate scored under a DIFFERENT lens ruler -> the canonical baseline does NOT apply.
    cand = dict(KEY)
    cand["lens_config_version"] = "lc_OTHER"
    cand.update({"run_id": "c", "story_overall": 3.0, "behavioral": "GREEN"})
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "NO_BASELINE"  # cross-ruler comparison is refused, not a false REGRESSED


def test_missing_lens_is_no_data_not_false_regression(tmp_path):
    db = tmp_path / "s.db"
    _seed_baseline(db)
    # Candidate only scored story; mech/angry absent -> NO_DATA for those, verdict from story alone.
    cand = {"run_id": "c", **KEY, "story_overall": 4.1, "behavioral": "GREEN"}
    out = dr.detect_regression(cand, db_path=db)
    assert out["verdict"] == "WITHIN_NOISE"
    mech = next(p for p in out["per_lens"] if p["lens"] == "mech_overall")
    assert mech["classification"] == "NO_DATA"


def test_cli_candidate_by_run_id_json_and_exit_codes(tmp_path, capsys):
    db = tmp_path / "s.db"
    _seed_baseline(db)
    scores_db.add_run("cand-regressed", db_path=db, **KEY, story_overall=3.3, mech_overall=3.6, angrydm_overall=3.4, behavioral="GREEN")
    rc = dr.main(["--candidate", "cand-regressed", "--db", str(db), "--json"])
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["verdict"] == "REGRESSED"
    assert payload["candidate_run"] == "cand-regressed"
    assert payload["baseline_run"] == "baseline-1"
    assert rc == 2  # REGRESSED -> exit 2 (so CI / the agent can gate on it)


def test_cli_no_baseline_exit_3(tmp_path, capsys):
    db = tmp_path / "s.db"
    scores_db.add_run("lonely", db_path=db, **KEY, story_overall=4.0, behavioral="GREEN")
    rc = dr.main(["--candidate", "lonely", "--db", str(db), "--json"])
    assert rc == 3  # NO_BASELINE -> advisory exit 3 (distinct from regression)
