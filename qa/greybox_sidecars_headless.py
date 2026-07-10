#!/usr/bin/env python3
"""greybox_sidecars_headless.py — headless DEPTH + NORMAL sidecars for a room greybox.

Emits room_greybox_depth.png (camera-space depth, near=bright) and room_greybox_normal.png (world-
space per-face normals encoded as RGB) from the SAME room_geometry.json + SAME dimetric camera basis
that qa/greybox_render_headless.py uses (imported directly, so the sidecars register pixel-for-pixel
with the greybox). No Unity / no GEX44 box — a pure-PIL analog of the box CohesionProbe.cs G-buffer.

  python3 qa/greybox_sidecars_headless.py <room_geometry.json> <out_depth.png> <out_normal.png> [--wall-height 9]

★ SCOPE (a PLATE SPRINT finding, not a live dependency): the ARM-B WINNING recipe (flux ControlNet
depth base -> flat Gemini style pass) does NOT consume these sidecars — Scenario derives the depth
control server-side from the greybox controlImage, and issue #1481 concluded the WOSRelight lane that
DID consume depth/normal sidecars should STOP (shared-greybox sidecars stamped vertical-banding seams
onto warm plates; only a per-plate sidecar would be safe). These are produced for parity with the
crypt relight-lane artifact + as a reproducible API-side (no-box) sidecar path for any future relight.

Engine = SOLE WRITER; this is read-only view-layer rendering.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Reuse the EXACT camera + cell math from the greybox renderer so the sidecars co-register.
from greybox_render_headless import (  # noqa: E402
    PX_W, PX_H, _FWD, _POS, _KIND_SPECS, _DEFAULT_SPEC, _spec_for_kind,
    cell_to_world, world_to_screen,
)


def _cam_depth(wx: float, wy: float, wz: float) -> float:
    """Signed distance along the camera forward axis (larger = farther from the camera)."""
    dx, dy, dz = wx - _POS[0], wy - _POS[1], wz - _POS[2]
    return dx * _FWD[0] + dy * _FWD[1] + dz * _FWD[2]


def _encode_normal(n: tuple) -> tuple:
    return tuple(int(round((c * 0.5 + 0.5) * 255)) for c in n)


def _collect_boxes(geo: dict, wall_height: float) -> list:
    """Same item set greybox_render_headless builds: (r_sort, cx, cz, half, height)."""
    cols, rows = int(geo["cols"]), int(geo["rows"])
    prop_cells = {(int(c), int(r)) for prop in geo.get("props", []) for (c, r) in prop.get("cells", [])}
    items: list = []
    for (c, r) in geo.get("walls", []):
        if (int(c), int(r)) in prop_cells:
            continue
        wc = cell_to_world(c, r, cols, rows)
        items.append((r, wc[0], wc[2], 1.0, wall_height))
    for prop in geo.get("props", []):
        height, half, _ = _spec_for_kind(prop.get("kind", "prop"))
        pcells = prop.get("cells", [])
        if not pcells:
            continue
        xs = [cell_to_world(c, r, cols, rows)[0] for (c, r) in pcells]
        zs = [cell_to_world(c, r, cols, rows)[2] for (c, r) in pcells]
        cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
        half_x = max(half, (max(xs) - min(xs)) / 2.0 + half)
        half_z = max(half, (max(zs) - min(zs)) / 2.0 + half)
        r_avg = sum(r for (_, r) in pcells) / len(pcells)
        items.append((r_avg, cx, cz, max(half_x, half_z), height))
    return sorted(items, key=lambda it: it[0])


def render(geo: dict, out_depth: str, out_normal: str, wall_height: float = 9.0) -> None:
    cols, rows = int(geo["cols"]), int(geo["rows"])
    depth_im = Image.new("L", (PX_W, PX_H), 0)          # background = farthest (black)
    normal_im = Image.new("RGB", (PX_W, PX_H), _encode_normal((0.0, 0.0, -1.0)))  # bg faces camera
    d_draw, n_draw = ImageDraw.Draw(depth_im), ImageDraw.Draw(normal_im)

    # Depth normalisation range from the grid extent (near corner -> far corner along camera forward).
    half_x = (cols / 2.0) * 2.0
    half_z = (rows / 2.0) * 2.0
    corners = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z),
               (half_x, wall_height, half_z), (-half_x, wall_height, half_z)]
    depths = [_cam_depth(*c) for c in corners]
    d_near, d_far = min(depths), max(depths)
    span = (d_far - d_near) or 1.0

    def _d255(wx: float, wy: float, wz: float) -> int:
        # near -> 255 (bright), far -> 0 (dark); standard ControlNet depth convention.
        t = (_cam_depth(wx, wy, wz) - d_near) / span
        return max(0, min(255, int(round((1.0 - t) * 255))))

    # Floor quad (world normal +Y).
    floor = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z), (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    d_draw.polygon([world_to_screen(*p) for p in floor],
                   fill=int(round(sum(_d255(*p) for p in floor) / 4)))
    n_draw.polygon([world_to_screen(*p) for p in floor], fill=_encode_normal((0.0, 1.0, 0.0)))

    # Boxes far-to-near (painter's algorithm), each face filled with its depth / world-normal.
    for (_, cx, cz, half, height) in _collect_boxes(geo, wall_height):
        cb = [(cx - half, 0.0, cz - half), (cx + half, 0.0, cz - half),
              (cx + half, 0.0, cz + half), (cx - half, 0.0, cz + half)]
        ct = [(x, height, z) for (x, _, z) in cb]
        sb = [world_to_screen(*p) for p in cb]
        st = [world_to_screen(*p) for p in ct]
        # Right face (+X), front face (+Z), top face (+Y) — the three visible toward the -x,-z camera.
        faces = [
            ([cb[1], cb[2], ct[2], ct[1]], [sb[1], sb[2], st[2], st[1]], (1.0, 0.0, 0.0)),
            ([cb[2], cb[3], ct[3], ct[2]], [sb[2], sb[3], st[3], st[2]], (0.0, 0.0, 1.0)),
            (ct, st, (0.0, 1.0, 0.0)),
        ]
        for world_pts, screen_pts, normal in faces:
            d_draw.polygon(screen_pts, fill=int(round(sum(_d255(*p) for p in world_pts) / len(world_pts))))
            n_draw.polygon(screen_pts, fill=_encode_normal(normal))

    for out_path, im in ((out_depth, depth_im), (out_normal, normal_im)):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path)
    print(f"[greybox_sidecars_headless] {cols}x{rows} -> depth {out_depth} · normal {out_normal}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("geometry_json")
    ap.add_argument("out_depth")
    ap.add_argument("out_normal")
    ap.add_argument("--wall-height", type=float, default=9.0)
    args = ap.parse_args(argv)
    geo = json.loads(Path(args.geometry_json).read_text())
    render(geo, args.out_depth, args.out_normal, wall_height=args.wall_height)


if __name__ == "__main__":
    main()
