#!/usr/bin/env python3
"""Tests for qa/nominate.py — the HV5 closeout auto-nominator (epic #1327).

Pure-stdlib (sqlite3 via scores_db + json); imports neither the engine nor the viewer. EVERY test
FABRICATES its own scored-run fixture: a temp scores.db (tmp_path) + a temp artifacts_out tree of
HV2-shaped envelope JSONs. The committed qa/scores.db and qa/nominations.jsonl are NEVER touched.
OFFLINE-SAFE: no live LLM calls, no scoring. Run:

    uv run --directory servers/engine --group dev python -m pytest ../../qa/test_nominate.py -q -p no:xdist
or:
    python3 -m pytest qa/test_nominate.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import closeout  # noqa: E402
import nominate  # noqa: E402


# ---------------------------------------------------------------------------
# fabrication helpers — a scored run + its extracted-artifact envelopes
# ---------------------------------------------------------------------------
def _seed_run(db: Path, run_id: str, *, story: float | None) -> None:
    scores_db.add_run(
        run_id, db_path=db, ts="2026-07-06T08:00:00+00:00", surface="engine-duo",
        dm_model="opus", methodology="3-lens duo 8-beat", story_overall=story, mech_overall=4.6,
    )


def _envelope(artifact_id: str, cls: str, payload: dict, *, run_id: str, campaign="bg") -> dict:
    """Mirror export_campaign_artifacts._envelope's shape (the HV2 contract nominate reads)."""
    return {
        "artifact_id": artifact_id,
        "class": cls,
        "world": "baldurs-gate",
        "provenance": {"campaign_id": campaign, "run_id": run_id, "sha": "deadbeef",
                       "extracted_at": "2026-07-06T08:00:00+00:00"},
        "payload": payload,
        "scores": None,
    }


def _write_artifact(artifacts_dir: Path, campaign: str, art: dict) -> Path:
    d = artifacts_dir / campaign / art["class"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{art['artifact_id'].replace(':', '_')}.json"
    p.write_text(json.dumps(art, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "scores.db"  # add_run creates the schema on first write
    return {
        "db": db,
        "artifacts": tmp_path / "artifacts_out",
        "noms": tmp_path / "nominations.jsonl",
    }


def _quest(qid, status, *, run_id="run-hi"):
    return _envelope(f"quest:bg:{qid}", "quest",
                     {"id": qid, "name": qid, "resolution_status": status}, run_id=run_id)


def _npc(cid, n_snippets, *, run_id="run-hi"):
    return _envelope(f"npc:bg:{cid}", "npc",
                     {"id": cid, "name": cid, "dialogue_snippets": [f"line {i}" for i in range(n_snippets)]},
                     run_id=run_id)


def _run(env, run_id="run-hi", campaign=None, dry_run=False):
    return nominate.nominate(run_id, artifacts_dir=env["artifacts"], campaign=campaign,
                             nominations_path=env["noms"], db_path=env["db"], dry_run=dry_run)


def _lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# the run gate
# ---------------------------------------------------------------------------
def test_run_below_story_bar_nominates_nothing(env):
    _seed_run(env["db"], "run-lo", story=closeout.NOMINATION_STORY_BAR - 0.5)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed", run_id="run-lo"))
    assert _run(env, "run-lo") == []
    assert not env["noms"].exists()  # additive no-op: no file created


def test_run_with_no_story_score_nominates_nothing(env):
    _seed_run(env["db"], "run-unscored", story=None)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed", run_id="run-unscored"))
    assert _run(env, "run-unscored") == []


def test_unknown_run_nominates_nothing(env):
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    assert _run(env, "run-hi") == []  # run not in db


# ---------------------------------------------------------------------------
# per-class heuristics
# ---------------------------------------------------------------------------
def test_completed_quest_is_nominated(env):
    _seed_run(env["db"], "run-hi", story=closeout.NOMINATION_STORY_BAR)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    fresh = _run(env)
    assert [r["artifact_id"] for r in fresh] == ["quest:bg:q1"]
    assert "completed" in fresh[0]["curation_note"]


def test_resolved_quest_is_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q2", "resolved"))
    assert [r["artifact_id"] for r in _run(env)] == ["quest:bg:q2"]


def test_active_quest_is_not_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q3", "active"))
    assert _run(env) == []


def test_npc_at_turn_floor_is_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _npc("nadia", closeout.NOMINATION_TURN_MIN))
    assert [r["artifact_id"] for r in _run(env)] == ["npc:bg:nadia"]


def test_npc_below_turn_floor_is_not_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _npc("quiet", closeout.NOMINATION_TURN_MIN - 1))
    assert _run(env) == []


def test_location_class_is_not_auto_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg",
                    _envelope("location:bg:tavern", "location", {"id": "tavern", "visited": True}, run_id="run-hi"))
    assert _run(env) == []


# ---------------------------------------------------------------------------
# provenance scoping — only THIS run's artifacts
# ---------------------------------------------------------------------------
def test_only_matching_run_id_artifacts_are_nominated(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("mine", "completed", run_id="run-hi"))
    _write_artifact(env["artifacts"], "bg", _quest("other", "completed", run_id="run-other"))
    assert [r["artifact_id"] for r in _run(env)] == ["quest:bg:mine"]


# ---------------------------------------------------------------------------
# record shape matches promote.py's reader (PR #1338)
# ---------------------------------------------------------------------------
def test_record_shape_has_required_and_optional_keys(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    p = _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    _run(env)
    rec = _lines(env["noms"])[0]
    # promote.read_nominations requires a JSON object with 'artifact_id'; the rest are optional.
    assert "artifact_id" in rec and isinstance(rec["artifact_id"], str)
    assert set(rec) <= {"artifact_id", "source_path", "curation_note"}
    assert rec["source_path"].endswith(p.name)  # repo-relative path to the extracted JSON


# ---------------------------------------------------------------------------
# append-only + idempotency + dry-run
# ---------------------------------------------------------------------------
def test_append_only_preserves_prior_lines(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    env["noms"].write_text(json.dumps({"artifact_id": "quest:pre:existing"}) + "\n")
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    _run(env)
    ids = [r["artifact_id"] for r in _lines(env["noms"])]
    assert ids == ["quest:pre:existing", "quest:bg:q1"]  # prior line untouched, new one appended


def test_idempotent_second_run_appends_nothing(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    first = _run(env)
    assert len(first) == 1
    second = _run(env)  # same run again
    assert second == []
    assert len(_lines(env["noms"])) == 1  # no duplicate line


def test_dry_run_writes_nothing_but_reports_candidates(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    fresh = _run(env, dry_run=True)
    assert [r["artifact_id"] for r in fresh] == ["quest:bg:q1"]
    assert not env["noms"].exists()


def test_no_artifacts_dir_is_noop(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    assert _run(env) == []
    assert not env["noms"].exists()


def test_campaign_scoping_narrows_the_scan(env):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg",
                    _envelope("quest:bg:in", "quest", {"resolution_status": "completed"}, run_id="run-hi", campaign="bg"))
    _write_artifact(env["artifacts"], "waterdeep",
                    _envelope("quest:wd:out", "quest", {"resolution_status": "completed"}, run_id="run-hi", campaign="waterdeep"))
    assert [r["artifact_id"] for r in _run(env, campaign="bg")] == ["quest:bg:in"]


# ---------------------------------------------------------------------------
# closeout hook integration (the additive, non-fatal tail)
# ---------------------------------------------------------------------------
def test_build_closeout_fires_nomination_hook(env, monkeypatch):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    # Point the nominator's defaults at the temp tree so the real hook writes there.
    monkeypatch.setattr(nominate, "DEFAULT_ARTIFACTS_DIR", env["artifacts"])
    monkeypatch.setattr(nominate, "DEFAULT_NOMINATIONS", env["noms"])
    block = closeout.build_closeout("run-hi", db_path=env["db"])
    assert "RUN: run-hi" in block  # block still renders
    assert [r["artifact_id"] for r in _lines(env["noms"])] == ["quest:bg:q1"]


def test_build_closeout_nominate_false_writes_no_queue(env, monkeypatch):
    _seed_run(env["db"], "run-hi", story=4.5)
    _write_artifact(env["artifacts"], "bg", _quest("q1", "completed"))
    monkeypatch.setattr(nominate, "DEFAULT_ARTIFACTS_DIR", env["artifacts"])
    monkeypatch.setattr(nominate, "DEFAULT_NOMINATIONS", env["noms"])
    closeout.build_closeout("run-hi", db_path=env["db"], nominate=False)
    assert not env["noms"].exists()


def test_closeout_hook_is_nonfatal_on_nominator_error(env, monkeypatch):
    _seed_run(env["db"], "run-hi", story=4.5)

    def _boom(*a, **k):
        raise RuntimeError("harvest side blew up")

    monkeypatch.setattr(nominate, "nominate", _boom)
    # The block must still render despite the hook raising.
    block = closeout.build_closeout("run-hi", db_path=env["db"])
    assert "RUN: run-hi" in block
