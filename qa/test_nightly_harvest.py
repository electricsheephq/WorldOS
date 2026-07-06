#!/usr/bin/env python3
"""test_nightly_harvest.py — HV5 nightly batch artifact scorer (epic #1327, slice 2).

Exercises the batch PATH fully OFFLINE: every test fabricates its own nominations.jsonl + a temp
artifacts_out tree of loadable artifact JSONs + a temp scores.db, and monkeypatches
``artifact_score.score_artifact_panel`` to a fabricated stub — mirrors
qa/test_promote_pipeline.py's ``test_score_if_unscored_is_isolated_from_promotion_path`` discipline.
NO test invokes score.sh / a live claude -p. Run:

    uv run --directory servers/engine --group dev python -m pytest ../../qa/test_nightly_harvest.py -q -p no:xdist
or:
    python3 -m pytest qa/test_nightly_harvest.py -q -p no:xdist

Invariant assertions live here:
  * scores ONLY unscored nominations (an already-scored artifact_id is never re-scored/re-billed).
  * idempotent: re-running after a full batch scores nothing new.
  * resumable: --max-per-run caps one invocation; the remainder is picked up by the next call.
  * bounded: --max-per-run never over-scores; a negative cap is rejected at the CLI.
  * one artifact's load/score failure never aborts the rest of the batch (non-fatal isolation).
  * dry-run writes nothing and calls the scorer zero times.
  * writes ONLY new `artifacts` rows + its own log — never touches library/ (promotion stays separate).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import artifact_score  # noqa: E402
import nightly_harvest  # noqa: E402


# ---------------------------------------------------------------------------
# fabrication helpers
# ---------------------------------------------------------------------------
def _artifact_json(artifacts_dir: Path, artifact_id: str, cls: str = "quest", **payload_extra) -> Path:
    """Write a loadable artifact JSON (satisfies artifact_score.load_artifact's canonical shape)."""
    payload = {
        "quest": {"id": artifact_id, "name": artifact_id, "objectives": ["o1"],
                  "completed_objectives": ["o1"], "resolution_status": "completed",
                  "evolves_to": None, "consequences": []},
        "npc": {"id": artifact_id, "name": artifact_id, "voice_id": "v1", "personality": "p",
                "attitude_arc": [], "final_status": "ally", "dialogue_snippets": ["a", "b", "c"]},
    }[cls]
    payload.update(payload_extra)
    obj = {
        "artifact_id": artifact_id, "class": cls, "world": "baldurs-gate",
        "provenance": {"campaign_id": "bg", "run_id": "run-hi", "sha": "deadbeef"},
        "payload": payload, "scores": None,
    }
    p = artifacts_dir / f"{artifact_id.replace(':', '_')}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def _write_noms(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n" if records else "",
                    encoding="utf-8")


def _nom(artifact_id: str, source_path) -> dict:
    return {"artifact_id": artifact_id, "source_path": str(source_path) if source_path else None}


@pytest.fixture
def env(tmp_path):
    return {
        "db": tmp_path / "scores.db",
        "artifacts_out": tmp_path / "artifacts_out",
        "noms": tmp_path / "nominations.jsonl",
        "log": tmp_path / "nightly_harvest_log.jsonl",
    }


@pytest.fixture
def fake_panel(monkeypatch):
    """A deterministic, offline stand-in for artifact_score.score_artifact_panel: no score.sh, no
    subprocess, no network. Records a fixed overall + records the artifacts-table row exactly like
    the real function does (via record_artifact_score), so downstream idempotency checks (reading
    scores_db.fetch_artifacts) behave identically to a live run."""
    calls: list[str] = []

    def _fake(artifact, *, budget="1.50", panel_id=None, scorer_model="sonnet", is_control=False,
              control_anchor=None, source_path=None, db_path=scores_db.DB_PATH):
        calls.append(artifact["artifact_id"])
        card = {"overall": 4.4, "scores": {"d1": 4.4, "d2": 4.4}}
        artifact_score.record_artifact_score(
            artifact, card, panel_id=panel_id, scorer_model=scorer_model, is_control=is_control,
            control_anchor=control_anchor, source_path=source_path, db_path=db_path,
        )
        return card

    monkeypatch.setattr(artifact_score, "score_artifact_panel", _fake)
    return calls


@pytest.fixture
def failing_panel(monkeypatch):
    """A scorer stub that always raises — for the non-fatal-isolation test."""
    def _fake(*a, **k):
        raise RuntimeError("score.sh sentinel: quota_exhausted")
    monkeypatch.setattr(artifact_score, "score_artifact_panel", _fake)


def _run(env, **kw):
    return nightly_harvest.harvest_batch(
        nominations_path=env["noms"], db_path=env["db"], log_path=env["log"], **kw
    )


# ---------------------------------------------------------------------------
# scores only unscored nominations
# ---------------------------------------------------------------------------
def test_scores_unscored_nomination(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    report = _run(env)

    assert report["scored"] == 1
    assert fake_panel == ["quest:bg:q1"]
    rows = scores_db.fetch_artifacts(env["db"])
    assert len(rows) == 1
    assert rows[0]["artifact_id"] == "quest:bg:q1"
    assert rows[0]["overall"] == 4.4


def test_already_scored_artifact_is_not_rescored(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    scores_db.add_artifact("quest:bg:q1", db_path=env["db"], **{"class": "quest"}, overall=4.7,
                           dims_json={"d": 4.7})
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    report = _run(env)

    assert report["scored"] == 0
    assert report["already_scored"] == 1
    assert fake_panel == []  # the scorer must NEVER be called for an already-scored artifact
    rows = scores_db.fetch_artifacts(env["db"])
    assert len(rows) == 1
    assert rows[0]["overall"] == 4.7  # unchanged — not overwritten by a re-score


def test_unscored_row_with_no_overall_is_still_rescored(env, fake_panel):
    """A row present in `artifacts` but with overall=None (e.g. a prior failed/partial write) is
    treated as UNSCORED — mirrors promote.py's own "row missing OR overall is None" definition."""
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    scores_db.add_artifact("quest:bg:q1", db_path=env["db"], **{"class": "quest"}, overall=None)
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    report = _run(env)

    assert report["scored"] == 1
    assert fake_panel == ["quest:bg:q1"]


def test_empty_queue_is_additive_noop(env, fake_panel):
    _write_noms(env["noms"], [])
    report = _run(env)
    assert report == {
        "nominations_total": 0, "already_scored": 0, "candidates_unscored": 0, "scored": 0,
        "load_failed": 0, "score_failed": 0, "remaining_for_next_run": 0, "dry_run": False,
        "details": [],
    }
    assert fake_panel == []


def test_missing_nominations_file_is_noop(env, fake_panel):
    report = _run(env)  # env["noms"] was never written
    assert report["nominations_total"] == 0
    assert report["scored"] == 0
    assert fake_panel == []


# ---------------------------------------------------------------------------
# idempotency + resumability + bounded batch size
# ---------------------------------------------------------------------------
def test_idempotent_second_run_scores_nothing_new(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    first = _run(env)
    second = _run(env)

    assert first["scored"] == 1
    assert second["scored"] == 0
    assert second["already_scored"] == 1
    assert fake_panel == ["quest:bg:q1"]  # scorer called exactly once across BOTH runs
    assert len(scores_db.fetch_artifacts(env["db"])) == 1


def test_duplicate_artifact_id_in_queue_scored_once(env, fake_panel):
    """The same artifact_id nominated twice (two separate nominate.py runs appending to the same
    queue) must be scored — and billed — at most once per batch."""
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p), _nom("quest:bg:q1", p)])

    report = _run(env)

    assert report["scored"] == 1
    assert fake_panel == ["quest:bg:q1"]


def test_max_per_run_caps_the_batch_and_reports_remainder(env, fake_panel):
    for i in range(5):
        p = _artifact_json(env["artifacts_out"], f"quest:bg:q{i}")
        with env["noms"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_nom(f"quest:bg:q{i}", p)) + "\n")

    report = _run(env, max_per_run=2)

    assert report["scored"] == 2
    assert report["candidates_unscored"] == 5
    assert report["remaining_for_next_run"] == 3
    assert len(fake_panel) == 2


def test_resumable_across_two_capped_runs_drains_the_queue(env, fake_panel):
    for i in range(3):
        p = _artifact_json(env["artifacts_out"], f"quest:bg:q{i}")
        with env["noms"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_nom(f"quest:bg:q{i}", p)) + "\n")

    first = _run(env, max_per_run=2)
    second = _run(env, max_per_run=2)

    assert first["scored"] == 2
    assert second["scored"] == 1  # the leftover 1 from the first, capped run
    assert second["remaining_for_next_run"] == 0
    assert len(scores_db.fetch_artifacts(env["db"])) == 3
    assert sorted(fake_panel) == ["quest:bg:q0", "quest:bg:q1", "quest:bg:q2"]


def test_negative_max_per_run_rejected_at_cli(env, capsys):
    with pytest.raises(SystemExit):
        nightly_harvest.main(["--nominations", str(env["noms"]), "--db", str(env["db"]),
                              "--max-per-run", "-1"])


# ---------------------------------------------------------------------------
# non-fatal isolation: one bad artifact never aborts the batch
# ---------------------------------------------------------------------------
def test_load_failed_artifact_does_not_abort_batch(env, fake_panel):
    good = _artifact_json(env["artifacts_out"], "quest:bg:good")
    _write_noms(env["noms"], [
        _nom("quest:bg:missing-file", env["artifacts_out"] / "does_not_exist.json"),
        _nom("quest:bg:good", good),
    ])

    report = _run(env)

    assert report["load_failed"] == 1
    assert report["scored"] == 1
    assert fake_panel == ["quest:bg:good"]


def test_nomination_with_no_source_path_is_load_failed(env, fake_panel):
    _write_noms(env["noms"], [_nom("quest:bg:q1", None)])
    report = _run(env)
    assert report["load_failed"] == 1
    assert report["scored"] == 0
    assert fake_panel == []


def test_mismatched_artifact_id_is_load_failed(env, fake_panel):
    """source_path resolves to a DIFFERENT artifact_id than the nomination claims — refuse to score
    the wrong artifact (mirrors promote.py's score_if_unscored guard)."""
    p = _artifact_json(env["artifacts_out"], "quest:bg:actual")
    _write_noms(env["noms"], [_nom("quest:bg:claimed", p)])

    report = _run(env)

    assert report["load_failed"] == 1
    assert report["scored"] == 0
    assert fake_panel == []


def test_score_failed_artifact_does_not_abort_batch(env, failing_panel):
    good = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    other = _artifact_json(env["artifacts_out"], "quest:bg:q2")
    _write_noms(env["noms"], [_nom("quest:bg:q1", good), _nom("quest:bg:q2", other)])

    report = _run(env)

    assert report["score_failed"] == 2
    assert report["scored"] == 0
    assert len(scores_db.fetch_artifacts(env["db"])) == 0


def test_score_failed_artifact_is_retried_next_run(env):
    """A score-failed artifact must NOT be permanently skipped — it stays a candidate until scored."""
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    import artifact_score as _as
    calls = {"n": 0}

    def _flaky(artifact, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        card = {"overall": 4.2, "scores": {"d": 4.2}}
        _as.record_artifact_score(artifact, card, db_path=kw.get("db_path", env["db"]))
        return card

    orig = _as.score_artifact_panel
    _as.score_artifact_panel = _flaky
    try:
        first = _run(env)
        second = _run(env)
    finally:
        _as.score_artifact_panel = orig

    assert first["score_failed"] == 1
    assert first["scored"] == 0
    assert second["scored"] == 1
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# dry-run: pure preview, zero scoring calls, zero writes
# ---------------------------------------------------------------------------
def test_dry_run_reports_would_score_and_writes_nothing(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    report = _run(env, dry_run=True)

    assert report["dry_run"] is True
    assert report["would_score"] == ["quest:bg:q1"]
    assert fake_panel == []  # the scorer is NEVER invoked under --dry-run
    assert len(scores_db.fetch_artifacts(env["db"])) == 0
    assert not env["log"].exists()


def test_dry_run_respects_max_per_run_cap(env, fake_panel):
    for i in range(3):
        p = _artifact_json(env["artifacts_out"], f"quest:bg:q{i}")
        with env["noms"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_nom(f"quest:bg:q{i}", p)) + "\n")

    report = _run(env, dry_run=True, max_per_run=1)

    assert len(report["would_score"]) == 1
    assert report["candidates_unscored"] == 3


# ---------------------------------------------------------------------------
# progress log (this module's OWN log — separate from library/.promoted.jsonl)
# ---------------------------------------------------------------------------
def test_log_appends_one_line_per_attempt(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    _run(env)

    lines = [json.loads(l) for l in env["log"].read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["artifact_id"] == "quest:bg:q1"
    assert lines[0]["verdict"] == "scored"
    assert lines[0]["overall"] == 4.4


def test_log_records_failures_too(env, failing_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])

    _run(env)

    lines = [json.loads(l) for l in env["log"].read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["verdict"] == "score-failed"
    assert "error" in lines[0]


# ---------------------------------------------------------------------------
# malformed queue lines are skipped, not fatal (tolerant reader — a nightly batch outlives typos)
# ---------------------------------------------------------------------------
def test_malformed_queue_line_is_skipped_not_fatal(env, fake_panel):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    env["noms"].write_text(
        "not json at all\n" + json.dumps(_nom("quest:bg:q1", p)) + "\n{}\n",
        encoding="utf-8",
    )

    report = _run(env)

    assert report["nominations_total"] == 1  # only the one well-formed, artifact_id-bearing line
    assert report["scored"] == 1


# ---------------------------------------------------------------------------
# never touches library/ — a fresh assertion the harvest batch stays scoring-only
# ---------------------------------------------------------------------------
def test_never_writes_library_dir(env, fake_panel, tmp_path):
    p = _artifact_json(env["artifacts_out"], "quest:bg:q1")
    _write_noms(env["noms"], [_nom("quest:bg:q1", p)])
    library_dir = tmp_path / "library"

    _run(env)

    assert not library_dir.exists()
