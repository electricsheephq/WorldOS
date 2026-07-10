#!/usr/bin/env python3
"""dungen_to_fixtures.py — convert a generator layout export into WorldOS engine fixtures (epic #1508).

DunGen (Unity, owner-purchased 2026-07-11) is an AUTHORING-TIME structure accelerator. Its layout export
(extensions/renderers/unity/scripts/Editor/DunGenLayoutExporter.cs -> dungen_layout.json) proposes a
room-graph in Unity WORLD units; this tool bakes that into the two fixtures the rest of the pipeline
already consumes, so a DunGen-authored dungeon flows straight into greybox -> registered plate -> derived
manifest with NO schema fork.

Stage-2 (epic #1508): Tessera Pro's tile-WFC exporter (TesseraLayoutExporter.cs -> tessera_layout.json,
see docs/roadmap/GENERATOR-EXPORT-CONTRACT.md) emits the SAME top-level shape, one tile instance = one
`rooms[]` entry, so this converter needed NO changes for the core path. The one genuine 1:1-mapping gap —
a WFC "big tile" can occupy a non-rectangular multi-cell footprint that a bounds-AABB would over-carve —
is closed by an ADDITIVE, tolerated `rooms[].cell_positions` field (see `_room_footprint` below); DunGen
layouts that omit it fall back to the original bounds-AABB rasterization, unchanged.

  (a) <name>.scenegrid.json     — the engine SceneGrid fixture (servers/engine/scene_grid.py). The Python
                                  engine remains the SOLE WRITER of grid truth; DunGen only proposed the
                                  layout, this is the authored fixture the engine loads. Exterior is solid
                                  rock (cell_default = void, non-walkable); carved room/corridor cells are
                                  explicit walkable floor; doorways are `door` cells; props are impassable
                                  footprints with occluder/height_band for the Tier-2 depth proxies.

  (b) <name>_geometry.json      — the greybox geometry json BOTH qa/greybox_render_headless.py AND
                                  tools/derive_room_manifest.py (lane/eval-upgrade) consume:
                                  {location, cols, rows, material, cell_default_walkable, walls, props,
                                   impassable, door_cells, protected_lane_cells}. cell_default_walkable is
                                  True and `walls` lists EVERY non-floor cell, so the derived walkable set
                                  == the carved floor (matches the forest_road fixture's model, verified).
                                  With --room <id> the geometry is cropped to one room (+1-cell wall
                                  margin) — the per-room input the registered-plate pipeline renders.

SCALE MAPPING (DunGen world units -> 5-ft cells): the engine cell is 5 ft; the greybox renderer already
uses 2.0 world-units-per-cell (greybox_render_headless.cell_to_world multiplies by 2.0). So the default
`--world-units-per-cell 2.0` makes 2 Unity units == one 5-ft cell and keeps the whole chain unit-
consistent. Cell indexing mirrors the greybox back->front convention (row 0 = max world-Z, col 0 = min
world-X):  col = round((wx - min_x)/upc),  row = round((max_z - wz)/upc).

Shape-appropriate proxies (PR #1495 lesson: box-trees read as buildings to depth models): the exporter
tags each prop shape_class box|cylinder|cone; masonry boxes -> crate/rubble, cylinders -> pillar,
cones -> large_tree, unless the prop's kind_hint already names a known greybox kind. The emitted `kind`
always lands in greybox_render_headless._KIND_SPECS so a shape-right proxy box+height is drawn.

  python3 tools/dungen_to_fixtures.py <dungen_layout.json> --out-dir qa/evidence/dungen-spike \
      [--name mydungeon] [--world-units-per-cell 2.0] [--room room_0] [--material stone]

Deterministic, offline, stdlib only. Read-only w.r.t. engine state.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

# ── kind mapping ──────────────────────────────────────────────────────────────────────────────────────
# Keywords the greybox renderer's _KIND_SPECS recognises (must stay in sync with
# qa/greybox_render_headless.py::_KIND_SPECS). Detected inside a prop's kind_hint to preserve intent.
_KNOWN_KIND_KEYWORDS = [
    "pillar", "column", "large_tree", "stone_well", "sarcophagus", "altar", "bar", "table", "pew",
    "market_stall", "brazier", "campfire", "bedroll", "fallen_log", "boulder", "supply_crates", "cart",
    "merchants_cart", "rubble", "barrel", "crate",
]
# Approx greybox height per kind (mirrors _KIND_SPECS heights) — used only to classify height_band /
# occluder for the SceneGrid props; NOT a second source of geometry (the renderer owns the real boxes).
_KIND_HEIGHT = {
    "pillar": 7.5, "column": 7.5, "large_tree": 9.0, "stone_well": 3.2, "sarcophagus": 2.0, "altar": 2.0,
    "bar": 2.0, "table": 2.0, "pew": 2.0, "market_stall": 2.0, "brazier": 2.2, "campfire": 0.6,
    "bedroll": 0.28, "fallen_log": 0.8, "boulder": 2.0, "supply_crates": 1.5, "cart": 1.5,
    "merchants_cart": 1.5, "rubble": 1.4, "barrel": 1.4, "crate": 1.4,
}
# Fallback proxy per shape class when kind_hint names nothing known.
_SHAPE_DEFAULT_KIND = {"box": "crate", "cylinder": "pillar", "cone": "large_tree"}


def _resolve_kind(shape_class: str, kind_hint: str) -> str:
    hint = (kind_hint or "").lower()
    for kw in _KNOWN_KIND_KEYWORDS:
        if kw in hint:
            return kw
    return _SHAPE_DEFAULT_KIND.get((shape_class or "box").lower(), "crate")


def _height_band(kind: str) -> str:
    h = _KIND_HEIGHT.get(kind, 2.6)
    if h >= 5.0:
        return "tall"
    if h >= 1.2:
        return "mid"
    return "low"


# ── world <-> cell projection ───────────────────────────────────────────────────────────────────────
class Projector:
    """Snaps DunGen world XZ to the integer cell grid (col 0 = min world-X, row 0 = max world-Z)."""

    def __init__(self, overall_min: list, overall_max: list, upc: float):
        self.min_x, self.max_z = float(overall_min[0]), float(overall_max[2])
        self.upc = float(upc)
        self.cols = int(round((float(overall_max[0]) - float(overall_min[0])) / upc)) + 1
        self.rows = int(round((float(overall_max[2]) - float(overall_min[2])) / upc)) + 1

    def cell_of(self, wx: float, wz: float) -> tuple:
        c = int(round((wx - self.min_x) / self.upc))
        r = int(round((self.max_z - wz) / self.upc))
        return (max(0, min(self.cols - 1, c)), max(0, min(self.rows - 1, r)))

    def cells_in_xz_bounds(self, bmin: list, bmax: list, inset: float = 0.01) -> list:
        """Every cell whose CENTRE falls inside the world XZ box [min,max] (small inset avoids grabbing a
        neighbour across a shared tile wall)."""
        c0, r1 = self.cell_of(float(bmin[0]) + inset, float(bmax[2]) - inset)
        c1, r0 = self.cell_of(float(bmax[0]) - inset, float(bmin[2]) + inset)
        out = []
        for r in range(min(r0, r1), max(r0, r1) + 1):
            for c in range(min(c0, c1), max(c0, c1) + 1):
                out.append((c, r))
        return out


# ── the conversion ────────────────────────────────────────────────────────────────────────────────────
def _room_footprint(rm: dict, proj: Projector) -> set:
    """Footprint cells for one room/tile entry.

    Prefers the ADDITIVE `cell_positions` field (a world-space center point per grid cell the tile
    instance occupies) when present — needed for generators whose placed units aren't a simple continuous
    AABB (Tessera Pro's tile-WFC output can place non-rectangular multi-cell "big tiles"; rasterizing the
    bounding box would over-include cells that were never actually part of the tile). Falls back to
    rasterizing the room's `bounds` AABB when `cell_positions` is absent — DunGen's original room-graph
    shape, unchanged, still exactly reproduces the pre-Tessera behaviour.
    """
    cps = rm.get("cell_positions")
    if cps:
        return {proj.cell_of(float(p[0]), float(p[2])) for p in cps}
    b = rm.get("bounds", {})
    return set(proj.cells_in_xz_bounds(b["min"], b["max"]))


def _prop_entries(layout: dict, proj: Projector, floor: set) -> list:
    """Snap each exported prop to a footprint (cells within its XZ bounds, clamped to floor)."""
    entries = []
    for i, p in enumerate(layout.get("props", [])):
        b = p.get("bounds") or {}
        bmin, bmax = b.get("min"), b.get("max")
        if bmin and bmax:
            cells = [cr for cr in proj.cells_in_xz_bounds(bmin, bmax) if cr in floor]
        else:
            pos = p.get("position") or [0, 0, 0]
            cr = proj.cell_of(float(pos[0]), float(pos[2]))
            cells = [cr] if cr in floor else []
        if not cells:
            continue
        kind = _resolve_kind(p.get("shape_class", "box"), p.get("kind_hint", ""))
        pos = p.get("position") or [0, 0, 0]
        anchor = proj.cell_of(float(pos[0]), float(pos[2]))
        if anchor not in cells:
            anchor = cells[0]
        band = _height_band(kind)
        entries.append({
            "id": str(p.get("id") or f"prop_{i}"),
            "kind": kind,
            "cells": [list(cr) for cr in sorted(cells, key=lambda x: (x[1], x[0]))],
            "anchor_cell": list(anchor),
            "height_band": band,
            "occluder": band in ("mid", "tall"),
            "kind_hint": p.get("kind_hint", ""),
        })
    return entries


def _door_cells(layout: dict, proj: Projector) -> list:
    doors = []
    for d in layout.get("doorways", []):
        pos = d.get("position") or [0, 0, 0]
        doors.append((proj.cell_of(float(pos[0]), float(pos[2])), d))
    return doors


def _exits(layout: dict, proj: Projector) -> list:
    """A doorway with an empty room_a or room_b connects to nothing generated == a level exit."""
    out = []
    for d in layout.get("doorways", []):
        if d.get("room_a") and d.get("room_b"):
            continue
        pos = d.get("position") or [0, 0, 0]
        c, r = proj.cell_of(float(pos[0]), float(pos[2]))
        out.append({"cell": [c, r], "to_location_id": "", "label": "the passage out"})
    return out


def convert(layout: dict, *, name: str, upc: float, material: str) -> dict:
    overall = layout.get("bounds") or {}
    omin, omax = overall.get("min"), overall.get("max")
    if not (omin and omax):
        # derive from rooms if the exporter omitted the overall AABB
        xs, ys, zs = [], [], []
        for rm in layout.get("rooms", []):
            b = rm.get("bounds", {})
            for key in ("min", "max"):
                v = b.get(key)
                if v:
                    xs.append(v[0]); ys.append(v[1]); zs.append(v[2])
        omin, omax = [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
    proj = Projector(omin, omax, upc)

    # floor = union of every room/corridor tile footprint.
    floor: set = set()
    room_cells: dict = {}
    for rm in layout.get("rooms", []):
        cells = _room_footprint(rm, proj)
        room_cells[rm.get("id", "")] = cells
        floor |= cells

    doors = _door_cells(layout, proj)
    door_set = {cr for (cr, _d) in doors}
    floor |= door_set  # a doorway cell is always carved floor

    props = _prop_entries(layout, proj, floor)
    exits = _exits(layout, proj)

    return {
        "_projector": proj,
        "_floor": floor,
        "_room_cells": room_cells,
        "_doors": doors,
        "_door_set": door_set,
        "_props": props,
        "_exits": exits,
        "name": name,
        "upc": upc,
        "material": material,
        "seed": int((layout.get("generator") or {}).get("seed", 0)),
    }


# ── (a) engine SceneGrid fixture ──────────────────────────────────────────────────────────────────────
def build_scenegrid(ctx: dict, *, location_id: str) -> dict:
    proj: Projector = ctx["_projector"]
    floor: set = ctx["_floor"]
    door_set: set = ctx["_door_set"]
    prop_cells = {tuple(c) for p in ctx["_props"] for c in p["cells"]}

    cells = []
    # Explicit walkable floor for every carved cell (exterior stays cell_default void = solid rock).
    for (c, r) in sorted(floor, key=lambda x: (x[1], x[0])):
        if (c, r) in door_set:
            cells.append({"c": c, "r": r, "type": "door", "walkable": True, "cost": 1,
                          "elevation": 0, "prop_ref": None})
        elif (c, r) in prop_cells:
            continue  # emitted as a prop-backed impassable cell below
        else:
            cells.append({"c": c, "r": r, "type": "floor", "walkable": True, "cost": 1,
                          "elevation": 0, "prop_ref": None})
    # Prop footprints: impassable, prop_ref back to the SceneProp.
    for p in ctx["_props"]:
        for (c, r) in p["cells"]:
            cells.append({"c": c, "r": r, "type": "prop", "walkable": False, "cost": 1,
                          "elevation": 0, "prop_ref": p["id"]})

    props = [{
        "id": p["id"], "kind": p["kind"], "cells": [list(x) for x in p["cells"]],
        "anchor_cell": p["anchor_cell"], "occluder": p["occluder"],
        "height_band": p["height_band"], "silhouette": p.get("kind_hint", ""),
    } for p in ctx["_props"]]

    return {
        "scene_id": f"dungen:{ctx['name']}",
        "location_id": location_id,
        "kind": "dungeon",
        "biome": f"DunGen-authored {ctx['material']} dungeon",
        "seed": ctx["seed"],
        "grid": {"cols": proj.cols, "rows": proj.rows, "cell_size_ft": 5, "projection": "dimetric-2to1"},
        "cell_default": {"type": "void", "walkable": False, "cost": 1},
        "cells": cells,
        "props": props,
        "zone_anchors": {},
        "exits": ctx["_exits"],
        "spawns": {},
        "door_cells": [list(c) for c in sorted(door_set, key=lambda x: (x[1], x[0]))],
        "protected_lane_cells": [],
        "lighting": {"key_dir_deg": 45, "key_color": "#ffe6b0", "ambient_color": "#2a2f3f",
                     "mood": "torchlit dungeon"},
        "art": {"status": "tier1_blockout"},
    }


# ── (b) greybox geometry json (whole dungeon or one cropped room) ───────────────────────────────────
def build_geometry(ctx: dict, *, room: Optional[str] = None) -> dict:
    proj: Projector = ctx["_projector"]
    floor: set = ctx["_floor"]
    door_set: set = ctx["_door_set"]
    props_all = ctx["_props"]

    if room is not None:
        room_floor = ctx["_room_cells"].get(room)
        if room_floor is None:
            raise SystemExit(f"[dungen_to_fixtures] room {room!r} not found (rooms: "
                             f"{sorted(ctx['_room_cells'])})")
        # crop window = room bbox + 1-cell wall margin; re-origin so col/row start at 0.
        cs = [c for (c, r) in room_floor]
        rs = [r for (c, r) in room_floor]
        c0, c1 = min(cs) - 1, max(cs) + 1
        r0, r1 = min(rs) - 1, max(rs) + 1
        cols, rows = c1 - c0 + 1, r1 - r0 + 1

        def shift(cr):
            return (cr[0] - c0, cr[1] - r0)

        window = {(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        floor_w = {shift(cr) for cr in (room_floor | (door_set & window)) if cr in window}
        door_w = {shift(cr) for cr in door_set if cr in window}
        props = [p for p in props_all if any(tuple(c) in room_floor for c in p["cells"])]
        props_w = [{"kind": p["kind"], "cells": [list(shift(tuple(c))) for c in p["cells"]
                                                 if shift(tuple(c)) in floor_w]} for p in props]
        props_w = [p for p in props_w if p["cells"]]
    else:
        cols, rows = proj.cols, proj.rows
        floor_w = set(floor)
        door_w = set(door_set)
        props_w = [{"kind": p["kind"], "cells": [list(c) for c in p["cells"]]} for p in props_all]

    prop_cells = {tuple(c) for p in props_w for c in p["cells"]}
    # walls = every in-window cell that is NOT carved floor (matches the forest_road model: with
    # cell_default_walkable True, walkable == grid - walls - prop footprints == carved floor).
    walls = [[c, r] for r in range(rows) for c in range(cols)
             if (c, r) not in floor_w]
    impassable = sorted(
        {(c, r) for (c, r) in ((cc, rr) for [cc, rr] in walls)} | prop_cells,
        key=lambda x: (x[1], x[0]))

    return {
        "location": f"{ctx['name']}" + (f":{room}" if room else ""),
        "cols": cols, "rows": rows,
        "material": ctx["material"],
        "cell_default_walkable": True,
        "walls": walls,
        "props": props_w,
        "impassable": [list(c) for c in impassable],
        "door_cells": [list(c) for c in sorted(door_w, key=lambda x: (x[1], x[0]))],
        "protected_lane_cells": [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout_json")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--name", default=None, help="fixture base name (default: layout stem)")
    ap.add_argument("--world-units-per-cell", type=float, default=2.0)
    ap.add_argument("--material", default="stone", choices=["stone", "wood"])
    ap.add_argument("--room", default=None, help="also emit a cropped per-room geometry json")
    ap.add_argument("--location-id", default=None)
    args = ap.parse_args(argv)

    layout_path = Path(args.layout_json)
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    name = args.name or layout_path.stem.replace("_layout", "")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = convert(layout, name=name, upc=args.world_units_per_cell, material=args.material)

    scenegrid = build_scenegrid(ctx, location_id=args.location_id or name)
    sg_path = out_dir / f"{name}.scenegrid.json"
    sg_path.write_text(json.dumps(scenegrid, indent=1) + "\n", encoding="utf-8")

    geometry = build_geometry(ctx)
    geo_path = out_dir / f"{name}_geometry.json"
    geo_path.write_text(json.dumps(geometry) + "\n", encoding="utf-8")

    proj: Projector = ctx["_projector"]
    print(f"[dungen_to_fixtures] {name}: grid {proj.cols}x{proj.rows}, "
          f"{len(ctx['_floor'])} floor cells, {len(ctx['_props'])} props, "
          f"{len(ctx['_door_set'])} doors -> {sg_path.name} + {geo_path.name}", file=sys.stderr)

    if args.room is not None:
        room_geo = build_geometry(ctx, room=args.room)
        rg_path = out_dir / f"{name}_{args.room}_geometry.json"
        rg_path.write_text(json.dumps(room_geo) + "\n", encoding="utf-8")
        print(f"[dungen_to_fixtures] room {args.room}: {room_geo['cols']}x{room_geo['rows']} "
              f"-> {rg_path.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
