#!/usr/bin/env python3
"""mask_plate_extent.py — the EXTENT-CONTRACT hotfix (#1543 / M-ALIGN).

An existing plate that OUT-PAINTED its grid (the tavern painted a room LARGER than its authored 12x10
diamond — unreachable painted floor, invisible walls at the grid edges; owner playtest #8) can't be
un-painted, but it CAN be masked: feather everything OUTSIDE the grid diamond + perimeter wall band to a
dark vignette, so the room fades to darkness instead of showing unreachable painted floor. This is the
stop-gap while the RECIPE half (greybox_render_headless camera-fit + the authored wall band) regenerates
the room correctly by construction.

The "keep" region is the room's own screen footprint, reprojected from the plate's geometry through the
SAME contract camera the plate was registered under (greybox_render_headless.world_to_screen /
cell_to_world — honouring the geometry's opt-in camera_fit flag, so a camera-fit plate masks under its
fitted ortho and a legacy fixed-rig plate masks under ortho=13). Everything outside it feathers to a dark
vignette. Deterministic, PIL-only, read-only w.r.t. the plate (writes a NEW file; the engine is unaffected).

  python3 tools/mask_plate_extent.py <plate.png> <geometry.json> -o <masked.png> [--feather 40]
                                     [--wall-height 9] [--before-after <side_by_side.png>] [--overlay]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR.parent / "qa"))

import greybox_render_headless as g  # noqa: E402

_DARK = (6, 5, 9)  # the vignette colour the out-painted extent fades to


def _box_hull(cx: float, cz: float, half_x: float, half_z: float, height: float,
              ortho: float) -> list:
    """Screen-space convex-ish envelope of a world box (its 8 corners projected) — good enough to fill
    as part of the keep-mask; feathering softens the edge either way."""
    corners = []
    for sx in (cx - half_x, cx + half_x):
        for sz in (cz - half_z, cz + half_z):
            for sy in (0.0, height):
                corners.append(g.world_to_screen(sx, sy, sz, ortho))
    # order the hull by angle around the centroid so ImageDraw.polygon fills it convex
    mx = sum(p[0] for p in corners) / len(corners)
    my = sum(p[1] for p in corners) / len(corners)
    import math
    corners.sort(key=lambda p: math.atan2(p[1] - my, p[0] - mx))
    return corners


def build_keep_mask(geo: dict, size: tuple, wall_height: float = 9.0) -> "Image.Image":
    """An L-mode mask: 255 over the grid diamond + wall band + prop volumes, 0 elsewhere (pre-feather)."""
    cols, rows = int(geo["cols"]), int(geo["rows"])
    ortho = g._fit_ortho_size(cols, rows) if geo.get("camera_fit") else g.ORTHO_SIZE
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)

    # 1) the floor diamond (the playable grid extent)
    half_x = (cols / 2.0) * 2.0
    half_z = (rows / 2.0) * 2.0
    floor = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z),
             (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    draw.polygon([g.world_to_screen(*p, ortho) for p in floor], fill=255)

    # 2) the perimeter wall band + prop volumes rising up-screen (so real walls aren't vignetted). Use
    #    the same box extents render() draws, per cell for walls, per bbox for props.
    prop_cells = {(int(c), int(r)) for pr in geo.get("props", []) for (c, r) in pr.get("cells", [])}
    for (c, r) in geo.get("walls", []):
        if (int(c), int(r)) in prop_cells:
            continue
        wx, _, wz = g.cell_to_world(c, r, cols, rows)
        draw.polygon(_box_hull(wx, wz, 1.0, 1.0, wall_height, ortho), fill=255)
    for pr in geo.get("props", []):
        cells = pr.get("cells", [])
        if not cells:
            continue
        xs = [g.cell_to_world(c, r, cols, rows)[0] for (c, r) in cells]
        zs = [g.cell_to_world(c, r, cols, rows)[2] for (c, r) in cells]
        cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
        kind = pr.get("kind", "prop")
        if g._is_wall_run_kind(kind):
            hx = (max(xs) - min(xs)) / 2.0 + 1.0
            hz = (max(zs) - min(zs)) / 2.0 + 1.0
            height = wall_height
        else:
            height, half, _ = g._spec_for_kind(kind)
            hx = max(half, (max(xs) - min(xs)) / 2.0 + half)
            hz = max(half, (max(zs) - min(zs)) / 2.0 + half)
        draw.polygon(_box_hull(cx, cz, hx, hz, height, ortho), fill=255)
    return mask


def mask_plate(plate_path: str, geo: dict, out_path: str, *, feather: int = 40,
               wall_height: float = 9.0, dark=_DARK) -> "Image.Image":
    plate = Image.open(plate_path).convert("RGB")
    mask = build_keep_mask(geo, plate.size, wall_height=wall_height)
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    dark = Image.new("RGB", plate.size, dark)
    out = Image.composite(plate, dark, mask)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    return out


def _side_by_side(before_path: str, after: "Image.Image", out_path: str) -> None:
    before = Image.open(before_path).convert("RGB")
    w, h = before.size
    canvas = Image.new("RGB", (w * 2 + 12, h), (0, 0, 0))
    canvas.paste(before, (0, 0))
    canvas.paste(after, (w + 12, 0))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plate")
    ap.add_argument("geometry_json")
    ap.add_argument("-o", "--out", required=True, help="masked plate output PNG")
    ap.add_argument("--feather", type=int, default=40, help="vignette feather radius in px (default 40)")
    ap.add_argument("--wall-height", type=float, default=9.0,
                    help="wall band height for the keep-mask envelope (default 9; use the plate's own)")
    ap.add_argument("--before-after", default=None, help="also write a before|after side-by-side PNG")
    args = ap.parse_args(argv)
    geo = json.loads(Path(args.geometry_json).read_text())
    after = mask_plate(args.plate, geo, args.out, feather=args.feather, wall_height=args.wall_height)
    print(f"[mask_plate_extent] masked {args.plate} -> {args.out} "
          f"({geo['cols']}x{geo['rows']}, feather={args.feather}, "
          f"camera_fit={bool(geo.get('camera_fit'))})")
    if args.before_after:
        _side_by_side(args.plate, after, args.before_after)
        print(f"[mask_plate_extent] before/after -> {args.before_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
