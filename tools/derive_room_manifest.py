#!/usr/bin/env python3
"""derive_room_manifest.py — DERIVE a room cells.json manifest from greybox geometry (single source).

Owner playtest #5 architecture decision: the greybox geometry is the single source of truth for a room's
FOOTPRINT + OCCLUSION + WALKABLE, and manifests must be DERIVED from it, not hand-authored — plates are
already registered to the greybox (>=0.95), so paint-vs-grid drift dies at the source. This tool turns a
`export_scene_grid.py` geometry JSON ({cols, rows, walls, props:[{kind, cells}], ...}) into the same
per-prop manifest qa/check_grid_paint_coherence.py + qa/check_plate_drift.py consume, computing each
prop's:
  * footprint — the impassable FLOOR cells the prop occupies (collision + the coherence gate check).
  * occlusion — the screen-space SILHOUETTE cells: every grid cell whose grounded (floor y=0) projection
                falls under the prop's projected BOX (the point-in-polygon method PR #1505 used for the
                crypt sarcophagus, generalised — a tall prop's silhouette rises UP-SCREEN off its floor
                footprint, so occlusion strictly contains but is offset from the footprint).
  * screen_bbox — the footprint reprojected under the contract camera (the coherence-gate search anchor).
and a room-level WALKABLE set (all in-bounds cells minus walls minus every prop footprint).

Reuses the ONE verified camera rig (greybox_render_headless — the #1396 recipe, <1e-3 vs Unity). The
manifest is stamped `derivation: "derived"` + its source geometry, distinguishing it from the interim
`measured` manifests (crypt/camp) that qa/build_room_manifest.py reconstructs from measured calibrations
until their geometry JSON exists.

  python3 tools/derive_room_manifest.py <geometry.json> [-o qa/room_manifests/<room>.cells.json] \
      [--room <name>] [--recipe-key <key>]

Deterministic, offline (numpy optional; pure-python fallback). Read-only w.r.t. engine state.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_QA_DIR = _TOOLS_DIR.parent / "qa"
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

from greybox_render_headless import (  # noqa: E402
    ORTHO_SIZE, _fit_ortho_size, cell_to_world, world_to_screen, _spec_for_kind,
)
from check_plate_drift import project_cell_bbox  # noqa: E402

_CAMERA = {
    "recipe": "greybox_render_headless (verified vs Unity Quaternion.Euler(30,45,0) <1e-3)",
    "ortho_size": 13.0, "pitch_deg": 30.0, "yaw_deg": 45.0,
    "px_w": 1344, "px_h": 768, "cell_world_units": 2.0, "cam_dist": 80.0,
}


# ── geometry: a prop's projected box silhouette (footprint extruded to the kind's height) ────────────
def _prop_box_corners(footprint: list, kind: str, cols: int, rows: int,
                      *, ortho: Optional[float] = None) -> list:
    """The 8 world corners of the prop's greybox box (floor y=0 -> y=height), reproducing
    greybox_render_headless's per-prop box (centre + padded half-extent + the kind's height). `ortho`
    None ⇒ the fixed contract rig; a camera_fit room passes its fitted ortho so the projected silhouette
    matches the plate it was painted at (M-ALIGN)."""
    o = ORTHO_SIZE if ortho is None else ortho
    height, half, _ = _spec_for_kind(kind)
    xs_w = [cell_to_world(c, r, cols, rows)[0] for (c, r) in footprint]
    zs_w = [cell_to_world(c, r, cols, rows)[2] for (c, r) in footprint]
    cx, cz = (min(xs_w) + max(xs_w)) / 2.0, (min(zs_w) + max(zs_w)) / 2.0
    half_x = max(half, (max(xs_w) - min(xs_w)) / 2.0 + half)
    half_z = max(half, (max(zs_w) - min(zs_w)) / 2.0 + half)
    hh = max(half_x, half_z)
    corners = []
    for (dx, dz) in ((-hh, -hh), (hh, -hh), (hh, hh), (-hh, hh)):
        for wy in (0.0, height):
            corners.append(world_to_screen(cx + dx, wy, cz + dz, o))
    return corners


def _convex_hull(points: list) -> list:
    """Andrew's monotone-chain convex hull (CCW). The prop box silhouette is convex under the ortho
    dimetric camera, so its hull is exactly the silhouette outline."""
    pts = sorted(set((round(x, 3), round(y, 3)) for (x, y) in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _point_in_convex(pt: tuple, hull: list) -> bool:
    """Inside-or-on a CCW convex polygon: every edge cross-product >= 0."""
    n = len(hull)
    if n < 3:
        return False
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        if (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax) < -1e-6:
            return False
    return True


def derive_occlusion_cells(footprint: list, kind: str, cols: int, rows: int,
                           *, ortho: Optional[float] = None) -> list:
    """Every grid cell whose grounded (floor-centre) projection falls under the prop's projected box
    silhouette — the point-in-polygon derivation (#1505, generalised). Always includes the footprint.
    `ortho` None ⇒ the fixed rig (byte-identical for every non-fit room); a camera_fit room passes its
    fitted ortho so both the prop's projected hull AND the grid cell-centre projections use the SAME
    scale the plate was painted at (M-ALIGN)."""
    o = ORTHO_SIZE if ortho is None else ortho
    hull = _convex_hull(_prop_box_corners(footprint, kind, cols, rows, ortho=o))
    occ = set(tuple(c) for c in footprint)
    for r in range(rows):
        for c in range(cols):
            sp = world_to_screen(*cell_to_world(c, r, cols, rows), o)
            if _point_in_convex(sp, hull):
                occ.add((c, r))
    return sorted([list(c) for c in occ], key=lambda cr: (cr[1], cr[0]))


def derive_walkable(cols: int, rows: int, walls: list, footprints: list,
                    cell_default_walkable: bool = True) -> list:
    """All in-bounds cells minus walls minus every prop footprint (the room's walkable set)."""
    blocked = {(int(c), int(r)) for (c, r) in walls}
    for fp in footprints:
        blocked |= {(int(c), int(r)) for (c, r) in fp}
    if not cell_default_walkable:
        return []
    return [[c, r] for r in range(rows) for c in range(cols) if (c, r) not in blocked]


# ── the derivation ──────────────────────────────────────────────────────────────────────────────────
_REPO_ROOT = _TOOLS_DIR.parent


def _repo_relative(path: Optional[str]) -> Optional[str]:
    """Store the source geometry path repo-root-relative so a derived manifest is reproducible
    regardless of where the repo is checked out."""
    if not path:
        return path
    p = Path(path)
    try:
        return str((p if p.is_absolute() else (Path.cwd() / p)).resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return path


def derive_manifest(geometry: dict, *, room: str, recipe_key: str,
                    source_geometry: Optional[str] = None) -> dict:
    source_geometry = _repo_relative(source_geometry)
    cols, rows = int(geometry["cols"]), int(geometry["rows"])
    # M-ALIGN camera_fit-awareness: a camera_fit room is PAINTED at its own fitted ortho (crypt_fresh
    # @10.5224, tavern_fit2 @9.2597), so every screen-space derivation here (occlusion silhouettes +
    # screen_bboxes) must project at that SAME ortho, and the value is STAMPED into the manifest as the
    # single source of truth for the QA consumers (check_grid_paint_coherence, journey_visual_sweep,
    # check_plate_drift). A non-fit room stamps neither field and derives byte-identically to before.
    camera_fit = bool(geometry.get("camera_fit", False))
    room_ortho = _fit_ortho_size(cols, rows) if camera_fit else None
    walls = geometry.get("walls", [])
    props_in = geometry.get("props", [])
    prop_entries = []
    footprints = []
    for i, p in enumerate(props_in):
        footprint = [[int(c), int(r)] for (c, r) in p.get("cells", [])]
        if not footprint:
            continue
        footprints.append(footprint)
        kind = p.get("kind", "prop")
        pid = str(p.get("id") or f"{kind}_{i}")
        occlusion = derive_occlusion_cells(footprint, kind, cols, rows, ortho=room_ortho)
        bbox = [round(v, 2) for v in project_cell_bbox(footprint, cols, rows, ortho=room_ortho)]
        prop_entries.append({"id": pid, "kind": kind, "footprint": footprint,
                             "occlusion": occlusion, "cells": footprint, "screen_bbox": bbox})
    walkable = derive_walkable(cols, rows, walls, footprints,
                               bool(geometry.get("cell_default_walkable", True)))
    camera = dict(_CAMERA)
    if camera_fit:
        camera["ortho_size"] = round(float(room_ortho), 4)
        camera["camera_fit"] = True
    manifest = {
        "manifest_version": 1,
        "room": room,
        "recipe_key": recipe_key,
        "derivation": "derived",
        "source_geometry": source_geometry,
        "grid": {"cols": cols, "rows": rows},
        "camera": camera,
        "fingerprint": {"grid": 20, "metric": "mean-sub L2-normalised luma NCC"},
        "props": prop_entries,
        "walkable": walkable,
    }
    if camera_fit:
        # Top-level stamp the QA consumers read directly (single source of truth for the room ortho).
        manifest["camera_fit"] = True
        manifest["ortho"] = round(float(room_ortho), 4)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("geometry_json")
    ap.add_argument("-o", "--out", default=None, help="output manifest path (default: stdout)")
    ap.add_argument("--room", default=None, help="room id (default: geometry stem)")
    ap.add_argument("--recipe-key", default=None, help="room_recipes key (default: room)")
    args = ap.parse_args(argv)

    geo_path = Path(args.geometry_json)
    geometry = json.loads(geo_path.read_text(encoding="utf-8"))
    room = args.room or geo_path.stem.replace("_geometry", "")
    manifest = derive_manifest(geometry, room=room, recipe_key=args.recipe_key or room,
                               source_geometry=str(geo_path))
    text = json.dumps(manifest, indent=1) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        n_occ = sum(len(p["occlusion"]) for p in manifest["props"])
        print(f"[derive_room_manifest] {room}: {len(manifest['props'])} props "
              f"({n_occ} occlusion cells), {len(manifest['walkable'])} walkable -> {args.out}",
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
