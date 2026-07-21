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


# ── item 15: STAGES bound to quest_progress (no hand-mirrored drift) ─────────────────────────────

def test_stages_bound_to_quest_progress():
    import quest_progress as qp  # noqa: PLC0415
    assert tuple(ae.STAGES) == tuple(qp.STAGES)
    assert ae._STAGES_FALLBACK == tuple(qp.STAGES)  # the literal fallback never silently drifts


# ── item 20: the av_ ruler fences the aggregation FORMULAS too ───────────────────────────────────

def test_adventure_ruler_fences_formulas():
    import scoring_config_version as scv  # noqa: PLC0415
    assert "adventure_eval.py" in scv.ADVENTURE_CONFIG_FILES
    assert "adventure_eval_config.json" in scv.ADVENTURE_CONFIG_FILES


# ── item 3: stage-gap outlier uses abs() (guards corrupted/partial traces) ───────────────────────

def test_stage_gap_outlier_uses_abs():
    # A BACKWARDS jump (a corrupted/partial trace) beyond the threshold is still an outlier.
    backwards = [{"stage": "reached_giver", "beat": 20}, {"stage": "quest_accepted", "beat": 2}]
    assert ae._stage_gap_outlier(backwards, 5) is True
    # A forward step within the threshold is NOT an outlier.
    fwd = [{"stage": "reached_giver", "beat": 1}, {"stage": "quest_accepted", "beat": 3}]
    assert ae._stage_gap_outlier(fwd, 5) is False


# ── item 5: completion honesty (a terminal "failed" is NOT a completion) ─────────────────────────

def test_failed_quest_is_not_a_completion(tmp_path):
    prefix = str(tmp_path / "advfail")
    Path(f"{prefix}.quest_trace.json").write_text(json.dumps({
        "quest_status": "failed",
        "stamps": [{"stage": "reached_giver", "beat": 1, "signal": "objective:x"},
                   {"stage": "quest_completed", "beat": 4, "signal": "status:failed"}]}))
    agg = ae.aggregate([prefix])
    assert agg["completion_rate"] == 0.0
    per = {r["run"]: r for r in agg["runs"]}
    assert per["advfail"]["completed"] is False
    assert per["advfail"]["beats_to_complete"] is None


def test_completed_status_signal_counts(tmp_path):
    prefix = str(tmp_path / "advok")
    Path(f"{prefix}.quest_trace.json").write_text(json.dumps({
        "quest_status": "completed",
        "stamps": [{"stage": "quest_completed", "beat": 5, "signal": "status:completed"}]}))
    agg = ae.aggregate([prefix])
    assert agg["completion_rate"] == 1.0
    assert agg["median_beats_to_complete"] == 5


# ── item 17: behavioral GREEN requires POSITIVE evidence, not the absence of [FAIL] ──────────────

def test_behavioral_requires_positive_green_evidence(tmp_path):
    prefix = str(tmp_path / "advb")
    # A truncated gate (header + a [PASS] but NO terminal GREEN marker) -> None, never assumed GREEN.
    Path(f"{prefix}.gate.txt").write_text("=== behavioral assertions ===\n  [PASS] some_check\n")
    assert ae._behavioral(prefix, None) is None
    # A gate carrying the terminal GREEN verdict -> GREEN.
    Path(f"{prefix}.gate.txt").write_text("=== behavioral assertions ===\n  [PASS] some_check\nGREEN\n")
    assert ae._behavioral(prefix, None) == "GREEN"
    Path(f"{prefix}.gate.txt").write_text("=== behavioral assertions ===\n  [PASS] x\nGREEN (2 warning(s))\n")
    assert ae._behavioral(prefix, None) == "GREEN"
    # A [FAIL] line -> RED.
    Path(f"{prefix}.gate.txt").write_text("=== behavioral assertions ===\n  [FAIL] x\n")
    assert ae._behavioral(prefix, None) == "RED"
    # Empty gate -> None.
    Path(f"{prefix}.gate.txt").write_text("")
    assert ae._behavioral(prefix, None) is None


# ── item 1: MIXED behavioral + pass requires gate evidence ───────────────────────────────────────

def test_persist_mixed_behavioral(tmp_path):
    prefixes = [
        _write_run(tmp_path, "adv0", behavioral="GREEN"),
        _write_run(tmp_path, "adv1", behavioral="RED"),
    ]
    agg = ae.aggregate(prefixes)
    assert agg["green_rate"] == 0.5
    db = tmp_path / "scores.db"
    fields = ae.persist_row(agg, run_id="adv-mixed", db_path=str(db))
    assert fields["behavioral"] == "MIXED"
    row = scores_db.fetch_rows(str(db))[0]
    assert row["behavioral"] == "MIXED"
    assert row["pass"] == 1  # green_rate 0.5 >= 0.5 and completion 1.0 >= bar


def test_persist_missing_behavioral_fails_pass(tmp_path):
    # No behavioral evidence on any run -> behavioral None, pass=0 even at full completion.
    prefixes = [_write_run(tmp_path, f"adv{i}", behavioral=None) for i in range(2)]
    agg = ae.aggregate(prefixes)
    assert agg["green_rate"] is None
    db = tmp_path / "scores.db"
    fields = ae.persist_row(agg, run_id="adv-nobehav", db_path=str(db))
    assert fields["behavioral"] is None
    row = scores_db.fetch_rows(str(db))[0]
    assert row["pass"] == 0


# ── item 14: the av_ ADVENTURE ruler round-trips as a first-class column ──────────────────────────

def test_persist_stamps_adventure_config_version_column(tmp_path):
    prefixes = [_write_run(tmp_path, f"adv{i}") for i in range(2)]
    agg = ae.aggregate(prefixes)
    db = tmp_path / "scores.db"
    fields = ae.persist_row(agg, run_id="adv-avcol", db_path=str(db))
    av = adventure_config_version()
    assert fields["adventure_config_version"] == av
    assert av.startswith("av_")
    row = scores_db.fetch_rows(str(db))[0]
    assert row["adventure_config_version"] == av
    assert av in row["notes"]  # the notes citation matches the stamped column (guard stays happy)


# ── item 6: model provenance (recorded > default; CLI override wins) ──────────────────────────────

def test_persist_reads_model_provenance_from_summaries(tmp_path):
    p0 = _write_run(tmp_path, "adv0")
    s = json.loads(Path(f"{p0}.adventure.json").read_text())
    s["dm_model"] = "gpt-5.5"
    s["actor_model"] = "sonnet-5"
    Path(f"{p0}.adventure.json").write_text(json.dumps(s))
    agg = ae.aggregate([p0])
    assert agg["dm_model_recorded"] == "gpt-5.5"
    db = tmp_path / "scores.db"
    fields = ae.persist_row(agg, run_id="adv-prov", db_path=str(db))
    assert fields["dm_model"] == "gpt-5.5" and fields["actor_model"] == "sonnet-5"
    assert "provenance:defaulted" not in fields["notes"]
    # A CLI override wins over the recorded value.
    fields2 = ae.persist_row(agg, run_id="adv-prov2", db_path=str(db), dm_model="opus-override")
    assert fields2["dm_model"] == "opus-override"


def test_persist_defaults_models_when_unrecorded(tmp_path):
    p = _write_run(tmp_path, "adv0")  # summary carries no dm_model/actor_model
    agg = ae.aggregate([p])
    db = tmp_path / "scores.db"
    fields = ae.persist_row(agg, run_id="adv-def", db_path=str(db))
    assert fields["dm_model"] == "opus" and fields["actor_model"] == "sonnet"
    assert "provenance:defaulted(dm+actor)" in fields["notes"]


# ── item 19: launched-run validation (infra-contamination gate) ──────────────────────────────────

def test_validate_launched_run(tmp_path):
    prefix = str(tmp_path / "advL")
    # No artifacts at all -> contaminated.
    ok, reason = ae._validate_launched_run(prefix, 0)
    assert not ok and "missing" in reason
    # Write the core artifacts.
    Path(f"{prefix}.adventure.json").write_text("{}")
    Path(f"{prefix}.gate.txt").write_text("=== behavioral assertions ===\nGREEN\n")
    Path(f"{prefix}.quest_trace.json").write_text("{}")
    # Clean GREEN exit -> ok.
    assert ae._validate_launched_run(prefix, 0) == (True, "")
    # Behavioral RED exit (1) with full artifacts -> STILL ok (RED is a valid product measurement).
    assert ae._validate_launched_run(prefix, 1)[0] is True
    # An abort exit code (EX_TEMPFAIL) -> contaminated.
    ok, reason = ae._validate_launched_run(prefix, 75)
    assert not ok and "aborted" in reason
    # A contaminated-flagged summary -> contaminated even on a clean exit.
    Path(f"{prefix}.adventure.json").write_text(json.dumps({"contaminated": True, "contaminated_reason": "quota"}))
    ok, reason = ae._validate_launched_run(prefix, 0)
    assert not ok and "contaminated" in reason
