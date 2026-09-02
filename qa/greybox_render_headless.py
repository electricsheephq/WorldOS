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

  python3 qa/greybox_render_headless.py <room_geometry.json> <out.png> [--wall-height 9] [--camera-fit]

CAMERA-FIT mode (the EXTENT CONTRACT, #1543 / M-ALIGN): the fixed ortho=13 rig leaves canvas margins
around a small room, and the style pass happily out-paints those margins into "more room" (the tavern
out-painted a room LARGER than its 12x10 grid — unreachable painted floor, invisible walls at the grid
edges). `--camera-fit` (or a geometry field `"camera_fit": true`) instead computes the ortho SCALE from
the room's own grid extent so the grid diamond + perimeter wall band fills the frame edge-to-edge — no
margin left to out-paint. It is STRICTLY OPT-IN: the projection basis (pitch/yaw/right/up, cell_to_world)
is byte-identical; only the ortho scale changes, and only when opted in. Existing plates were rendered
under the fixed rig and the registration/coherence instruments (check_grid_paint_coherence.py,
check_plate_drift.py, journey_visual_sweep.py) project with that SAME fixed rig — so a room WITHOUT
camera_fit renders exactly as before (world_to_screen/cell_to_world default to ORTHO_SIZE), leaving
those instruments' math untouched.

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
# camera-fit: fraction of the half-frame the grid diamond fills on its binding axis. <1.0 leaves a
# thin safety margin so the diamond corners don't touch the exact pixel edge; 0.96 => ~96% width fill
# on a wide room like the tavern, comfortably past the #1543 >=90%-of-frame-width extent contract.
CAMERA_FIT_FILL = 0.96


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


def world_to_screen(wx: float, wy: float, wz: float, ortho_size: float = ORTHO_SIZE) -> tuple:
    # `ortho_size` defaults to the fixed contract ORTHO_SIZE (13) — every existing caller (the
    # coherence/drift/journey instruments) omits it and gets the byte-identical fixed rig. Only the
    # opt-in camera-fit render passes a fitted ortho_size; nothing else about the projection changes.
    dx, dy, dz = wx - _POS[0], wy - _POS[1], wz - _POS[2]
    cam_r = dx * _RIGHT[0] + dy * _RIGHT[1] + dz * _RIGHT[2]
    cam_u = dx * _UP[0] + dy * _UP[1] + dz * _UP[2]
    half_h = ortho_size
    half_w = ortho_size * ASPECT
    sx = (cam_r / half_w) * (PX_W / 2.0) + PX_W / 2.0
    sy = PX_H / 2.0 - (cam_u / half_h) * (PX_H / 2.0)
    return sx, sy


def cell_to_world(c: float, r: float, cols: int, rows: int) -> tuple:
    cx0, cy0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    return ((c - cx0) * 2.0, 0.0, (cy0 - r) * 2.0)


def _camera_ru(wx: float, wy: float, wz: float) -> tuple:
    """(cam_r, cam_u) — a world point's coordinates on the camera right/up axes, independent of the
    ortho scale (the _POS offset cancels: _RIGHT and _UP are perpendicular to the view forward). This
    is what world_to_screen divides by half_w/half_h, so it's the exact basis for fitting the ortho."""
    return (wx * _RIGHT[0] + wy * _RIGHT[1] + wz * _RIGHT[2],
            wx * _UP[0] + wy * _UP[1] + wz * _UP[2])


def _fit_ortho_size(cols: int, rows: int, fill: float = CAMERA_FIT_FILL) -> float:
    """Ortho scale that makes the grid diamond (the floor quad = the perimeter wall band's footprint)
    fill the frame on its binding axis. Fit on the y=0 grid corners only — walls rise up-screen from
    here and may extend past the top edge, which is the DESIRED "room fills the frame" look (there is
    no margin left to out-paint), while the playable floor stays fully framed. Width binds for a room
    at least as wide as the frame aspect (e.g. the tavern), so width fill == `fill`."""
    half_x = (cols / 2.0) * 2.0
    half_z = (rows / 2.0) * 2.0
    corners = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z),
               (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    rus = [_camera_ru(*c) for c in corners]
    max_r = max(abs(r) for (r, _) in rus)
    max_u = max(abs(u) for (_, u) in rus)
    return max(max_r / ASPECT, max_u) / fill


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


# The explicit perimeter-wall-band kinds (#1543): a continuous wall RUN authored as ONE prop box per
# edge run (never per-cell — the #1539 crenellation rule). EXACT-match kinds so the pre-existing
# "stone_wall" prop (camp) is untouched and keeps its _DEFAULT_SPEC square-box rendering byte-identical.
_WALL_RUN_KINDS = frozenset({"wall_run", "perimeter_wall"})
_WALL_RUN_COLOR = (110, 108, 104)  # same grey as the per-cell wall boxes below


def _is_wall_run_kind(kind: str) -> bool:
    return (kind or "").lower() in _WALL_RUN_KINDS


def _shade(color: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, int(ch * factor))) for ch in color)


def _draw_box(draw: "ImageDraw.ImageDraw", cx: float, cz: float, half_x: float, half_z: float,
              height: float, cols: int, rows: int, color: tuple,
              ortho_size: float = ORTHO_SIZE) -> None:
    """Draw a box centered at world (cx, 0, cz) with the given x/z half-extents and height, as a top
    face + two visible side faces (simple flat-shaded painter's-algorithm box). Separate half_x/half_z
    let a wall RUN draw as one continuous thin box; a square prop passes half_x == half_z (identical to
    the prior single-`half` behaviour)."""
    corners_bottom = [
        (cx - half_x, 0.0, cz - half_z), (cx + half_x, 0.0, cz - half_z),
        (cx + half_x, 0.0, cz + half_z), (cx - half_x, 0.0, cz + half_z),
    ]
    corners_top = [(x, height, z) for (x, _, z) in corners_bottom]
    sb = [world_to_screen(*p, ortho_size) for p in corners_bottom]
    st = [world_to_screen(*p, ortho_size) for p in corners_top]
    # Right face (bottom[1],bottom[2],top[2],top[1]) and front face (bottom[2],bottom[3],top[3],top[2])
    # are the two faces toward the -x,-z camera corner that are actually visible.
    draw.polygon([sb[1], sb[2], st[2], st[1]], fill=_shade(color, 0.72))
    draw.polygon([sb[2], sb[3], st[3], st[2]], fill=_shade(color, 0.58))
    draw.polygon(st, fill=_shade(color, 1.0))


def render(geo: dict, out_path: str, wall_height: float = 9.0, camera_fit=None) -> None:
    cols, rows = int(geo["cols"]), int(geo["rows"])
    # camera_fit: explicit arg wins; else the geometry's opt-in field. STRICTLY opt-in — an absent
    # field renders under the fixed ORTHO_SIZE, byte-identical to before (the instruments' rig).
    if camera_fit is None:
        camera_fit = bool(geo.get("camera_fit", False))
    ortho = _fit_ortho_size(cols, rows) if camera_fit else ORTHO_SIZE
    im = Image.new("RGB", (PX_W, PX_H), (13, 13, 18))
    draw = ImageDraw.Draw(im)

    # Floor: one big flat quad spanning the whole grid extent (a single flat plate reads fine at
    # low img2img strength — per-cell grout lines are a texture-pass nicety build_room_greybox.cs
    # adds for interiors; skipped here since both new rooms are outdoor/open-plaza).
    half_x = (cols / 2.0) * 2.0
    half_z = (rows / 2.0) * 2.0
    floor_quad = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z), (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    draw.polygon([world_to_screen(*p, ortho) for p in floor_quad], fill=(58, 58, 62))

    # Depth-sort draw items far-to-near: row 0 (largest world z) is FARTHEST from the -x,-z camera
    # corner (build_room_greybox.cs convention); draw ascending r so nearer rows paint over farther ones.
    # NOTE: export_scene_grid.py's "walls" field is actually "every non-walkable cell" (type=="wall"
    # OR not walkable) — it CONFLATES true wall cells with prop footprint cells. Exclude any cell a
    # prop already covers so we don't double-draw a prop as a generic grey wall box underneath it.
    prop_cells = {(int(c), int(r)) for prop in geo.get("props", []) for (c, r) in prop.get("cells", [])}
    items = []  # (r, cx, cz, half_x, half_z, height, color)
    for (c, r) in geo.get("walls", []):
        if (int(c), int(r)) in prop_cells:
            continue
        wc = cell_to_world(c, r, cols, rows)
        items.append((r, wc[0], wc[2], 1.0, 1.0, wall_height, (110, 108, 104)))
    for prop in geo.get("props", []):
        kind = prop.get("kind", "prop")
        pcells = prop.get("cells", [])
        if not pcells:
            continue
        xs = [cell_to_world(c, r, cols, rows)[0] for (c, r) in pcells]
        zs = [cell_to_world(c, r, cols, rows)[2] for (c, r) in pcells]
        cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
        r_avg = sum(r for (_, r) in pcells) / len(pcells)
        if _is_wall_run_kind(kind):
            # A continuous perimeter wall RUN: one THIN rectangular box spanning the run at wall_height
            # (#1543 extent contract / #1539 crenellation rule — never a stack of per-cell boxes).
            base = 1.0
            hx = (max(xs) - min(xs)) / 2.0 + base
            hz = (max(zs) - min(zs)) / 2.0 + base
            items.append((r_avg, cx, cz, hx, hz, wall_height, _WALL_RUN_COLOR))
        else:
            height, half, color = _spec_for_kind(kind)
            half_x2 = max(half, (max(xs) - min(xs)) / 2.0 + half)
            half_z2 = max(half, (max(zs) - min(zs)) / 2.0 + half)
            sq = max(half_x2, half_z2)  # square box — preserves the prior single-`half` prop rendering
            items.append((r_avg, cx, cz, sq, sq, height, color))

    for (_, cx, cz, hx, hz, height, color) in sorted(items, key=lambda it: it[0]):
        _draw_box(draw, cx, cz, hx, hz, height, cols, rows, color, ortho)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    print(f"[greybox_render_headless] {cols}x{rows}"
          f"{' camera-fit ortho=%.2f' % ortho if camera_fit else ''}: "
          f"{len(geo.get('walls', []))} wall cells, "
          f"{len(geo.get('props', []))} props -> {out_path}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("geometry_json")
    ap.add_argument("out_png")
    ap.add_argument("--wall-height", type=float, default=9.0)
    ap.add_argument("--camera-fit", action="store_true",
                    help="fit the ortho scale so the grid diamond fills the frame (the #1543 extent "
                         "contract). Opt-in; overrides the geometry's camera_fit field when set.")
    args = ap.parse_args(argv)
    geo = json.loads(Path(args.geometry_json).read_text())
    render(geo, args.out_png, wall_height=args.wall_height,
           camera_fit=True if args.camera_fit else None)


if __name__ == "__main__":
    main()
