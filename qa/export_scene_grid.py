#!/usr/bin/env python3
"""export_scene_grid.py — export a location's authored scene_grid as greybox geometry JSON (gfx M-E).

The authored-pathing keystone: a room's scene_grid (walls + props at KNOWN cells) is the SINGLE source
for BOTH the painted room (a greybox rendered from these cells -> img2img control) AND the combat pathing
(impassable_cells). This dumps that geometry so the Unity greybox renderer can build floor + wall/prop
boxes at the contract camera, producing a control image whose painted props sit exactly where the
pathing obstacles are — the decoupling fix the owner asked about ("how does pathing work" for gen rooms).

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/export_scene_grid.py <campaign> [out.json]

Output JSON: { cols, rows, cell_default_walkable, walls: [[c,r]...], props: [{kind, cells:[[c,r]...]}],
               impassable: [[x,y]...] }  (impassable == the combat-grid obstacles, identity c->x,r->y).
Engine = SOLE WRITER: read-only on engine state (no mutation).
"""
import json
import os
import sys


def main() -> None:
    # optional --location <loc_id> selects a SPECIFIC location's grid (for multi-room-unit composition,
    # where one campaign holds several linked room-units); default = the campaign's current location.
    args = sys.argv[1:]
    location_id = None
    if "--location" in args:
        i = args.index("--location")
        location_id = args[i + 1]
        del args[i:i + 2]
    if len(args) < 1:
        print("usage: export_scene_grid.py <campaign> [out.json] [--location <loc_id>]", file=sys.stderr)
        sys.exit(2)
    cid = args[0]
    out = args[1] if len(args) > 1 else None
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    c = server._require(cid)
    if location_id:
        loc = c.locations.get(location_id)
    else:
        loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    grid = getattr(loc, "scene_grid", None) if loc is not None else None
    if grid is None:
        print(json.dumps({"error": "no scene_grid on current location"}))
        return

    cols = grid.grid.cols
    rows = grid.grid.rows
    walls = [[sc.c, sc.r] for sc in grid.cells if getattr(sc, "type", "") == "wall" or not getattr(sc, "walkable", True)]
    props = [{"kind": getattr(p, "kind", "prop"), "cells": [[c0, r0] for (c0, r0) in p.cells]} for p in grid.props]
    impassable = sg.impassable_cells(grid, cols, rows)

    # material hint for the greybox texture (stone masonry vs wood planks) — the room's MATERIAL axis.
    _bio = (getattr(grid, "biome", "") + " " + getattr(loc, "name", "")).lower()
    material = "wood" if any(w in _bio for w in ("wood", "timber", "tavern", "plank", "hall of") ) and "stone" not in _bio else "stone"

    # PRE-GREYBOX GATE: pathing/lanes must be valid BEFORE any art is generated (Diablo's topology-then-
    # dressing). Refuse to write room_geometry.json on a violation so a broken room can never reach the
    # Unity greybox / img2img step. See docs/roadmap/ROOM-OCCLUSION-PATHING-SPRINTS.md.
    violations = sg.validate_scene_grid(grid, cols, rows)
    if violations:
        print("[export_scene_grid] VALIDATION FAILED — refusing to write geometry:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "location": getattr(loc, "name", ""),
        "cols": cols, "rows": rows, "material": material,
        "cell_default_walkable": bool(getattr(grid.cell_default, "walkable", True)),
        "walls": walls, "props": props, "impassable": impassable,
        "door_cells": [[int(c), int(r)] for (c, r) in (getattr(grid, "door_cells", None) or [])],
        "protected_lane_cells": [[int(c), int(r)] for (c, r) in (getattr(grid, "protected_lane_cells", None) or [])],
    }
    text = json.dumps(payload)
    if out:
        with open(out, "w") as f:
            f.write(text)
        print(f"[export_scene_grid] {getattr(loc,'name','')} {cols}x{rows}: {len(props)} props, {len(impassable)} impassable -> {out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
