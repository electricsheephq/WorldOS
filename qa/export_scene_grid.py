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
    if len(sys.argv) < 2:
        print("usage: export_scene_grid.py <campaign> [out.json]", file=sys.stderr)
        sys.exit(2)
    cid = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    c = server._require(cid)
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

    payload = {
        "location": getattr(loc, "name", ""),
        "cols": cols, "rows": rows, "material": material,
        "cell_default_walkable": bool(getattr(grid.cell_default, "walkable", True)),
        "walls": walls, "props": props, "impassable": impassable,
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
