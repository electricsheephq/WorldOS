#!/usr/bin/env python3
"""Registration evidence builder for the W6.0 relight RE-RUN (#1469 rerun, #1470 method).

The failed #1469 A/B lost on INPUT REGISTRATION: the shipped crypt plate (crypt_dense_v1) was a
full-frame img2img OUTPAINT, but the WOSRelight depth/normal sidecars frame a centered diamond on
black — the geometry-driven light landed as a ghosted band misaligned with the painted pillars.

The fix (#1470 resequence): condition the plate on the crypt greybox via ControlNet depth so the
plate inherits the greybox framing BY CONSTRUCTION, making the SAME sidecars register 1:1.

This writes, for the conditioned plate:
  * overlay_conditioned_crypt.jpg  — greybox structural EDGES (magenta) composited over the plate.
  * plate_conditioned_crypt.jpg    — the downscaled plate.
and reports EDGE-ALIGNMENT RECALL (fraction of greybox edge pixels with a plate edge within 3px),
the exact brightness-robust metric from qa/evidence/1470/build_overlays.py. High recall == the plate
preserves the greybox framing == the relight's depth/normal sidecars register against it.

Deterministic, offline (PIL only). Regenerate: python3 qa/evidence/1469/rerun/build_overlays.py
"""
import os
from PIL import Image, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))
SRC = {
    "greybox_render": f"{OUT}/greybox_control_1344x768.png",   # ControlNet control (shaded greybox)
    "depth_sidecar": f"{OUT}/crypt_greybox_depth.png",         # the relight's actual depth sidecar
    "conditioned": f"{OUT}/plate_conditioned_crypt.png",       # the new #1470-conditioned crypt plate
}
W, H = 1344, 768
SMALL = (896, 512)
TOL = 3


def edge_mask(im, thr):
    return im.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > thr else 0)


def recall(grey_edges, plate_edges, tol=TOL):
    dil = plate_edges.filter(ImageFilter.MaxFilter(2 * tol + 1))
    gd, dd = list(grey_edges.getdata()), list(dil.getdata())
    tot = cov = 0
    for g, d in zip(gd, dd):
        if g:
            tot += 1
            if d:
                cov += 1
    return cov / tot if tot else 0.0


grey = Image.open(SRC["greybox_render"]).convert("RGB").resize((W, H))
grey_edges = edge_mask(grey, 24)
depth_edges = edge_mask(Image.open(SRC["depth_sidecar"]).convert("RGB").resize((W, H)), 24)
edge_rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
edge_rgba.paste(Image.new("RGBA", (W, H), (255, 0, 255, 255)), (0, 0), grey_edges)

plate = Image.open(SRC["conditioned"]).convert("RGB").resize((W, H))
over = plate.convert("RGBA")
over.alpha_composite(edge_rgba)
over.convert("RGB").resize(SMALL).save(f"{OUT}/overlay_conditioned_crypt.jpg", quality=85)
plate.resize(SMALL).save(f"{OUT}/plate_conditioned_crypt.jpg", quality=85)
grey.resize(SMALL).save(f"{OUT}/greybox_control.jpg", quality=85)

pe = edge_mask(plate, 24)
r_struct = recall(grey_edges, pe)
r_depth = recall(depth_edges, pe)
print("REGISTRATION — conditioned crypt plate vs greybox (1.0 = every authored edge has paint on it):")
print(f"  edge-recall vs greybox structural edges (#1470 metric, camp armB=0.870): {r_struct:.3f}")
print(f"  edge-recall vs depth SIDECAR edges (relight input, #1469 criterion):     {r_depth:.3f}")
