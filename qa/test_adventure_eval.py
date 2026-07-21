"""Offline unit tests for qa/adventure_eval.py — the A-T N-run aggregator.

Builds SYNTHETIC run dirs (the ``<prefix>.*`` artifact files a real run_adventure.sh run would
leave) and asserts the per-dimension aggregate, the weakest-link pick + lever, and the persisted
scores_db row — no LLM, no engine, no `claude -p`.

Single-process:
    uv run --directory servers/engine python -m pytest qa/test_adventure_eval.py -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

QA = Path(__file__).resolve().parent
sys.path.insert(0, str(QA))

import adventure_eval as ae  # noqa: E402
import scores_db  # noqa: E402
from scoring_config_version import adventure_config_version  # noqa: E402


def _write_run(
    tmp: Path,
    run_id: str,
    *,
    completed: bool = True,
    complete_beat: int = 6,
    stages: list[tuple[str, int]] | None = None,
    story: float | None = 4.2,
    mech: float | None = 4.0,
    angrydm: float | None = 4.1,
    behavioral: str | None = "GREEN",
    engagement_pct: float | None = 0.75,
    dead_beats: int | None = 0,
    wall_s: float | None = 300.0,
    s_per_beat: float | None = 20.0,
) -> str:
    """Write one synthetic run's artifacts under tmp/<run_id>.* and return the prefix."""
    prefix = str(tmp / run_id)

    # quest_trace.json — arc stamps
    if stages is None:
        stages = [("reached_giver", 1), ("quest_accepted", 2), ("entered_dungeon", 3),
                  ("boss_dead", 4), ("reward_received", 5), ("quest_completed", complete_beat)]
        if not completed:
            stages = stages[:3]  # never reached the boss/completion
    trace = {
        "campaign_id": "adventure_demo_v1",
        "quest_status": "completed" if completed else "active",
        "stamps": [{"stage": s, "beat": b, "ts": "t", "signal": "objective:x"} for s, b in stages],
    }
    Path(f"{prefix}.quest_trace.json").write_text(json.dumps(trace))

    # lenses
    for suffix, val in (("tolkien", story), ("score", mech), ("angrydm", angrydm)):
        if val is not None:
            Path(f"{prefix}.{suffix}.json").write_text(json.dumps({"overall": val}))

    # behavioral gate + summary
    if behavioral is not None:
        gate = "[PASS] all good\n" if behavioral == "GREEN" else "[FAIL] player_turns_structured\n"
        Path(f"{prefix}.gate.txt").write_text(gate)

    summary = {"behavioral": behavioral, "engagement_pct": engagement_pct, "dead_beats": dead_beats}
    Path(f"{prefix}.adventure.json").write_text(json.dumps(summary))

    # latency
    Path(f"{prefix}.latency.json").write_text(json.dumps(
        {"duration_wall_s": wall_s, "s_per_beat": s_per_beat, "failed_beats": dead_beats or 0}))
    return prefix


# ── tests ─────────────────────────────────────────────────────────────────────────────────────

def test_all_complete_high_quality(tmp_path):
    prefixes = [_write_run(tmp_path, f"adv{i}") for i in range(3)]
    agg = ae.aggregate(prefixes)
    assert agg["n"] == 3
    assert agg["completion_rate"] == 1.0
    assert agg["median_beats_to_complete"] == 6
    assert agg["green_rate"] == 1.0
    assert agg["dimensions"]["completion"] == 1.0
    assert agg["dimensions"]["behavioral"] == 1.0
    # story 4.2/5 = 0.84, mechanics mean(4.0,4.1)/5 = 0.81
    assert abs(agg["dimensions"]["story"] - 0.84) < 0.01
    assert abs(agg["dimensions"]["mechanics"] - 0.81) < 0.02


def test_weakest_link_is_the_lowest_dimension(tmp_path):
    # Engagement is deliberately the floor -> it must win the weakest-link pick and its lever prints.
    prefixes = [
        _write_run(tmp_path, "adv0", engagement_pct=0.20),
        _write_run(tmp_path, "adv1", engagement_pct=0.25),
    ]
    agg = ae.aggregate(prefixes)
    assert agg["weakest_link"] == "engagement"
    assert agg["dimensions"]["engagement"] < 0.3
    lever = ae.load_config()["dimensions"]["engagement"]["lever"]
    assert lever in agg["verdict"]
    assert agg["verdict"].startswith("WEAKEST-LINK: engagement")


def test_partial_completion_rate_and_median_beats(tmp_path):
    prefixes = [
        _write_run(tmp_path, "adv0", completed=True, complete_beat=8),
        _write_run(tmp_path, "adv1", completed=False),
        _write_run(tmp_path, "adv2", completed=True, complete_beat=10),
    ]
    agg = ae.aggregate(prefixes)
    assert abs(agg["completion_rate"] - (2 / 3)) < 0.01
    assert agg["median_beats_to_complete"] == 9  # median of [8, 10]
    # a non-completion drags completion below the 1.0 bar
    assert agg["dimensions"]["completion"] < 1.0


def test_stuck_detection_dead_beats_and_stage_gap(tmp_path):
    # adv0: clean. adv1: dead beats over threshold. adv2: a big stage gap (2 -> 12) outlier.
    p0 = _write_run(tmp_path, "adv0", dead_beats=0)
    p1 = _write_run(tmp_path, "adv1", dead_beats=3)
    p2 = _write_run(tmp_path, "adv2", dead_beats=0,
                    stages=[("reached_giver", 1), ("quest_accepted", 2),
                            ("entered_dungeon", 12), ("quest_completed", 14)])
    agg = ae.aggregate([p0, p1, p2])
    # 2 of 3 runs stuck -> stuck_rate ~0.667, stuck dimension ~0.333
    assert abs(agg["stuck_rate"] - (2 / 3)) < 0.01
    assert abs(agg["dimensions"]["stuck"] - (1 / 3)) < 0.01
    per = {r["run"]: r for r in agg["runs"]}
    assert per["adv1"]["stuck"] and per["adv1"]["dead_beats"] == 3
    assert per["adv2"]["stuck"] and per["adv2"]["stage_gap_outlier"]
    assert not per["adv0"]["stuck"]


def test_missing_artifacts_are_tolerated(tmp_path):
    # A run with ONLY a quest_trace (no lenses, no behavioral, no engagement).
    prefix = str(tmp_path / "bare")
    Path(f"{prefix}.quest_trace.json").write_text(json.dumps(
        {"quest_status": "completed",
         "stamps": [{"stage": "quest_completed", "beat": 7, "signal": "status:completed"}]}))
    agg = ae.aggregate([prefix])
    assert agg["completion_rate"] == 1.0
    assert agg["dimensions"]["story"] is None
    assert agg["dimensions"]["mechanics"] is None
    assert agg["dimensions"]["behavioral"] is None
    # weakest-link only picks among dimensions WITH data (completion/pace/stuck are always present).
    assert agg["weakest_link"] in ("completion", "pace", "stuck", "engagement")


def test_persist_writes_one_adventure_row(tmp_path):
    prefixes = [_write_run(tmp_path, f"adv{i}") for i in range(3)]
    agg = ae.aggregate(prefixes)
    db = tmp_path / "scores.db"
    ae.persist_row(agg, run_id="adv-agg-test", db_path=str(db), build_sha="deadbeef",
                   source_path=str(tmp_path))
    rows = scores_db.fetch_rows(str(db))
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "adv-agg-test"
    assert row["surface"] == "adventure"
    assert row["methodology"] == "arc-duo N=3"
    assert row["behavioral"] == "GREEN"
    assert "WEAKEST-LINK:" in row["notes"]
    assert adventure_config_version() in row["notes"]  # av_ ruler stamped in notes
    assert row["pass"] == 1
    # the engine-duo lens ruler is still auto-stamped (the lens numbers are the same rubrics)
    assert row["lens_config_version"] and row["lens_config_version"].startswith("lc_")


def test_persist_red_and_fail_when_completion_below_bar(tmp_path):
    prefixes = [
        _write_run(tmp_path, "adv0", completed=False, behavioral="RED"),
        _write_run(tmp_path, "adv1", completed=False, behavioral="RED"),
    ]
    agg = ae.aggregate(prefixes)
    db = tmp_path / "scores.db"
    ae.persist_row(agg, run_id="adv-red", db_path=str(db))
    row = scores_db.fetch_rows(str(db))[0]
    assert row["behavioral"] == "RED"
    assert row["pass"] == 0
