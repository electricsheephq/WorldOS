#!/usr/bin/env python3
"""Project a room_boxes.json sidecar onto a plate/greybox image — the alignment eyeball + solver.

Two verification instruments of the unified room pipeline (UNIFY-THE-FRAMES, PR #1575), ported
from the 2026-07-15 crypt v3.5 session tooling:

1. WIREFRAME OVERLAY (--out): draws every non-floor box through the exact client camera rig
   (greybox_render_headless.world_to_screen at the sidecar's stamped ortho). The alignment
   authority is the EYEBALL on this overlay — note that floor apron-skirt boxes read as global
   misalignment if included (measured false alarm), so floor-kind boxes are skipped by default.

2. BLOB SOLVE (--solve): least-squares fit of the render's TRUE (ortho, screen-offset) from the
   brazier fire blobs vs projected bowl centers. This numerically confirms/refutes camera drift:
   crypt v3.5 measured 2/3 braziers at 3px (0.04 cells) and refuted a suspected cameraPin override
   (fitted ortho 11.705 == the 11.7851 stamp within blob bias).

Usage:
  python3 qa/overlay_boxes.py boxes.json plate.png --out overlay.png [--solve] [--include-floor]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import greybox_render_headless as G  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

_EDGES = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
          (0, 4), (1, 5), (2, 6), (3, 7)]


def _color(name: str, kind: str) -> tuple:
    if "pillar" in name or "column" in name:
        return (255, 80, 80)
    if any(k in name for k in ("arch", "door", "jamb", "lintel")) or kind == "door_frame":
        return (255, 255, 0)
    if kind == "wall_run":
        return (80, 200, 255)
    return (80, 255, 120)


def draw_overlay(boxes: dict, image: Path, out: Path, include_floor: bool) -> int:
    img = Image.open(image).convert("RGB")
    d = ImageDraw.Draw(img)
    ortho = boxes["ortho"]
    n = 0
    for b in boxes["boxes"]:
        if not include_floor and b.get("kind") == "floor":
            continue
        c, s = b["center"], b["size"]
        corners = [(x, y, z)
                   for x in (c[0] - s[0] / 2, c[0] + s[0] / 2)
                   for y in (c[1] - s[1] / 2, c[1] + s[1] / 2)
                   for z in (c[2] - s[2] / 2, c[2] + s[2] / 2)]
        pts = [G.world_to_screen(*p, ortho_size=ortho) for p in corners]
        col = _color(b.get("name", ""), b.get("kind", ""))
        for a, bb in _EDGES:
            d.line([pts[a], pts[bb]], fill=col, width=2)
        n += 1
    img.save(out)
    return n


def blob_solve(boxes: dict, image: Path) -> dict:
    """Fit (ortho, dx, dy) from warm fire blobs vs projected brazier bowls; report per-bowl error."""
    import numpy as np

    img = np.array(Image.open(image).convert("RGB"), dtype=float)
    mask = (img[:, :, 0] > 190) & (img[:, :, 1] > 110) & (img[:, :, 2] < 120)
    ys, xs = np.nonzero(mask)
    clusters: list[dict] = []
    for x, y in zip(xs, ys):
        for c in clusters:
            if abs(x - c["x"] / c["n"]) < 35 and abs(y - c["y"] / c["n"]) < 35:
                c["x"] += x; c["y"] += y; c["n"] += 1
                break
        else:
            clusters.append({"x": float(x), "y": float(y), "n": 1})
    blobs = [(c["x"] / c["n"], c["y"] / c["n"]) for c in clusters if c["n"] > 60]

    braz = [b for b in boxes["boxes"] if b.get("kind") == "brazier"]
    groups: dict = {}
    for b in braz:
        groups.setdefault((round(b["center"][0]), round(b["center"][2])), []).append(b)
    bowls = [max(g, key=lambda b: b["center"][1]) for g in groups.values()]
    if not blobs or not bowls:
        return {"error": f"insufficient signal (blobs={len(blobs)}, bowls={len(bowls)})"}

    W, H, A = 1344, 768, 1344 / 768
    best = None
    for o in np.arange(max(4.0, boxes["ortho"] - 3), boxes["ortho"] + 3, 0.005):
        proj = []
        for b in bowls:
            r, u = G._camera_ru(*b["center"])
            proj.append((r / (o * A) * (W / 2) + W / 2, H / 2 - u / o * (H / 2)))
        pairs = []
        for bx, by in blobs:
            _, pi = min(((bx - px) ** 2 + (by - py) ** 2, i) for i, (px, py) in enumerate(proj))
            pairs.append(((bx, by), proj[pi]))
        dx = float(np.mean([b[0] - p[0] for b, p in pairs]))
        dy = float(np.mean([b[1] - p[1] for b, p in pairs]))
        res = float(np.mean([np.hypot(b[0] - p[0] - dx, b[1] - p[1] - dy) for b, p in pairs]))
        if best is None or res < best["residual_px"]:
            best = {"residual_px": round(res, 1), "fitted_ortho": round(float(o), 4),
                    "screen_offset": [round(dx, 1), round(dy, 1)]}
    best["stamped_ortho"] = boxes["ortho"]
    best["n_blobs"] = len(blobs)
    best["n_bowls"] = len(bowls)
    # per-bowl nearest-blob error at the STAMPED ortho (the shipping question)
    per = []
    for b in bowls:
        x, y, z = b["center"]
        sx, sy = G.world_to_screen(x, y + 0.5, z, ortho_size=boxes["ortho"])
        dmin = min(float(((bx - sx) ** 2 + (by - sy) ** 2) ** 0.5) for bx, by in blobs)
        cells = dmin / (768 / (boxes["ortho"] * 2)) / 2.0
        per.append({"bowl_world": [round(x, 1), round(z, 1)], "err_px": round(dmin), "err_cells": round(cells, 2)})
    best["per_bowl_at_stamp"] = per
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("boxes", help="room_boxes.json sidecar (build_room_unified.cs output)")
    ap.add_argument("image", help="plate / styled candidate / greybox PNG (1344x768)")
    ap.add_argument("--out", help="write the wireframe overlay PNG here")
    ap.add_argument("--solve", action="store_true", help="run the brazier blob solve and print JSON")
    ap.add_argument("--include-floor", action="store_true",
                    help="draw floor-kind boxes too (WARNING: apron skirts read as false misalignment)")
    args = ap.parse_args()

    boxes = json.loads(Path(args.boxes).read_text())
    if args.out:
        n = draw_overlay(boxes, Path(args.image), Path(args.out), args.include_floor)
        print(f"overlay: {n} boxes at ortho {boxes['ortho']} -> {args.out}")
    if args.solve:
        print(json.dumps(blob_solve(boxes, Path(args.image)), indent=1))
    if not args.out and not args.solve:
        ap.error("nothing to do: pass --out and/or --solve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
