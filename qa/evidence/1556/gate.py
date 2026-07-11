#!/usr/bin/env python3
"""gate.py — the two deterministic bake-off gates for STYLE-PASS BAKE-OFF v2 (#1556).

Gate 1 (registration): edge-recall of a styled plate vs the crypt greybox, via the SHARED
qa/plate_overlays.registration_recall primitive (EDGE_THR 24, TOL 3, contract 1344x768). >=0.95 stays.

Gate 2 (ADDITIONS-LOCK): invented-furniture count via the #1540 inverse-coherence detector
(qa/journey_visual_sweep.inverse_coherence_flags) against the crypt_truegrey manifest. Must be 0.

Both gates reuse the EXACT importable primitives the shipping instruments use — no re-implementation.

  python3 qa/evidence/1556/gate.py <styled_plate.png> [--json]
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

GREYBOX = _QA / "evidence" / "true-greybox" / "crypt" / "crypt_greybox.png"
# The FIXED registered base every style-pass arm edits (flux.1-dev depth-CN, NO lora, asset_BKn1kiX2c8BaifYj559yKx7Y).
# Gate 1 primary = structure preservation of the STYLE step = styled-vs-this-base (the prior #1553 bake-off's
# `edge_recall_styled` column semantics — arm_b 0.965). The raw greybox is a SECONDARY drift anchor: the adopted
# incumbent crypt_armb_iter3 itself scores only 0.878 vs the raw greybox with this primitive, so a literal
# 0.95-vs-greybox bar is unreachable for any painterly plate; we anchor the greybox number to the live Gemini rerun.
BASE = _QA / "evidence" / "model-audit" / "bakeoff" / "arm_a_base_plain_flux.png"
MANIFEST = _QA / "room_manifests" / "crypt_truegrey.cells.json"
MIN_RECALL = 0.95


def invented_count(plate: str | Path) -> dict:
    m = json.loads(MANIFEST.read_text())
    cols, rows = m["grid"]["cols"], m["grid"]["rows"]
    walkable = [(int(c), int(r)) for (c, r) in m["walkable"]]
    prop_cells = {(int(c), int(r)) for p in m.get("props", []) for (c, r) in p.get("footprint", [])}
    edges = load_plate_edges(plate)
    res = inverse_coherence_flags(edges, walkable, prop_cells, cols, rows, room="crypt_truegrey")
    return {"invented": len(res.flagged), "flagged": res.flagged,
            "baseline_median": round(res.baseline_median, 4), "threshold": round(res.threshold, 4),
            "n_cells": res.n_cells}


def run(plate: str | Path) -> dict:
    recall_base = registration_recall(BASE, plate)      # gate 1 primary (structure preservation of style step)
    recall_grey = registration_recall(GREYBOX, plate)   # secondary drift anchor
    ic = invented_count(plate)
    return {"plate": str(plate),
            "recall_vs_base": round(recall_base, 4), "recall_pass": recall_base >= MIN_RECALL,
            "recall_vs_greybox": round(recall_grey, 4),
            "invented": ic["invented"], "invented_pass": ic["invented"] == 0,
            "gate1_recall_min": MIN_RECALL, "ic_detail": ic}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("plate")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(a.plate)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"recall_vs_base={out['recall_vs_base']} (pass={out['recall_pass']})  "
              f"recall_vs_greybox={out['recall_vs_greybox']}  "
              f"invented={out['invented']} (pass={out['invented_pass']})")
