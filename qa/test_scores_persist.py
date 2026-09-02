"""Tests for qa/scores_persist.py (#1414 — auto-persist scores rows for the manual-append bucket).

Every test targets a TEMP scores.db (tmp_path) — NEVER the committed qa/scores.db (the same
additive/read-only discipline qa/test_scores_db.py and qa/test_generate_release_notes.py follow).

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_scores_persist.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import scores_persist as sp  # noqa: E402


def _write(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# qa/run_duo.sh
# ---------------------------------------------------------------------------
def test_persist_duo_row_clean_completion(tmp_path):
    db = tmp_path / "scores.db"
    story = _write(tmp_path / "duo.tolkien.json", {"overall": 4.2})
    mech = _write(tmp_path / "duo.score.json", {"overall": 4.0})
    angry = _write(tmp_path / "duo.angrydm.json", {"overall": 3.5})
    latency = _write(tmp_path / "duo.latency.json", {"s_per_beat": 12.3, "coldopen_s": 45.0, "turns_per_beat": 3.1})

    sp.persist_duo_row(
        "duo-test1", db_path=db, build_sha="abc1234", dm_model="opus", actor_model="sonnet",
        beats=8, completed_beats=8, behavioral="GREEN", story_json=story, mech_json=mech,
        angry_json=angry, latency_json=latency, source_path="qa/transcripts/duo-test1",
        infra_note="no throttle detected",
    )
    rows = {r["run_id"]: r for r in scores_db.fetch_rows(db)}
    row = rows["duo-test1"]
    assert row["surface"] == "engine-duo"
    assert row["dm_model"] == "opus"
    assert row["actor_model"] == "sonnet"
    assert row["story_overall"] == 4.2
    assert row["mech_overall"] == 4.0
    assert row["angrydm_overall"] == 3.5
    assert row["behavioral"] == "GREEN"
    assert row["s_per_beat"] == 12.3
    assert "3-lens duo 8-beat" == row["methodology"]
    assert row["build_sha"] == "abc1234"


def test_persist_duo_row_contaminated_writes_marker_not_clean_row(tmp_path):
    db = tmp_path / "scores.db"
    story = _write(tmp_path / "duo.tolkien.json", {"overall": 4.9})  # must NOT be cited
    sp.persist_duo_row(
        "duo-aborted1", db_path=db, build_sha="deadbee", dm_model="opus", beats=8,
        completed_beats=3, story_json=story, contaminated_reason="QUOTA ABORT at beat 3 (HTTP 429)",
    )
    rows = {r["run_id"]: r for r in scores_db.fetch_rows(db)}
    row = rows["duo-aborted1"]
    assert row["behavioral"] == "CONTAMINATED"
    assert row["story_overall"] is None  # no citable lens numbers on a contaminated run
    assert row["mech_overall"] is None
    assert "QUOTA ABORT" in row["notes"]
    assert "CONTAMINATED" in row["notes"]


def test_persist_duo_row_rerun_same_run_id_replaces_not_duplicates(tmp_path):
    db = tmp_path / "scores.db"
    sp.persist_duo_row("duo-dup", db_path=db, behavioral="RED", beats=6)
    sp.persist_duo_row("duo-dup", db_path=db, behavioral="GREEN", beats=6)
    rows = [r for r in scores_db.fetch_rows(db) if r["run_id"] == "duo-dup"]
    assert len(rows) == 1
    assert rows[0]["behavioral"] == "GREEN"


# ---------------------------------------------------------------------------
# qa/run_combat_sprint.sh
# ---------------------------------------------------------------------------
def test_persist_combat_sprint_row(tmp_path):
    db = tmp_path / "scores.db"
    angry = _write(tmp_path / "cs.angrydm.json", {"overall": 3.7})
    sp.persist_combat_sprint_row(
        "cs-test1", db_path=db, build_sha="c0ffee1", dm_model="opus", behavioral="GREEN",
        angry_json=angry, source_path="qa/transcripts/cs-test1",
    )
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["cs-test1"]
    assert row["surface"] == "engine-duo"
    assert row["methodology"] == "combat-sprint"
    assert row["angrydm_overall"] == 3.7
    assert row["behavioral"] == "GREEN"


# ---------------------------------------------------------------------------
# qa/vm/sweep_v2.sh
# ---------------------------------------------------------------------------
def test_persist_sweep_persona_row(tmp_path):
    db = tmp_path / "scores.db"
    score = _write(tmp_path / "score-newbie.json", {
        "persona_satisfaction": 7.5, "satisfaction_source": "self-reported",
        "bug_reports_critical": 1, "gave_up": False,
    })
    sp.persist_sweep_persona_row(
        "vm2-newbie-abc1234", db_path=db, persona="newbie", build_sha="abc1234",
        dm_model="opus", actor_model="sonnet", score_json=score,
    )
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["vm2-newbie-abc1234"]
    assert row["surface"] == "GUI-headless-proxy"
    assert row["persona"] == "newbie"
    assert row["cross_persona_sat"] == 7.5
    assert row["critical_bugs"] == 1
    assert row["scorer_model"] == "self-reported"
    per = json.loads(row["per_persona_json"])
    assert per["satisfaction_source"] == "self-reported"


# ---------------------------------------------------------------------------
# qa/ui_playtest_app.sh
# ---------------------------------------------------------------------------
def test_persist_app_gate_row_preserves_satisfaction_source(tmp_path):
    db = tmp_path / "scores.db"
    score = _write(tmp_path / "score.json", {
        "persona_satisfaction": 8.0, "satisfaction_source": "derived", "bug_reports_critical": 0,
    })
    sp.persist_app_gate_row(
        "app-test1", db_path=db, build_sha="f00d123", dm_model="opus", actor_model="sonnet",
        part="B", provider="claude", score_pass=True, score_json=score,
    )
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["app-test1"]
    assert row["surface"] == "GUI-built-app"
    assert row["cross_persona_sat"] == 8.0
    assert row["scorer_model"] == "derived"
    assert row["pass"] == 1


# ---------------------------------------------------------------------------
# qa/release_readiness.py
# ---------------------------------------------------------------------------
def test_persist_rri_row_clean(tmp_path):
    db = tmp_path / "scores.db"
    rri_json = _write(tmp_path / "RRI.json", {
        "rri": 8.2, "release_ready": True, "status": "READY", "build_sha": "abc1234",
        "gates_passed": 10, "gates_total": 11, "failed_gates": [],
        "signals": {"story_overall": 4.4, "mech_overall": 4.1, "behavioral": "GREEN",
                    "cross_persona_satisfaction": 7.2, "total_critical_bugs": 0,
                    "image_render_rate": 0.95},
    })
    sp.persist_rri_row("rri-abc1234", db_path=db, rri_json=rri_json, build_sha="abc1234")
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["rri-abc1234"]
    assert row["surface"] == "GUI-built-app"
    assert row["rri"] == 8.2
    assert row["story_overall"] == 4.4
    assert row["behavioral"] == "GREEN"
    assert row["pass"] == 1


def test_persist_rri_row_aborted_writes_contaminated_marker(tmp_path):
    db = tmp_path / "scores.db"
    rri_json = _write(tmp_path / "RRI.json", {
        "rri": 1.8, "release_ready": False, "aborted": True,
        "abort_detail": "newbie resets 3:50pm UTC", "build_sha": "deadbee",
        "signals": {"story_overall": 4.4},  # must NOT be cited on an aborted row
    })
    sp.persist_rri_row("rri-deadbee", db_path=db, rri_json=rri_json, build_sha="deadbee")
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["rri-deadbee"]
    assert row["behavioral"] == "CONTAMINATED"
    assert row["rri"] is None
    assert row["story_overall"] is None
    assert "resets 3:50pm UTC" in row["notes"]


# ---------------------------------------------------------------------------
# CLI fail-loud contract
# ---------------------------------------------------------------------------
def test_cli_duo_writes_row_and_returns_zero(tmp_path):
    db = tmp_path / "scores.db"
    rc = sp.main(["duo", "--run-id", "cli-duo1", "--db", str(db), "--behavioral", "GREEN", "--beats", "4"])
    assert rc == 0
    row = {r["run_id"]: r for r in scores_db.fetch_rows(db)}["cli-duo1"]
    assert row["surface"] == "engine-duo"


def test_cli_missing_required_arg_exits_nonzero(tmp_path):
    with pytest.raises(SystemExit) as exc:
        sp.main(["duo", "--db", str(tmp_path / "scores.db")])  # missing --run-id
    assert exc.value.code != 0


def test_cli_fails_loud_on_write_error(tmp_path, monkeypatch, capsys):
    def _boom(*a, **kw):
        raise RuntimeError("disk full (simulated)")

    monkeypatch.setattr(sp, "persist_duo_row", _boom)
    with pytest.raises(SystemExit) as exc:
        sp.main(["duo", "--run-id", "cli-duo-boom", "--db", str(tmp_path / "scores.db")])
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "FATAL" in captured.err
    assert "cli-duo-boom" in captured.err
