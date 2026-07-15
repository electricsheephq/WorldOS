#!/usr/bin/env python3
"""gate.py — camera-pin img2img EXPERIMENT gates (Phase D datum, DATUM-ONLY lane).

Reuses the EXACT shipping primitives, same pattern as qa/evidence/1556/gate.py:

Gate 1 (registration): edge-recall of a candidate vs the INPUT plate (crypt_fresh_v1.png, the
  registered/adopted fresh crypt — NOT the raw greybox), via qa/plate_overlays.registration_recall
  (EDGE_THR 24, TOL 3, contract 1344x768). >=0.95 stays; below bar => refuted at that strength.

Gate 2 (ADDITIONS-LOCK / no invented furniture): qa/journey_visual_sweep.inverse_coherence_flags
  against the qa/room_manifests/crypt_fresh.cells.json manifest (camera_fit ortho 10.5224, occlusion
  cells taken directly from the manifest's own per-prop `occlusion` field — no live engine surface
  needed since the camera + geometry are IDENTICAL to the input by construction, camera-pin). NET-NEW
  is computed vs the INPUT plate's own flags (same detector run on crypt_fresh_v1.png first) so a cell
  already flagged (a pre-existing painterly-texture false-positive on the input) does not count against
  a candidate that merely re-paints the same surface a bit more.

Gate 4 (hard floor): qa/visual_pregate.run_pregates — frame-lit (G1) + luma-staging-law (G6); no
  scenegrid/actors so G2-G4 (occupancy/floor-contact/screen-scale) SKIP by design (background-only
  detail pass, no actors in frame).

  python3 qa/evidence/camerapin-img2img/gate.py <candidate_plate.png> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_QA = Path(__file__).resolve().parents[2]  # .../qa
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))

from plate_overlays import registration_recall  # noqa: E402
from journey_visual_sweep import inverse_coherence_flags, load_plate_edges  # noqa: E402
from visual_pregate import run_pregates  # noqa: E402

INPUT = _QA / "evidence" / "crypt-fresh" / "crypt_fresh_v1.png"
MANIFEST = _QA / "room_manifests" / "crypt_fresh.cells.json"
MIN_RECALL = 0.95


def _load_manifest_geometry() -> dict:
    m = json.loads(MANIFEST.read_text())
    cols, rows = m["grid"]["cols"], m["grid"]["rows"]
    walkable = [(int(c), int(r)) for (c, r) in m["walkable"]]
    prop_cells = {(int(c), int(r)) for p in m.get("props", []) for (c, r) in p.get("footprint", [])}
    occlusion_cells = {(int(c), int(r)) for p in m.get("props", []) for (c, r) in p.get("occlusion", [])}
    ortho = m.get("ortho") if m.get("camera_fit") else None
    return {"cols": cols, "rows": rows, "walkable": walkable, "prop_cells": prop_cells,
            "occlusion_cells": occlusion_cells, "ortho": ortho}


def _ic_flagged_cells(plate: str | Path, geo: dict) -> set:
    edges = load_plate_edges(plate)
    res = inverse_coherence_flags(edges, geo["walkable"], geo["prop_cells"], geo["cols"], geo["rows"],
                                   room="crypt_fresh", occlusion_cells=geo["occlusion_cells"],
                                   ortho=geo["ortho"])
    return {tuple(f["cell"]) for f in res.flagged}, {tuple(f["cell"]) for f in res.exempted}, res


def run(plate: str | Path) -> dict:
    geo = _load_manifest_geometry()

    recall_input = registration_recall(INPUT, plate)

    input_flagged, input_exempted, input_res = _ic_flagged_cells(INPUT, geo)
    cand_flagged, cand_exempted, cand_res = _ic_flagged_cells(plate, geo)

    net_new_flagged = sorted(cand_flagged - input_flagged)
    net_new_exempted = sorted(cand_exempted - input_exempted - input_flagged)
    # A net-new flag that also falls in the manifest's authored occlusion band is NOT invented
    # furniture (authored tall-prop silhouette painted a touch stronger) — only a net-new flag on
    # genuinely clear floor (outside every authored occlusion band) is a real ADDITIONS-LOCK concern.
    net_new_on_clear_floor = [c for c in net_new_flagged if c not in geo["occlusion_cells"]]

    pregate = run_pregates(render_png=str(plate))

    return {
        "plate": str(plate),
        "gate1_recall_vs_input": {"value": round(recall_input, 4), "bar": MIN_RECALL,
                                   "pass": recall_input >= MIN_RECALL},
        "gate2_inverse_coherence": {
            "input_flagged": sorted(input_flagged), "input_exempted": sorted(input_exempted),
            "candidate_flagged": sorted(cand_flagged), "candidate_exempted": sorted(cand_exempted),
            "net_new_flagged_total": net_new_flagged,
            "net_new_inside_occlusion": [c for c in net_new_flagged if c in geo["occlusion_cells"]],
            "net_new_on_clear_floor": net_new_on_clear_floor,
            "pass": len(net_new_on_clear_floor) == 0,
        },
        "gate4_visual_pregate": {"verdict": pregate["verdict"],
                                  "gates": [{"gate": g["gate"], "severity": g["severity"],
                                             "metric": g.get("metric"), "value": g.get("value")}
                                            for g in pregate["gates"]],
                                  "pass": pregate["verdict"] != "FLAG"},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plate")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(a.plate)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        g1, g2, g4 = out["gate1_recall_vs_input"], out["gate2_inverse_coherence"], out["gate4_visual_pregate"]
        print(f"recall_vs_input={g1['value']} (pass={g1['pass']})  "
              f"net_new_on_clear_floor={len(g2['net_new_on_clear_floor'])} (pass={g2['pass']})  "
              f"pregate={g4['verdict']} (pass={g4['pass']})")
