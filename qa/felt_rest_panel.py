#!/usr/bin/env python3
"""FELT REST-SCENE panel — thin logger + control-anchored verdict for W1 scene-at-rest (#1318).

The PROTOCOL is `qa/felt_rest_panel.md` (disguised real-game controls, 5 blind scorers, no
AI-prior primers). This module is the thin scripting the protocol names: it takes the panel's
per-frame scores (our rest frames + the disguised real-game control), logs each to the EXISTING
visual-critic lane in `scores_db` (surface="visual"), and computes the ONLY citable metric — the
DELTA of our median vs the control's same-panel median.

The instrument's ABSOLUTE scale is broken at the top (see the .md / visual-critic SKILL
§CALIBRATION-CONTROL): an absolute number is NEVER a quality verdict. So this tool refuses to
emit a PASS from an absolute; it emits PASS only when, per scene, our median >= the disguised
control's median (delta >= 0). Zero engine state, zero render — pure post-scoring aggregation.

USAGE
-----
    # panel.json: {"scene":"rest:tavern-innkeeper", "backend":"unity-cl", "round":1,
    #              "pregate":"PASS", "frames":[
    #                {"id":"frame_01","kind":"ours","overall":6.1,
    #                 "dims":{"placement_plausibility":6,"inhabitation":6,"idle_life":5,
    #                         "scene_light_coherence":7,"grounding_integration":6}},
    #                {"id":"frame_03","kind":"control","overall":5.4,"dims":{...}}]}
    python3 qa/felt_rest_panel.py --panel panel.json            # log + print the delta verdict
    python3 qa/felt_rest_panel.py --panel panel.json --dry-run  # verdict only, no scores_db write

The 5 rest lenses (0-10 each): placement_plausibility, inhabitation, idle_life,
scene_light_coherence, grounding_integration (see qa/felt_rest_panel.md).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# The 5 rest lenses the .md protocol scores on — the visual_dims_json map for a rest frame.
REST_LENSES = (
    "placement_plausibility",
    "inhabitation",
    "idle_life",
    "scene_light_coherence",
    "grounding_integration",
)


def _median(vals: list[float]) -> float | None:
    vals = [float(v) for v in vals if v is not None]
    return round(statistics.median(vals), 3) if vals else None


def summarize(panel: dict) -> dict:
    """Aggregate a panel dict into the citable metric: our median vs the disguised control median.

    The absolute medians are reported for the ledger, but the VERDICT is delta-only: PASS iff
    ours_median >= control_median (delta >= 0) AND no CRITICAL pre-gate short-circuited the panel.
    """
    frames = panel.get("frames") or []
    ours = [f for f in frames if f.get("kind") == "ours"]
    controls = [f for f in frames if f.get("kind") == "control"]
    ours_median = _median([f.get("overall") for f in ours])
    control_median = _median([f.get("overall") for f in controls])
    pregate = str(panel.get("pregate", "SKIPPED")).upper()
    delta = None
    if ours_median is not None and control_median is not None:
        delta = round(ours_median - control_median, 3)
    # The instrument's absolute scale is not citable; the verdict is delta-only + pre-gate clean.
    verdict = "INCONCLUSIVE"
    if not controls:
        verdict = "NO_CONTROL"  # a panel without a disguised control violates the calibration law
    elif pregate == "FLAG":
        verdict = "PREGATE_BLOCKED"  # a CRITICAL/HIGH pre-gate short-circuits the panel
    elif delta is not None:
        verdict = "PASS" if delta >= 0 else "FAIL"
    return {
        "scene": panel.get("scene", ""),
        "ours_median": ours_median,
        "control_median": control_median,
        "delta": delta,
        "pregate": pregate,
        "n_ours": len(ours),
        "n_control": len(controls),
        "verdict": verdict,
    }


def _log_frames(panel: dict, run_prefix: str, db_path: str | None) -> int:
    """Log each frame as a scores_db surface="visual" row. Control rows get a ":control" scene
    suffix so the same-panel delta is queryable from the ledger, not only the panel report."""
    from scores_db import add_run  # local import so --dry-run needs no db

    scene = str(panel.get("scene", "rest:unknown"))
    backend = str(panel.get("backend", "unity-cl"))
    vround = int(panel.get("round", 1))
    pregate = str(panel.get("pregate", "SKIPPED")).upper()
    n = 0
    for f in panel.get("frames") or []:
        fid = str(f.get("id", f"frame_{n}"))
        is_control = f.get("kind") == "control"
        dims = {k: f.get("dims", {}).get(k) for k in REST_LENSES if f.get("dims", {}).get(k) is not None}
        run_id = f"{run_prefix}-{scene.replace(':', '_')}-r{vround}-{fid}"
        kw: dict = dict(
            surface="visual",
            visual_scene=scene + (":control" if is_control else ""),
            visual_backend=backend,
            visual_round=vround,
            visual_pregate=pregate,
            visual_overall=f.get("overall"),
        )
        if dims:
            kw["visual_dims_json"] = dims
        if db_path:
            add_run(run_id, db_path=db_path, **kw)
        else:
            add_run(run_id, **kw)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `import scores_db` resolves
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", required=True, help="panel JSON (scene/backend/round/pregate/frames[])")
    ap.add_argument("--run-prefix", default="felt-rest", help="run_id prefix for logged rows")
    ap.add_argument("--db-path", default=None, help="scores.db path (default: scores_db.DB_PATH)")
    ap.add_argument("--dry-run", action="store_true", help="print the verdict only; do NOT write scores_db")
    args = ap.parse_args(argv)

    panel = json.loads(Path(args.panel).read_text(encoding="utf-8"))
    summary = summarize(panel)
    if not args.dry_run:
        summary["logged_rows"] = _log_frames(panel, args.run_prefix, args.db_path)
    print(json.dumps(summary, indent=2))
    # exit non-zero on a hard FAIL / missing control so a gate wrapper can react.
    return 0 if summary["verdict"] in ("PASS", "INCONCLUSIVE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
