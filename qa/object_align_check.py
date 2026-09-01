"""Per-object alignment of a styled plate vs its depth-proven base (kit pipeline styled-layer gate v1).

The global gate (styled_align_check.py) measures ONE whole-frame transform and is blind to a styling
pass re-centering individual objects: the adopted kit-tavern plate was globally ALIGNED (dx=0) while
all four round tables had drifted ~half a cell off their collision footprints — the owner felt it as
"walking through tables" (2026-07-23 playtest). This instrument closes that hole: for every non-wall
box in the room's sidecar, it crops the box's projected screen region (padded) from BOTH images and
runs local FFT phase correlation. An object passes iff its local |offset| <= the cell budget AND the
correlation peak is strong enough to trust.

The response floor is load-bearing (review finding, 2026-09-02). Phase correlation of two FEATURELESS
crops mean-centres to zero on both sides, and `argmax` then returns (0, 0) with a near-zero peak — a
missing, deleted, or unrendered object reads as PERFECT alignment. So a peak response below
--min-resp is reported LOW-CONFIDENCE and counted as a FAILURE, never as a pass. The default (0.05)
is a conservative starting floor, not a calibrated constant: recalibrate it against known-aligned
fixtures for a new room/style and pass the measured value via --min-resp.

Objects the gate does not measure are REPORTED, never silently dropped (same review finding): long
wall/parapet/floor/door runs have no localisable feature for a local phase correlation, and a box
whose projected bbox is smaller than the FFT window has no stable spectrum. Both counts (and the
skipped kinds) appear in the verdict line so a green verdict can never hide an empty check.

Usage:
  python qa/object_align_check.py <boxes.json> <base.png> <styled.png> [--budget-cells 0.35]
                                  [--min-resp 0.05]

Base should be the room's depth-proven flux base (its furniture sits at kit-truth by the recall
gate); styled is the candidate plate. Exit 0 = every object measured and within budget; 1 = any
object out of budget or low-confidence.
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from greybox_render_headless import world_to_screen  # noqa: E402

PX_W, PX_H = 1344, 768
SKIP_KINDS = {"floor", "wall", "walls", "wallback", "wallright", "wallleft", "wallfront", "parapet", "wall_run", "door"}
MIN_WINDOW_PX = 48  # below this the crop has no stable local spectrum
DEFAULT_MIN_RESP = 0.05


def luma(path):
    im = Image.open(path).convert("L")
    if im.size != (PX_W, PX_H):
        im = im.resize((PX_W, PX_H), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64)


def local_phasecorr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    sa, sb = a.std(), b.std()
    if sa > 0:
        a = a / sa
    if sb > 0:
        b = b / sb
    wy = np.hanning(a.shape[0])[:, None]
    wx = np.hanning(a.shape[1])[None, :]
    A = np.fft.rfft2(a * wy * wx)
    B = np.fft.rfft2(b * wy * wx)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    r = np.fft.irfft2(R, a.shape)
    dy, dx = np.unravel_index(np.argmax(r), r.shape)
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    return int(dx), int(dy), float(r.max())


def box_screen_bbox(box, ortho, pad):
    cx, cy, cz = box["center"]
    sx, sy, sz = box["size"]
    xs, ys = [], []
    for dx in (-0.5, 0.5):
        for dy in (-0.5, 0.5):
            for dz in (-0.5, 0.5):
                px, py = world_to_screen(cx + dx * sx, cy + dy * sy, cz + dz * sz, ortho)
                xs.append(px)
                ys.append(py)
    x0 = max(0, int(min(xs)) - pad)
    x1 = min(PX_W, int(max(xs)) + pad)
    y0 = max(0, int(min(ys)) - pad)
    y1 = min(PX_H, int(max(ys)) + pad)
    return x0, y0, x1, y1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boxes")
    ap.add_argument("base")
    ap.add_argument("styled")
    ap.add_argument("--budget-cells", type=float, default=0.35)
    ap.add_argument("--pad", type=int, default=40, help="context margin around each box's screen bbox")
    ap.add_argument("--min-resp", type=float, default=DEFAULT_MIN_RESP,
                    help="phase-correlation peak floor; below it the object is LOW-CONFIDENCE and FAILS "
                         "(a featureless crop otherwise reports a perfect dx=dy=0)")
    args = ap.parse_args()

    sidecar = json.loads(Path(args.boxes).read_text())
    ortho = float(sidecar["ortho"])
    base, styled = luma(args.base), luma(args.styled)
    # px per cell along a screen-aligned world unit: 2 world units/cell at 768px over 2*ortho world units
    px_per_cell = (PX_H / (2.0 * ortho)) * 2.0

    drifted = 0
    low_conf = 0
    checked = 0
    skipped_kinds = Counter()
    skipped_small = 0
    for i, box in enumerate(sidecar["boxes"]):
        kind = str(box.get("kind", "?")).lower()
        if kind in SKIP_KINDS:
            # local phase correlation is meaningless on a long uniform run — counted + named below,
            # never silently dropped.
            skipped_kinds[kind] += 1
            continue
        x0, y0, x1, y1 = box_screen_bbox(box, ortho, args.pad)
        if x1 - x0 < MIN_WINDOW_PX or y1 - y0 < MIN_WINDOW_PX:
            skipped_small += 1
            print(f"  {kind}#{i} @({box['center'][0]:g},{box['center'][2]:g}) "
                  f"bbox={x1 - x0}x{y1 - y0}px -> SKIPPED-TOO-SMALL (< {MIN_WINDOW_PX}px window)")
            continue
        dx, dy, resp = local_phasecorr(styled[y0:y1, x0:x1], base[y0:y1, x0:x1])
        err_cells = math.hypot(dx, dy) / px_per_cell
        checked += 1
        if resp < args.min_resp:
            # A featureless / missing / unrendered object correlates to (0,0) with a dead peak. Trust
            # the peak before the offset.
            low_conf += 1
            status = "LOW-CONFIDENCE"
        elif err_cells > args.budget_cells:
            drifted += 1
            status = "DRIFTED"
        else:
            status = "OK"
        print(f"  {kind}#{i} @({box['center'][0]:g},{box['center'][2]:g}) "
              f"dx={dx} dy={dy} resp={resp:.3f} err_cells={err_cells:.2f} -> {status}")

    failures = drifted + low_conf
    kinds_note = (", ".join(f"{k}x{n}" for k, n in sorted(skipped_kinds.items()))
                  if skipped_kinds else "none")
    if failures == 0:
        verdict = "PER-OBJECT-ALIGNED"
    else:
        verdict = f"OBJECT-GATE-FAIL ({drifted} drifted, {low_conf} low-confidence of {checked} checked)"
    print(f"{verdict} budget={args.budget_cells} cells, min_resp={args.min_resp}, "
          f"px/cell={px_per_cell:.1f}, checked={checked}, "
          f"skipped_kinds={sum(skipped_kinds.values())} [{kinds_note}], skipped_too_small={skipped_small}")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
