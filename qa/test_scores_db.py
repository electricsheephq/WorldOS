#!/usr/bin/env python3
"""Tests for the canonical scores ledger tooling (qa/scores_db.py + the forensic seed).

Pure-stdlib (sqlite3 + json); imports neither the engine nor the viewer. Run with:
    uv run --directory servers/engine python -m pytest qa/test_scores_db.py -q -p no:xdist
or simply:
    python3 -m pytest qa/test_scores_db.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import scores_seed_forensics  # noqa: E402


def test_add_run_and_fetch(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run(
        "duo-x", db_path=db, surface="engine-duo", ts="2026-05-29T10:00:00+00:00",
        build_sha="abc1234", story_overall=4.2, mech_overall=3.8, behavioral="GREEN",
    )
    rows = scores_db.fetch_rows(db)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "duo-x"
    assert rows[0]["surface"] == "engine-duo"
    assert rows[0]["story_overall"] == 4.2
    assert rows[0]["behavioral"] == "GREEN"


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(ValueError):
        scores_db.add_run("x", db_path=tmp_path / "t.db", storyz=4.0)  # typo'd field


def test_bad_surface_rejected(tmp_path):
    with pytest.raises(ValueError):
        scores_db.add_run("x", db_path=tmp_path / "t.db", surface="not-a-surface")


def test_all_real_surfaces_accepted(tmp_path):
    db = tmp_path / "t.db"
    for i, s in enumerate(scores_db.SURFACES):
        scores_db.add_run(f"r{i}", db_path=db, surface=s)
    assert len(scores_db.fetch_rows(db)) == len(scores_db.SURFACES)


def test_per_persona_json_roundtrips(tmp_path):
    db = tmp_path / "t.db"
    payload = {"newbie": {"sat": 7, "gaveup": False}}
    scores_db.add_run("r", db_path=db, surface="GUI-headless-proxy", per_persona_json=payload)
    stored = scores_db.fetch_rows(db)[0]["per_persona_json"]
    assert json.loads(stored) == payload


def test_replace_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("dup", db_path=db, surface="engine-duo", story_overall=4.0)
    scores_db.add_run("dup", db_path=db, surface="engine-duo", story_overall=4.3)
    rows = scores_db.fetch_rows(db)
    assert len(rows) == 1 and rows[0]["story_overall"] == 4.3  # last write wins


def test_newest_first_ordering(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("old", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00")
    scores_db.add_run("new", db_path=db, surface="engine-duo", ts="2026-05-30T00:00:00+00:00")
    rows = scores_db.fetch_rows(db)
    assert [r["run_id"] for r in rows] == ["new", "old"]


def test_pass_bool_coerced_to_int(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("p", db_path=db, surface="GUI-built-app", **{"pass": True})
    assert scores_db.fetch_rows(db)[0]["pass"] == 1


# --- F13-4: the latency ledger columns (s_per_beat / coldopen_s / turns_per_beat) ---

def test_latency_columns_are_registered_and_real(tmp_path):
    # The three F13-4 columns exist, are REAL-typed, and round-trip a write.
    for col in ("s_per_beat", "coldopen_s", "turns_per_beat"):
        assert col in scores_db.COLUMNS, f"{col} missing from COLUMNS"
        assert scores_db._coltype(col) == "REAL"
    db = tmp_path / "t.db"
    scores_db.add_run(
        "duo-lat", db_path=db, surface="engine-duo",
        s_per_beat=80.5, coldopen_s=174.0, turns_per_beat=4.3,
    )
    row = scores_db.fetch_rows(db)[0]
    assert row["s_per_beat"] == 80.5
    assert row["coldopen_s"] == 174.0
    assert row["turns_per_beat"] == 4.3


def test_latency_columns_read_null_on_pre_f134_rows(tmp_path):
    # ADDITIVITY: a row recorded WITHOUT the latency fields (a pre-F13-4 run) reads back
    # NULL for all three — the new columns never break an old write.
    db = tmp_path / "t.db"
    scores_db.add_run("duo-old", db_path=db, surface="engine-duo", story_overall=4.1)
    row = scores_db.fetch_rows(db)[0]
    assert row["s_per_beat"] is None
    assert row["coldopen_s"] is None
    assert row["turns_per_beat"] is None


def test_latency_columns_alter_into_an_old_db(tmp_path):
    # A db created before F13-4 (no latency columns) gets them ALTER-added on connect.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT, "story_overall" REAL)')
    conn.commit()
    conn.close()
    conn = scores_db.connect(db)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert {"s_per_beat", "coldopen_s", "turns_per_beat"} <= cols


def test_render_markdown_contains_header_and_rows(tmp_path):
    db = tmp_path / "t.db"
    md = tmp_path / "led.md"
    scores_db.add_run("rendered-run", db_path=db, surface="engine-duo", story_overall=4.1)
    text = scores_db.render_markdown(db, md)
    assert "Canonical Scores Ledger" in text
    assert "rendered-run" in text
    assert md.exists()
    # render is deterministic apart from the timestamp line
    a = [l for l in text.splitlines() if not l.startswith("> Rows") and "rendered" not in l]
    b = [l for l in scores_db.render_markdown(db, md).splitlines()
         if not l.startswith("> Rows") and "rendered" not in l]
    assert a == b


def test_schema_migration_adds_missing_column(tmp_path):
    """An old db missing a newer COLUMNS entry gets it added (additive migration)."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT)')
    conn.commit()
    conn.close()
    # connect() must ALTER in every other column without error
    conn = scores_db.connect(db)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert "story_overall" in cols and "notes" in cols and "rri" in cols


# --- the forensic seed itself (proves the reconstruction is reproducible) ---

def test_seed_is_reproducible_and_classified(tmp_path):
    """Seeding into a fresh db yields the expected surface distribution and is idempotent."""
    db = tmp_path / "scores.db"
    md = tmp_path / "scores_ledger.md"

    n1 = scores_seed_forensics.seed(db_path=db, md_path=md)
    rows = scores_db.fetch_rows(db)
    assert n1 == len(rows) >= 50  # the full historical reconstruction

    surfaces = {}
    for r in rows:
        surfaces[r["surface"]] = surfaces.get(r["surface"], 0) + 1
    # every row is classified into exactly the four-surface taxonomy
    assert set(surfaces) <= set(scores_db.SURFACES)
    assert surfaces.get("engine-duo", 0) >= 25      # the "4.x" numbers
    assert surfaces.get("GUI-headless-proxy", 0) >= 5
    assert surfaces.get("GUI-built-app", 0) >= 5

    # re-seeding is idempotent (INSERT OR REPLACE keyed on run_id)
    n2 = scores_seed_forensics.seed(db_path=db, md_path=md)
    assert len(scores_db.fetch_rows(db)) == n2 == n1


def test_seed_flags_the_decisive_rows(tmp_path):
    """The forensic verdict hinges on specific rows — assert they exist + are classified right."""
    db = tmp_path / "scores.db"
    md = tmp_path / "scores_ledger.md"
    scores_seed_forensics.seed(db_path=db, md_path=md)
    by_id = {r["run_id"]: r for r in scores_db.fetch_rows(db)}

    # the "2/10 GUI" baseline is a GUI-built-app wiring artifact, NOT an engine-duo quality read
    base = by_id["worldos-app-baseline"]
    assert base["surface"] == "GUI-built-app"
    assert base["cross_persona_sat"] == 2.0
    assert "WIRING" in base["notes"].upper()

    # the RRI "2.7" is GUI-built-app and flagged partial/contaminated
    rri = by_id["gate-f5500ac-partial"]
    assert rri["surface"] == "GUI-built-app" and rri["rri"] == 2.7
    assert "contaminated" in rri["notes"].lower() or "partial" in rri["notes"].lower()

    # the engine high-water marks are engine-duo, no GUI
    assert by_id["ow-fixC-043416"]["surface"] == "engine-duo"
    assert by_id["ow-fixC-043416"]["story_overall"] == 4.3
    assert by_id["sprint-cs3"]["surface"] == "engine-duo"
    assert by_id["sprint-cs3"]["angrydm_overall"] == 4.2

    # the narrative GUI persona rated prose high (9) yet failed on latency — surface, not quality
    nar = by_id["str2-narrative"]
    assert nar["surface"] == "GUI-headless-proxy" and nar["cross_persona_sat"] == 9
    assert "latency" in nar["notes"].lower()
