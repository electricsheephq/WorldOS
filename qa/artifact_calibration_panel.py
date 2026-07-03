#!/usr/bin/env python3
"""artifact_calibration_panel.py — run ONE calibration panel per artifact class (HV1, #1323).

A panel = N blind scorers (default 5, sonnet) scoring a SHUFFLED set of artifacts for one class, with
the disguised hand-authored canon CONTROLS embedded among them. The panel is VALID only if the
controls land inside their expected band (the ±1.2 noise law, per qa/artifact_controls_identity.json).
The scorer never sees which artifacts are controls (build_card is payload-only); the identity map is
read here — AFTER scoring — to compute the control-band verdict.

INPUTS per class:
  * candidate artifacts  — extracted from an existing finished campaign snapshot
    (qa/artifact_snapshot_reader.py) for quest/npc; the location/encounter classes calibrate on the
    canon controls themselves (no live extractor yet — that's HV2), which is fine: the panel still
    validates instrument stability (repeat-scorer variance) and the band check is trivially met.
  * controls             — qa/artifact_controls/*.json (disguised canon), embedded in the same panel.

For each artifact the panel scores it N times, records N rows in scores.db `artifacts` (one per
scorer, sharing a panel_id), and reports the median overall. The control-band verdict per class:
every control's MEDIAN overall must be within [anchor-1.2, anchor+1.2]; otherwise the panel is FLAGGED
INVALID for that class.

    python3 qa/artifact_calibration_panel.py --class quest \
        --candidates-dir /tmp/cand_quests --panel-size 5 [--budget 1.50] [--db qa/scores.db]
    python3 qa/artifact_calibration_panel.py --class location --controls-only --panel-size 3

Set WORLDOS_ARTIFACT_PANEL_DRYRUN=1 to skip the live scorer and emit deterministic stub scores at the
anchor (offline CI / wiring proof — no claude -p, no cost).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402
import artifact_score  # noqa: E402
from scoring_config_version import artifact_config_version  # noqa: E402

CONTROLS_DIR = QA_DIR / "artifact_controls"
IDENTITY_PATH = QA_DIR / "artifact_controls_identity.json"


def _load_identity() -> dict:
    if IDENTITY_PATH.exists():
        return json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    return {"anchor": 4.0, "noise_law": 1.2, "controls": {}}


def _controls_for_class(cls: str) -> list[dict]:
    out = []
    for p in sorted(glob.glob(str(CONTROLS_DIR / f"control__{cls}__*.json"))):
        out.append(json.loads(Path(p).read_text(encoding="utf-8")))
    return out


def _candidates_for_class(candidates_dir: Optional[str], cls: str) -> list[dict]:
    if not candidates_dir:
        return []
    out = []
    for p in sorted(glob.glob(str(Path(candidates_dir) / "*.json"))):
        obj = json.loads(Path(p).read_text(encoding="utf-8"))
        if obj.get("class") == cls:
            out.append(obj)
    return out


def _dryrun_card(artifact: dict, anchor: float) -> dict:
    """Deterministic stub scorecard at the anchor (offline wiring proof — no live scorer)."""
    _, schema_name = artifact_score.RUBRIC_FOR_CLASS[artifact["class"]]
    schema = json.loads((QA_DIR / schema_name).read_text(encoding="utf-8"))
    dims = list(schema["properties"]["scores"]["properties"].keys())
    return {"scores": {d: anchor for d in dims}, "overall": anchor,
            "defects": [], "highlights": [], "verdict": "dryrun stub"}


def run_panel(
    cls: str,
    *,
    candidates_dir: Optional[str] = None,
    controls_only: bool = False,
    panel_size: int = 5,
    budget: str = "1.50",
    db_path: Path | str = scores_db.DB_PATH,
    write_db: bool = True,
) -> dict:
    identity = _load_identity()
    noise = float(identity.get("noise_law", 1.2))
    controls = _controls_for_class(cls)
    candidates = [] if controls_only else _candidates_for_class(candidates_dir, cls)
    id_map = identity.get("controls", {})

    panel_id = f"cal-{cls}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    dryrun = os.environ.get("WORLDOS_ARTIFACT_PANEL_DRYRUN") == "1"

    # The blind pool: candidates + controls, marked internally (never in the card).
    pool: list[tuple[dict, bool, Optional[float]]] = []
    for a in candidates:
        pool.append((a, False, None))
    for a in controls:
        entry = id_map.get(a["artifact_id"], {})
        pool.append((a, True, entry.get("anchor", identity.get("anchor", 4.0))))

    results: list[dict] = []
    for artifact, is_control, anchor in pool:
        overalls: list[float] = []
        for scorer_i in range(panel_size):
            if dryrun:
                card = _dryrun_card(artifact, float(anchor or identity.get("anchor", 4.0)))
            else:
                card = artifact_score.score_artifact(artifact, budget=budget)
            overalls.append(float(card["overall"]))
            if write_db:
                # One row per scorer; the panel_id + a scorer suffix keep artifact_id unique.
                aid = f"{artifact['artifact_id']}#{panel_id}#s{scorer_i}"
                prov = artifact.get("provenance") or {}
                scores_db.add_artifact(
                    aid, db_path=db_path, **{"class": cls}, run_id=prov.get("run_id"),
                    world=artifact.get("world"), sha=prov.get("sha"),
                    dims_json=card.get("scores"), overall=card.get("overall"),
                    panel_id=panel_id, scorer_model="sonnet",
                    is_control=int(is_control), control_anchor=anchor,
                    source_path=artifact["artifact_id"],
                )
        med = statistics.median(overalls)
        results.append({
            "artifact_id": artifact["artifact_id"], "is_control": is_control,
            "anchor": anchor, "median_overall": round(med, 2),
            "overalls": [round(o, 2) for o in overalls],
        })

    # Control-band verdict: every control's median within [anchor-noise, anchor+noise].
    control_results = [r for r in results if r["is_control"]]
    out_of_band = []
    for r in control_results:
        anchor = float(r["anchor"] if r["anchor"] is not None else identity.get("anchor", 4.0))
        lo, hi = anchor - noise, anchor + noise
        if not (lo <= r["median_overall"] <= hi):
            out_of_band.append({"artifact_id": r["artifact_id"], "median": r["median_overall"],
                                "band": [round(lo, 1), round(hi, 1)]})
    candidate_results = [r for r in results if not r["is_control"]]
    cand_median = (round(statistics.median([r["median_overall"] for r in candidate_results]), 2)
                   if candidate_results else None)

    return {
        "panel_id": panel_id,
        "class": cls,
        "ac_ruler": artifact_config_version(),
        "panel_size": panel_size,
        "dryrun": dryrun,
        "n_candidates": len(candidate_results),
        "n_controls": len(control_results),
        "candidate_median_overall": cand_median,
        "control_medians": [{"artifact_id": r["artifact_id"], "median": r["median_overall"],
                             "anchor": r["anchor"]} for r in control_results],
        "controls_in_band": not out_of_band,
        "out_of_band": out_of_band,
        "panel_valid": not out_of_band,
        "results": results,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class", dest="cls", required=True, choices=list(artifact_score.RUBRIC_FOR_CLASS))
    ap.add_argument("--candidates-dir", default=None, help="dir of extracted candidate artifact JSONs")
    ap.add_argument("--controls-only", action="store_true", help="score only the canon controls (no candidates)")
    ap.add_argument("--panel-size", type=int, default=5, help="number of blind scorers (default 5)")
    ap.add_argument("--budget", default="1.50")
    ap.add_argument("--db", default=str(scores_db.DB_PATH))
    ap.add_argument("--no-db", action="store_true", help="do not write panel rows to scores.db")
    args = ap.parse_args(argv)

    report = run_panel(
        args.cls, candidates_dir=args.candidates_dir, controls_only=args.controls_only,
        panel_size=args.panel_size, budget=args.budget, db_path=args.db, write_db=not args.no_db,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["panel_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
