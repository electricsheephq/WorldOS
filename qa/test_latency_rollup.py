#!/usr/bin/env python3
"""Tests for qa/latency_rollup.py — the F13-4 latency-ledger derivation.

Pure-stdlib; builds synthetic stream-json DM beat transcripts so the rollup is verified
against KNOWN inputs (independent of the gitignored qa/transcripts/* fixtures). Run with:
    uv run --directory servers/engine python -m pytest qa/test_latency_rollup.py -q -p no:xdist
or:
    python3 -m pytest qa/test_latency_rollup.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import latency_rollup  # noqa: E402


def _write_beat(d: Path, run: str, nanos: int, *, api_ms, num_turns=1,
                is_error=False, api_error_status=None, with_field=True):
    """Write a minimal stream-json transcript whose terminal result event carries the
    given duration_api_ms / num_turns (the only fields the rollup reads)."""
    res = {"type": "result", "subtype": "success", "is_error": is_error,
           "api_error_status": api_error_status, "duration_ms": (api_ms or 0) + 3000,
           "num_turns": num_turns, "result": "prose"}
    if with_field and api_ms is not None:
        res["duration_api_ms"] = api_ms
    path = d / f"{run}.dm.{nanos}.jsonl"
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": "…"}}),
        json.dumps(res),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cold_open_separated_from_routine_mean(tmp_path):
    run = "duo-r"
    # beat 0 (cold open): 240s / 18 turns. beats 1-3 (routine): 100/80/120s, 4/3/5 turns.
    _write_beat(tmp_path, run, 1000, api_ms=240000, num_turns=18)
    _write_beat(tmp_path, run, 2000, api_ms=100000, num_turns=4)
    _write_beat(tmp_path, run, 3000, api_ms=80000, num_turns=3)
    _write_beat(tmp_path, run, 4000, api_ms=120000, num_turns=5)
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["coldopen_s"] == 240.0           # the first beat, reported separately
    assert r["s_per_beat"] == 100.0           # mean(100,80,120) — cold open EXCLUDED
    assert r["turns_per_beat"] == 4.0         # mean(4,3,5)
    assert r["beats"] == 4 and r["failed_beats"] == 0


def test_beats_ordered_by_filename_nanos_not_glob_order(tmp_path):
    # The cold open is the EARLIEST nanosecond timestamp even if written/globbed out of order.
    run = "duo-o"
    _write_beat(tmp_path, run, 5000, api_ms=90000, num_turns=4)    # routine, later
    _write_beat(tmp_path, run, 1000, api_ms=300000, num_turns=20)  # COLD OPEN, earliest
    _write_beat(tmp_path, run, 3000, api_ms=110000, num_turns=6)   # routine, middle
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["coldopen_s"] == 300.0                 # nanos=1000 beat, not the first globbed
    assert r["s_per_beat"] == 100.0                 # mean(90,110)


def test_failed_beats_are_excluded(tmp_path):
    # A 401-class failed beat (is_error / api_error_status) is dropped from every statistic.
    run = "duo-f"
    _write_beat(tmp_path, run, 1000, api_ms=200000, num_turns=10)               # cold open
    _write_beat(tmp_path, run, 2000, api_ms=100000, num_turns=4)                # routine OK
    _write_beat(tmp_path, run, 3000, api_ms=1164, num_turns=1, is_error=True)   # 401, drop
    _write_beat(tmp_path, run, 4000, api_ms=0, num_turns=1, api_error_status="401")  # drop
    _write_beat(tmp_path, run, 5000, api_ms=120000, num_turns=6)               # routine OK
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["failed_beats"] == 2
    assert r["beats"] == 3                          # cold open + 2 good routine beats
    assert r["s_per_beat"] == 110.0                 # mean(100,120) — the failed beats gone


def test_single_cold_open_only_yields_null_routine(tmp_path):
    # A run with ONLY a cold open (no continuing beat) records coldopen_s but NULL routine
    # stats — never a misleading 0.
    run = "duo-1"
    _write_beat(tmp_path, run, 1000, api_ms=180000, num_turns=12)
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["coldopen_s"] == 180.0
    assert r["s_per_beat"] is None and r["turns_per_beat"] is None
    assert r["beats"] == 1


def test_empty_run_is_all_null(tmp_path):
    r = latency_rollup.rollup_run(tmp_path, "no-such-run")
    assert r["coldopen_s"] is None and r["s_per_beat"] is None
    assert r["turns_per_beat"] is None and r["beats"] == 0


def test_missing_duration_api_ms_beat_is_skipped(tmp_path):
    # A beat whose result lacks duration_api_ms can't be costed — skip it (don't crash).
    run = "duo-m"
    _write_beat(tmp_path, run, 1000, api_ms=200000, num_turns=10)        # cold open
    _write_beat(tmp_path, run, 2000, api_ms=None, with_field=False, num_turns=4)  # no api_ms
    _write_beat(tmp_path, run, 3000, api_ms=100000, num_turns=5)         # routine
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["beats"] == 2                           # the no-api_ms beat dropped
    assert r["s_per_beat"] == 100.0


def test_negative_api_ms_clamped_to_zero(tmp_path):
    # VM parallel-segment artifact: a negative/oversized api split clamps at 0 (audit caveat).
    run = "duo-n"
    _write_beat(tmp_path, run, 1000, api_ms=200000, num_turns=10)
    _write_beat(tmp_path, run, 2000, api_ms=-5000, num_turns=3)
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["s_per_beat"] == 0.0                    # clamped, not negative


def test_cli_writes_out_json(tmp_path, capsys):
    run = "duo-cli"
    _write_beat(tmp_path, run, 1000, api_ms=240000, num_turns=18)
    _write_beat(tmp_path, run, 2000, api_ms=100000, num_turns=4)
    out = tmp_path / "lat.json"
    rc = latency_rollup._main(["--dir", str(tmp_path), "--run", run, "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["coldopen_s"] == 240.0 and data["s_per_beat"] == 100.0


# ── stamp_sidecars: the bridge that activates the RRI latency gate ─────────────────
# The runners derive the ledger into the TRANSCRIPT dir; release_readiness reads each PERSONA
# run dir's <run>/latency.json sidecar. These cover the stamp that closes that gap.

def test_stamp_sidecars_writes_per_run_latency_json(tmp_path):
    run = "duo-stamp"
    _write_beat(tmp_path, run, 1000, api_ms=240000, num_turns=18)   # cold open 240s
    _write_beat(tmp_path, run, 2000, api_ms=100000, num_turns=4)    # routine 100s
    r = latency_rollup.rollup_run(tmp_path, run)
    rundirs = [tmp_path / "gate-newbie", tmp_path / "gate-veteran"]
    for d in rundirs:
        d.mkdir()
    written = latency_rollup.stamp_sidecars(r, rundirs)
    assert len(written) == 2
    for d in rundirs:
        sidecar = json.loads((d / "latency.json").read_text())
        # exactly the columns release_readiness.read_latency() consumes
        assert sidecar["s_per_beat"] == 100.0
        assert sidecar["coldopen_s"] == 240.0
        assert sidecar["turns_per_beat"] == 4.0


def test_stamp_sidecars_skips_nonexistent_dirs_never_creating_them(tmp_path):
    # A stale/typo run-dir path must never fabricate latency evidence by creating a dir.
    run = "duo-skip"
    _write_beat(tmp_path, run, 1000, api_ms=200000, num_turns=10)
    _write_beat(tmp_path, run, 2000, api_ms=90000, num_turns=4)
    r = latency_rollup.rollup_run(tmp_path, run)
    real = tmp_path / "gate-real"; real.mkdir()
    missing = tmp_path / "gate-missing"          # does NOT exist
    written = latency_rollup.stamp_sidecars(r, [real, missing])
    assert written == [str(real / "latency.json")]
    assert not missing.exists()


def test_stamp_sidecars_preserves_null_columns_not_zero(tmp_path):
    # A cold-open-only run has NULL routine stats; the sidecar must keep null so read_latency
    # treats it as ABSENT (a skip), never a fabricated 0.0 that silently passes the gate.
    run = "duo-null"
    _write_beat(tmp_path, run, 1000, api_ms=180000, num_turns=12)   # cold open only
    r = latency_rollup.rollup_run(tmp_path, run)
    d = tmp_path / "gate-x"; d.mkdir()
    latency_rollup.stamp_sidecars(r, [d])
    sidecar = json.loads((d / "latency.json").read_text())
    assert sidecar["s_per_beat"] is None
    assert sidecar["coldopen_s"] == 180.0


def test_cli_stamp_into_writes_sidecars(tmp_path):
    # The exact path qa/release_gate.sh drives: --dir/--run + --stamp-into "dir1,dir2".
    run = "duo-clistamp"
    _write_beat(tmp_path, run, 1000, api_ms=240000, num_turns=18)
    _write_beat(tmp_path, run, 2000, api_ms=130000, num_turns=5)
    d1 = tmp_path / "gate-a"; d1.mkdir()
    d2 = tmp_path / "gate-b"; d2.mkdir()
    rc = latency_rollup._main(["--dir", str(tmp_path), "--run", run, "--stamp-into", f"{d1},{d2}"])
    assert rc == 0
    for d in (d1, d2):
        sidecar = json.loads((d / "latency.json").read_text())
        assert sidecar["coldopen_s"] == 240.0 and sidecar["s_per_beat"] == 130.0
