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
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

# The latency columns release_readiness.py:read_latency() reads from a <run>/latency.json
# sidecar (it only consumes s_per_beat + coldopen_s; turns_per_beat is carried for parity
# with scores_db.add_run and is harmless extra detail for the reader).
SIDECAR_COLUMNS = ("s_per_beat", "coldopen_s", "turns_per_beat")

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


# Token/cache fields read off a beat's terminal ``result`` event for the tool-schema-slab A/B
# (slab decision, Phase 3). The slab is a per-request PREFILL tax, so its effect shows up in the
# token ledger, not in duration_api_ms (which is generation-bound). The load-bearing A/B signals:
#   * cache_creation_input_tokens — tokens WRITTEN to the prompt cache. On a healthy CACHED run the
#     continuing beats sit on a stable prefix, so routine cache_creation ≈ 0; a tiering change that
#     DENTS the cache (re-creates the changed tool block) shows up as elevated routine cache_creation.
#   * cache_read_input_tokens — tokens served from cache (cheap). * input_tokens — uncached prefill.
#   * ttft_ms — time-to-first-token; a smaller pinned tool block should lower it (prior art).
_USAGE_TOKEN_FIELDS = (
    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
)


def _usage(res: dict) -> dict[str, Optional[float]]:
    """Per-beat token usage + time-to-first-token from a ``result`` event's ``usage`` block.
    Absent fields are None (tolerant of older transcripts that predate the field)."""
    u = res.get("usage") if isinstance(res.get("usage"), dict) else {}
    out: dict[str, Optional[float]] = {}
    for k in _USAGE_TOKEN_FIELDS:
        v = u.get(k)
        out[k] = float(v) if isinstance(v, (int, float)) else None
    ttft = res.get("ttft_ms")
    out["ttft_ms"] = float(ttft) if isinstance(ttft, (int, float)) else None
    return out


def _mean(xs: Iterable[Any]) -> Optional[float]:
    vals = [float(x) for x in xs if isinstance(x, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _token_aggregates(rows: list[dict]) -> Optional[dict]:
    """Split per-beat usage rows (beat order, cold-open first) into cold-open vs routine means."""
    if not rows:
        return None

    def block(rs: list[dict]) -> Optional[dict]:
        if not rs:
            return None
        return {
            "cache_creation": _mean(r.get("cache_creation_input_tokens") for r in rs),
            "cache_read": _mean(r.get("cache_read_input_tokens") for r in rs),
            "input": _mean(r.get("input_tokens") for r in rs),
            "output": _mean(r.get("output_tokens") for r in rs),
            "ttft_ms": _mean(r.get("ttft_ms") for r in rs),
        }

    return {"coldopen": block(rows[:1]), "routine": block(rows[1:])}


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
    usage_rows: list[dict] = []      # successful-beat token usage + ttft, in beat order
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
        usage_rows.append(_usage(res))

    out: dict[str, Any] = {
        "s_per_beat": None,
        "coldopen_s": None,
        "turns_per_beat": None,
        "beats": len(api_s),          # successful beats counted
        "cold_open_turns": None,
        "failed_beats": failed,
        # Additive (slab decision, Phase 3): per-beat token/cache ledger for the tiering A/B.
        # cold-open vs routine means of cache_creation / cache_read / input / output / ttft_ms.
        "tokens": _token_aggregates(usage_rows),
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


def stamp_sidecars(rollup: dict[str, Any], run_dirs: Iterable[str | Path]) -> list[str]:
    """Write a run's latency ``rollup`` as a ``<run>/latency.json`` sidecar into each run dir,
    in the exact shape ``qa/release_readiness.py:read_latency()`` reads
    (``{s_per_beat, coldopen_s, turns_per_beat}``).

    This is the bridge that ACTIVATES the additive RRI latency gate on a real sweep: the runners
    derive the per-beat ledger into the TRANSCRIPT dir (``$T/$RUN.latency.json``), but
    release_readiness reads each PERSONA run dir's sidecar — so without this stamp the gate is a
    dormant evidence-gap SKIP. The rollup is a BUILD-level measurement (one deep duo play), so it
    is replicated into every persona run dir; the gate aggregates the MAX across personas, and
    identical values yield exactly that build figure.

    NULL columns are preserved verbatim — read_latency treats a null ``s_per_beat``/``coldopen_s``
    as ABSENT evidence (an evidence-gap skip), never a fabricated 0.0 that would silently pass.
    A run dir that does not already exist is SKIPPED (never created), so a stale/typo path can
    never fabricate latency evidence. Returns the sidecar paths actually written."""
    sidecar = {k: rollup.get(k) for k in SIDECAR_COLUMNS}
    written: list[str] = []
    for raw in run_dirs:
        d = Path(raw)
        if not d.is_dir():
            continue
        target = d / "latency.json"
        target.write_text(json.dumps(sidecar) + "\n", encoding="utf-8")
        written.append(str(target))
    return written


# --- Tool-schema-slab tiering A/B (slab decision, Phase 3) -----------------------------------
# Compare two SAME-SHA / SAME-SEED duo rollups — arm A = baseline (WORLDOS_ENGINE_ALWAYSLOAD=1,
# whole-server pin) vs arm B = tiered (=0, per-tool PINNED_ALLOWLIST). The merge gate for the
# tiering PR (per the FPAD decision) is: cold-open NOT worse, routine NOT worse, and the prompt
# cache NOT dented — PLUS a chance-corrected tool-SELECTION check that lives outside this module
# (selection is about which tool the DM picks, not latency). Thresholds are deliberately explicit
# and tunable; this encodes the gate so a sweep yields a PASS/FAIL, not a wall of numbers.
AB_LATENCY_TOLERANCE = 0.05          # a 5% regression on a wall-clock metric counts as "worse"
AB_CACHE_CREATION_ABS_TOL = 2_000    # routine cache re-creation tokens tolerated as cache noise


def _delta(a: Any, b: Any) -> dict:
    """A->B delta cell. None-safe (missing metric => null delta, never a crash)."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return {"a": a, "b": b, "delta": None, "pct": None}
    d = b - a
    return {"a": a, "b": b, "delta": round(d, 1), "pct": (round(100.0 * d / a, 1) if a else None)}


def compare_arms(arm_a: dict, arm_b: dict) -> dict:
    """Compare a baseline rollup (arm_a) against a tiered rollup (arm_b) on the slab-A/B gate.

    Returns ``{verdict, checks, metrics, note}``. ``verdict`` is PASS / FAIL / INSUFFICIENT_DATA
    (the last when a required metric is missing on either arm). ``checks`` are the hard gates;
    ``metrics`` carries every A/B delta (incl. the expected token-mass WIN) for the reviewer."""
    ca = (arm_a.get("tokens") or {}).get("coldopen") or {}
    cb = (arm_b.get("tokens") or {}).get("coldopen") or {}
    ra = (arm_a.get("tokens") or {}).get("routine") or {}
    rb = (arm_b.get("tokens") or {}).get("routine") or {}

    metrics = {
        "coldopen_s": _delta(arm_a.get("coldopen_s"), arm_b.get("coldopen_s")),
        "coldopen_cache_creation": _delta(ca.get("cache_creation"), cb.get("cache_creation")),
        "coldopen_ttft_ms": _delta(ca.get("ttft_ms"), cb.get("ttft_ms")),
        "s_per_beat": _delta(arm_a.get("s_per_beat"), arm_b.get("s_per_beat")),
        "routine_cache_creation": _delta(ra.get("cache_creation"), rb.get("cache_creation")),
        "routine_input": _delta(ra.get("input"), rb.get("input")),         # the expected WIN (down)
        "routine_ttft_ms": _delta(ra.get("ttft_ms"), rb.get("ttft_ms")),
    }

    checks: dict[str, bool] = {}
    a_co, b_co = arm_a.get("coldopen_s"), arm_b.get("coldopen_s")
    if isinstance(a_co, (int, float)) and isinstance(b_co, (int, float)):
        checks["cold_open_not_worse"] = b_co <= a_co * (1 + AB_LATENCY_TOLERANCE)
    a_sb, b_sb = arm_a.get("s_per_beat"), arm_b.get("s_per_beat")
    if isinstance(a_sb, (int, float)) and isinstance(b_sb, (int, float)):
        checks["routine_not_worse"] = b_sb <= a_sb * (1 + AB_LATENCY_TOLERANCE)
    a_cc, b_cc = ra.get("cache_creation"), rb.get("cache_creation")
    if isinstance(a_cc, (int, float)) and isinstance(b_cc, (int, float)):
        checks["cache_not_dented"] = b_cc <= a_cc + AB_CACHE_CREATION_ABS_TOL
    # Informational (not a safety gate): the tiered arm SHOULD inject less uncached prefill.
    a_in, b_in = ra.get("input"), rb.get("input")
    if isinstance(a_in, (int, float)) and isinstance(b_in, (int, float)):
        checks["input_mass_down"] = b_in < a_in

    required = ("cold_open_not_worse", "routine_not_worse", "cache_not_dented")
    if any(k not in checks for k in required):
        verdict = "INSUFFICIENT_DATA"
    elif all(checks[k] for k in required):
        verdict = "PASS"
    else:
        verdict = "FAIL"

    return {
        "verdict": verdict,
        "checks": checks,
        "metrics": metrics,
        "note": (
            "tiering A/B: arm_a=baseline (WORLDOS_ENGINE_ALWAYSLOAD=1), arm_b=tiered (=0). "
            "Hard gate = cold_open_not_worse AND routine_not_worse AND cache_not_dented. "
            "input_mass_down is the expected WIN (informational). The chance-corrected tool-"
            "SELECTION check is gated separately. Run paired same-SHA/same-seed duos for signal."
        ),
    }


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Derive the F13-4 latency ledger from DM beat transcripts.")
    ap.add_argument("--dir", help="transcript directory ($T) — used with --run")
    ap.add_argument("--run", help="run id ($RUN) — used with --dir")
    ap.add_argument("--out", help="write the rollup JSON here (also printed to stdout)")
    ap.add_argument("--stamp-into", default="", help="comma-separated PERSONA run dirs to stamp the "
                    "rollup into as <dir>/latency.json (the shape release_readiness.read_latency reads) "
                    "— this is what ACTIVATES the additive RRI latency gate on a real sweep")
    ap.add_argument("--compare", nargs=2, metavar=("BASELINE_JSON", "TIERED_JSON"),
                    help="slab-A/B (Phase 3): compare two rollup JSON files (arm A=baseline "
                         "WORLDOS_ENGINE_ALWAYSLOAD=1, arm B=tiered =0) on the cold-open / routine / "
                         "cache-dent gate; prints {verdict, checks, metrics}, exits non-zero on FAIL")
    ap.add_argument("files", nargs="*", help="explicit beat transcript paths (overrides --dir/--run)")
    args = ap.parse_args(argv)

    if args.compare:
        arm_a = json.loads(Path(args.compare[0]).read_text())
        arm_b = json.loads(Path(args.compare[1]).read_text())
        cmp = compare_arms(arm_a, arm_b)
        print(json.dumps(cmp, indent=2))
        return 0 if cmp["verdict"] == "PASS" else 1

    if args.files:
        files = sorted(args.files, key=lambda p: int(m.group(1)) if (m := _DM_RE.search(p)) else 0)
        result = rollup_files(files)
    elif args.dir and args.run:
        result = rollup_run(args.dir, args.run)
    else:
        ap.error("pass either explicit transcript files, or --dir and --run")
        return 2

    if args.stamp_into:
        dirs = [p.strip() for p in args.stamp_into.split(",") if p.strip()]
        written = stamp_sidecars(result, dirs)
        # stderr so --out / stdout stay pure JSON for piping; a no-op (no existing dirs) is silent.
        if written:
            print(f"latency: stamped sidecar into {len(written)} run dir(s)", file=sys.stderr)

    blob = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(blob + "\n", encoding="utf-8")
    print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
