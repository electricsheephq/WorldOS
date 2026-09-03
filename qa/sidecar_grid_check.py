#!/usr/bin/env python3
"""Occluder sidecar vs engine grid — the Day-1 gate of "the room is the scene" (#1793).

For every room whose plates_manifest entry names a ``boxes`` sidecar AND whose geometry is known
(walk_static.GEOMETRY_OF), project each box (world-space centre/size, kind != floor, height >= 0.15)
back onto the grid with the contract map (greybox_render_headless.cell_to_world: CELL = 2u, grid
centred at the origin) and compare with the geometry's ``impassable`` set:

* blocked-without-occluder — an impassable cell no box covers: a walker camera-behind it draws
  THROUGH the painted mass (the crypt (13,7) sarcophagus class).
* occluder-over-open-floor — a box covering a walkable cell: the walk-behind silhouette fires on
  legal floor (the cyan-ghost class, #1736).

A door cell is WALKABLE floor: only a door-piece box (kind containing door/gate/arch/jamb/lintel/frame)
may cover it; any other box over a door cell is a ghost like any other. A room passes only at 0/0. Exit 0 = every checked room 0/0; 1 = disagreement; 2 = nothing checkable.

    python3 qa/sidecar_grid_check.py                      # every manifest room with a sidecar + geometry
    python3 qa/sidecar_grid_check.py --room tavern_snug --boxes /path/to/candidate_boxes.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITY = ROOT / "extensions" / "renderers" / "unity"
GEO_DIR = ROOT / "qa" / "room_geometries"
MARGIN = 0.05   # a box edge exactly on a cell centre never claims that cell
DOOR_KINDS = ("door", "gate", "arch", "jamb", "lintel", "frame")   # pieces that legitimately straddle a doorway


def cell_center(c: int, r: int, cols: int, rows: int) -> tuple[float, float]:
    return ((c - (cols - 1) / 2.0) * 2.0, ((rows - 1) / 2.0 - r) * 2.0)


def cells_of_box(box: dict, cols: int, rows: int) -> set[tuple[int, int]]:
    cx, _cy, cz = box["center"]
    sx, _sy, sz = box["size"]
    out = set()
    for r in range(rows):
        for c in range(cols):
            wx, wz = cell_center(c, r, cols, rows)
            if abs(wx - cx) <= sx / 2 - MARGIN and abs(wz - cz) <= sz / 2 - MARGIN:
                out.add((c, r))
    return out


def check(geometry: dict, sidecar: dict) -> dict:
    cols, rows = int(geometry["cols"]), int(geometry["rows"])
    impassable = {tuple(c) for c in geometry.get("impassable") or geometry.get("walls") or []}
    doors = {tuple(c) for c in geometry.get("door_cells") or []}
    covered: set[tuple[int, int]] = set()       # cells any solid box claims
    ghost: set[tuple[int, int]] = set()         # walkable cells a NON-door-piece box claims
    for box in sidecar.get("boxes", []):
        kind = str(box.get("kind", "")).lower()
        if kind == "floor" or float(box["size"][1]) < 0.15:
            continue
        cells = cells_of_box(box, cols, rows)
        covered |= cells
        door_piece = any(k in kind for k in DOOR_KINDS)
        for cell in cells - impassable:
            if cell in doors and door_piece:
                continue                        # a jamb/lintel over its own doorway is the one legitimate straddle
            ghost.add(cell)
    return {
        "blocked_without_occluder": sorted(impassable - covered - doors),
        "occluder_over_open_floor": sorted(ghost),
        "boxes": len(sidecar.get("boxes", [])),
    }


def geometry_for(room: str) -> Path | None:
    sys.path.insert(0, str(ROOT / "qa"))
    from walk_static import GEOMETRY_OF  # noqa: WPS433 (the one mapping of record)
    name = GEOMETRY_OF.get(room)
    return GEO_DIR / name if name else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", help="check ONE room (manifest key)")
    ap.add_argument("--boxes", help="candidate sidecar path (default: the manifest's boxes entry)")
    ap.add_argument("--manifest", default=str(UNITY / "plates_manifest.json"))
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    plates = manifest.get("plates", manifest)          # plates_manifest.json: {"version", "_comment", "plates": {room: {...}}}
    rooms = [args.room] if args.room else sorted(k for k, v in plates.items() if isinstance(v, dict) and v.get("boxes"))
    worst = 0
    checked = 0
    for room in rooms:
        geof = geometry_for(room)
        if geof is None or not geof.exists():
            print(f"{room}: SKIP (no geometry mapping)")
            continue
        boxes_path = Path(args.boxes) if args.boxes else UNITY / plates.get(room, {}).get("boxes", "MISSING")
        if not boxes_path.exists():
            print(f"{room}: SKIP (no sidecar at {boxes_path})")
            continue
        res = check(json.loads(geof.read_text()), json.loads(boxes_path.read_text()))
        bwo, oof = res["blocked_without_occluder"], res["occluder_over_open_floor"]
        verdict = "OK" if not bwo and not oof else "DISAGREE"
        print(f"{room}: {verdict} boxes={res['boxes']} blocked-without-occluder {len(bwo)} {bwo[:12]}{'…' if len(bwo) > 12 else ''}"
              f" occluder-over-open-floor {len(oof)} {oof[:12]}{'…' if len(oof) > 12 else ''}  [{boxes_path.name}]")
        checked += 1
        if verdict != "OK":
            worst = 1
    if not checked:
        print("nothing checkable"); return 2
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
