#!/usr/bin/env python3
"""Units for the walkability surface in the scores ledger (epic #1581 queue item).

`record_room_walk` writes class="room" artifact rows carrying walk_gate/walk_report_path with
latest-per-room semantics; room_pipeline stamps it on every DECIDED walk verdict (GREEN and RED).
Run: uv run --directory servers/engine python -m pytest ../../qa/test_walk_scores_surface.py -q -p no:xdist
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scores_db as SD  # noqa: E402


def _rooms(db):
    return [r for r in SD.fetch_artifacts(db_path=db) if r["class"] == "room"]


def test_record_room_walk_green(tmp_path):
    db = tmp_path / "scores.db"
    SD.record_room_walk("shop", "GREEN", db_path=db, sha="abc1234",
                        walk_report_path="qa/evidence/walk-shop/walk_report.json",
                        source_path="qa/certifications/shop.json")
    rows = _rooms(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact_id"] == "room:shop"
    assert row["walk_gate"] == "GREEN"
    assert row["walk_report_path"].endswith("walk_report.json")
    assert row["overall"] is None  # beauty scale for visual classes is a separate, open decision


def test_latest_per_room_replace_semantics(tmp_path):
    db = tmp_path / "scores.db"
    SD.record_room_walk("crypt", "GREEN", db_path=db)
    SD.record_room_walk("crypt", "RED", db_path=db, notes="plate drifted")
    rows = _rooms(db)
    assert len(rows) == 1  # stable artifact_id -> one latest row, not history
    assert rows[0]["walk_gate"] == "RED"


def test_invalid_verdict_is_loud(tmp_path):
    db = tmp_path / "scores.db"
    with pytest.raises(ValueError, match="walk_gate"):
        SD.record_room_walk("shop", "SHIPPABLE", db_path=db)


def test_walk_gate_rejected_on_direct_add_artifact_too(tmp_path):
    db = tmp_path / "scores.db"
    with pytest.raises(ValueError, match="walk_gate"):
        SD.add_artifact("room:x", db_path=db, **{"class": "room"}, walk_gate="ok")


def test_additive_migration_backfills_old_db(tmp_path):
    """A db created before the walk columns existed must gain them additively on next connect."""
    import sqlite3
    db = tmp_path / "scores.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY, class TEXT)")
    conn.execute("INSERT INTO artifacts VALUES ('room:old', 'room')")
    conn.commit()
    conn.close()
    SD.record_room_walk("tavern", "GREEN", db_path=db)  # triggers _ensure_schema
    rows = SD.fetch_artifacts(db_path=db)
    by_id = {r["artifact_id"]: r for r in rows}
    assert by_id["room:old"]["walk_gate"] is None  # backfilled column, no invented value
    assert by_id["room:tavern"]["walk_gate"] == "GREEN"
