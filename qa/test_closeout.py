#!/usr/bin/env python3
"""Tests for qa/closeout.py — the standardized closeout-block emitter.

Pure-stdlib (sqlite3 via scores_db + json); imports neither the engine nor the viewer. EVERY test
seeds its own TEMP scores.db (``tmp_path``) — the committed ``qa/scores.db`` is NEVER touched. Run:

    uv run --directory servers/engine --group dev python -m pytest ../../qa/test_closeout.py -q -p no:xdist
or simply:
    python3 -m pytest qa/test_closeout.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import closeout  # noqa: E402

# A fixed ruler pair for the "comparable" runs, and a DIFFERENT pair for the ruler-fence test.
RULER_A = ("sc_aaaaaaaaaaaa", "lc_aaaaaaaaaaaa")
RULER_B = ("sc_bbbbbbbbbbbb", "lc_bbbbbbbbbbbb")


def _seed(db):
    """Seed a temp ledger: two comparable opus engine-duo runs (older=base, newer=current) under
    RULER_A, plus one different-ruler run (RULER_B) on the same surface+model (the fence must
    EXCLUDE it), plus one non-opus GUI run (the ⚠ flag test)."""
    # older comparable (the expected Δ baseline)
    scores_db.add_run(
        "duo-base", db_path=db, ts="2026-06-10T08:00:00+00:00", build_date="2026-06-10",
        surface="engine-duo", dm_model="opus", actor_model="sonnet", scorer_model="claude",
        methodology="3-lens duo 8-beat", story_overall=4.0, mech_overall=3.5, angrydm_overall=2.5,
        behavioral="GREEN", acts_reached=2,
        structural_coverage="recruit ✓ travel ✓ combat ✓ quest-resolved · betrayal ·",
        scoring_config_version=RULER_A[0], lens_config_version=RULER_A[1],
        notes="older comparable baseline", **{"pass": 0},
    )
    # newer comparable (the run under test) — SAME surface+dm_model+ruler -> compares to duo-base
    scores_db.add_run(
        "duo-current", db_path=db, ts="2026-06-17T08:00:00+00:00", build_date="2026-06-17",
        surface="engine-duo", dm_model="opus", actor_model="sonnet", scorer_model="claude",
        methodology="3-lens duo 8-beat", story_overall=4.3, mech_overall=3.9, angrydm_overall=2.8,
        behavioral="GREEN", acts_reached=3,
        structural_coverage="recruit ✓ travel ✓ combat ✓ quest-resolved ✓ betrayal ·",
        scoring_config_version=RULER_A[0], lens_config_version=RULER_A[1],
        notes="run under test", **{"pass": 1},
    )
    # a DIFFERENT-ruler run, NEWER than duo-current but on the same surface+model. The fence must
    # NOT pick this as duo-current's comparable (different ruler), and this run itself has no prior
    # comparable in the seed.
    scores_db.add_run(
        "duo-diff-ruler", db_path=db, ts="2026-06-18T08:00:00+00:00", build_date="2026-06-18",
        surface="engine-duo", dm_model="opus", actor_model="sonnet", scorer_model="claude",
        methodology="3-lens duo 8-beat", story_overall=4.9, mech_overall=4.6,
        behavioral="GREEN", acts_reached=3,
        scoring_config_version=RULER_B[0], lens_config_version=RULER_B[1],
        notes="different ruler — must be fenced out", **{"pass": 1},
    )
    # a NON-OPUS GUI run (the ⚠ flag).
    scores_db.add_run(
        "gui-sonnet", db_path=db, ts="2026-06-16T08:00:00+00:00", build_date="2026-06-16",
        surface="GUI-headless-proxy", dm_model="sonnet", actor_model="sonnet", scorer_model="claude",
        methodology="5-persona part-B", story_overall=4.2, mech_overall=3.5,
        behavioral="GREEN", cross_persona_sat=7.0, rri=6.4, critical_bugs=0,
        scoring_config_version=RULER_A[0], lens_config_version=RULER_A[1],
        notes="sonnet GUI sweep", **{"pass": 0},
    )


def test_block_has_core_lines(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    block = closeout.build_closeout("duo-current", db_path=db)
    assert "RUN: duo-current | 2026-06-17 | engine-duo" in block
    assert "MODEL: DM=opus · actor=sonnet · scorer=claude" in block
    # opus DM -> NO non-opus flag
    assert "NON-OPUS DM" not in block
    assert "RULER: sc_aaaaaaaaaaaa/lc_aaaaaaaaaaaa" in block
    assert "SCORES: story 4.3 · mech 3.9 · angrydm 2.8 · behavioral GREEN" in block
    assert "structural PASS" in block  # pass==1 -> PASS
    assert "UNIVERSE: 3-lens duo 8-beat · 8 beats" in block  # beats parsed from methodology


def test_delta_vs_correct_prior(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    block = closeout.build_closeout("duo-current", db_path=db)
    # Δ must be computed vs duo-base (the prior comparable), NOT duo-diff-ruler.
    assert "duo-base" in block
    assert "duo-diff-ruler" not in block
    # story 4.3 - 4.0 = +0.3 ; mech 3.9 - 3.5 = +0.4
    assert "story +0.3 mech +0.4" in block


def test_ruler_fence_excludes_different_ruler(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rows = scores_db.fetch_rows(db)
    cur = closeout.find_run(rows, "duo-current")
    prior = closeout.last_comparable(rows, cur)
    assert prior is not None and prior["run_id"] == "duo-base"
    # the different-ruler run itself has no comparable prior in the seed (only run under its ruler).
    diff = closeout.find_run(rows, "duo-diff-ruler")
    assert closeout.last_comparable(rows, diff) is None
    diff_block = closeout.build_closeout("duo-diff-ruler", db_path=db)
    assert "no comparable prior run" in diff_block


def test_nonopus_dm_flagged(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    block = closeout.build_closeout("gui-sonnet", db_path=db)
    assert "⚠ NON-OPUS DM" in block
    assert "DM=sonnet" in block


def test_surface_fence_excludes_other_surface(tmp_path):
    """A run only compares within its OWN surface — the engine-duo runs must not be the GUI run's
    comparable even though they share dm_model is moot (different surface AND model here)."""
    db = tmp_path / "t.db"
    _seed(db)
    rows = scores_db.fetch_rows(db)
    gui = closeout.find_run(rows, "gui-sonnet")
    assert closeout.last_comparable(rows, gui) is None  # no other GUI-sonnet run


def test_missing_run_raises_and_cli_errors(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    with pytest.raises(KeyError):
        closeout.build_closeout("nope", db_path=db)
    # CLI returns non-zero and does not crash.
    rc = closeout.main(["--db", str(db), "nope"])
    assert rc == 1


def test_cli_list(tmp_path, capsys):
    db = tmp_path / "t.db"
    _seed(db)
    rc = closeout.main(["--db", str(db), "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    for rid in ("duo-current", "duo-base", "duo-diff-ruler", "gui-sonnet"):
        assert rid in out


def test_verdict_pass_when_both_bars_met(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run(
        "duo-clean", db_path=db, surface="engine-duo", dm_model="opus",
        story_overall=4.5, mech_overall=4.6, methodology="duo 12-beat",
        scoring_config_version=RULER_A[0], lens_config_version=RULER_A[1], **{"pass": 1},
    )
    block = closeout.build_closeout("duo-clean", db_path=db)
    assert "VERDICT: pass vs bar" in block


def test_verdict_inconclusive_when_unmeasured(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run(
        "duo-nomech", db_path=db, surface="engine-duo", dm_model="opus",
        story_overall=4.5, methodology="duo 6-beat",
        scoring_config_version=RULER_A[0], lens_config_version=RULER_A[1],
    )
    block = closeout.build_closeout("duo-nomech", db_path=db)
    assert "VERDICT: inconclusive" in block
    assert "mech ?" in block  # missing mech renders as ?


def test_coverage_line_parses_tokens(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    block = closeout.build_closeout("duo-current", db_path=db)
    # explicit-token roll-up: recruit/travel/combat/quest-resolved ✓, betrayal ·, acts 3/3.
    cov = [ln for ln in block.splitlines() if ln.startswith("COVERAGE:")][0]
    assert "recruit ✓" in cov and "quest-resolved ✓" in cov
    assert "betrayal ·" in cov
    assert "acts 3/3" in cov


def test_committed_db_untouched_by_import(tmp_path):
    """Guard: closeout is a pure reader — building a block from a temp db must not write the
    committed qa/scores.db (defends the 'never mutate the committed ledger' rule)."""
    committed = QA_DIR / "scores.db"
    before = committed.stat().st_mtime_ns if committed.exists() else None
    db = tmp_path / "t.db"
    _seed(db)
    closeout.build_closeout("duo-current", db_path=db)
    after = committed.stat().st_mtime_ns if committed.exists() else None
    assert before == after  # mtime unchanged (or both None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:xdist"]))
