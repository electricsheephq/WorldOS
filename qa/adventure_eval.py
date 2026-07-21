#!/usr/bin/env python3
"""The A-series adventure-loop AGGREGATOR (A-T): N arc-directed runs -> one scored verdict.

Consumes the artifacts N ``qa/run_adventure.sh`` runs leave behind (one PREFIX per run — the same
``qa/transcripts/<run>.*`` naming run_duo uses, plus ``<run>.quest_trace.json`` from
qa/quest_progress.py), aggregates them per DIMENSION, writes ONE ``scores_db`` row
(``surface="adventure"``), and prints + stores a WEAKEST-LINK verdict line — the routing instrument
the A2 flywheel reads to pick the next sprint (the weakest dimension gets the next lever).

Dimensions (each normalized 0..1, higher = better; a dimension with no data is excluded from the
weakest-link pick):

  completion   — completion_rate: fraction of runs whose quest reached ``quest_completed``.
  pace         — how fast completions land vs. the beat budget (a completed run at few beats scores
                 high; a run that never completed scores 0). Median beats-to-complete is reported.
  stuck        — 1 - stuck_rate, where a run is "stuck" if it had dead beats (>= threshold) OR a
                 stage-gap outlier (a jump between consecutive quest stages larger than the config
                 threshold). Fewer stuck runs -> higher.
  engagement   — mean feature-engagement coverage (engaged / (engaged+inert)) across runs.
  story        — median Tolkien story-craft lens / 5.
  mechanics    — median of the mechanical + Angry-DM (5e-fidelity) lenses / 5.
  behavioral   — GREEN rate across runs (the deterministic gate).

Per-run inputs, read from ``<prefix>.<suffix>`` (all optional; a missing file degrades that run's
contribution to None, never an error):
  .quest_trace.json  — qa/quest_progress.py stamps (completion, beats-to-complete, stage gaps)
  .tolkien.json / .score.json / .angrydm.json — the 3 lenses' ``.overall`` (qa/scores_persist conv.)
  .gate.txt          — the behavioral gate output ([FAIL] lines -> RED)
  .latency.json      — qa/latency_rollup.py output (s_per_beat, duration_wall_s)
  .state.json        — the engine snapshot (feature-engagement fallback when no summary present)
  .adventure.json    — the optional per-run summary run_adventure.sh emits (dead_beats,
                       engagement_pct, behavioral, completed_beats) — authoritative when present.

RULER DISCIPLINE (load-bearing): the aggregation thresholds + weakest-link levers live in
``qa/adventure_eval_config.json``, hashed into the ``av_`` family by
scoring_config_version.adventure_config_version() — its OWN namespace, NEVER appended to
SCORING_CONFIG_FILES (that would silently re-version the sc_/lc_ engine-duo rulers).

Offline-testable: ``aggregate()`` reads plain files, so a test builds synthetic run dirs and asserts
the dimensions + verdict + the persisted row. Run the tests single-process:
    uv run --directory servers/engine python -m pytest qa/test_adventure_eval.py -p no:xdist
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CONFIG_PATH = HERE / "adventure_eval_config.json"

# The arc stages, mirrored from quest_progress so this module needs no engine import for the offline
# path. quest_progress.STAGES is the authority; this is a read-only copy used for stage-gap ordering.
STAGES: tuple[str, ...] = (
    "reached_giver", "quest_accepted", "entered_dungeon",
    "boss_dead", "reward_received", "quest_completed",
)


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── small readers (all None-tolerant) ───────────────────────────────────────────────────────────
def _read_json(path: str | Path) -> Optional[dict]:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _lens_overall(prefix: str, suffix: str) -> Optional[float]:
    data = _read_json(f"{prefix}.{suffix}.json")
    if not data:
        return None
    v = data.get("overall")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _behavioral(prefix: str, summary: Optional[dict]) -> Optional[str]:
    """GREEN / RED / None. Summary field wins; else scan the gate.txt for a [FAIL] line."""
    if summary and summary.get("behavioral") in ("GREEN", "RED"):
        return summary["behavioral"]
    gate = Path(f"{prefix}.gate.txt")
    if gate.is_file():
        try:
            txt = gate.read_text(encoding="utf-8")
        except OSError:
            return None
        return "RED" if "[FAIL]" in txt else "GREEN"
    return None


def _completion_from_trace(trace: Optional[dict]) -> tuple[bool, Optional[int], list[dict]]:
    """(completed, beats_to_complete, stamps) from a quest_trace.json dict."""
    if not trace:
        return False, None, []
    stamps = list(trace.get("stamps") or [])
    completed_stamp = next((s for s in stamps if s.get("stage") == "quest_completed"), None)
    if completed_stamp is not None:
        return True, _int_or_none(completed_stamp.get("beat")), stamps
    # A status flip recorded outside the stamps (defensive): trust an explicit status too.
    if str(trace.get("quest_status") or "active") not in ("active", "", "None"):
        return True, _int_or_none(trace.get("updated_beat")), stamps
    return False, None, stamps


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _stage_gap_outlier(stamps: list[dict], threshold: int) -> bool:
    """True when the beat gap between two CONSECUTIVE reached stages (in arc order) exceeds
    ``threshold`` — a long stall on one leg of the arc."""
    by_stage = {s.get("stage"): _int_or_none(s.get("beat")) for s in stamps}
    ordered = [by_stage[s] for s in STAGES if by_stage.get(s) is not None]
    for a, b in zip(ordered, ordered[1:]):
        if b - a > threshold:
            return True
    return False


def _engagement_pct(prefix: str, summary: Optional[dict]) -> Optional[float]:
    """Engagement coverage for a run. Summary wins; else compute from the engine snapshot via
    feature_engagement (the same coverage the WS0 tracker reports)."""
    if summary and summary.get("engagement_pct") is not None:
        try:
            return float(summary["engagement_pct"])
        except (TypeError, ValueError):
            return None
    state = _read_json(f"{prefix}.state.json")
    if not state:
        return None
    try:
        import feature_engagement as fe  # noqa: PLC0415
        cov = fe.engagement_coverage(state, tool_counts=None)
        engaged, expected = str(cov.get("coverage", "0/0")).split("/")
        expected_n = int(expected)
        return (int(engaged) / expected_n) if expected_n else None
    except Exception:
        return None


def _dead_beats(prefix: str, summary: Optional[dict]) -> Optional[int]:
    """Dead (failed) beats for a run. Summary wins; else read latency.json's failed_beats
    (qa/latency_rollup.py stamps it — the dm_beat_mark-adjacent failed-beat count)."""
    if summary and summary.get("dead_beats") is not None:
        return _int_or_none(summary["dead_beats"])
    lat = _read_json(f"{prefix}.latency.json")
    if lat and lat.get("failed_beats") is not None:
        return _int_or_none(lat["failed_beats"])
    return None


def _wall_and_pace(prefix: str) -> tuple[Optional[float], Optional[float]]:
    """(duration_wall_s, s_per_beat) from the run's latency.json (qa/latency_rollup output)."""
    lat = _read_json(f"{prefix}.latency.json")
    if not lat:
        return None, None
    return _float_or_none(lat.get("duration_wall_s")), _float_or_none(lat.get("s_per_beat"))


def _float_or_none(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── per-run + aggregate ─────────────────────────────────────────────────────────────────────────
def read_run(prefix: str, config: dict) -> dict:
    """Read one run's artifacts (by path PREFIX) into a per-run record."""
    summary = _read_json(f"{prefix}.adventure.json")
    trace = _read_json(f"{prefix}.quest_trace.json")
    completed, beats_to_complete, stamps = _completion_from_trace(trace)
    dead = _dead_beats(prefix, summary)
    gap_outlier = _stage_gap_outlier(stamps, int(config.get("stage_gap_outlier_beats", 5)))
    dead_thr = int(config.get("dead_beat_stuck_threshold", 2))
    stuck = bool((dead is not None and dead >= dead_thr) or gap_outlier)
    wall_s, s_per_beat = _wall_and_pace(prefix)
    return {
        "run": Path(prefix).name,
        "prefix": prefix,
        "completed": completed,
        "beats_to_complete": beats_to_complete,
        "dead_beats": dead,
        "stage_gap_outlier": gap_outlier,
        "stuck": stuck,
        "engagement_pct": _engagement_pct(prefix, summary),
        "story": _lens_overall(prefix, "tolkien"),
        "mech": _lens_overall(prefix, "score"),
        "angrydm": _lens_overall(prefix, "angrydm"),
        "behavioral": _behavioral(prefix, summary),
        "wall_s": wall_s,
        "s_per_beat": s_per_beat,
        "stages_reached": [s.get("stage") for s in stamps],
    }


def _median(xs: list[Optional[float]]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return round(statistics.median(vals), 3) if vals else None


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return round(statistics.mean(vals), 3) if vals else None


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def aggregate(prefixes: list[str], config: Optional[dict] = None) -> dict:
    """Aggregate N run prefixes into per-dimension scores + a weakest-link verdict."""
    config = config or load_config()
    beat_budget = float(config.get("beat_budget", 15))
    runs = [read_run(p, config) for p in prefixes]
    n = len(runs)
    if n == 0:
        raise ValueError("aggregate() needs at least one run prefix")

    completion_rate = _mean([1.0 if r["completed"] else 0.0 for r in runs]) or 0.0
    completed_beats = [r["beats_to_complete"] for r in runs if r["completed"] and r["beats_to_complete"] is not None]
    median_beats_to_complete = _median(completed_beats)

    # pace per run: a completion at few beats scores high; a non-completion scores 0.
    pace_vals = []
    for r in runs:
        if r["completed"] and r["beats_to_complete"] is not None:
            pace_vals.append(_clamp01(1.0 - (r["beats_to_complete"] / beat_budget)))
        else:
            pace_vals.append(0.0)
    pace = _mean(pace_vals) or 0.0

    stuck_rate = _mean([1.0 if r["stuck"] else 0.0 for r in runs]) or 0.0
    stuck_dim = _clamp01(1.0 - stuck_rate)

    engagement = _mean([r["engagement_pct"] for r in runs])
    story_med = _median([r["story"] for r in runs])
    mech_med = _median([r["mech"] for r in runs])
    angry_med = _median([r["angrydm"] for r in runs])

    behavioral_greens = [1.0 if r["behavioral"] == "GREEN" else 0.0
                         for r in runs if r["behavioral"] is not None]
    green_rate = _mean(behavioral_greens)

    # normalized dimensions (None where there's no data -> excluded from the weakest-link pick)
    story_dim = (story_med / 5.0) if story_med is not None else None
    mech_parts = [x / 5.0 for x in (mech_med, angry_med) if x is not None]
    mech_dim = round(statistics.mean(mech_parts), 3) if mech_parts else None
    behavioral_dim = green_rate  # already 0..1 or None

    dims: dict[str, Optional[float]] = {
        "completion": round(completion_rate, 3),
        "pace": round(pace, 3),
        "stuck": round(stuck_dim, 3),
        "engagement": round(engagement, 3) if engagement is not None else None,
        "story": round(story_dim, 3) if story_dim is not None else None,
        "mechanics": mech_dim,
        "behavioral": round(behavioral_dim, 3) if behavioral_dim is not None else None,
    }

    weakest, weakest_score, lever = _weakest_link(dims, config)
    verdict = (
        f"WEAKEST-LINK: {weakest} ({weakest_score:.2f}) -> {lever}"
        if weakest is not None else
        "WEAKEST-LINK: n/a (no scored dimension) -> collect at least one scorable run"
    )

    return {
        "n": n,
        "completion_rate": round(completion_rate, 3),
        "median_beats_to_complete": median_beats_to_complete,
        "median_wall_s": _median([r["wall_s"] for r in runs]),
        "median_s_per_beat": _median([r["s_per_beat"] for r in runs]),
        "stuck_rate": round(stuck_rate, 3),
        "green_rate": round(green_rate, 3) if green_rate is not None else None,
        "story_overall": story_med,
        "mech_overall": mech_med,
        "angrydm_overall": angry_med,
        "engagement_pct": round(engagement, 3) if engagement is not None else None,
        "dimensions": dims,
        "weakest_link": weakest,
        "weakest_score": weakest_score,
        "verdict": verdict,
        "runs": runs,
    }


def _weakest_link(dims: dict[str, Optional[float]], config: dict):
    """Return (dimension, score, lever) for the lowest-scoring dimension that has data."""
    scored = {k: v for k, v in dims.items() if v is not None}
    if not scored:
        return None, None, None
    weakest = min(scored, key=lambda k: (scored[k], k))  # ties -> alphabetical for determinism
    lever = (config.get("dimensions", {}).get(weakest) or {}).get("lever", weakest)
    return weakest, scored[weakest], lever


# ── persistence (one scores_db row) ─────────────────────────────────────────────────────────────
def persist_row(
    agg: dict,
    *,
    run_id: str,
    db_path: Optional[str] = None,
    build_sha: str = "",
    dm_model: str = "opus",
    actor_model: str = "sonnet",
    source_path: str = "",
) -> dict:
    """Write ONE ``surface="adventure"`` row to scores_db and return the field dict written."""
    import scores_db  # noqa: PLC0415
    from scoring_config_version import adventure_config_version  # noqa: PLC0415

    n = agg["n"]
    green_rate = agg.get("green_rate")
    behavioral = "GREEN" if green_rate == 1.0 else ("RED" if green_rate is not None else None)
    pass_bar = float(load_config().get("pass_completion_rate", 0.5))
    passed = 1 if (agg["completion_rate"] >= pass_bar and (green_rate is None or green_rate >= 0.5)) else 0

    av = adventure_config_version()
    notes = (
        f"{agg['verdict']} | adventure-ruler {av} | "
        f"completion={agg['completion_rate']:.2f} median_beats={agg['median_beats_to_complete']} "
        f"stuck_rate={agg['stuck_rate']:.2f}"
    )
    fields: dict[str, Any] = {
        "surface": "adventure",
        "build_sha": build_sha or None,
        "dm_model": dm_model,
        "actor_model": actor_model,
        "methodology": f"arc-duo N={n}",
        "story_overall": agg["story_overall"],
        "mech_overall": agg["mech_overall"],
        "angrydm_overall": agg["angrydm_overall"],
        "behavioral": behavioral,
        "engagement_pct": agg["engagement_pct"],
        "s_per_beat": agg["median_s_per_beat"],
        "duration_wall_s": agg["median_wall_s"],
        "pass": passed,
        "source_path": source_path or None,
        "notes": notes,
    }
    scores_db.add_run(run_id, db_path=db_path or scores_db.DB_PATH,
                      **{k: v for k, v in fields.items() if v is not None or k in ("behavioral",)})
    return fields


# ── launch (the REAL run path — never exercised by tests; LLM spend is the orchestrator's call) ──
def launch_runs(n: int, *, beats: int, budget: str, persona: str, run_stamp: str,
                transcripts_dir: str) -> list[str]:
    """Launch N run_adventure.sh runs SEQUENTIALLY (isolated state dirs) and return their prefixes.

    Deliberately sequential + foreground (each run drives two `claude -p` sessions and is
    API-bound); the run_parallel.sh staggered pattern is available for a faster lane but the
    default here is the safe sequential path. NOT invoked by the offline tests."""
    runner = str(HERE / "run_adventure.sh")
    prefixes: list[str] = []
    for i in range(1, n + 1):
        run_id = f"{run_stamp}-{i}"
        cmd = ["bash", runner, run_id, str(beats), budget, persona]
        subprocess.run(cmd, cwd=str(HERE.parent), check=False)
        prefixes.append(str(Path(transcripts_dir) / run_id))
    return prefixes


def _resolve_prefixes(args) -> list[str]:
    if args.runs:
        return list(args.runs)
    if args.dir and args.run_ids:
        return [str(Path(args.dir) / rid) for rid in args.run_ids.split(",") if rid]
    raise SystemExit("provide --runs <prefix...> or --dir <D> --run-ids a,b,c (or --launch N)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="A-series adventure-eval aggregator (A-T).")
    ap.add_argument("--runs", nargs="*", help="explicit per-run path PREFIXES (e.g. qa/transcripts/adv1)")
    ap.add_argument("--dir", help="transcripts dir (with --run-ids)")
    ap.add_argument("--run-ids", help="comma-separated run ids under --dir")
    ap.add_argument("--out", help="write the aggregate JSON here (also printed)")
    ap.add_argument("--persist", action="store_true", help="write a scores_db surface=adventure row")
    ap.add_argument("--run-id", default="", help="the aggregate run id for the scores_db row")
    ap.add_argument("--db", default="", help="override scores.db path (testing only)")
    ap.add_argument("--build-sha", default="")
    ap.add_argument("--source-path", default="")
    # --launch: the REAL run path (spends LLM budget). Left out of the tested surface on purpose.
    ap.add_argument("--launch", type=int, default=0, help="launch N run_adventure.sh runs first")
    ap.add_argument("--beats", type=int, default=int(load_config().get("beat_budget", 15)))
    ap.add_argument("--budget", default="4.00")
    ap.add_argument("--persona", default="qa/play_player_adventure.txt")
    ap.add_argument("--stamp", default="adv")
    ap.add_argument("--transcripts-dir", default="qa/transcripts")
    args = ap.parse_args(argv)

    if args.launch:
        prefixes = launch_runs(args.launch, beats=args.beats, budget=args.budget,
                               persona=args.persona, run_stamp=args.stamp,
                               transcripts_dir=args.transcripts_dir)
    else:
        prefixes = _resolve_prefixes(args)

    agg = aggregate(prefixes)
    print(f"[adventure_eval] N={agg['n']} completion={agg['completion_rate']:.2f} "
          f"median_beats={agg['median_beats_to_complete']} stuck_rate={agg['stuck_rate']:.2f}")
    for dim, val in agg["dimensions"].items():
        print(f"  {dim:12s} {'n/a' if val is None else f'{val:.2f}'}")
    print(agg["verdict"])

    if args.out:
        # Drop the bulky per-run 'runs' detail into a compact copy for the JSON sidecar.
        Path(args.out).write_text(json.dumps(agg, indent=2) + "\n", encoding="utf-8")
        print(f"[adventure_eval] wrote {args.out}")

    if args.persist:
        run_id = args.run_id or f"{args.stamp}-agg"
        persist_row(agg, run_id=run_id, db_path=args.db or None,
                    build_sha=args.build_sha, source_path=args.source_path or (args.out or ""))
        print(f"[adventure_eval] persisted scores_db row run_id={run_id} surface=adventure")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
