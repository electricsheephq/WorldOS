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


# --- P1: persona + is_canonical_baseline (additive comparability columns) ---

def test_persona_and_canonical_columns_are_registered(tmp_path):
    # Both new columns exist in COLUMNS; persona is TEXT, is_canonical_baseline is INTEGER.
    assert "persona" in scores_db.COLUMNS, "persona missing from COLUMNS"
    assert "is_canonical_baseline" in scores_db.COLUMNS, "is_canonical_baseline missing from COLUMNS"
    assert scores_db._coltype("persona") == "TEXT"
    assert scores_db._coltype("is_canonical_baseline") == "INTEGER"


def test_persona_roundtrips_and_canonical_defaults_zero(tmp_path):
    # add_run accepts persona; is_canonical_baseline defaults to 0 (NOT canonical) when omitted.
    db = tmp_path / "t.db"
    scores_db.add_run(
        "duo-p", db_path=db, surface="engine-duo", persona="qa/play_player_duo.txt",
        story_overall=4.2,
    )
    row = scores_db.fetch_rows(db)[0]
    assert row["persona"] == "qa/play_player_duo.txt"
    assert row["is_canonical_baseline"] == 0  # default — today's behavior is "not canonical"


def test_persona_defaults_none_when_omitted(tmp_path):
    # A row recorded without a persona reads back NULL (additive, old-snapshot behavior).
    db = tmp_path / "t.db"
    scores_db.add_run("duo-np", db_path=db, surface="engine-duo", story_overall=4.0)
    row = scores_db.fetch_rows(db)[0]
    assert row["persona"] is None


def test_new_columns_read_default_on_pre_p1_rows(tmp_path):
    # ADDITIVITY: a row written to a db created BEFORE these columns existed reads back
    # the defaults (persona NULL, is_canonical_baseline 0) once the column is ALTER-added.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        'CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT, "story_overall" REAL)'
    )
    conn.execute('INSERT INTO runs ("run_id", "surface", "story_overall") VALUES (?,?,?)',
                 ("legacy", "engine-duo", 4.5))
    conn.commit()
    conn.close()
    rows = scores_db.fetch_rows(db)  # connect() ALTERs in the new columns
    legacy = {r["run_id"]: r for r in rows}["legacy"]
    assert legacy["persona"] is None
    # ALTER TABLE ADD COLUMN gives existing rows NULL (not 0) — the read path / add_run is what
    # supplies the 0 default for NEW rows; old rows are simply "unstamped" (NULL), which is fine.
    assert legacy["is_canonical_baseline"] is None


def test_new_columns_alter_into_an_old_db(tmp_path):
    # A db created before P1 (no persona/is_canonical_baseline) gets them ALTER-added on connect.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE runs ("run_id" TEXT PRIMARY KEY, "surface" TEXT)')
    conn.commit()
    conn.close()
    conn = scores_db.connect(db)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert {"persona", "is_canonical_baseline"} <= cols


def test_migration_is_idempotent(tmp_path):
    # Connecting twice (re-running the ADD COLUMN migration) does not error or duplicate columns.
    db = tmp_path / "t.db"
    scores_db.connect(db).close()
    scores_db.connect(db).close()  # second connect must be a no-op migration
    conn = scores_db.connect(db)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(runs)")]
    conn.close()
    # no duplicate columns, and both new ones present exactly once
    assert cols.count("persona") == 1
    assert cols.count("is_canonical_baseline") == 1


def test_set_and_get_canonical_baseline_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    key = dict(surface="engine-duo", dm_model="sonnet",
               methodology="3-lens duo 8-beat", lens_config_version="lc_aaaaaaaaaaaa")
    scores_db.add_run("duo-base", db_path=db, story_overall=4.3, **key)
    # before marking, there is no canonical baseline for this key
    assert scores_db.get_canonical_baseline(db_path=db, **key) is None
    scores_db.set_canonical_baseline("duo-base", db_path=db)
    got = scores_db.get_canonical_baseline(db_path=db, **key)
    assert got is not None
    assert got["run_id"] == "duo-base"
    assert got["is_canonical_baseline"] == 1


def test_single_canonical_per_comparability_key_enforced(tmp_path):
    # Setting a new canonical baseline for the SAME comparability key clears the prior one.
    db = tmp_path / "t.db"
    key = dict(surface="engine-duo", dm_model="opus",
               methodology="3-lens duo 8-beat", lens_config_version="lc_bbbbbbbbbbbb")
    scores_db.add_run("base-1", db_path=db, story_overall=4.1, **key)
    scores_db.add_run("base-2", db_path=db, story_overall=4.4, **key)
    scores_db.set_canonical_baseline("base-1", db_path=db)
    scores_db.set_canonical_baseline("base-2", db_path=db)  # supersedes base-1

    by_id = {r["run_id"]: r for r in scores_db.fetch_rows(db)}
    assert by_id["base-1"]["is_canonical_baseline"] == 0  # cleared
    assert by_id["base-2"]["is_canonical_baseline"] == 1
    got = scores_db.get_canonical_baseline(db_path=db, **key)
    assert got["run_id"] == "base-2"


def test_canonical_baselines_isolated_across_keys(tmp_path):
    # Two DIFFERENT comparability keys may EACH have their own canonical baseline simultaneously.
    db = tmp_path / "t.db"
    key_a = dict(surface="engine-duo", dm_model="sonnet",
                 methodology="duo", lens_config_version="lc_111111111111")
    key_b = dict(surface="GUI-built-app", dm_model="opus",
                 methodology="5-persona", lens_config_version="lc_222222222222")
    scores_db.add_run("a", db_path=db, **key_a)
    scores_db.add_run("b", db_path=db, **key_b)
    scores_db.set_canonical_baseline("a", db_path=db)
    scores_db.set_canonical_baseline("b", db_path=db)  # must NOT clear "a" (different key)

    by_id = {r["run_id"]: r for r in scores_db.fetch_rows(db)}
    assert by_id["a"]["is_canonical_baseline"] == 1
    assert by_id["b"]["is_canonical_baseline"] == 1
    assert scores_db.get_canonical_baseline(db_path=db, **key_a)["run_id"] == "a"
    assert scores_db.get_canonical_baseline(db_path=db, **key_b)["run_id"] == "b"


def test_set_canonical_baseline_unknown_run_raises(tmp_path):
    db = tmp_path / "t.db"
    scores_db.connect(db).close()
    with pytest.raises(ValueError):
        scores_db.set_canonical_baseline("nope-not-here", db_path=db)


def test_add_run_accepts_is_canonical_baseline_directly(tmp_path):
    # add_run can stamp is_canonical_baseline=1 at insert time (e.g. a backfill of a known baseline).
    db = tmp_path / "t.db"
    scores_db.add_run("direct", db_path=db, surface="engine-duo", is_canonical_baseline=1)
    assert scores_db.fetch_rows(db)[0]["is_canonical_baseline"] == 1


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


# ---------------------------------------------------------------------------
# Phase-3 observability reader (1): trends_json — per-field time-series
# ---------------------------------------------------------------------------

def test_trends_json_shape_and_default_fields(tmp_path):
    """Default call returns the documented per-field time-series shape over the spec fields."""
    db = tmp_path / "t.db"
    scores_db.add_run("a", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00",
                      build_sha="aaa", story_overall=3.9, mech_overall=3.5)
    scores_db.add_run("b", db_path=db, surface="engine-duo", ts="2026-05-02T00:00:00+00:00",
                      build_sha="bbb", story_overall=4.2, mech_overall=3.8, rri=6.0)
    out = scores_db.trends_json(db)
    # top-level shape
    assert set(out) >= {"fields", "fence", "points"}
    # the spec's default fields are all present
    assert set(out["fields"]) == {
        "story_overall", "mech_overall", "angrydm_overall", "rri", "s_per_beat", "coldopen_s",
    }
    # one point per run, each carries identity + ts + the field values
    assert len(out["points"]) == 2
    pt = out["points"][0]
    assert {"run_id", "ts"} <= set(pt)
    # every requested field key is present on every point (NULL/None when unscored)
    for p in out["points"]:
        for f in out["fields"]:
            assert f in p


def test_trends_json_is_chronological_oldest_first(tmp_path):
    """A trend reads left-to-right in time: points are ordered oldest-first (opposite of fetch_rows)."""
    db = tmp_path / "t.db"
    scores_db.add_run("new", db_path=db, surface="engine-duo", ts="2026-05-30T00:00:00+00:00")
    scores_db.add_run("old", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00")
    scores_db.add_run("mid", db_path=db, surface="engine-duo", ts="2026-05-15T00:00:00+00:00")
    out = scores_db.trends_json(db)
    assert [p["run_id"] for p in out["points"]] == ["old", "mid", "new"]


def test_trends_json_values_carry_through(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("a", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00",
                      story_overall=4.0, s_per_beat=80.5, coldopen_s=170.0)
    out = scores_db.trends_json(db, fields=["story_overall", "s_per_beat", "coldopen_s"])
    p = out["points"][0]
    assert p["story_overall"] == 4.0
    assert p["s_per_beat"] == 80.5
    assert p["coldopen_s"] == 170.0


def test_trends_json_custom_fields(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("a", db_path=db, surface="engine-duo", angrydm_overall=4.1)
    out = scores_db.trends_json(db, fields=["angrydm_overall"])
    assert out["fields"] == ["angrydm_overall"]
    assert out["points"][0]["angrydm_overall"] == 4.1
    assert "story_overall" not in out["points"][0]


def test_trends_json_rejects_unknown_field(tmp_path):
    db = tmp_path / "t.db"
    scores_db.connect(db).close()
    with pytest.raises(ValueError):
        scores_db.trends_json(db, fields=["not_a_column"])


def test_trends_json_fences_by_surface(tmp_path):
    """surface= keeps only matching-surface runs out of the trend (fencing)."""
    db = tmp_path / "t.db"
    scores_db.add_run("duo", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00",
                      story_overall=4.0)
    scores_db.add_run("gui", db_path=db, surface="GUI-built-app", ts="2026-05-02T00:00:00+00:00",
                      cross_persona_sat=2.0)
    out = scores_db.trends_json(db, surface="engine-duo")
    assert [p["run_id"] for p in out["points"]] == ["duo"]
    assert out["fence"]["surface"] == "engine-duo"


def test_trends_json_fences_by_lens_config_version(tmp_path):
    """lens_config_version= keeps only runs scored under that lens ruler (the comparability fence)."""
    db = tmp_path / "t.db"
    scores_db.add_run("rulerA", db_path=db, surface="engine-duo", ts="2026-05-01T00:00:00+00:00",
                      lens_config_version="lc_aaaaaaaaaaaa", story_overall=4.5)
    scores_db.add_run("rulerB", db_path=db, surface="engine-duo", ts="2026-05-02T00:00:00+00:00",
                      lens_config_version="lc_bbbbbbbbbbbb", story_overall=3.6)
    out = scores_db.trends_json(db, lens_config_version="lc_aaaaaaaaaaaa")
    assert [p["run_id"] for p in out["points"]] == ["rulerA"]
    assert out["fence"]["lens_config_version"] == "lc_aaaaaaaaaaaa"


def test_trends_json_limit_keeps_last_n(tmp_path):
    """limit=N keeps the N most-recent runs (still emitted oldest-first)."""
    db = tmp_path / "t.db"
    for d in range(1, 6):  # 5 runs, ts 2026-05-01..05
        scores_db.add_run(f"r{d}", db_path=db, surface="engine-duo",
                          ts=f"2026-05-0{d}T00:00:00+00:00", story_overall=float(d))
    out = scores_db.trends_json(db, limit=2)
    # the two newest runs (r4, r5), chronological
    assert [p["run_id"] for p in out["points"]] == ["r4", "r5"]


def test_trends_json_empty_db(tmp_path):
    db = tmp_path / "t.db"
    scores_db.connect(db).close()
    out = scores_db.trends_json(db)
    assert out["points"] == []
    assert set(out["fields"]) >= {"story_overall", "rri"}


# ---------------------------------------------------------------------------
# Phase-3 observability reader (2): reconcile — READ-ONLY ledger<->INDEX.jsonl check
# ---------------------------------------------------------------------------

def _write_index(path: Path, lines: list) -> None:
    """Write an INDEX.jsonl-shaped file (one JSON object per line)."""
    with path.open("w", encoding="utf-8") as fh:
        for obj in lines:
            if isinstance(obj, str):
                fh.write(obj + "\n")          # raw line (for malformed-line tests)
            else:
                fh.write(json.dumps(obj) + "\n")


def test_reconcile_detects_orphans_both_directions(tmp_path):
    """A ledger row with no index line, and an index line with no ledger row, are each reported."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "INDEX.jsonl"
    # ledger: two rows
    scores_db.add_run("shared-run", db_path=db, surface="engine-duo", story_overall=4.0)
    scores_db.add_run("orphan-ledger", db_path=db, surface="engine-duo", story_overall=3.5)
    # index: the shared run + one index-only row (real INDEX uses the "id" key)
    _write_index(idx, [
        {"kind": "run", "id": "shared-run", "path": "qa/ui_playtest_runs/shared-run"},
        {"kind": "run", "id": "orphan-index", "path": "qa/ui_playtest_runs/orphan-index"},
    ])
    rep = scores_db.reconcile(db, idx)
    assert rep["in_ledger_not_index"] == ["orphan-ledger"]
    assert rep["in_index_not_ledger"] == ["orphan-index"]
    assert "shared-run" not in rep["in_ledger_not_index"]
    assert "shared-run" not in rep["in_index_not_ledger"]
    assert rep["matched_count"] == 1
    assert rep["ledger_count"] == 2
    assert rep["index_count"] == 2


def test_reconcile_is_tolerant_of_run_id_key_variants(tmp_path):
    """INDEX may use id / run_id / run for the run identifier — all are recognized."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "INDEX.jsonl"
    scores_db.add_run("by-id", db_path=db, surface="engine-duo")
    scores_db.add_run("by-run-id", db_path=db, surface="engine-duo")
    scores_db.add_run("by-run", db_path=db, surface="engine-duo")
    _write_index(idx, [
        {"id": "by-id"},
        {"run_id": "by-run-id"},
        {"run": "by-run"},
    ])
    rep = scores_db.reconcile(db, idx)
    assert rep["in_ledger_not_index"] == []
    assert rep["in_index_not_ledger"] == []
    assert rep["matched_count"] == 3


def test_reconcile_skips_unparseable_and_idless_lines(tmp_path):
    """Malformed JSON, blank lines, and JSON objects with no run-id key are SKIPPED + warned, never crash."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "INDEX.jsonl"
    scores_db.add_run("good", db_path=db, surface="engine-duo")
    _write_index(idx, [
        {"id": "good"},
        "",                                   # blank line
        "{not valid json",                    # malformed
        {"kind": "rubric", "note": "no id here"},  # object w/o any run-id key
        "[1,2,3]",                            # valid JSON but not an object
    ])
    rep = scores_db.reconcile(db, idx)
    assert rep["in_ledger_not_index"] == []        # "good" matched
    assert rep["in_index_not_ledger"] == []
    assert rep["matched_count"] == 1
    # the unparseable / id-less lines are reported as skipped (tolerant, never raises)
    assert len(rep["skipped_lines"]) >= 2


def test_reconcile_does_not_rewrite_index(tmp_path):
    """reconcile is READ-ONLY: INDEX.jsonl bytes are unchanged after the check."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "INDEX.jsonl"
    scores_db.add_run("r", db_path=db, surface="engine-duo")
    _write_index(idx, [{"id": "r"}, {"id": "extra"}])
    before = idx.read_bytes()
    scores_db.reconcile(db, idx)
    assert idx.read_bytes() == before


def test_reconcile_missing_index_file(tmp_path):
    """A missing INDEX.jsonl yields all ledger rows as orphans, with index_count 0 (no crash)."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "does-not-exist.jsonl"
    scores_db.add_run("only-ledger", db_path=db, surface="engine-duo")
    rep = scores_db.reconcile(db, idx)
    assert rep["index_count"] == 0
    assert rep["in_ledger_not_index"] == ["only-ledger"]
    assert rep["in_index_not_ledger"] == []


def test_reconcile_orphan_lists_are_sorted(tmp_path):
    """Orphan lists are deterministically sorted so the report diffs cleanly."""
    db = tmp_path / "scores.db"
    idx = tmp_path / "INDEX.jsonl"
    for rid in ("zeta", "alpha", "mike"):
        scores_db.add_run(rid, db_path=db, surface="engine-duo")
    _write_index(idx, [{"id": "yankee"}, {"id": "bravo"}])
    rep = scores_db.reconcile(db, idx)
    assert rep["in_ledger_not_index"] == sorted(rep["in_ledger_not_index"])
    assert rep["in_index_not_ledger"] == sorted(rep["in_index_not_ledger"])
    assert rep["in_ledger_not_index"] == ["alpha", "mike", "zeta"]
    assert rep["in_index_not_ledger"] == ["bravo", "yankee"]
