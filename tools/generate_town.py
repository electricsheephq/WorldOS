#!/usr/bin/env python3
"""Generate a multi-room TOWN/district from a generator layout export — the N-room orchestrator.

The tiled-space ruling (docs/roadmap/TILED-SPACE-SPIKE.md): rooms are ATOMIC paint units; a town is
a LAYOUT problem — a generator room-graph connected by door-cross transitions. Every downstream
consumer already exists; this tool is the missing loop:

  layout.json (DunGen/Tessera export)
    └─ per selected room: dungen_to_fixtures.build_geometry(room=id)   [parametric crop]
         + _perimeter_wall_run_props(...)      [author_room_geometry — the #1543 extent contract]
         + camera_fit / wall_height stamps     [what build_room_unified.cs needs]
       → <town>_<room>_geometry.json           [feeds the unified painter per room]
    └─ <town>_world.json                       [ordered door_cell↔connection pairs per room —
                                                the engine's door_cells[i]→connections[i] contract
                                                (servers/engine/server.py cross_door) + reciprocity
                                                verified]
    └─ <town>_plates_fragment.json             [plates_manifest entries: plate path + cameraPin.ortho
                                                from the SAME fit math as the render + boxes sidecar]

Usage:
  python3 tools/generate_town.py qa/evidence/dungen-spike/dungen_basic_layout.json \
      --rooms room_0,room_1,room_2 --town-id oldgate --out-dir /tmp/town
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS))
sys.path.insert(0, str(_TOOLS.parent / "qa"))

from dungen_to_fixtures import convert, build_geometry, dress_focal, dress_tall_anchors  # noqa: E402
from author_room_geometry import _perimeter_wall_run_props  # noqa: E402
from greybox_render_headless import _fit_ortho_size  # noqa: E402
from walk_static import check_geometry  # noqa: E402


def _stamp_room(geo: dict, *, material: str, wall_height: float) -> dict:
    """Make a cropped per-room geometry unified-painter-ready: perimeter wall RUNS split at doors
    (build_room_unified errors without wall_run props, by design), camera_fit, wall_height."""
    cols, rows = geo["cols"], geo["rows"]
    doors = [tuple(d) for d in geo.get("door_cells", [])]
    runs = _perimeter_wall_run_props(cols, rows, door_cells=doors)
    wall_cells = {tuple(c) for (_id, _kind, cells) in runs for c in cells}
    props = [{"id": rid, "kind": kind, "cells": [list(c) for c in cells]} for rid, kind, cells in runs]
    props += [p for p in geo.get("props", []) if p.get("kind") != "wall_run"]
    prop_cells = {tuple(c) for p in props for c in p["cells"] if p["kind"] != "wall_run"}
    geo["props"] = props
    geo["walls"] = sorted(wall_cells)
    geo["impassable"] = sorted(wall_cells | prop_cells - {tuple(d) for d in doors})
    geo["material"] = material
    geo["camera_fit"] = True
    geo["wall_height"] = wall_height
    return geo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout_json")
    ap.add_argument("--rooms", required=True, help="comma-separated room ids from the layout's rooms[]")
    ap.add_argument("--town-id", required=True, help="location-id prefix (e.g. 'oldgate' -> oldgate_room_0)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--material", default="stone", choices=["stone", "wood"])
    ap.add_argument("--wall-height", type=float, default=5.0)
    ap.add_argument("--world-units-per-cell", type=float, default=2.0)
    args = ap.parse_args(argv)

    layout = json.loads(Path(args.layout_json).read_text(encoding="utf-8"))
    room_ids = [r.strip() for r in args.rooms.split(",") if r.strip()]
    known = {r["id"] for r in layout.get("rooms", [])}
    missing = [r for r in room_ids if r not in known]
    if missing:
        ap.error(f"rooms not in layout: {missing} (known: {sorted(known)[:8]}…)")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ctx = convert(layout, name=args.town_id, upc=args.world_units_per_cell, material=args.material)

    # per-room geometries, unified-painter-ready
    loc_of = {rid: f"{args.town_id}_{rid}" for rid in room_ids}
    geos: dict[str, dict] = {}
    gate_fails: list[str] = []
    for rid in room_ids:
        geo = _stamp_room(build_geometry(ctx, room=rid),
                          material=args.material, wall_height=args.wall_height)
        geo = dress_focal(geo, name=loc_of[rid])  # narrative focal set FIRST (an altar also satisfies the tall-anchor bar)
        geo = dress_tall_anchors(geo, name=loc_of[rid])
        geo["location"] = loc_of[rid]
        # ★ STATIC WALKABILITY GATE (mirrors qa/seed_gfx_registered_world.py): a room geometry that
        # fails the static gate (off-perimeter door, blocked landing, orphan pocket, duplicate door)
        # never ships — refuse the whole run. Caught here, the three DunGen defect classes (boundary
        # doors, prop-blocked landings, flat-interior mass) are pre-render-impossible.
        gate_fails += [f"{loc_of[rid]}: {f}" for f in check_geometry(loc_of[rid], geo)]
        path = out / f"{args.town_id}_{rid}_geometry.json"
        path.write_text(json.dumps(geo) + "\n", encoding="utf-8")
        geos[rid] = geo
        print(f"[generate_town] {loc_of[rid]}: {geo['cols']}x{geo['rows']}, "
              f"{len(geo['props'])} props, doors {geo.get('door_cells')} -> {path.name}", file=sys.stderr)
    if gate_fails:
        print("[generate_town] STATIC GATE RED:", file=sys.stderr)
        for f in gate_fails:
            print(f"  - {f}", file=sys.stderr)
        return 1

    # world wiring: per room, (door_cell, to_location) pairs matched GEOMETRICALLY — the engine
    # contract is door_cells[i] -> connections[i], and a positional zip can wire the north door to
    # the east neighbour. Each doorway's world position is projected through the SAME Projector
    # into the room's cropped frame and matched to the room's nearest door_cell.
    proj = ctx["_projector"]
    room_cells = ctx["_room_cells"]

    def crop_origin(rid: str) -> tuple:
        cs = [c for (c, r) in room_cells[rid]]
        rs = [r for (c, r) in room_cells[rid]]
        return (min(cs) - 1, min(rs) - 1)

    adj: dict[str, list] = {rid: [] for rid in room_ids}
    selected = set(room_ids)
    errors: list[str] = []
    for dw in layout.get("doorways", []):
        a, b = dw.get("room_a"), dw.get("room_b")
        pos = dw.get("position")
        if a in selected and b in selected and pos:
            g = proj.cell_of(float(pos[0]), float(pos[2]))
            adj[a].append((b, g))
            adj[b].append((a, g))
    world = {"town_id": args.town_id, "rooms": []}
    for rid in room_ids:
        cells = [tuple(c) for c in geos[rid].get("door_cells", [])]
        c0, r0 = crop_origin(rid)
        pairs, used = [], set()
        for (target, g) in adj[rid]:
            local = (g[0] - c0, g[1] - r0)
            if not cells:
                errors.append(f"{rid}: doorway to {target} but geometry has no door_cells")
                continue
            dist, cell = min((abs(local[0] - c[0]) + abs(local[1] - c[1]), c) for c in cells)
            if dist > 2:
                errors.append(f"{rid}: doorway to {target} projects to {local} but nearest "
                              f"door_cell {cell} is {dist} away — projection mismatch")
                continue
            if cell in used:
                errors.append(f"{rid}: door_cell {cell} matched twice (targets incl. {target})")
                continue
            used.add(cell)
            pairs.append({"cell": list(cell), "to": loc_of[target]})
        # door cells beyond the in-town targets stay unwired (doors to unselected rooms)
        world["rooms"].append({"room": rid, "location_id": loc_of[rid],
                               "geometry": f"{args.town_id}_{rid}_geometry.json", "doors": pairs})
    # connectivity of the selected subgraph
    seen, stack = {room_ids[0]}, [room_ids[0]]
    while stack:
        for (n, _g) in adj[stack.pop()]:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    if seen != selected:
        errors.append(f"selected rooms not connected: unreachable {sorted(selected - seen)}")
    (out / f"{args.town_id}_world.json").write_text(json.dumps(world, indent=1) + "\n", encoding="utf-8")

    # plates_manifest fragment — cameraPin.ortho from the SAME fit math as the unified render
    frag = {"plates": {loc_of[rid]: {
        "plate": f"plates/{loc_of[rid]}.png",
        "cameraPin": {"ortho": round(_fit_ortho_size(geos[rid]["cols"], geos[rid]["rows"]), 4),
                      # pitch/yaw stamped explicitly (provenance + belt-and-suspenders): the client
                      # DEFAULTS to the 30/45 contract rig when only ortho is pinned (#1591), but
                      # every shipped manifest entry carries them and walk_static lints them.
                      "pitch": 30, "yaw": 45},
        "boxes": f"boxes/{loc_of[rid]}_boxes.json",
    } for rid in room_ids}}
    (out / f"{args.town_id}_plates_fragment.json").write_text(json.dumps(frag, indent=1) + "\n", encoding="utf-8")

    if errors:
        print("[generate_town] ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"[generate_town] OK: {len(room_ids)} rooms, world + plates fragment under {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
