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
"""
import os
from PIL import Image, ImageFilter

SRC = {
    "greybox": "/tmp/camp1470_greybox.png",
    "armA_current": "/tmp/camp1470_armA_current/room_camp_clearing_night_0.png",
    "armB_controlnet": "/tmp/camp1470_armB_controlnet/room_camp_clearing_night.png",
}
OUT = os.path.dirname(os.path.abspath(__file__))
W, H = 1344, 768
SMALL = (896, 512)
TOL = 3  # px tolerance for an edge to count as aligned


def edge_mask(im, thr):
    return im.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > thr else 0)


def recall(grey_edges, plate_edges, tol=TOL):
    """Fraction of greybox edge pixels covered by a plate edge within `tol` px (via dilation)."""
    dil = plate_edges.filter(ImageFilter.MaxFilter(2 * tol + 1))
    gd, dd = grey_edges.getdata(), dil.getdata()
    tot = cov = 0
    for g, d in zip(gd, dd):
        if g:
            tot += 1
            if d:
                cov += 1
    return cov / tot if tot else 0.0


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
