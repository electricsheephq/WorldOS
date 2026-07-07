#!/usr/bin/env python3
"""test_library_metrics.py — HV5 library_metrics table + snapshot writer (epic #1327, slice 2).

Covers two things:
  1. qa/scores_db.py's additive `library_metrics` table (schema, add_library_metrics,
     fetch_library_metrics, render_library_metrics_markdown) — mirrors
     qa/test_artifact_evals.py's schema round-trip coverage for the `artifacts` table.
  2. qa/library_metrics.py's scan_library / scan_promotion_log / snapshot_library — a pure
     filesystem + sqlite reader/writer over a FABRICATED library/ tree (never the committed
     library/ or qa/scores.db). Pure-stdlib; no LLM, no subprocess, no network. Run:

    uv run --directory servers/engine python -m pytest ../../qa/test_library_metrics.py -q -p no:xdist
or:
    python3 -m pytest qa/test_library_metrics.py -q -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import library_metrics  # noqa: E402


# ---------------------------------------------------------------------------
# scores_db.py schema additions
# ---------------------------------------------------------------------------
def test_library_metrics_coltype_mapping():
    assert scores_db._library_metrics_coltype("size_total") == "INTEGER"
    assert scores_db._library_metrics_coltype("reuse_count_sum") == "INTEGER"
    assert scores_db._library_metrics_coltype("promotion_pass_rate") == "REAL"
    assert scores_db._library_metrics_coltype("pct_library_sourced") == "REAL"
    assert scores_db._library_metrics_coltype("library_sha") == "TEXT"
    assert scores_db._library_metrics_coltype("size_by_class_json") == "TEXT"


def test_add_library_metrics_and_fetch_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    row_id = scores_db.add_library_metrics(
        db_path=db, library_sha="abc1234", size_total=5,
        size_by_class_json={"quest": 3, "npc": 2}, size_by_tier_json={"stable": 5},
        reuse_count_sum=12, promotion_pass_rate=0.8, promoted_total=4, rejected_total=1,
        source_path="library/", notes="test snapshot",
    )
    assert isinstance(row_id, int)
    rows = scores_db.fetch_library_metrics(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["library_sha"] == "abc1234"
    assert r["size_total"] == 5
    assert json.loads(r["size_by_class_json"]) == {"quest": 3, "npc": 2}
    assert json.loads(r["size_by_tier_json"]) == {"stable": 5}
    assert r["reuse_count_sum"] == 12
    assert r["promotion_pass_rate"] == 0.8
    assert r["ts"] is not None  # auto-stamped


def test_add_library_metrics_rejects_unknown_field(tmp_path):
    with pytest.raises(ValueError):
        scores_db.add_library_metrics(db_path=tmp_path / "t.db", bogus=1)


def test_add_library_metrics_never_touches_runs_or_artifacts(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_run("r1", db_path=db, surface="engine-duo", story_overall=4.5)
    scores_db.add_artifact("a1", db_path=db, **{"class": "quest"}, overall=4.0)
    scores_db.add_library_metrics(db_path=db, size_total=1)

    assert len(scores_db.fetch_rows(db)) == 1
    assert len(scores_db.fetch_artifacts(db)) == 1
    assert len(scores_db.fetch_library_metrics(db)) == 1


def test_each_call_appends_a_new_row_not_a_replace(tmp_path):
    """Unlike add_run/add_artifact (INSERT OR REPLACE keyed on a caller id), library_metrics has
    no natural key — every call is a fresh time-series point."""
    db = tmp_path / "t.db"
    scores_db.add_library_metrics(db_path=db, size_total=1)
    scores_db.add_library_metrics(db_path=db, size_total=2)
    scores_db.add_library_metrics(db_path=db, size_total=3)
    rows = scores_db.fetch_library_metrics(db)
    assert len(rows) == 3
    assert sorted(r["size_total"] for r in rows) == [1, 2, 3]


def test_fetch_library_metrics_newest_first(tmp_path):
    db = tmp_path / "t.db"
    scores_db.add_library_metrics(db_path=db, ts="2026-07-01T00:00:00+00:00", size_total=1)
    scores_db.add_library_metrics(db_path=db, ts="2026-07-02T00:00:00+00:00", size_total=2)
    rows = scores_db.fetch_library_metrics(db)
    assert [r["size_total"] for r in rows] == [2, 1]


def test_empty_table_fetch_is_empty_list(tmp_path):
    db = tmp_path / "t.db"
    scores_db.connect(db).close()  # ensure schema exists, zero rows
    assert scores_db.fetch_library_metrics(db) == []


def test_render_library_metrics_markdown_writes_file(tmp_path):
    db = tmp_path / "t.db"
    md = tmp_path / "out.md"
    scores_db.add_library_metrics(db_path=db, size_total=2, promotion_pass_rate=0.5)
    text = scores_db.render_library_metrics_markdown(db, md)
    assert md.exists()
    assert "Library Metrics Ledger" in text
    assert "50%" in text  # promotion_pass_rate rendered as a percentage


def test_render_library_metrics_markdown_empty_db(tmp_path):
    db = tmp_path / "t.db"
    md = tmp_path / "out.md"
    scores_db.connect(db).close()
    text = scores_db.render_library_metrics_markdown(db, md)
    assert "Rows: **0**" in text


# ---------------------------------------------------------------------------
# library_metrics.py — scan_library (a fabricated library/ tree)
# ---------------------------------------------------------------------------
def _entry(tier="stable", reuse_count=0, **extra) -> dict:
    e = {"artifact_id": "x", "class": "quest", "tier": tier, "reuse_count": reuse_count,
        "provenance": {}, "scores": {"overall": 4.2, "dims": {"d": 4.2}}, "license": "proprietary",
        "promoted_at": "2026-07-06T00:00:00+00:00"}
    e.update(extra)
    return e


def _write_entry(library_dir: Path, subdir: str, filename: str, entry: dict) -> None:
    d = library_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(json.dumps(entry), encoding="utf-8")


def test_scan_empty_or_missing_library_is_all_zero(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = library_metrics.scan_library(missing)
    assert result["size_total"] == 0
    assert result["size_by_class"] == {}
    assert result["size_by_tier"] == {"experimental": 0, "stable": 0, "canonical": 0}
    assert result["reuse_count_sum"] == 0


def test_scan_counts_size_by_class_and_tier(tmp_path):
    lib = tmp_path / "library"
    _write_entry(lib, "quests", "q1.json", _entry(tier="stable"))
    _write_entry(lib, "quests", "q2.json", _entry(tier="experimental"))
    _write_entry(lib, "npcs", "n1.json", _entry(tier="stable"))

    result = library_metrics.scan_library(lib)

    assert result["size_total"] == 3
    assert result["size_by_class"] == {"quest": 2, "npc": 1}
    assert result["size_by_tier"] == {"experimental": 1, "stable": 2, "canonical": 0}


def test_scan_sums_reuse_count(tmp_path):
    lib = tmp_path / "library"
    _write_entry(lib, "quests", "q1.json", _entry(reuse_count=3))
    _write_entry(lib, "quests", "q2.json", _entry(reuse_count=7))
    _write_entry(lib, "npcs", "n1.json", _entry(reuse_count=0))

    result = library_metrics.scan_library(lib)

    assert result["reuse_count_sum"] == 10


def test_scan_ignores_non_class_files(tmp_path):
    lib = tmp_path / "library"
    lib.mkdir(parents=True)
    (lib / "pack.json").write_text("{}", encoding="utf-8")
    (lib / ".promoted.jsonl").write_text("", encoding="utf-8")

    result = library_metrics.scan_library(lib)

    assert result["size_total"] == 0  # pack.json / .promoted.jsonl live at the root, not a class subdir


def test_scan_malformed_entry_counted_in_size_but_not_reuse_or_tier(tmp_path):
    lib = tmp_path / "library"
    d = lib / "quests"
    d.mkdir(parents=True)
    (d / "broken.json").write_text("not valid json{{{", encoding="utf-8")

    result = library_metrics.scan_library(lib)

    assert result["size_total"] == 1  # occupies a library slot
    assert result["reuse_count_sum"] == 0
    assert result["size_by_tier"] == {"experimental": 0, "stable": 0, "canonical": 0}


def test_scan_unrecognized_tier_not_counted_in_any_tier_bucket(tmp_path):
    lib = tmp_path / "library"
    _write_entry(lib, "quests", "q1.json", _entry(tier="bogus-tier"))
    result = library_metrics.scan_library(lib)
    assert result["size_total"] == 1
    assert sum(result["size_by_tier"].values()) == 0


# ---------------------------------------------------------------------------
# library_metrics.py — scan_promotion_log
# ---------------------------------------------------------------------------
def _write_promoted_log(library_dir: Path, lines: list[dict]) -> None:
    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / ".promoted.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def test_promotion_log_missing_yields_none_pass_rate(tmp_path):
    lib = tmp_path / "library"
    result = library_metrics.scan_promotion_log(lib)
    assert result == {"promoted_total": 0, "rejected_total": 0, "promotion_pass_rate": None}


def test_promotion_log_empty_yields_none_pass_rate(tmp_path):
    lib = tmp_path / "library"
    _write_promoted_log(lib, [])
    result = library_metrics.scan_promotion_log(lib)
    assert result["promotion_pass_rate"] is None


def test_promotion_log_pass_rate_computed(tmp_path):
    lib = tmp_path / "library"
    _write_promoted_log(lib, [
        {"artifact_id": "a1", "verdict": "promoted", "tier": "stable"},
        {"artifact_id": "a2", "verdict": "promoted", "tier": "stable"},
        {"artifact_id": "a3", "verdict": "promoted", "tier": "stable"},
        {"artifact_id": "a4", "verdict": "rejected", "tier": None},
    ])
    result = library_metrics.scan_promotion_log(lib)
    assert result["promoted_total"] == 3
    assert result["rejected_total"] == 1
    assert result["promotion_pass_rate"] == pytest.approx(0.75)


def test_promotion_log_skipped_unscored_not_counted_in_pass_rate_denominator(tmp_path):
    lib = tmp_path / "library"
    _write_promoted_log(lib, [
        {"artifact_id": "a1", "verdict": "promoted", "tier": "stable"},
        {"artifact_id": "a2", "verdict": "skipped-unscored", "tier": None},
        {"artifact_id": "a3", "verdict": "score-failed", "tier": None},
    ])
    result = library_metrics.scan_promotion_log(lib)
    # only the ONE gated (promoted) line counts; the two non-gated verdicts are excluded entirely
    assert result["promoted_total"] == 1
    assert result["rejected_total"] == 0
    assert result["promotion_pass_rate"] == 1.0


def test_promotion_log_malformed_line_skipped_not_fatal(tmp_path):
    lib = tmp_path / "library"
    lib.mkdir(parents=True)
    (lib / ".promoted.jsonl").write_text(
        "not json\n" + json.dumps({"artifact_id": "a1", "verdict": "promoted"}) + "\n",
        encoding="utf-8",
    )
    result = library_metrics.scan_promotion_log(lib)
    assert result["promoted_total"] == 1


# ---------------------------------------------------------------------------
# library_metrics.py — snapshot_library (the writer, end to end over a fabricated library/)
# ---------------------------------------------------------------------------
def test_snapshot_library_writes_one_row_reflecting_known_state(tmp_path):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry(tier="stable", reuse_count=5))
    _write_entry(lib, "npcs", "n1.json", _entry(tier="stable", reuse_count=2))
    _write_promoted_log(lib, [
        {"artifact_id": "q1", "verdict": "promoted", "tier": "stable"},
        {"artifact_id": "x2", "verdict": "rejected", "tier": None},
    ])

    payload = library_metrics.snapshot_library(library_dir=lib, db_path=db, library_sha="deadbeef")

    assert payload["row_id"] is not None
    rows = scores_db.fetch_library_metrics(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["library_sha"] == "deadbeef"
    assert r["size_total"] == 2
    assert json.loads(r["size_by_class_json"]) == {"quest": 1, "npc": 1}
    assert json.loads(r["size_by_tier_json"]) == {"experimental": 0, "stable": 2, "canonical": 0}
    assert r["reuse_count_sum"] == 7
    assert r["promoted_total"] == 1
    assert r["rejected_total"] == 1
    assert r["promotion_pass_rate"] == pytest.approx(0.5)
    assert r["source_path"] == str(lib)


def test_snapshot_library_pct_library_sourced_defaults_to_none(tmp_path):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    library_metrics.snapshot_library(library_dir=lib, db_path=db)
    rows = scores_db.fetch_library_metrics(db)
    assert rows[0]["pct_library_sourced"] is None


def test_snapshot_library_pct_library_sourced_can_be_supplied(tmp_path):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    library_metrics.snapshot_library(library_dir=lib, db_path=db, pct_library_sourced=0.35)
    rows = scores_db.fetch_library_metrics(db)
    assert rows[0]["pct_library_sourced"] == 0.35


def test_snapshot_library_dry_run_writes_nothing(tmp_path):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry())

    payload = library_metrics.snapshot_library(library_dir=lib, db_path=db, dry_run=True)

    assert "row_id" not in payload
    assert payload["size_total"] == 1
    assert not db.exists()  # dry-run never even opens/creates the db


def test_snapshot_library_two_calls_append_two_rows_time_series(tmp_path):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry())
    library_metrics.snapshot_library(library_dir=lib, db_path=db)
    _write_entry(lib, "quests", "q2.json", _entry())
    library_metrics.snapshot_library(library_dir=lib, db_path=db)

    rows = scores_db.fetch_library_metrics(db)
    assert len(rows) == 2
    sizes = sorted(json.loads(r["size_total"]) if isinstance(r["size_total"], str) else r["size_total"]
                   for r in rows)
    assert sizes == [1, 2]


def test_snapshot_library_never_writes_into_library_dir(tmp_path):
    """This module is a READER of library/ — it must never create/modify anything under it (that
    stays promote.py's sole-writer job)."""
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry())
    before = sorted(p.name for p in lib.rglob("*"))

    library_metrics.snapshot_library(library_dir=lib, db_path=db)

    after = sorted(p.name for p in lib.rglob("*"))
    assert before == after


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_dry_run_prints_payload_and_writes_nothing(tmp_path, capsys):
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry())

    rc = library_metrics.main(["--library", str(lib), "--db", str(db), "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["size_total"] == 1
    assert not db.exists()


def test_cli_writes_and_renders(tmp_path, capsys, monkeypatch):
    """--render must call scores_db.render_library_metrics_markdown after the snapshot. The real
    render function's md_path default is bound (at import time) to the real repo path — like every
    other --render* flag in this codebase (scores_db.py's own --render/--render-artifacts have no
    output-path override either) — so this test verifies the CALL happens via a monkeypatched
    render function, rather than trying to redirect the module-level path constant (which a bound
    default argument would not observe)."""
    lib = tmp_path / "library"
    db = tmp_path / "scores.db"
    _write_entry(lib, "quests", "q1.json", _entry())

    calls: list[str] = []
    monkeypatch.setattr(
        scores_db, "render_library_metrics_markdown",
        lambda db_path=None: calls.append(str(db_path)),
    )

    rc = library_metrics.main(["--library", str(lib), "--db", str(db), "--render"])

    assert rc == 0
    assert len(scores_db.fetch_library_metrics(db)) == 1
    assert calls == [str(db)]  # rendered exactly once, against the snapshot's own db
