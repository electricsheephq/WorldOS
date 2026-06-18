#!/usr/bin/env python3
"""Tests for story_readout's Wave-1 1B TIMING stamp (reuses qa/latency_rollup, no re-parse).

The TIMING stamp sits next to the COVERAGE stamp and reports the per-beat / per-kind timing
ledger derived from the run's *.dm.<nanos>.jsonl beat transcripts — plus an OPTIONAL tool-exec
clause from the 1A {ts,tool,wall_ms,ok,campaign_id} sidecar (omitted when no sidecar).

Stdlib + pytest only; self-contained synthetic transcripts. Run single-process:
    uv run --directory servers/engine python -m pytest qa/test_story_readout_timing.py -p no:xdist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import story_readout as sr  # noqa: E402


def _write_beat(d: Path, run: str, nanos: int, *, api_ms, tools, num_turns=2, duration_ms=None):
    """A claude -p stream-json beat transcript carrying tool_use names + a terminal result."""
    res = {"type": "result", "subtype": "success", "is_error": False,
           "api_error_status": None, "num_turns": num_turns, "result": "prose",
           "duration_api_ms": api_ms}
    if duration_ms is not None:
        res["duration_ms"] = duration_ms
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for name in tools:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": {}}]},
        }))
    lines.append(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "The DM narrates."}]},
    }))
    lines.append(json.dumps(res))
    p = d / f"{run}.dm.{nanos}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_timing_stamp_per_kind_no_sidecar(tmp_path):
    # cold open 240s, a combat beat 140s, a social beat 70s — no sidecar, so NO tool clause.
    run = "duo-t"
    _write_beat(tmp_path, run, 1000, api_ms=240000, tools=["mcp__engine__start_adventure"])
    _write_beat(tmp_path, run, 2000, api_ms=140000,
                tools=["mcp__engine__start_combat", "mcp__engine__attack"])
    _write_beat(tmp_path, run, 3000, api_ms=70000, tools=["mcp__engine__social_check"])
    line = sr.timing_stamp(str(tmp_path), run)
    assert line.startswith("TIMING |")
    assert "beat~" in line          # routine s_per_beat present
    assert "cold~240s" in line
    assert "combat~140s" in line
    assert "social~70s" in line
    assert "tool=" not in line      # graceful degrade: no sidecar → no tool clause
    assert "slowest=" not in line


def test_timing_stamp_with_tooltiming_sidecar(tmp_path):
    run = "duo-t2"
    _write_beat(tmp_path, run, 1000, api_ms=200000, tools=[], duration_ms=205000)
    _write_beat(tmp_path, run, 2000, api_ms=100000,
                tools=["mcp__engine__attack"], duration_ms=103000)
    sc = tmp_path / "tools.jsonl"
    with sc.open("w", encoding="utf-8") as fh:
        for r in [
            {"ts": 1.0, "tool": "scene_context", "wall_ms": 200.0, "ok": True, "campaign_id": "c"},
            {"ts": 2.0, "tool": "scene_context", "wall_ms": 220.0, "ok": True, "campaign_id": "c"},
            {"ts": 3.0, "tool": "attack", "wall_ms": 40.0, "ok": True, "campaign_id": "c"},
        ]:
            fh.write(json.dumps(r) + "\n")
    line = sr.timing_stamp(str(tmp_path), run, tooltiming=str(sc))
    assert "tool=" in line                    # the tool clause now appears
    assert "slowest=scene_context" in line    # largest total wall_ms


def test_timing_stamp_no_beat_data(tmp_path):
    # An empty / non-matching run yields a stable placeholder, never a crash.
    assert sr.timing_stamp(str(tmp_path), "no-such-run") == "TIMING | (no beat data)"
    assert sr.timing_stamp(None, None) == "TIMING | (no beat data)"


def test_main_prints_timing_next_to_coverage(tmp_path, capsys):
    # The full readout (and --coverage-only) prints BOTH the COVERAGE and the TIMING line.
    run = "duo-main"
    _write_beat(tmp_path, run, 1000, api_ms=200000, tools=["mcp__engine__start_adventure"])
    _write_beat(tmp_path, run, 2000, api_ms=90000, tools=["mcp__engine__attack"])
    path = str(tmp_path / f"{run}.dm.2000.jsonl")
    rc = sr.main([path, "--coverage-only"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "COVERAGE |" in out
    assert "TIMING |" in out
    assert "combat~90s" in out


def test_main_full_render_carries_timing(tmp_path, capsys):
    run = "duo-full"
    _write_beat(tmp_path, run, 1000, api_ms=200000, tools=["mcp__engine__start_adventure"])
    _write_beat(tmp_path, run, 2000, api_ms=80000, tools=["mcp__engine__social_check"])
    path = str(tmp_path / f"{run}.dm.2000.jsonl")
    rc = sr.main([path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TIMING |" in out
    assert "social~80s" in out
