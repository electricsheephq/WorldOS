"""Tests for the comparable-scoring layer (RRI 2026-06-09 reorientation): every score must be
stamped with the scoring RULER that produced it, and the ledger must FENCE comparisons by ruler so
"we used to hit 4.5, now 3.6" can't silently line up scores from different rubrics/gate-sets.

Stdlib + pytest only (sqlite3, tempfile). Run:
    uv run --directory servers/engine python -m pytest qa/test_scores_db_comparability.py -q -p no:xdist
"""
import importlib.util
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

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
        for c in ("scoring_config_version", "rubric_label", "rc_label", "lens_config_version"):
            assert c in cols, f"{c} must be migrated into an old db"
        legacy = [r for r in scores_db.fetch_rows(db) if r["run_id"] == "legacy"][0]
        assert legacy["scoring_config_version"] is None, "pre-versioning rows read NULL (honest)"
        assert legacy["lens_config_version"] is None, "pre-lens rows read NULL (honest)"


# --- #725: the LENS ruler (rubrics + schemas + behavioral gate, NOT the RRI gate) ------------
def test_lens_files_are_scoring_files_minus_rri_gate():
    """The lens ruler = exactly the 8 files that produce the LENS numbers. release_readiness.py
    (the 11-gate RRI) is deliberately excluded — an RRI-gate-only edit must not re-fence the
    engine-duo lens trend (issue #725)."""
    assert set(scv.LENS_CONFIG_FILES) == set(scv.SCORING_CONFIG_FILES) - {"release_readiness.py"}
    assert len(scv.LENS_CONFIG_FILES) == 8


def test_lens_version_unchanged_when_only_rri_gate_changes():
    """The #725 false-fence case: a release_readiness.py-only edit (like #723/#728) re-versions
    the FULL ruler but must NOT re-version the lens ruler."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for name in scv.SCORING_CONFIG_FILES:
            (root / name).write_text("baseline\n", encoding="utf-8")
        full_before = scv.scoring_config_version(root)
        lens_before = scv.lens_config_version(root)
        assert lens_before.startswith("lc_") and len(lens_before) == 15, lens_before
        (root / "release_readiness.py").write_text("# RRI gate hardened\n", encoding="utf-8")
        assert scv.scoring_config_version(root) != full_before, "full ruler must re-version"
        assert scv.lens_config_version(root) == lens_before, (
            "an RRI-gate-only edit must NOT re-version the lens ruler (#725)"
        )


def test_lens_version_changes_when_a_rubric_or_behavioral_gate_changes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for name in scv.SCORING_CONFIG_FILES:
            (root / name).write_text("baseline\n", encoding="utf-8")
        base = scv.lens_config_version(root)
        (root / "rubric_tolkien.md").write_text("STINGY recalibration\n", encoding="utf-8")
        after_rubric = scv.lens_config_version(root)
        assert after_rubric != base, "a rubric edit must re-version the lens ruler"
        # The behavioral gate caps every lens to <=2.5 — it IS part of what a lens number means
        # (the exact #739 case that split rc1/rc2).
        (root / "assert_behavioral.py").write_text("# +1 gate\n", encoding="utf-8")
        assert scv.lens_config_version(root) != after_rubric, "a behavioral-gate edit must re-version"


def test_add_run_stamps_both_rulers():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("r1", db_path=db, surface="engine-duo", story_overall=4.1)
        r = scores_db.fetch_rows(db)[0]
        assert r["scoring_config_version"] == scv.scoring_config_version()
        assert r["lens_config_version"] == scv.lens_config_version()


def test_pinned_full_ruler_leaves_lens_null_not_falsely_current():
    """Backfilling an OLD run pins the full ruler used THEN. Auto-stamping TODAY's lens hash onto
    it would be a new false claim — the honest value is NULL (unknown), which compare_rc then
    falls back on."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("old", db_path=db, surface="engine-duo", story_overall=4.5,
                          scoring_config_version="sc_oldruler0000")
        r = scores_db.fetch_rows(db)[0]
        assert r["lens_config_version"] is None
        # ...but an explicitly pinned lens version is respected.
        scores_db.add_run("old2", db_path=db, surface="engine-duo", story_overall=4.4,
                          scoring_config_version="sc_oldruler0000", lens_config_version="lc_oldlens00000")
        r2 = [x for x in scores_db.fetch_rows(db) if x["run_id"] == "old2"][0]
        assert r2["lens_config_version"] == "lc_oldlens00000"


# --- the notes/stamp consistency guard (the rc2 false-claim class) ---------------------------
def test_add_run_rejects_notes_citing_a_different_ruler_hash():
    """The exact rc2 incident: notes claimed 'Same ruler sc_5ac7a1d9103c as rc1' while the row
    was stamped sc_df34ecd02b4f. add_run must refuse to record such a row."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        with pytest.raises(ValueError, match="sc_5ac7a1d9103c"):
            scores_db.add_run("rc2", db_path=db, surface="GUI-built-app", rri=2.7,
                              scoring_config_version="sc_df34ecd02b4f",
                              notes="Same ruler sc_5ac7a1d9103c as rc1 (apples-to-apples).")


def test_add_run_allows_notes_citing_the_rows_own_ruler_hash():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("ok", db_path=db, surface="GUI-built-app", rri=3.6,
                          scoring_config_version="sc_ab12cd34ef56",
                          notes="Scored under ruler sc_ab12cd34ef56.")
        assert scores_db.fetch_rows(db)[0]["run_id"] == "ok"


def test_add_run_rejects_notes_citing_a_different_lens_hash():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        with pytest.raises(ValueError, match="lc_000000000bad"):
            scores_db.add_run("bad-lens", db_path=db, surface="engine-duo", story_overall=4.0,
                              scoring_config_version="sc_ab12cd34ef56",
                              lens_config_version="lc_ab12cd34ef56",
                              notes="Same lens lc_000000000bad as before.")


def test_guard_catches_auto_stamped_mismatch_too():
    """No pinning: the stamp is the CURRENT ruler, so citing a stale hash must still raise."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        stale = "sc_000000000000"
        assert scv.scoring_config_version() != stale
        with pytest.raises(ValueError):
            scores_db.add_run("r", db_path=db, surface="engine-duo", story_overall=4.0,
                              notes=f"same ruler {stale} as last time")


# --- #725: compare_rc fences the engine-duo trend on the LENS ruler --------------------------
def test_compare_fences_engine_duo_on_lens_not_full_ruler():
    """Two duos with IDENTICAL lens ruler but different full rulers (an RRI-gate-only edit in
    between, the #725 case) must land in ONE block — same lens scoring, same trend."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("a", db_path=db, surface="engine-duo", story_overall=4.0,
                          scoring_config_version="sc_pre0rri0edit", lens_config_version="lc_samelens0000")
        scores_db.add_run("b", db_path=db, surface="engine-duo", story_overall=4.1,
                          scoring_config_version="sc_post0rri0edt", lens_config_version="lc_samelens0000")
        out = scores_db.compare_rc(db)
        assert out.count("=== ruler ") == 1, "same lens ruler => ONE comparable block:\n" + out
        assert "lc_samelens0000" in out


def test_compare_falls_back_to_full_ruler_for_pre_lens_rows():
    """Rows recorded before lens stamping (lens NULL) fence on their full ruler — conservative:
    may split more than strictly needed, never falsely merges."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("old", db_path=db, surface="engine-duo", story_overall=4.5,
                          scoring_config_version="sc_lenient00000")  # lens stays NULL
        scores_db.add_run("new", db_path=db, surface="engine-duo", story_overall=4.0,
                          scoring_config_version="sc_stingy000000", lens_config_version="lc_stingy000000")
        out = scores_db.compare_rc(db)
        assert out.count("=== ruler ") == 2
        assert "sc_lenient00000" in out and "lc_stingy000000" in out


def test_compare_rc_surface_block_is_opt_in():
    """--compare-rc-surface: GUI-built-app RC rows appear in their OWN fenced block (fenced on
    the FULL ruler — the RRI is produced by release_readiness.py), separate from the engine-duo
    lens trend; absent by default."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        scores_db.add_run("duo-x", db_path=db, surface="engine-duo", story_overall=4.1)
        scores_db.add_run("rc1-row", db_path=db, surface="GUI-built-app", rri=3.6,
                          rc_label="v1.0.4-rc1", scoring_config_version="sc_aaaaaaaaaaaa")
        scores_db.add_run("rc2-row", db_path=db, surface="GUI-built-app", rri=2.7,
                          rc_label="v1.0.4-rc2", scoring_config_version="sc_bbbbbbbbbbbb")
        out_default = scores_db.compare_rc(db)
        assert "rc1-row" not in out_default and "RC surface" not in out_default
        out = scores_db.compare_rc(db, include_rc_surface=True)
        assert "RC surface" in out
        assert "v1.0.4-rc1" in out and "v1.0.4-rc2" in out
        assert out.count("rc-surface ruler") == 2, "different FULL rulers => separate RC blocks:\n" + out


# --- the shipped ledger itself stays honest (the amended rc1/rc2 rows) -----------------------
def test_shipped_db_has_no_foreign_ruler_claims_in_notes():
    """Negative disclosure over the REAL committed qa/scores.db: no row's notes may cite a ruler
    hash that differs from the row's stamp (the rc2 'apples-to-apples' false claim, amended
    2026-06-10)."""
    if not scores_db.DB_PATH.is_file():
        pytest.skip("no committed scores.db")
    for r in scores_db.fetch_rows(scores_db.DB_PATH):
        notes = r.get("notes") or ""
        for h in re.findall(r"\bsc_[0-9a-f]{12}\b", notes):
            assert h == r.get("scoring_config_version"), (
                f"run {r['run_id']!r} notes cite {h} but the row is stamped "
                f"{r.get('scoring_config_version')!r}"
            )


def test_shipped_rc1_rc2_rows_carry_the_amended_truth():
    if not scores_db.DB_PATH.is_file():
        pytest.skip("no committed scores.db")
    rows = {r["run_id"]: r for r in scores_db.fetch_rows(scores_db.DB_PATH)}
    rc1 = rows.get("v1.0.4-rc1-fa97b34")
    rc2 = rows.get("v1.0.4-rc2-c92a393")
    if rc1 is None or rc2 is None:
        pytest.skip("rc1/rc2 rows not in this db")
    # rc1: axe never ran on the VM (browser-driver-manager missing, silent WARN-skip) — the
    # actual ui_audit failures were launcher play_reachable + merchant art placeholders.
    assert "FAIL(axe)" not in (rc1["notes"] or "")
    assert "axe never ran" in (rc1["notes"] or "").lower()
    # rc2: #739 changed assert_behavioral.py between recordings — cross-ruler, NOT apples-to-apples.
    assert "apples-to-apples)" not in (rc2["notes"] or "") or "NOT apples-to-apples" in (rc2["notes"] or "")
    assert "Same ruler sc_" not in (rc2["notes"] or "")
    assert "CROSS-RULER" in (rc2["notes"] or "")
