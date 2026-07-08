#!/usr/bin/env python3
"""greybox_render_headless.py — a HEADLESS (no Unity/no box) equivalent of
extensions/renderers/unity/scripts/build_room_greybox.cs, for API-side room generation when the
GEX44 box's live Unity editor is not needed/available (backdrop-cadence restart, HV5/#1386).

Reads the SAME room_geometry.json export_scene_grid.py produces ({cols, rows, walls, props,
door_cells, ...}) and draws a simple flat-shaded dimetric greybox PNG at the SAME contract camera
build_room_greybox.cs uses (orthoSize=13, pitch=30deg/yaw=45deg, pulled back 80 world units,
1344x768 capture) — the exact world_to_screen basis is the one qa/visual_pregate.py::CameraSpec
derives and has verified <1e-3 against Unity's own Quaternion.Euler(30,45,0) transform, just
re-parented here at build_room_greybox.cs's own ortho_size/resolution (13 / 1344x768, not
visual_pregate's 18 / 1920x1097 — a different capture stage/zoom, same camera rig).

This is a STRUCTURAL control image (composition-pinning for img2img), not a lit/textured render —
it does not attempt build_room_greybox.cs's procedural stone texture or deferred lighting; a flat
per-face shade is enough for low-strength (0.5-0.6) img2img to hold the layout while the LoRA paints
the material. Same cell->world contract as build_room_greybox.cs: cx0=(cols-1)/2, cy0=(rows-1)/2,
isotropic cell 2.0 world units, cellToWorld(c,r) = ((c-cx0)*2, 0, (cy0-r)*2).

  python3 qa/greybox_render_headless.py <room_geometry.json> <out.png> [--wall-height 9] [--cutnear]

Engine = SOLE WRITER of the scene_grid; this tool is read-only view-layer rendering, never mutates
engine state.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

CAM_DIST = 80.0
PX_W, PX_H = 1344, 768
ORTHO_SIZE = 13.0
ASPECT = PX_W / PX_H
PITCH_DEG = 30.0
YAW_DEG = 45.0


def _forward() -> tuple:
    p, y = math.radians(PITCH_DEG), math.radians(YAW_DEG)
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


_FWD = _forward()
_RIGHT = (math.cos(math.radians(YAW_DEG)), 0.0, -math.sin(math.radians(YAW_DEG)))
_UP = (
    _FWD[1] * _RIGHT[2] - _FWD[2] * _RIGHT[1],
    _FWD[2] * _RIGHT[0] - _FWD[0] * _RIGHT[2],
    _FWD[0] * _RIGHT[1] - _FWD[1] * _RIGHT[0],
)
_POS = tuple(-_FWD[i] * CAM_DIST for i in range(3))


def world_to_screen(wx: float, wy: float, wz: float) -> tuple:
    dx, dy, dz = wx - _POS[0], wy - _POS[1], wz - _POS[2]
    cam_r = dx * _RIGHT[0] + dy * _RIGHT[1] + dz * _RIGHT[2]
    cam_u = dx * _UP[0] + dy * _UP[1] + dz * _UP[2]
    half_h = ORTHO_SIZE
    half_w = ORTHO_SIZE * ASPECT
    sx = (cam_r / half_w) * (PX_W / 2.0) + PX_W / 2.0
    sy = PX_H / 2.0 - (cam_u / half_h) * (PX_H / 2.0)
    return sx, sy


def cell_to_world(c: float, r: float, cols: int, rows: int) -> tuple:
    cx0, cy0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    return ((c - cx0) * 2.0, 0.0, (cy0 - r) * 2.0)


# kind -> (height, half-width, RGB) — mirrors build_room_greybox.cs's kind heuristic, extended with
# the outdoor camp/market kinds this batch introduces.
_KIND_SPECS = [
    (("pillar", "column"), (7.5, 0.8, (143, 140, 135))),
    (("large_tree",), (9.0, 0.9, (58, 74, 52))),
    (("stone_well",), (3.2, 0.9, (150, 148, 140))),
    (("sarcophagus", "altar", "bar", "table", "pew", "market_stall"), (2.0, 0.9, (153, 148, 140))),
    (("brazier",), (2.2, 0.4, (96, 92, 86))),
    (("campfire",), (0.6, 0.55, (200, 110, 40))),
    (("bedroll",), (0.28, 0.55, (110, 96, 78))),
    (("fallen_log",), (0.8, 0.55, (90, 74, 54))),
    (("boulder",), (2.0, 0.7, (110, 112, 108))),
    (("supply_crates", "cart", "merchants_cart"), (1.5, 0.7, (115, 110, 96))),
    (("rubble", "barrel", "crate"), (1.4, 0.75, (115, 110, 102))),
]
_DEFAULT_SPEC = (2.6, 0.7, (133, 128, 122))


def _spec_for_kind(kind: str) -> tuple:
    k = (kind or "").lower()
    for keys, spec in _KIND_SPECS:
        if any(key in k for key in keys):
            return spec
    return _DEFAULT_SPEC


def _shade(color: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, int(ch * factor))) for ch in color)


def _draw_box(draw: "ImageDraw.ImageDraw", cx: float, cz: float, half: float, height: float,
              cols: int, rows: int, color: tuple) -> None:
    """Draw a box centered at world (cx, 0, cz) with the given half-width and height, as a top
    face + two visible side faces (simple flat-shaded painter's-algorithm box)."""
    corners_bottom = [
        (cx - half, 0.0, cz - half), (cx + half, 0.0, cz - half),
        (cx + half, 0.0, cz + half), (cx - half, 0.0, cz + half),
    ]
    corners_top = [(x, height, z) for (x, _, z) in corners_bottom]
    sb = [world_to_screen(*p) for p in corners_bottom]
    st = [world_to_screen(*p) for p in corners_top]
    # Right face (bottom[1],bottom[2],top[2],top[1]) and front face (bottom[2],bottom[3],top[3],top[2])
    # are the two faces toward the -x,-z camera corner that are actually visible.
    draw.polygon([sb[1], sb[2], st[2], st[1]], fill=_shade(color, 0.72))
    draw.polygon([sb[2], sb[3], st[3], st[2]], fill=_shade(color, 0.58))
    draw.polygon(st, fill=_shade(color, 1.0))


def render(geo: dict, out_path: str, wall_height: float = 9.0) -> None:
    cols, rows = int(geo["cols"]), int(geo["rows"])
    im = Image.new("RGB", (PX_W, PX_H), (13, 13, 18))
    draw = ImageDraw.Draw(im)

    # Floor: one big flat quad spanning the whole grid extent (a single flat plate reads fine at
    # low img2img strength — per-cell grout lines are a texture-pass nicety build_room_greybox.cs
    # adds for interiors; skipped here since both new rooms are outdoor/open-plaza).
    half_x = (cols / 2.0) * 2.0
    half_z = (rows / 2.0) * 2.0
    floor_quad = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z), (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    draw.polygon([world_to_screen(*p) for p in floor_quad], fill=(58, 58, 62))

    # Depth-sort draw items far-to-near: row 0 (largest world z) is FARTHEST from the -x,-z camera
    # corner (build_room_greybox.cs convention); draw ascending r so nearer rows paint over farther ones.
    # NOTE: export_scene_grid.py's "walls" field is actually "every non-walkable cell" (type=="wall"
    # OR not walkable) — it CONFLATES true wall cells with prop footprint cells. Exclude any cell a
    # prop already covers so we don't double-draw a prop as a generic grey wall box underneath it.
    prop_cells = {(int(c), int(r)) for prop in geo.get("props", []) for (c, r) in prop.get("cells", [])}
    items = []  # (r, cx, cz, half, height, color)
    for (c, r) in geo.get("walls", []):
        if (int(c), int(r)) in prop_cells:
            continue
        wc = cell_to_world(c, r, cols, rows)
        items.append((r, wc[0], wc[2], 1.0, wall_height, (110, 108, 104)))
    for prop in geo.get("props", []):
        kind = prop.get("kind", "prop")
        height, half, color = _spec_for_kind(kind)
        pcells = prop.get("cells", [])
        if not pcells:
            continue
        xs = [cell_to_world(c, r, cols, rows)[0] for (c, r) in pcells]
        zs = [cell_to_world(c, r, cols, rows)[2] for (c, r) in pcells]
        cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
        half_x2 = max(half, (max(xs) - min(xs)) / 2.0 + half)
        half_z2 = max(half, (max(zs) - min(zs)) / 2.0 + half)
        r_avg = sum(r for (_, r) in pcells) / len(pcells)
        items.append((r_avg, cx, cz, max(half_x2, half_z2), height, color))

    for (_, cx, cz, half, height, color) in sorted(items, key=lambda it: it[0]):
        _draw_box(draw, cx, cz, half, height, cols, rows, color)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    print(f"[greybox_render_headless] {cols}x{rows}: {len(geo.get('walls', []))} wall cells, "
          f"{len(geo.get('props', []))} props -> {out_path}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("geometry_json")
    ap.add_argument("out_png")
    ap.add_argument("--wall-height", type=float, default=9.0)
    args = ap.parse_args(argv)
    geo = json.loads(Path(args.geometry_json).read_text())
    render(geo, args.out_png, wall_height=args.wall_height)


if __name__ == "__main__":
    main()
