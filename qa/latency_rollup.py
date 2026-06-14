#!/usr/bin/env python3
"""Derive the F13-4 latency ledger (s_per_beat / coldopen_s / turns_per_beat) for a duo
run from the per-beat ``*.dm.<ns>.jsonl`` transcripts the runners ALREADY write.

WHY THIS EXISTS
---------------
#753 asks for a per-beat latency BUDGET, but ``qa/scores_db.py`` had no latency columns to
judge a run against (audit F13-4) — and the ``worldos-latency-forensics`` skill already
MANDATES recording ``s/beat``, ``cold-open-s``, ``turns/beat`` on every run. This module is
the missing derivation: it reads the SDK-authoritative ``duration_api_ms`` (the model
thinking + emitting — "the real cost", per the skill's MEASURE-FIRST method) and ``num_turns``
out of each beat's final ``result`` event and rolls them up into the three columns, so the
runners can stamp them with no new instrumentation.

THE METHOD (matches worldos-latency-forensics)
----------------------------------------------
* GENERATION time per beat = the final ``result`` event's ``duration_api_ms`` (NOT
  ``duration_ms``, which folds in tool/orchestration time — the skill is explicit that
  ``duration_api_ms`` is authoritative and engine tool-exec is ~1–4% of a beat).
* The COLD OPEN is the FIRST beat of a run (the one-time, max-effort world-build). Its cost
  is reported SEPARATELY as ``coldopen_s`` and EXCLUDED from the routine ``s_per_beat`` mean —
  mixing it in poisons the routine-beat budget (a cold open is 2–4× a routine beat).
* ``s_per_beat`` / ``turns_per_beat`` are the MEAN over the CONTINUING (routine) beats.
* FAILED beats (``is_error`` / non-null ``api_error_status`` — e.g. a 401) are NOT real beats
  and are dropped from every statistic (they would otherwise deflate the means).

A beat's transcript is the run's ``<run>.dm.<nanos>.jsonl`` file; the cold open is the
EARLIEST by the nanosecond timestamp in the filename (the runners write one file per beat in
beat order). The final ``result`` event is the authoritative one (a stream-json transcript
emits exactly one terminal ``result``); we take the LAST ``result`` line defensively.

USAGE
-----
    # As a CLI — point it at a run's transcript dir + run id; prints the rollup JSON:
    python3 qa/latency_rollup.py --dir "$T" --run "$RUN"
    python3 qa/latency_rollup.py path/to/run.dm.123.jsonl path/to/run.dm.456.jsonl

    # From Python:
    from latency_rollup import rollup_run
    r = rollup_run(transcript_dir, run_id)   # -> {"s_per_beat", "coldopen_s",
                                             #     "turns_per_beat", "beats", ...}
    add_run(..., **{k: r[k] for k in ("s_per_beat", "coldopen_s", "turns_per_beat")})
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

# A beat transcript is "<run>.dm.<nanoseconds>.jsonl"; capture the nanos for beat ordering.
_DM_RE = re.compile(r"\.dm\.(\d+)\.jsonl$")


def _final_result(path: str | Path) -> Optional[dict]:
    """Return the LAST ``type=="result"`` event in a stream-json transcript, or None.

    Streams are large; scan line-by-line and keep the last result (the terminal one).
    Tolerant of partial/garbage lines (a crashed beat may leave a half-written file)."""
    last: Optional[dict] = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or '"type":"result"' not in line and '"type": "result"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "result":
                    last = obj
    except OSError:
        return None
    return last


def _is_failed(res: dict) -> bool:
    """A beat is FAILED (drop it) if the SDK flags an error result — e.g. a 401 whose
    ``result`` text is the API error string, not a reply (SYN-01 / F13-5 class)."""
    return bool(res.get("is_error")) or res.get("api_error_status") not in (None, "")


def _api_seconds(res: dict) -> Optional[float]:
    """GENERATION seconds for a beat from ``duration_api_ms``. Clamp negatives to 0 (the
    VM parallel-segment artifact the audit flags); None when the field is absent."""
    ms = res.get("duration_api_ms")
    if not isinstance(ms, (int, float)):
        return None
    return max(0.0, float(ms) / 1000.0)


def beat_files(transcript_dir: str | Path, run_id: str) -> list[str]:
    """The run's per-beat DM transcripts, ordered cold-open-FIRST (ascending nanos)."""
    pat = os.path.join(str(transcript_dir), f"{run_id}.dm.*.jsonl")
    files = [p for p in glob.glob(pat) if _DM_RE.search(p)]
    files.sort(key=lambda p: int(_DM_RE.search(p).group(1)))  # type: ignore[union-attr]
    return files


def rollup_files(files: list[str]) -> dict[str, Any]:
    """Roll a beat-ordered list of DM transcript paths up into the latency ledger.

    Returns ``{s_per_beat, coldopen_s, turns_per_beat, beats, cold_open_turns,
    failed_beats}``. The latency columns are None when there is no data to derive them
    from (no successful beats / no continuing beat), so an empty run records NULL rather
    than a misleading 0."""
    api_s: list[float] = []          # successful-beat generation seconds, in beat order
    turns: list[float] = []          # successful-beat num_turns, in beat order
    failed = 0
    for path in files:
        res = _final_result(path)
        if res is None:
            continue
        if _is_failed(res):
            failed += 1
            continue
        secs = _api_seconds(res)
        n = res.get("num_turns")
        if secs is None:
            continue
        api_s.append(secs)
        turns.append(float(n) if isinstance(n, (int, float)) else 0.0)

    out: dict[str, Any] = {
        "s_per_beat": None,
        "coldopen_s": None,
        "turns_per_beat": None,
        "beats": len(api_s),          # successful beats counted
        "cold_open_turns": None,
        "failed_beats": failed,
    }
    if not api_s:
        return out

    # Beat 0 is the cold open; beats 1..N are the continuing (routine) beats.
    out["coldopen_s"] = round(api_s[0], 1)
    out["cold_open_turns"] = turns[0]
    routine_s = api_s[1:]
    routine_t = turns[1:]
    if routine_s:
        out["s_per_beat"] = round(sum(routine_s) / len(routine_s), 1)
        out["turns_per_beat"] = round(sum(routine_t) / len(routine_t), 1)
    return out


def rollup_run(transcript_dir: str | Path, run_id: str) -> dict[str, Any]:
    """Convenience: discover a run's beat transcripts under ``transcript_dir`` and roll up."""
    return rollup_files(beat_files(transcript_dir, run_id))


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Derive the F13-4 latency ledger from DM beat transcripts.")
    ap.add_argument("--dir", help="transcript directory ($T) — used with --run")
    ap.add_argument("--run", help="run id ($RUN) — used with --dir")
    ap.add_argument("--out", help="write the rollup JSON here (also printed to stdout)")
    ap.add_argument("files", nargs="*", help="explicit beat transcript paths (overrides --dir/--run)")
    args = ap.parse_args(argv)

    if args.files:
        files = sorted(args.files, key=lambda p: int(m.group(1)) if (m := _DM_RE.search(p)) else 0)
        result = rollup_files(files)
    elif args.dir and args.run:
        result = rollup_run(args.dir, args.run)
    else:
        ap.error("pass either explicit transcript files, or --dir and --run")
        return 2

    blob = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(blob + "\n", encoding="utf-8")
    print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
