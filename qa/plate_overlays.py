#!/usr/bin/env python3
"""plate_overlays.py — the shared REGISTRATION primitives (edge-mask + edge-alignment recall).

Extracted from qa/evidence/1470/build_overlays.py so BOTH callers share ONE implementation:
  * qa/evidence/1470/build_overlays.py — the #1470 A/B evidence-image builder (its CLI still works,
    now importing these functions instead of carrying its own copy).
  * qa/plate_loop.py — the plate-sprint harness's registration gate (edge-recall vs the room greybox).

EDGE-ALIGNMENT RECALL is the deterministic registration signal: the fraction of the greybox's
structural-edge pixels that have a plate edge within a few px. High recall == the painted plate
preserves the authored greybox framing (the relight/registration stack can register its depth/normal
sidecars against it); low recall == props dropped / floor outpainted (drift). It is brightness-robust
(edge-based, not a luma silhouette), so a firelit/dark plate is judged on structure, not exposure.

Deterministic, offline (PIL only). No numpy, no LLM.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

# The contract greybox pixel frame (greybox_render_headless.PX_W/PX_H) — both images are resized to
# this before comparison so recall is computed in one fixed frame regardless of input resolution.
W, H = 1344, 768
EDGE_THR = 24     # FIND_EDGES brightness threshold: a pixel is a "structural edge" above this
TOL = 3           # px tolerance for a plate edge to count as covering a greybox edge


def edge_mask(im: Image.Image, thr: int = EDGE_THR) -> Image.Image:
    """Binary structural-edge mask (mode 'L', 0/255) of `im` via FIND_EDGES thresholded at `thr`."""
    return im.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > thr else 0)


def recall(grey_edges: Image.Image, plate_edges: Image.Image, tol: int = TOL) -> float:
    """Fraction of greybox edge pixels covered by a plate edge within `tol` px (via dilation).

    1.0 == every authored greybox edge has painted structure on it (perfect registration); 0.0 ==
    none do. `grey_edges` / `plate_edges` are the same-size binary masks from :func:`edge_mask`."""
    dil = plate_edges.filter(ImageFilter.MaxFilter(2 * tol + 1))
    gd, dd = grey_edges.getdata(), dil.getdata()
    tot = cov = 0
    for g, d in zip(gd, dd):
        if g:
            tot += 1
            if d:
                cov += 1
    return cov / tot if tot else 0.0


def registration_recall(greybox: str | Path, plate: str | Path, *,
                        thr: int = EDGE_THR, tol: int = TOL,
                        size: tuple[int, int] = (W, H)) -> float:
    """Edge-alignment recall of a plate vs its greybox control image (the registration number).

    Both images are opened, converted to RGB, and resized to `size` (default the contract WxH) so the
    edges are compared in one fixed frame. Returns the recall in [0.0, 1.0]."""
    grey = Image.open(greybox).convert("RGB").resize(size)
    plt = Image.open(plate).convert("RGB").resize(size)
    return recall(edge_mask(grey, thr), edge_mask(plt, thr), tol)
