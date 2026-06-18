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

# ---------------------------------------------------------------------------
# Per-beat KIND attribution (Wave-1 1B). A beat's "kind" is read straight from the
# tool_use names IN that beat's transcript — no sidecar needed. The earliest beat is the
# COLD-OPEN (the one-time world-build); after that, a beat that fired a COMBAT tool is
# `combat`, one that fired a CAMP/REST tool is `camp`, else `social`. Combat is checked
# BEFORE camp so a beat that both fights and then rests reads as combat (the heavier kind).
# Names match the engine's MCP tool names (server.py); transcripts carry them MCP-prefixed
# (mcp__engine__attack) — we strip the prefix the same way story_readout does.
_COMBAT_TOOLS = frozenset({
    "start_combat", "attack", "make_attack", "cast_spell", "use_action", "use_resource",
    "resolve_death_save", "death_save", "end_combat",
})
_CAMP_TOOLS = frozenset({"camp_scene", "long_rest", "record_camp_beat"})
_BEAT_KINDS = ("cold-open", "combat", "camp", "social")


def _short_tool(name: str) -> str:
    """Bare engine tool name from a possibly MCP-prefixed tool_use name.
    ``mcp__engine__attack`` -> ``attack`` (matches story_readout's ``.split("__")[-1]``)."""
    return str(name or "").split("__")[-1]


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
def _wall_ms_from_result(res: dict) -> Optional[float]:
    """Wall-clock ``duration_ms`` for a beat (generation + tool/orchestration time). None
    when absent. Used only as the tool_exec_pct denominator (the WHOLE beat wall cost)."""
    ms = res.get("duration_ms")
    if not isinstance(ms, (int, float)):
        return None
    return max(0.0, float(ms))


def _beat_tool_names(path: str | Path) -> set[str]:
    """The set of (short) engine tool names used in one beat transcript.

    Scans for ``tool_use`` events and collects their (de-prefixed) names. Tolerant of the
    system/hook noise the harness prepends and of partial/garbage lines (a crashed beat may
    leave a half-written file). Cheap, line-keyed pre-filter so we only json.loads lines that
    could carry a tool_use."""
    names: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") if isinstance(obj, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        names.add(_short_tool(c.get("name", "")))
    except OSError:
        return names
    return names


def _classify_kind(tool_names: set[str], *, is_cold_open: bool) -> str:
    """Classify a beat from the tool names it fired. cold-open wins (it is positional, not
    tool-derived); else combat > camp > social. Combat outranks camp so a beat that fights
    then rests is attributed to the heavier kind."""
    if is_cold_open:
        return "cold-open"
    if tool_names & _COMBAT_TOOLS:
        return "combat"
    if tool_names & _CAMP_TOOLS:
        return "camp"
    return "social"


def beat_files(transcript_dir: str | Path, run_id: str) -> list[str]:
    """The run's per-beat DM transcripts, ordered cold-open-FIRST (ascending nanos)."""
    pat = os.path.join(str(transcript_dir), f"{run_id}.dm.*.jsonl")
    files = [p for p in glob.glob(pat) if _DM_RE.search(p)]
    files.sort(key=lambda p: int(_DM_RE.search(p).group(1)))  # type: ignore[union-attr]
    return files


# ---------------------------------------------------------------------------
# Tool-exec split (Wave-1 1B) — read the OPTIONAL 1A tool-timing sidecar.
# ---------------------------------------------------------------------------
# THE CONTRACT (produced by the engine, one JSON object per line):
#   {"ts": <float unix epoch s>, "tool": "<name>", "wall_ms": <float>,
#    "ok": <bool>, "campaign_id": <str|null>}
# This is the AUTHORITATIVE per-tool wall-clock — the rollup's per-beat `duration_api_ms`
# can't see in-tool time, so engine tool-exec cost only becomes visible via this sidecar.
# Best-effort + tolerant: a missing/empty/garbled file yields no rows (the three derived
# keys then degrade to None), and a row missing `wall_ms`/`tool` is skipped, never raised.


def read_tool_timing(path: str | Path) -> list[dict[str, Any]]:
    """Parse the 1A tool-timing JSONL sidecar into a list of ``{tool, wall_ms, ok, ...}`` rows.

    Tolerant: a missing file -> ``[]``; blank lines and unparseable lines are skipped; a row
    that is not an object, or lacks a string ``tool`` or a numeric ``wall_ms``, is skipped
    (a partial/garbled sidecar degrades to fewer rows, never an exception)."""
    rows: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return rows
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                tool = obj.get("tool")
                wall = obj.get("wall_ms")
                if not isinstance(tool, str) or not tool:
                    continue
                if not isinstance(wall, (int, float)):
                    continue
                rows.append(obj)
    except OSError:
        return rows
    return rows


def _tool_exec_split(rows: list[dict[str, Any]], total_wall_s: Optional[float],
                     total_api_s: Optional[float]) -> dict[str, Any]:
    """Compute ``mean_tool_call_ms`` / ``slowest_tool`` / ``tool_exec_pct`` from sidecar rows.

    * ``mean_tool_call_ms``: mean of every row's ``wall_ms``.
    * ``slowest_tool``: the tool with the largest TOTAL ``wall_ms`` summed across its calls
      (the biggest cumulative cost, not a single slow outlier).
    * ``tool_exec_pct``: total tool wall-seconds / the beat-time denominator. We prefer the
      WHOLE-BEAT wall total (sum of each beat's ``duration_ms`` — generation + tool +
      orchestration), since tool-exec is a fraction OF the whole beat; that is the honest
      denominator. When no beat carried ``duration_ms`` we fall back to the total GENERATION
      seconds (sum of ``duration_api_ms``) and the pct is then "tool wall vs generation",
      which over-states slightly because generation excludes the tool/orchestration the tools
      themselves consumed — flagged in the rollup via ``tool_exec_pct_basis``.

    All three are None when there are no usable rows. tool_exec_pct is additionally None when
    neither denominator is available/positive."""
    out: dict[str, Any] = {
        "mean_tool_call_ms": None,
        "slowest_tool": None,
        "tool_exec_pct": None,
        "tool_exec_pct_basis": None,
    }
    if not rows:
        return out

    walls = [float(r["wall_ms"]) for r in rows]
    out["mean_tool_call_ms"] = round(sum(walls) / len(walls), 1)

    by_tool: dict[str, float] = {}
    for r in rows:
        by_tool[r["tool"]] = by_tool.get(r["tool"], 0.0) + float(r["wall_ms"])
    # largest TOTAL wall_ms; ties broken by name for determinism.
    out["slowest_tool"] = max(sorted(by_tool), key=lambda t: by_tool[t]) if by_tool else None

    tool_s = sum(walls) / 1000.0
    if total_wall_s is not None and total_wall_s > 0:
        out["tool_exec_pct"] = round(tool_s / total_wall_s, 4)
        out["tool_exec_pct_basis"] = "duration_ms"        # whole-beat wall (the honest denominator)
    elif total_api_s is not None and total_api_s > 0:
        out["tool_exec_pct"] = round(tool_s / total_api_s, 4)
        out["tool_exec_pct_basis"] = "duration_api_ms"    # generation-only fallback (over-states)
    return out


def rollup_files(files: list[str], tooltiming: str | Path | None = None) -> dict[str, Any]:
    """Roll a beat-ordered list of DM transcript paths up into the latency ledger.

    Returns ``{s_per_beat, coldopen_s, turns_per_beat, beats, cold_open_turns,
    failed_beats}`` plus the Wave-1 1B additions:
      * per-kind GENERATION means derived from the transcripts alone (no sidecar):
        ``combat_s_per_beat`` / ``social_s_per_beat`` / ``camp_s_per_beat`` — mean
        ``duration_api_ms`` seconds over the SUCCESSFUL beats of that kind (the cold-open
        beat is its OWN kind and excluded from all three; None when no beat of that kind).
      * a tool-exec split from the OPTIONAL 1A ``tooltiming`` sidecar:
        ``mean_tool_call_ms`` / ``slowest_tool`` / ``tool_exec_pct`` (+ ``tool_exec_pct_basis``).
        DEGRADES GRACEFULLY — when ``tooltiming`` is None/missing/empty these are None and
        the rest of the rollup is byte-for-byte unchanged.

    The base latency columns are None when there is no data to derive them from (no successful
    beats / no continuing beat), so an empty run records NULL rather than a misleading 0."""
    api_s: list[float] = []          # successful-beat generation seconds, in beat order
    turns: list[float] = []          # successful-beat num_turns, in beat order
    usage_rows: list[dict] = []      # successful-beat token usage + ttft, in beat order
    wall_s: list[float] = []         # successful-beat WHOLE-BEAT wall seconds (duration_ms), beat order
    kinds: list[str] = []            # successful-beat kind, in beat order (parallel to api_s)
    failed = 0
    for i, path in enumerate(files):
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
        w = _wall_ms_from_result(res)
        wall_s.append(w / 1000.0 if w is not None else 0.0)
        # Kind: the EARLIEST beat (i == 0 in the cold-open-first ordering) is the cold open.
        kinds.append(_classify_kind(_beat_tool_names(path), is_cold_open=(i == 0)))

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
        # Wave-1 1B per-kind generation means (None until proven by a beat of that kind).
        "combat_s_per_beat": None,
        "social_s_per_beat": None,
        "camp_s_per_beat": None,
        # Wave-1 1B tool-exec split (None unless a usable sidecar is supplied).
        "mean_tool_call_ms": None,
        "slowest_tool": None,
        "tool_exec_pct": None,
        "tool_exec_pct_basis": None,
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

    # Per-kind generation means over the successful beats (cold-open kind excluded by
    # construction — it is its own kind label, never combat/camp/social).
    for kind, col in (("combat", "combat_s_per_beat"),
                      ("social", "social_s_per_beat"),
                      ("camp", "camp_s_per_beat")):
        vals = [api_s[i] for i, k in enumerate(kinds) if k == kind]
        if vals:
            out[col] = round(sum(vals) / len(vals), 1)

    # Tool-exec split from the optional 1A sidecar. The denominator prefers the whole-beat
    # wall total (sum of duration_ms); falls back to total generation seconds when no beat
    # carried duration_ms (basis stamped so the reader knows which was used).
    if tooltiming is not None:
        rows = read_tool_timing(tooltiming)
        total_wall = sum(wall_s) if any(w > 0 for w in wall_s) else None
        total_api = sum(api_s) if api_s else None
        out.update(_tool_exec_split(rows, total_wall, total_api))

    return out


def rollup_run(transcript_dir: str | Path, run_id: str,
               tooltiming: str | Path | None = None) -> dict[str, Any]:
    """Convenience: discover a run's beat transcripts under ``transcript_dir`` and roll up.
    Pass ``tooltiming`` to fold in the optional 1A tool-timing sidecar (see ``rollup_files``)."""
    return rollup_files(beat_files(transcript_dir, run_id), tooltiming=tooltiming)


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
    ap.add_argument("--tooltiming", default=None, help="optional 1A tool-timing JSONL sidecar "
                    "({ts,tool,wall_ms,ok,campaign_id} per line) — adds mean_tool_call_ms / "
                    "slowest_tool / tool_exec_pct; absent/missing/empty degrades these to null")
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
        result = rollup_files(files, tooltiming=args.tooltiming)
    elif args.dir and args.run:
        result = rollup_run(args.dir, args.run, tooltiming=args.tooltiming)
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
