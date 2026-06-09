"""Tests for the comparable-scoring layer (RRI 2026-06-09 reorientation): every score must be
stamped with the scoring RULER that produced it, and the ledger must FENCE comparisons by ruler so
"we used to hit 4.5, now 3.6" can't silently line up scores from different rubrics/gate-sets.

Stdlib + pytest only (sqlite3, tempfile). Run:
    uv run --directory servers/engine python -m pytest qa/test_scores_db_comparability.py -q -p no:xdist
"""
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

_QA = Path(__file__).resolve().parent
sys.path.insert(0, str(_QA))

import scoring_config_version as scv  # noqa: E402
import scores_db  # noqa: E402


# --- the ruler hash: deterministic + content-sensitive --------------------------------------
def test_version_is_deterministic_and_prefixed():
    v1 = scv.scoring_config_version()
    v2 = scv.scoring_config_version()
    assert v1 == v2, "same files must hash identically"
    assert v1.startswith("sc_") and len(v1) == 15, v1  # 'sc_' + 12 hex


def test_version_changes_when_any_config_file_changes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for name in scv.SCORING_CONFIG_FILES:
            (root / name).write_text("baseline\n", encoding="utf-8")
        base = scv.scoring_config_version(root)
        # Editing a single rubric anchor must re-version (the stingy-recalibration case).
        (root / "rubric_tolkien.md").write_text("STINGY recalibration\n", encoding="utf-8")
        after = scv.scoring_config_version(root)
        assert after != base, "a rubric edit must change the ruler hash"
        # Adding a behavioral gate (file content change) must re-version too.
        (root / "assert_behavioral.py").write_text("# +1 new gate\n", encoding="utf-8")
        assert scv.scoring_config_version(root) != after, "a gate change must re-version"


def test_absent_file_is_versioned_distinctly():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for name in scv.SCORING_CONFIG_FILES:
            (root / name).write_text("x\n", encoding="utf-8")
        full = scv.scoring_config_version(root)
        (root / "release_readiness.py").unlink()  # a deleted/renamed gate must change the hash
        assert scv.scoring_config_version(root) != full


# --- add_run auto-stamps the ruler ----------------------------------------------------------
def test_add_run_auto_stamps_ruler():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("r1", db_path=db, surface="engine-duo", dm_model="opus", story_overall=4.1)
        rows = scores_db.fetch_rows(db)
        assert len(rows) == 1
        r = rows[0]
        assert r["scoring_config_version"] == scv.scoring_config_version(), "must stamp current ruler"
        assert r["rubric_label"], "must stamp a human ruler label"


def test_add_run_respects_an_explicitly_pinned_ruler():
    """Backfilling a re-scored OLD transcript pins the ruler used THEN — auto-stamp must not clobber it."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("old", db_path=db, surface="engine-duo", story_overall=4.5,
                          scoring_config_version="sc_oldruler0000", rubric_label="ruler@old")
        r = scores_db.fetch_rows(db)[0]
        assert r["scoring_config_version"] == "sc_oldruler0000"
        assert r["rubric_label"] == "ruler@old"


# --- compare_rc FENCES by ruler -------------------------------------------------------------
def test_compare_fences_distinct_rulers():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        # Two engine-duo runs scored under DIFFERENT rulers (the 4.5-era vs the stingy era).
        scores_db.add_run("a", db_path=db, surface="engine-duo", dm_model="opus", story_overall=4.5,
                          scoring_config_version="sc_lenient00000", rc_label="legacy")
        scores_db.add_run("b", db_path=db, surface="engine-duo", dm_model="opus", story_overall=4.0,
                          scoring_config_version="sc_stingy000000", rc_label="v1.0.4-rc1")
        out = scores_db.compare_rc(db)
        assert "sc_lenient00000" in out and "sc_stingy000000" in out
        assert out.count("=== ruler ") == 2, "each ruler is its own fenced block"
        assert "NOT directly" in out, "must warn that cross-ruler numbers aren't comparable"


def test_compare_rc_filter_restricts_to_one_candidate():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("a", db_path=db, surface="engine-duo", story_overall=4.0, rc_label="v1.0.4-rc1")
        scores_db.add_run("b", db_path=db, surface="engine-duo", story_overall=4.2, rc_label="v1.0.4-rc2")
        out = scores_db.compare_rc(db, rc="v1.0.4-rc2")
        assert "v1.0.4-rc2" in out and "v1.0.4-rc1" not in out


def test_compare_excludes_non_engine_duo_surfaces():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("gui", db_path=db, surface="GUI-built-app", rri=6.0)
        scores_db.add_run("duo", db_path=db, surface="engine-duo", story_overall=4.1)
        out = scores_db.compare_rc(db)
        assert "duo" in out and "gui" not in out, "the quality trend is engine-duo only"


# --- schema migration is additive -----------------------------------------------------------
def test_new_columns_present_and_old_schema_migrates():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        # Simulate an OLD db that predates the comparability columns.
        raw = sqlite3.connect(str(db))
        raw.execute('CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT, "story_overall" REAL)')
        raw.execute('INSERT INTO runs (run_id, surface, story_overall) VALUES ("legacy","engine-duo",4.5)')
        raw.commit(); raw.close()
        # connect() must ALTER IN the new columns; the old row reads NULL for them.
        conn = scores_db.connect(db)
        cols = {row[1] for row in conn.execute("pragma table_info(runs)")}
        conn.close()
        for c in ("scoring_config_version", "rubric_label", "rc_label"):
            assert c in cols, f"{c} must be migrated into an old db"
        legacy = [r for r in scores_db.fetch_rows(db) if r["run_id"] == "legacy"][0]
        assert legacy["scoring_config_version"] is None, "pre-versioning rows read NULL (honest)"
