#!/usr/bin/env python3
"""artifact_calibration_panel.py — run ONE calibration panel per artifact class (HV1, #1323).

A panel = N blind scorers (default 5, sonnet) scoring a blind set of artifacts for one class — candidates
followed by disguised hand-authored canon CONTROLS embedded among them (deterministic sorted-glob order;
NOT shuffled — the scorer never sees is-control status either way, so blindness comes from build_card's
payload-only disguise, not from pool order). The panel is VALID only if the
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
from typing import Optional

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
    # Route through artifact_score.load_artifact() — the SAME strict envelope + canonical payload-shape
    # guard the public `python3 qa/artifact_score.py <artifact.json>` CLI path enforces — so a control
    # fixture that would fail the documented CLI can't silently produce panel_valid: true here instead.
    out = []
    for p in sorted(glob.glob(str(CONTROLS_DIR / f"control__{cls}__*.json"))):
        out.append(artifact_score.load_artifact(Path(p)))
    return out


def _candidates_for_class(candidates_dir: Optional[str], cls: str) -> list[dict]:
    if not candidates_dir:
        return []
    out = []
    for p in sorted(glob.glob(str(Path(candidates_dir) / "*.json"))):
        # Peek the class first (load_artifact would raise on a different class's payload shape), then
        # route the matching file through the same strict guard _controls_for_class uses.
        if json.loads(Path(p).read_text(encoding="utf-8")).get("class") == cls:
            out.append(artifact_score.load_artifact(Path(p)))
    return out


def _band_staleness(artifact: dict, entry: dict) -> Optional[str]:
    """Return a NAMED reason if this control's stamped band was derived under a different prompt
    construction / scoring ruler than the current one, else None (#1380 guard).

    The expected band in qa/artifact_controls_identity.json is derived under ONE prompt construction
    (build_card output) and ONE artifact ruler. When the v2 extractor changed what real candidates
    carry (`description`/`resolution`) while the control lagged, the control drifted below band with
    NO signal WHY — a silent, mysterious below-band failure that blocked every quest promotion. This
    guard turns that into a named error forever: if the control's card hash or the ruler no longer
    matches what the band was calibrated against, the band is STALE and must be re-derived, and we
    say so explicitly instead of reporting a bare below-band verdict. Legacy entries with neither
    stamp are skipped (no false signal on un-migrated controls)."""
    stamped_ph = entry.get("band_prompt_hash")
    stamped_ruler = entry.get("band_ruler")
    if not stamped_ph and not stamped_ruler:
        return None
    drifted = []
    if stamped_ruler and stamped_ruler != artifact_config_version():
        drifted.append(f"ruler {stamped_ruler}->{artifact_config_version()}")
    if stamped_ph and stamped_ph != artifact_score.prompt_construction_hash(artifact):
        drifted.append(f"prompt {stamped_ph}->{artifact_score.prompt_construction_hash(artifact)}")
    if not drifted:
        return None
    return (
        "band derived under a different prompt construction (" + "; ".join(drifted) + ") — the "
        "control's field surface or the scoring ruler changed since calibration; the stamped band is "
        "STALE. Re-derive it: python3 qa/build_artifact_controls.py then a fresh calibration panel."
    )


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
        dims_per_scorer: list[dict] = []
        prov = artifact.get("provenance") or {}
        for scorer_i in range(panel_size):
            if dryrun:
                card = _dryrun_card(artifact, float(anchor or identity.get("anchor", 4.0)))
            else:
                card = artifact_score.score_artifact(artifact, budget=budget)
            overalls.append(float(card["overall"]))
            dims_per_scorer.append(card.get("scores") or {})
            if write_db:
                # One row per scorer; the panel_id + a scorer suffix keep artifact_id unique.
                aid = f"{artifact['artifact_id']}#{panel_id}#s{scorer_i}"
                scores_db.add_artifact(
                    aid, db_path=db_path, **{"class": cls}, run_id=prov.get("run_id"),
                    world=artifact.get("world"), sha=prov.get("sha"),
                    dims_json=card.get("scores"), overall=card.get("overall"),
                    panel_id=panel_id, scorer_model="sonnet",
                    is_control=int(is_control), control_anchor=anchor,
                    source_path=artifact["artifact_id"],
                )
        med = statistics.median(overalls)
        if write_db:
            # #1355: ALSO write the panel's own aggregate under the BARE artifact_id — the
            # median overall (matching this function's own control-band aggregation) plus the
            # per-dimension median across the N `#s{n}` scorer rows. Without this row,
            # tools/library/promote.py's bare-artifact_id lookup (_artifacts_by_id) never finds
            # a panel-scored artifact and a promotion batch needs a manual bridge to connect
            # them (found live in PR #1354). scores.db stays single-owner: the panel writer is
            # the one place that both produces the per-scorer rows AND knows how to aggregate
            # them, so this is additive bookkeeping on the same write path, not a second writer.
            all_dim_keys = {k for d in dims_per_scorer for k in d}
            median_dims = {
                k: round(statistics.median([d[k] for d in dims_per_scorer if k in d]), 2)
                for k in all_dim_keys
            }
            scores_db.add_artifact(
                artifact["artifact_id"], db_path=db_path, **{"class": cls},
                run_id=prov.get("run_id"), world=artifact.get("world"), sha=prov.get("sha"),
                dims_json=median_dims, overall=round(med, 2),
                panel_id=panel_id, scorer_model="sonnet",
                is_control=int(is_control), control_anchor=anchor,
                source_path=artifact["artifact_id"],
                notes=f"panel aggregate: median of {panel_size} scorer rows (#1355)",
            )
        results.append({
            "artifact_id": artifact["artifact_id"], "is_control": is_control,
            "anchor": anchor, "median_overall": round(med, 2),
            "overalls": [round(o, 2) for o in overalls],
        })

    # Control-band verdict: every control's median within [anchor-noise, anchor+noise]. Before the
    # band check, the #1380 staleness guard: a control whose stamped band was derived under a
    # different prompt construction / ruler than the current one has a STALE band — its below-band (or
    # even in-band) verdict is untrustworthy. Surface that as a NAMED reason so drift can never again
    # present as a bare, unexplained below-band failure.
    control_results = [r for r in results if r["is_control"]]
    controls_by_id = {a["artifact_id"]: a for a in controls}
    out_of_band = []
    stale_bands = []
    for r in control_results:
        entry = id_map.get(r["artifact_id"], {})
        stale_reason = _band_staleness(controls_by_id[r["artifact_id"]], entry)
        if stale_reason:
            stale_bands.append({"artifact_id": r["artifact_id"], "reason": stale_reason})
        anchor = float(r["anchor"] if r["anchor"] is not None else identity.get("anchor", 4.0))
        lo, hi = anchor - noise, anchor + noise
        if not (lo <= r["median_overall"] <= hi):
            oob = {"artifact_id": r["artifact_id"], "median": r["median_overall"],
                   "band": [round(lo, 1), round(hi, 1)]}
            if stale_reason:
                oob["reason"] = stale_reason
            out_of_band.append(oob)
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
        # A stale band invalidates the panel EVEN IF the control happens to land in the old band — the
        # band itself is no longer trustworthy until re-derived (#1380).
        "stale_bands": stale_bands,
        "panel_valid": not out_of_band and not stale_bands,
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
    if args.panel_size < 1:
        ap.error(f"--panel-size must be >= 1 (got {args.panel_size}); "
                 "panel_size<=0 scores nothing and statistics.median([]) raises")

    report = run_panel(
        args.cls, candidates_dir=args.candidates_dir, controls_only=args.controls_only,
        panel_size=args.panel_size, budget=args.budget, db_path=args.db, write_db=not args.no_db,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["panel_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
