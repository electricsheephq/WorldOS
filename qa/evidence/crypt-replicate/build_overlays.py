#!/usr/bin/env python3
"""CRYPT-REPLICATE (iter3) overlay + recall builder.

Composition gate for the crypt style-replication lane: for each iter3 candidate, compute
edge-alignment recall vs the #1514 crypt greybox and emit a magenta-edge overlay image so a
human can verify the painted structure lands on the authored greybox geometry (no added
objects / rooms / openings). Reuses the shared primitives in qa/plate_overlays.py.

Run: python3 qa/evidence/crypt-replicate/build_overlays.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "qa"))
from plate_overlays import W, H, edge_mask, recall  # noqa: E402

BASE = Path(__file__).resolve().parent
GREY = BASE / "refs" / "crypt_greybox.png"
SMALL = (896, 512)

CANDS = {
    "iter3_a1": BASE / "gen" / "iter3_a1.png",
    "iter3_a2": BASE / "gen" / "iter3_a2.png",
    "iter3_b1": BASE / "gen" / "iter3_b1.png",
    "iter3_b2": BASE / "gen" / "iter3_b2.png",
}
# reference points: iter2-f (the 6.0 rejected candidate) + incumbent, for a calibration baseline
REFS = {
    "iter2f_baseline": BASE / "refs" / "crypt_iter2_candidate_f_SELECTED.jpg",
    "incumbent": BASE / "refs" / "incumbent_crypt_armb_iter3.jpg",
}
OUT = BASE / "overlays"
OUT.mkdir(exist_ok=True)

grey = Image.open(GREY).convert("RGB").resize((W, H))
grey_edges = edge_mask(grey)
edge_rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
edge_rgba.paste(Image.new("RGBA", (W, H), (255, 0, 255, 255)), (0, 0), grey_edges)
grey.resize(SMALL).save(OUT / "greybox_control.jpg", quality=85)

print("EDGE-ALIGNMENT RECALL vs #1514 crypt greybox (1.0 = every authored edge has painted structure):")
results = {}
for name, path in {**CANDS, **REFS}.items():
    plate = Image.open(path).convert("RGB").resize((W, H))
    r = recall(grey_edges, edge_mask(plate))
    results[name] = r
    print(f"  {name:20s} recall={r:.4f}")
    if name in CANDS:
        over = plate.convert("RGBA")
        over.alpha_composite(edge_rgba)
        over.convert("RGB").resize(SMALL).save(OUT / f"overlay_{name}.jpg", quality=85)

import json
(OUT / "recall.json").write_text(json.dumps(results, indent=2))
print(f"\nOverlays + recall.json written to {OUT}")
