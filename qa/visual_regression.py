#!/usr/bin/env python3
"""Did this visual-critic round REGRESS vs the scene's canonical baseline frame? — the visual
analogue of qa/detect_regression.py, for surface="visual" rows.

WHY A SEPARATE HELPER
---------------------
qa/detect_regression.py compares the STORY/MECH/ANGRY lens columns against a canonical GREEN
baseline for a comparability key. The visual loop scores DIFFERENT columns (visual_overall +
the 6-lens visual_dims_json), so this helper reuses the SAME canonical-baseline machinery
(scores_db.set_canonical_baseline / get_canonical_baseline) but classifies the VISUAL deltas.
It is a pure reader: never writes state, never renders.

The comparability key for a visual scene is (surface="visual", visual_scene, visual_backend) —
NOT dm_model/methodology (those are story-loop axes). A scene's baseline is its first frame to
cross the bar (overall>=8, no CRITICAL/HIGH); every later round of THAT scene+backend is judged
against it. Cross-scene comparison is intentionally refused (a tavern frame is not a baseline for
a dungeon frame).

NOISE FLOOR (visual scores are 0-10, panel-synthesized; coarser than the engine lenses):
  overall : +/-0.7  (an LLM panel re-scores a fixed frame within ~0.5; 0.7 is a safe floor)
  per-dim : +/-1.0  (single-lens scores are noisier; only a >=1.0 drop is a real per-dim regression)

VERDICT: REGRESSED if overall drops below -0.7 OR any dim drops >=1.0 OR a new CRITICAL/HIGH
defect appears that the baseline did not have; IMPROVED if overall rises >+0.7 and nothing
regressed; WITHIN_NOISE otherwise; NO_BASELINE when the scene has no canonical baseline yet.

USAGE
-----
    from visual_regression import detect_visual_regression
    res = detect_visual_regression(candidate_row, db_path="qa/scores.db")
    print(res["verdict"], res["message"])

    # CLI (look the candidate up in the ledger by run_id):
    python qa/visual_regression.py --candidate vc-tavern-r3-ab12cd34 --json

Exit codes: 0 = IMPROVED/WITHIN_NOISE, 2 = REGRESSED, 3 = NO_BASELINE/NO_DATA.

NOTE: this assumes the scores_db `visual` surface + visual_* columns from
qa/scores_db_visual_patch.md have landed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import scores_db  # noqa: E402

OVERALL_FLOOR = 0.7
DIM_FLOOR = 1.0
# Comparability key: a scene's baseline is keyed on (visual_scene, visual_backend).
# Cross-scene comparison is intentionally refused (a tavern frame is not a baseline for a dungeon).
_EXIT = {"IMPROVED": 0, "WITHIN_NOISE": 0, "REGRESSED": 2, "NO_BASELINE": 3, "NO_DATA": 3}


def _visual_baseline(scene: Optional[str], backend: Optional[str], db_path) -> Optional[dict]:
    """The canonical baseline frame for a (visual_scene, visual_backend). We reuse the canonical
    marker but key on the visual axes: scan canonical rows on surface='visual' and match the pair."""
    rows = scores_db.fetch_rows(db_path=db_path)
    cands = [
        r for r in rows
        if r.get("surface") == "visual"
        and r.get("is_canonical_baseline") == 1
        and r.get("visual_scene") == scene
        and r.get("visual_backend") == backend
    ]
    return cands[0] if cands else None


def _dims(row: dict) -> dict:
    """Return the visual_dims_json as a {str: float} dict (only numeric-valued keys).
    Returns {} on any parse/type error — callers treat missing dims as NO_DATA."""
    raw = row.get("visual_dims_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(parsed, dict):
        return {}
    # Keep only keys with numeric values; skip non-numeric (malformed) entries.
    return {k: v for k, v in parsed.items() if isinstance(v, (int, float))}


def detect_visual_regression(candidate: dict, db_path: Path | str = scores_db.DB_PATH) -> dict:
    scene = candidate.get("visual_scene")
    backend = candidate.get("visual_backend")
    baseline = _visual_baseline(scene, backend, db_path)

    result: dict = {
        "candidate_run": candidate.get("run_id"),
        "baseline_run": baseline.get("run_id") if baseline else None,
        "scene": scene, "backend": backend,
        "overall": {"candidate": candidate.get("visual_overall"),
                    "baseline": baseline.get("visual_overall") if baseline else None,
                    "delta": None, "floor": OVERALL_FLOOR, "classification": "NO_DATA"},
        "per_dim": [],
        "new_blocking": [],
    }

    if baseline is None:
        result["verdict"] = "NO_BASELINE"
        result["message"] = (
            f"No canonical baseline frame for scene={scene!r} backend={backend!r}. "
            "Once a frame first crosses the bar (overall>=8, no CRITICAL/HIGH), call "
            "scores_db.set_canonical_baseline(<that run_id>); later rounds then detect regression."
        )
        return result

    # Overall.
    cv, bv = candidate.get("visual_overall"), baseline.get("visual_overall")
    regressed = False
    improved = False
    if cv is not None and bv is not None:
        d = round(float(cv) - float(bv), 3)
        cls = "REGRESSED" if d < -OVERALL_FLOOR else ("IMPROVED" if d > OVERALL_FLOOR else "WITHIN_NOISE")
        result["overall"].update(delta=d, classification=cls)
        regressed |= cls == "REGRESSED"
        improved |= cls == "IMPROVED"

    # Per-dim.
    cd, bd = _dims(candidate), _dims(baseline)
    for dim in sorted(set(cd) | set(bd)):
        c, b = cd.get(dim), bd.get(dim)
        if c is None or b is None:
            result["per_dim"].append({"dim": dim, "candidate": c, "baseline": b,
                                      "delta": None, "classification": "NO_DATA"})
            continue
        d = round(float(c) - float(b), 3)
        cls = "REGRESSED" if d <= -DIM_FLOOR else ("IMPROVED" if d >= DIM_FLOOR else "WITHIN_NOISE")
        result["per_dim"].append({"dim": dim, "candidate": c, "baseline": b,
                                  "delta": d, "floor": DIM_FLOOR, "classification": cls})
        regressed |= cls == "REGRESSED"
        improved |= cls == "IMPROVED"

    # New blocking defects the baseline did not carry.
    cand_block = {x.strip() for x in str(candidate.get("visual_blocking") or "").split(",") if x.strip()}
    base_block = {x.strip() for x in str(baseline.get("visual_blocking") or "").split(",") if x.strip()}
    new_block = sorted(cand_block - base_block)
    result["new_blocking"] = new_block
    if new_block:
        regressed = True

    result["verdict"] = "REGRESSED" if regressed else ("IMPROVED" if improved else "WITHIN_NOISE")
    result["message"] = _summary(result)
    return result


def _summary(r: dict) -> str:
    lines = [f"{r['verdict']}: {r['candidate_run']} vs baseline {r['baseline_run']} "
             f"(scene={r['scene']} backend={r['backend']})"]
    o = r["overall"]
    if o["delta"] is not None:
        sign = "+" if o["delta"] >= 0 else ""
        lines.append(f"  overall {o['baseline']} -> {o['candidate']} ({sign}{o['delta']}, floor +/-{o['floor']}) {o['classification']}")
    for p in r["per_dim"]:
        if p["delta"] is None:
            lines.append(f"  {p['dim']:24s} no-data")
        else:
            sign = "+" if p["delta"] >= 0 else ""
            lines.append(f"  {p['dim']:24s} {p['baseline']} -> {p['candidate']} ({sign}{p['delta']}) {p['classification']}")
    if r["new_blocking"]:
        lines.append(f"  NEW blocking defects vs baseline: {', '.join(r['new_blocking'])}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Visual-critic regression vs the scene's canonical baseline frame")
    ap.add_argument("--candidate", help="run_id to look up in the ledger")
    ap.add_argument("--candidate-json", help="candidate row JSON or @file.json")
    ap.add_argument("--db", default=str(scores_db.DB_PATH))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.candidate_json:
        raw = args.candidate_json
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text()
        cand = json.loads(raw)
    else:
        rows = {r["run_id"]: r for r in scores_db.fetch_rows(db_path=args.db)}
        cand = rows.get(args.candidate)
        if cand is None:
            raise SystemExit(f"visual_regression: no run '{args.candidate}' in {args.db}")
    res = detect_visual_regression(cand, db_path=args.db)
    print(json.dumps(res, indent=2) if args.json else res["message"])
    return _EXIT.get(res["verdict"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
