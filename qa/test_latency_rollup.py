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


def _usage(*, input_t, cache_creation, cache_read, output_t):
    return {"input_tokens": input_t, "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read, "output_tokens": output_t}


def _write_beat(d: Path, run: str, nanos: int, *, api_ms, num_turns=1,
                is_error=False, api_error_status=None, with_field=True,
                usage=None, ttft_ms=None):
    """Write a minimal stream-json transcript whose terminal result event carries the
    given duration_api_ms / num_turns (+ optional usage token block / ttft_ms)."""
    res = {"type": "result", "subtype": "success", "is_error": is_error,
           "api_error_status": api_error_status, "duration_ms": (api_ms or 0) + 3000,
           "num_turns": num_turns, "result": "prose"}
    if with_field and api_ms is not None:
        res["duration_api_ms"] = api_ms
    if usage is not None:
        res["usage"] = usage
    if ttft_ms is not None:
        res["ttft_ms"] = ttft_ms
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


# ── tool-schema-slab A/B (Phase 3): token/cache ledger + arm comparator ─────────────
# The slab is a per-request PREFILL tax, so the A/B reads the token ledger (cache_creation/
# cache_read/input + ttft), and the gate is cold-open-not-worse + routine-not-worse + cache-not-
# dented. These verify the parsing and the PASS/FAIL/INSUFFICIENT_DATA verdict logic.

def test_token_aggregates_cold_open_vs_routine(tmp_path):
    run = "duo-tok"
    # cold open: heavy cache CREATION (building the prefix); routine: cache_creation ~0 (cached).
    _write_beat(tmp_path, run, 1000, api_ms=240000, num_turns=18, ttft_ms=8000,
                usage=_usage(input_t=20000, cache_creation=150000, cache_read=1400000, output_t=12000))
    _write_beat(tmp_path, run, 2000, api_ms=100000, num_turns=4, ttft_ms=2000,
                usage=_usage(input_t=18000, cache_creation=0, cache_read=1500000, output_t=9000))
    _write_beat(tmp_path, run, 3000, api_ms=120000, num_turns=5, ttft_ms=2200,
                usage=_usage(input_t=20000, cache_creation=0, cache_read=1520000, output_t=11000))
    tok = latency_rollup.rollup_run(tmp_path, run)["tokens"]
    assert tok["coldopen"]["cache_creation"] == 150000.0
    assert tok["coldopen"]["ttft_ms"] == 8000.0
    assert tok["routine"]["cache_creation"] == 0.0          # mean(0, 0) — a healthy cached prefix
    assert tok["routine"]["input"] == 19000.0               # mean(18000, 20000)
    assert tok["routine"]["ttft_ms"] == 2100.0              # mean(2000, 2200)


def _arm(coldopen_s, s_per_beat, *, co_cc, ro_cc, ro_input, ro_ttft=2000.0):
    """A minimal rollup shaped like rollup_files() output, for comparator tests."""
    return {
        "coldopen_s": coldopen_s, "s_per_beat": s_per_beat, "turns_per_beat": 4.0, "beats": 4,
        "tokens": {
            "coldopen": {"cache_creation": co_cc, "cache_read": 1_400_000.0, "input": 20000.0,
                         "output": 12000.0, "ttft_ms": 8000.0},
            "routine": {"cache_creation": ro_cc, "cache_read": 1_500_000.0, "input": ro_input,
                        "output": 9000.0, "ttft_ms": ro_ttft},
        },
    }


def test_compare_arms_pass_when_tiered_not_worse_and_cache_clean():
    base = _arm(240.0, 100.0, co_cc=160000.0, ro_cc=0.0, ro_input=40000.0)
    tiered = _arm(236.0, 98.0, co_cc=150000.0, ro_cc=0.0, ro_input=26000.0)  # faster, leaner, clean
    cmp = latency_rollup.compare_arms(base, tiered)
    assert cmp["verdict"] == "PASS"
    assert cmp["checks"]["cold_open_not_worse"] and cmp["checks"]["routine_not_worse"]
    assert cmp["checks"]["cache_not_dented"] and cmp["checks"]["input_mass_down"]
    # the WIN is surfaced in metrics (input prefill mass down)
    assert cmp["metrics"]["routine_input"]["delta"] == -14000.0


def test_compare_arms_fail_on_cache_dent():
    base = _arm(240.0, 100.0, co_cc=160000.0, ro_cc=0.0, ro_input=40000.0)
    # tiered re-creates the prompt cache on routine beats (15k > 0 + 2k tol) -> DENT -> FAIL
    tiered = _arm(236.0, 98.0, co_cc=150000.0, ro_cc=15000.0, ro_input=26000.0)
    cmp = latency_rollup.compare_arms(base, tiered)
    assert cmp["verdict"] == "FAIL"
    assert cmp["checks"]["cache_not_dented"] is False


def test_compare_arms_fail_on_cold_open_regression():
    base = _arm(240.0, 100.0, co_cc=160000.0, ro_cc=0.0, ro_input=40000.0)
    tiered = _arm(300.0, 98.0, co_cc=150000.0, ro_cc=0.0, ro_input=26000.0)  # cold open +25% -> worse
    cmp = latency_rollup.compare_arms(base, tiered)
    assert cmp["verdict"] == "FAIL"
    assert cmp["checks"]["cold_open_not_worse"] is False


def test_compare_arms_insufficient_data_when_metric_missing():
    base = _arm(240.0, 100.0, co_cc=160000.0, ro_cc=0.0, ro_input=40000.0)
    thin = {"coldopen_s": 236.0, "s_per_beat": 98.0, "tokens": {"coldopen": {}, "routine": None}}
    cmp = latency_rollup.compare_arms(base, thin)
    assert cmp["verdict"] == "INSUFFICIENT_DATA"      # routine cache_creation absent on arm B


def test_cli_compare_exit_codes(tmp_path):
    base = tmp_path / "a.json"
    tiered_ok = tmp_path / "b_ok.json"
    tiered_bad = tmp_path / "b_bad.json"
    base.write_text(json.dumps(_arm(240.0, 100.0, co_cc=160000.0, ro_cc=0.0, ro_input=40000.0)))
    tiered_ok.write_text(json.dumps(_arm(236.0, 98.0, co_cc=150000.0, ro_cc=0.0, ro_input=26000.0)))
    tiered_bad.write_text(json.dumps(_arm(236.0, 98.0, co_cc=150000.0, ro_cc=15000.0, ro_input=26000.0)))
    assert latency_rollup._main(["--compare", str(base), str(tiered_ok)]) == 0
    assert latency_rollup._main(["--compare", str(base), str(tiered_bad)]) == 1
# ══════════════════════════════════════════════════════════════════════════════════════
# Wave-1 1B: per-kind attribution (from transcripts) + tool-exec split (from the 1A sidecar)
# ══════════════════════════════════════════════════════════════════════════════════════

def _write_beat_with_tools(d: Path, run: str, nanos: int, *, api_ms, tools,
                           num_turns=1, duration_ms=None):
    """A beat transcript carrying ``tools`` (list of MCP-prefixed tool_use names) so the
    per-kind classifier has something to read, plus a terminal result with duration_api_ms
    (and optionally duration_ms for the tool_exec_pct denominator)."""
    res = {"type": "result", "subtype": "success", "is_error": False,
           "api_error_status": None, "num_turns": num_turns, "result": "prose"}
    if api_ms is not None:
        res["duration_api_ms"] = api_ms
    if duration_ms is not None:
        res["duration_ms"] = duration_ms
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for name in tools:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": {}}]},
        }))
    lines.append(json.dumps({"type": "assistant", "message": {"content": "…"}}))
    lines.append(json.dumps(res))
    path = d / f"{run}.dm.{nanos}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_per_kind_combat_and_social_means(tmp_path):
    # cold open (beat0), then a combat beat (140s) and a social beat (70s). The per-kind means
    # are derived from the tool_use names alone — no sidecar.
    run = "duo-kind"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=240000,
                           tools=["mcp__engine__start_adventure"])           # cold open
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=140000,
                           tools=["mcp__engine__start_combat", "mcp__engine__attack"])  # combat
    _write_beat_with_tools(tmp_path, run, 3000, api_ms=70000,
                           tools=["mcp__engine__social_check"])              # social (no combat/camp tool)
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["coldopen_s"] == 240.0
    assert r["combat_s_per_beat"] == 140.0
    assert r["social_s_per_beat"] == 70.0
    assert r["camp_s_per_beat"] is None                # no camp beat


def test_per_kind_camp_and_combat_precedence(tmp_path):
    # camp means come from camp tools; a beat that BOTH fights and rests is attributed to combat
    # (the heavier kind wins). Two combat beats average; the camp beat is its own mean.
    run = "duo-camp"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])     # cold open (no tools)
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=100000,
                           tools=["mcp__engine__attack"])                    # combat
    _write_beat_with_tools(tmp_path, run, 3000, api_ms=120000,
                           tools=["mcp__engine__cast_spell", "mcp__engine__long_rest"])  # combat (precedence)
    _write_beat_with_tools(tmp_path, run, 4000, api_ms=90000,
                           tools=["mcp__engine__camp_scene", "mcp__engine__record_camp_beat"])  # camp
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["combat_s_per_beat"] == 110.0             # mean(100, 120) — the long_rest beat is combat
    assert r["camp_s_per_beat"] == 90.0
    assert r["social_s_per_beat"] is None


def test_per_kind_none_when_no_such_beat(tmp_path):
    # A run with only cold-open + social beats has None for combat/camp (no fabricated 0).
    run = "duo-soc"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])     # cold open
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=80000, tools=[])      # social (no tools)
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["social_s_per_beat"] == 80.0
    assert r["combat_s_per_beat"] is None
    assert r["camp_s_per_beat"] is None


def test_per_kind_present_without_sidecar(tmp_path):
    # Per-kind keys exist (None or value) even when NO --tooltiming is passed; the tool-exec
    # split keys are None (graceful degrade).
    run = "duo-nosc"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=90000,
                           tools=["mcp__engine__attack"])
    r = latency_rollup.rollup_run(tmp_path, run)               # NO tooltiming
    for k in ("combat_s_per_beat", "social_s_per_beat", "camp_s_per_beat",
              "mean_tool_call_ms", "slowest_tool", "tool_exec_pct"):
        assert k in r
    assert r["combat_s_per_beat"] == 90.0
    assert r["mean_tool_call_ms"] is None and r["slowest_tool"] is None
    assert r["tool_exec_pct"] is None


def _write_tooltiming(path: Path, rows) -> None:
    """Write a 1A tool-timing JSONL sidecar (the contract:
    {ts, tool, wall_ms, ok, campaign_id} per line)."""
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            if isinstance(r, str):
                fh.write(r + "\n")                 # raw line (for the malformed-line test)
            else:
                fh.write(json.dumps(r) + "\n")


def test_tooltiming_sidecar_slowest_and_mean(tmp_path):
    # mean_tool_call_ms is the mean wall_ms; slowest_tool is the largest TOTAL summed wall_ms.
    # scene_context: 200+220 = 420ms total; attack: 50ms; roll: 30ms → slowest = scene_context.
    run = "duo-tt"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[], duration_ms=205000)
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=100000,
                           tools=["mcp__engine__attack"], duration_ms=103000)
    sc = tmp_path / "tools.jsonl"
    _write_tooltiming(sc, [
        {"ts": 1.0, "tool": "scene_context", "wall_ms": 200.0, "ok": True, "campaign_id": "c1"},
        {"ts": 2.0, "tool": "scene_context", "wall_ms": 220.0, "ok": True, "campaign_id": "c1"},
        {"ts": 3.0, "tool": "attack", "wall_ms": 50.0, "ok": True, "campaign_id": "c1"},
        {"ts": 4.0, "tool": "roll", "wall_ms": 30.0, "ok": True, "campaign_id": "c1"},
    ])
    r = latency_rollup.rollup_run(tmp_path, run, tooltiming=sc)
    assert r["slowest_tool"] == "scene_context"
    assert r["mean_tool_call_ms"] == 125.0           # mean(200,220,50,30)


def test_tooltiming_exec_pct_uses_duration_ms_denominator(tmp_path):
    # tool_exec_pct = sum(tool wall_s) / sum(beat duration_ms s). 500ms tools / 10s wall = 5%.
    run = "duo-pct"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[], duration_ms=4000)
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=100000, tools=[], duration_ms=6000)
    sc = tmp_path / "tools.jsonl"
    _write_tooltiming(sc, [
        {"ts": 1.0, "tool": "a", "wall_ms": 300.0, "ok": True, "campaign_id": None},
        {"ts": 2.0, "tool": "b", "wall_ms": 200.0, "ok": True, "campaign_id": None},
    ])
    r = latency_rollup.rollup_run(tmp_path, run, tooltiming=sc)
    # 0.5s tools / 10.0s wall = 0.05; basis is the whole-beat wall (duration_ms).
    assert r["tool_exec_pct"] == 0.05
    assert r["tool_exec_pct_basis"] == "duration_ms"


def test_tooltiming_exec_pct_falls_back_to_api_when_no_duration_ms(tmp_path):
    # When NO beat carries duration_ms, the denominator falls back to total generation
    # (duration_api_ms) and the basis is stamped so the reader knows it over-states.
    run = "duo-fb"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])   # no duration_ms
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=300000, tools=[])   # no duration_ms
    sc = tmp_path / "tools.jsonl"
    _write_tooltiming(sc, [
        {"ts": 1.0, "tool": "a", "wall_ms": 5000.0, "ok": True, "campaign_id": None},
    ])
    r = latency_rollup.rollup_run(tmp_path, run, tooltiming=sc)
    # 5s tools / 500s generation = 0.01; basis stamped as the generation fallback.
    assert r["tool_exec_pct"] == 0.01
    assert r["tool_exec_pct_basis"] == "duration_api_ms"


def test_tooltiming_absent_degrades_to_none(tmp_path):
    # No --tooltiming at all → the three tool-exec keys are None, rest of rollup unchanged.
    run = "duo-none"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=90000, tools=[])
    r = latency_rollup.rollup_run(tmp_path, run)
    assert r["mean_tool_call_ms"] is None
    assert r["slowest_tool"] is None
    assert r["tool_exec_pct"] is None
    assert r["s_per_beat"] == 90.0                   # base rollup intact


def test_tooltiming_missing_file_degrades_gracefully(tmp_path):
    # A --tooltiming PATH that does not exist → empty sidecar → None tool-exec keys (no crash).
    run = "duo-miss"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[])
    _write_beat_with_tools(tmp_path, run, 2000, api_ms=90000, tools=[])
    r = latency_rollup.rollup_run(tmp_path, run, tooltiming=tmp_path / "nope.jsonl")
    assert r["mean_tool_call_ms"] is None and r["tool_exec_pct"] is None


def test_tooltiming_empty_and_garbled_rows_skipped(tmp_path):
    # Blank lines, malformed JSON, non-objects, and rows missing tool/wall_ms are skipped;
    # only the valid row survives (tolerant, never raises).
    run = "duo-garble"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[], duration_ms=4000)
    sc = tmp_path / "tools.jsonl"
    _write_tooltiming(sc, [
        "",                                                    # blank
        "{not valid json",                                     # malformed
        "[1,2,3]",                                             # not an object
        {"ts": 1.0, "ok": True},                               # no tool / wall_ms
        {"tool": "x"},                                         # no wall_ms
        {"tool": "good", "wall_ms": 42.0, "ok": True, "campaign_id": None},  # the one valid row
    ])
    r = latency_rollup.rollup_run(tmp_path, run, tooltiming=sc)
    assert r["mean_tool_call_ms"] == 42.0
    assert r["slowest_tool"] == "good"


def test_cli_tooltiming_flag(tmp_path):
    # The CLI threads --tooltiming through to the rollup.
    run = "duo-clitt"
    _write_beat_with_tools(tmp_path, run, 1000, api_ms=200000, tools=[], duration_ms=4000)
    sc = tmp_path / "tools.jsonl"
    _write_tooltiming(sc, [
        {"ts": 1.0, "tool": "scene_context", "wall_ms": 100.0, "ok": True, "campaign_id": None},
    ])
    out = tmp_path / "lat.json"
    rc = latency_rollup._main(["--dir", str(tmp_path), "--run", run,
                               "--tooltiming", str(sc), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["slowest_tool"] == "scene_context"
    assert data["mean_tool_call_ms"] == 100.0
