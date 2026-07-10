#!/usr/bin/env python3
"""Registration + A/B evidence builder for W6.3b (#1470 / relight-registration criterion).

For each generated plate this writes:
  * overlay_<arm>.jpg  — the greybox control-image EDGES (bright magenta) composited over the plate,
    so a human can see whether painted structure lands on the authored geometry.
  * plate_<arm>.jpg    — the downscaled plate itself.
and reports EDGE-ALIGNMENT RECALL: the fraction of greybox structural-edge pixels that have a plate
edge within a 3px tolerance (brightness-robust, unlike a luma silhouette — Arm B is firelit/dark).
High recall == the plate preserves the greybox framing (the relight stack can register its
depth/normal sidecars against it). Low recall == props dropped / floor outpainted (drift).

Deterministic, offline (PIL only). Regenerate: python3 qa/evidence/1470/build_overlays.py

The edge_mask/recall primitives now live in qa/plate_overlays.py (shared with qa/plate_loop.py's
registration gate); this file imports them so there is ONE implementation of the recall math.
"""
import os
import sys

from PIL import Image

# Share the registration primitives with qa/plate_loop.py (single source of truth).
_QA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)
from plate_overlays import TOL, edge_mask, recall  # noqa: E402

SRC = {
    "greybox": "/tmp/camp1470_greybox.png",
    "armA_current": "/tmp/camp1470_armA_current/room_camp_clearing_night_0.png",
    "armB_controlnet": "/tmp/camp1470_armB_controlnet/room_camp_clearing_night.png",
}
OUT = os.path.dirname(os.path.abspath(__file__))
W, H = 1344, 768
SMALL = (896, 512)  # TOL / edge_mask / recall now imported from qa/plate_overlays.py

grey = Image.open(SRC["greybox"]).convert("RGB").resize((W, H))
grey_edges = edge_mask(grey, 24)
edge_rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
edge_rgba.paste(Image.new("RGBA", (W, H), (255, 0, 255, 255)), (0, 0), grey_edges)
grey.resize(SMALL).save(f"{OUT}/greybox_control.jpg", quality=85)

print("EDGE-ALIGNMENT RECALL vs greybox (1.0 = every authored edge has painted structure on it):")
for name in ("armA_current", "armB_controlnet"):
    plate = Image.open(SRC[name]).convert("RGB").resize((W, H))
    over = plate.convert("RGBA")
    over.alpha_composite(edge_rgba)
    over.convert("RGB").resize(SMALL).save(f"{OUT}/overlay_{name}.jpg", quality=85)
    plate.resize(SMALL).save(f"{OUT}/plate_{name}.jpg", quality=85)
    r = recall(grey_edges, edge_mask(plate, 24))
    print(f"  {name:16s}: {r:.3f}")
