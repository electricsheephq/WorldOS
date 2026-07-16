#!/usr/bin/env python3
"""Seed an engine campaign world from a generate_town.py output directory — N rooms, reciprocal doors.

The engine half of the town pipeline (tools/generate_town.py is the data half):
  <town>_world.json + <town>_<room>_geometry.json  →  one location per room (STABLE location ids =
  plate-registry keys), a SceneGrid built from each room's geometry (walls/props/door cells), and
  door_cells[i] ↔ connections[i] wired IN PAIR ORDER (the cross_door contract,
  servers/engine/server.py) — reciprocity comes from generate_town's geometric door matching.

Usage:
  python3 qa/seed_gfx_town.py <state_dir> <town_dir> <town_id>
  # e.g. python3 qa/seed_gfx_town.py /tmp/town_state /tmp/town oldgate

Mirrors qa/seed_gfx_walkslice.py (hand-authored 3-room world); this is the generated-N-room analogue.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CID = "camp_townslice01"


def choose_spawns(cols: int, rows: int, blocked: set, door_cells: list,
                  n_party: int = 2, n_npc: int = 1) -> dict:
    """Place the party on the OPEN-FLOOR CENTROID as a compact cluster, clear of prop footprints and
    door landing rings — NOT the naive first-N-free interior cells (which land the party jammed in a
    back corner or inside a barrel: the 2026-07-15 'spawn in a barrel' bug, epic #1581 / issue #1584).

    Deterministic + pure so it is unit-testable (qa/test_seed_spawns.py). GEOMETRY IS GROUND TRUTH:
    `blocked` and `door_cells` come from the room's authored geometry, so the spawn is walkable by
    construction. Returns {"party": [(c,r),...], "npcs": [(c,r),...]}.
    """
    door_ring = {(dc + dx, dr + dy) for (dc, dr) in door_cells
                 for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    door_set = set(door_cells)

    def _free(exclude_ring: bool) -> list:
        return [(c, r) for r in range(1, rows - 1) for c in range(1, cols - 1)
                if (c, r) not in blocked and (c, r) not in door_set
                and (not exclude_ring or (c, r) not in door_ring)]

    free = _free(exclude_ring=True) or _free(exclude_ring=False)  # tiny rooms: relax the door ring
    if not free:
        return {"party": [], "npcs": []}
    cx = sum(c for c, _ in free) / len(free)
    cy = sum(r for _, r in free) / len(free)
    # anchor = the open cell nearest the floor centroid (skips a blocked central monument automatically)
    free.sort(key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[1], p[0]))
    chosen: list = [free[0]]
    remaining = free[1:]
    while len(chosen) < n_party + n_npc and remaining:
        nxt = min(remaining, key=lambda p: (min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in chosen),
                                            (p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[1], p[0]))
        chosen.append(nxt)
        remaining.remove(nxt)
    return {"party": chosen[:n_party], "npcs": chosen[n_party:n_party + n_npc]}


def build_grid_from_geometry(geo: dict, location_id: str, town_id: str, room: str,
                             door_pairs: list) -> "SceneGrid":
    """SceneGrid from a generate_town room geometry: perimeter/prop cells impassable, door cells
    punched walkable, door_cells ORDERED to match the room's connection pair order."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cols, rows = geo["cols"], geo["rows"]
    door_cells = [tuple(p["cell"]) for p in door_pairs]
    wall_cells = {tuple(c) for c in geo.get("walls", [])} - set(door_cells)

    cells: list = [SceneCell(c=c, r=r, type="wall", walkable=False) for (c, r) in sorted(wall_cells)]
    props: list = []
    for p in geo.get("props", []):
        if p.get("kind") == "wall_run":
            continue  # wall runs are render geometry; their cells are already in walls
        pid = p.get("id") or f"prop{len(props)}"
        footprint = [tuple(c) for c in p["cells"]]
        props.append(SceneProp(id=pid, kind=p["kind"], cells=footprint,
                               anchor_cell=footprint[0], occluder=True,
                               height_band="mid", silhouette="block"))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))
    for (c, r) in door_cells:
        cells.append(SceneCell(c=c, r=r, type="door", walkable=True))

    blocked = wall_cells | {tuple(c) for p in geo.get("props", []) if p.get("kind") != "wall_run"
                            for c in p["cells"]}

    grid = SceneGrid(
        scene_id=f"{CID}:{town_id}_{room}", location_id=location_id, kind="town_room",
        biome=f"generated town district ({room})",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=200, key_color="#e8b06a", ambient_color="#20242e",
                               mood="lantern-lit stone district"),
    )
    grid.door_cells = door_cells
    grid.spawns = choose_spawns(cols, rows, blocked, door_cells)
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    state_dir, town_dir, town_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    world = json.loads((town_dir / f"{town_id}_world.json").read_text())
    rooms = world["rooms"]
    if not rooms:
        sys.exit(f"no rooms in {town_id}_world.json")

    server.save_campaign(Campaign(
        id=CID, title=f"Town slice: {town_id}",
        summary=f"Generated {len(rooms)}-room town district ({town_id}) — generate_town.py pipeline.",
        is_sandbox=True,
    ))

    # pass 1: create every location with its stable id (connections wired in pass 2, pair-ordered)
    loc_handle: dict[str, dict] = {}
    for i, r in enumerate(rooms):
        loc_handle[r["location_id"]] = server.add_location(
            campaign_id=CID, name=f"{town_id} {r['room']}".replace("_", " ").title(),
            location_id=r["location_id"], make_current=(i == 0),
            description=f"A district of the generated town {town_id}.")

    c = server._require(CID)
    for r in rooms:
        lid = loc_handle[r["location_id"]]["id"]
        # connections IN PAIR ORDER == door_cells order (the cross_door index contract)
        conn = [loc_handle[p["to"]]["id"] for p in r["doors"]]
        c.locations[lid].connections = conn
        geo = json.loads((town_dir / r["geometry"]).read_text())
        c.locations[lid].scene_grid = build_grid_from_geometry(
            geo, lid, town_id, r["room"], r["doors"])
    server.save_campaign(c)
    server.start_session(CID, title=f"Town slice {town_id}")

    hub = rooms[0]["location_id"]
    server.create_character(
        campaign_id=CID, name="Scout", kind="player", race="human", class_name="rogue", level=3,
        abilities={"strength": 10, "dexterity": 16, "constitution": 12,
                   "intelligence": 13, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=loc_handle[hub]["id"])
    print(f"[seed_gfx_town] {CID}: {len(rooms)} rooms seeded, hub={hub}, "
          f"doors={[ (r['location_id'], r['doors']) for r in rooms ]}")


if __name__ == "__main__":
    main()
