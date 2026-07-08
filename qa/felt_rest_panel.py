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


# Worst-to-best precedence: a combined panel's rolled-up verdict is the WORST of its per-scene
# verdicts, so one below-control scene cannot be masked by a strong sibling (see _score_group).
_VERDICT_RANK = {
    "NO_CONTROL": 0,
    "PREGATE_BLOCKED": 1,
    "FAIL": 2,
    "INCONCLUSIVE": 3,
    "PASS": 4,
}


def _score_group(ours: list[dict], controls: list[dict], pregate: str) -> dict:
    """The delta verdict for ONE scene/control pair (ours frames vs the disguised control frames).

    The absolute medians are reported for the ledger, but the VERDICT is delta-only: PASS iff
    ours_median >= control_median (delta >= 0) AND the deterministic pre-gate actually cleared
    (pregate == "PASS"). A SKIPPED/missing/FLAG pre-gate never yields PASS — the W1 binding gate
    requires "no open CRITICAL pre-gate", so an unrun pre-gate is INCONCLUSIVE, not a pass.
    """
    ours_median = _median([f.get("overall") for f in ours])
    control_median = _median([f.get("overall") for f in controls])
    delta = None
    if ours_median is not None and control_median is not None:
        delta = round(ours_median - control_median, 3)
    verdict = "INCONCLUSIVE"
    if not controls:
        verdict = "NO_CONTROL"  # a group without a disguised control violates the calibration law
    elif pregate == "FLAG":
        verdict = "PREGATE_BLOCKED"  # a CRITICAL/HIGH pre-gate short-circuits the panel
    elif delta is not None and delta < 0:
        verdict = "FAIL"
    elif delta is not None and pregate == "PASS":
        verdict = "PASS"  # delta >= 0 AND the pre-gate cleared — the only path to a binding PASS
    # else: delta >= 0 but pre-gate not PASS (SKIPPED/missing) -> INCONCLUSIVE (not a real pass).
    return {
        "ours_median": ours_median,
        "control_median": control_median,
        "delta": delta,
        "n_ours": len(ours),
        "n_control": len(controls),
        "verdict": verdict,
    }


def summarize(panel: dict) -> dict:
    """Aggregate a panel into the citable metric: our median vs the disguised control median.

    Frames are grouped by scene (a per-frame ``scene`` overrides the panel ``scene``), and EACH
    scene/control pair is scored on its own — so a combined panel (tavern + church in one panel)
    cannot let a strong tavern offset a below-control church. The panel verdict is the WORST of
    the per-scene verdicts; the top-level medians/delta describe the worst-scoring scene.
    """
    frames = panel.get("frames") or []
    panel_scene = panel.get("scene", "")
    pregate = str(panel.get("pregate", "SKIPPED")).upper()

    # Group frames by scene (per-frame scene wins), preserving first-seen order for determinism.
    order: list[str] = []
    groups: dict[str, dict[str, list[dict]]] = {}
    for f in frames:
        scene = str(f.get("scene", panel_scene))
        if scene not in groups:
            groups[scene] = {"ours": [], "controls": []}
            order.append(scene)
        bucket = "controls" if f.get("kind") == "control" else "ours"
        groups[scene][bucket].append(f)

    if not groups:  # empty panel -> a single no-control group so the verdict is NO_CONTROL
        groups[panel_scene] = {"ours": [], "controls": []}
        order.append(panel_scene)

    scenes = []
    for scene in order:
        g = _score_group(groups[scene]["ours"], groups[scene]["controls"], pregate)
        g["scene"] = scene
        scenes.append(g)

    # The panel verdict rolls up to the WORST per-scene verdict; the reported medians/delta come
    # from that worst scene so the citable metric never over-states a mixed panel.
    worst = min(scenes, key=lambda g: (_VERDICT_RANK.get(g["verdict"], 0), g.get("delta") if g.get("delta") is not None else 0))
    return {
        "scene": worst["scene"] if len(scenes) == 1 else panel_scene,
        "ours_median": worst["ours_median"],
        "control_median": worst["control_median"],
        "delta": worst["delta"],
        "pregate": pregate,
        "n_ours": sum(g["n_ours"] for g in scenes),
        "n_control": sum(g["n_control"] for g in scenes),
        "verdict": worst["verdict"],
        "scenes": scenes,
    }


def _log_frames(panel: dict, run_prefix: str, db_path: str | None) -> int:
    """Log each frame as a scores_db surface="visual" row. Control rows get a ":control" scene
    suffix so the same-panel delta is queryable from the ledger, not only the panel report.

    Uses each frame's OWN ``scene`` (falling back to the panel-level scene) so a combined panel
    (tavern + church rows in one panel) logs each row under its real scene/control pair — matching
    the per-scene grouping ``summarize()`` already does. The row index ``n`` is folded into
    ``run_id`` so the required 5 blind scorers submitting scores for the SAME shuffled frame id
    each get a distinct row instead of colliding on ``add_run``'s default replace-on-PK write."""
    from scores_db import add_run  # local import so --dry-run needs no db

    panel_scene = str(panel.get("scene", "rest:unknown"))
    backend = str(panel.get("backend", "unity-cl"))
    vround = int(panel.get("round", 1))
    pregate = str(panel.get("pregate", "SKIPPED")).upper()
    n = 0
    for f in panel.get("frames") or []:
        scene = str(f.get("scene", panel_scene))
        fid = str(f.get("id", f"frame_{n}"))
        is_control = f.get("kind") == "control"
        raw_dims = f.get("dims") or {}
        if scene.split(":", 1)[0] == "rest":
            # The protocol's own 5 rest lenses (qa/felt_rest_panel.md) — filter to just those so a
            # stray/bogus key never leaks into the ledger's visual_dims_json for a rest frame.
            dims = {k: raw_dims.get(k) for k in REST_LENSES if raw_dims.get(k) is not None}
        else:
            # NON-rest invocation (this logger is generic infra, reused by any control-anchored
            # panel — RUNBOOK-INDEX gap 3, "felt_rest_panel non-rest write path"). The row itself
            # was ALWAYS written for every scene (add_run below runs unconditionally); the bug was
            # here: REST_LENSES-only filtering silently dropped every non-rest lens key, so a
            # non-rest panel's per-lens scores never reached visual_dims_json even though the row
            # existed. Pass through whatever dims the frame actually carries instead.
            dims = {k: v for k, v in raw_dims.items() if v is not None}
        run_id = f"{run_prefix}-{scene.replace(':', '_')}-r{vround}-{fid}-i{n}"
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
    # Exit 0 ONLY on a real binding-gate pass. INCONCLUSIVE (ours frames missing/unscored, or the
    # pre-gate never cleared) is NOT a pass of the W1 gate (ours_median >= control_median), so it
    # exits non-zero alongside FAIL / NO_CONTROL / PREGATE_BLOCKED — a gate wrapper must not read
    # an unscored panel as green.
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
