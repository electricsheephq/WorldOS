#!/usr/bin/env python3
"""Seed the SHIP-THREE-PLATES registered walkable world: crypt <-> tavern <-> throne_hall.

Each room's grid is built DIRECTLY from its shipped geometry (qa/room_geometries/*.json) via the
generic build_grid_from_geometry — so the walkable grid, the occluder boxes sidecar, and the painted
plate are all derived from ONE geometry (UNIFY-THE-FRAMES). This is the crypt-escape fix at the world
level: no 14x11-grid-under-a-16x12-plate scale mismatch. Combat (seed_gfx_combat) is UNTOUCHED —
this is a separate campaign for the rendered rest-world the owner walks.

Door graph (crypt = hub):
  crypt door (7,0)  <-> tavern door (7,0)
  crypt door (15,5) <-> throne_hall door (8,11)
door_cells[i] <-> connections[i] ORDER is the engine cross_door contract (servers/engine/server.py).

Usage: python3 qa/seed_gfx_registered_world.py <state_dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CID = "registered_world_v1"
GEO = HERE / "room_geometries"

# per room: (location_id, geometry file, [(door_cell, to_location), ...] in door_cells order)
ROOMS = [
    ("crypt", "crypt_v36_geometry.json",
     [([7, 0], "tavern"), ([15, 5], "throne_hall")]),
    ("tavern", "tavern_v2_geometry.json",
     [([7, 0], "crypt")]),
    ("throne_hall", "throne_hall_geometry.json",
     [([8, 11], "crypt")]),
]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_registered_world.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "servers" / "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    from seed_gfx_town import build_grid_from_geometry  # noqa: PLC0415

    server.save_campaign(Campaign(
        id=CID, title="Registered walkable world",
        summary="crypt <-> tavern <-> throne_hall — the SHIP-THREE-PLATES registered rooms "
                "(panel 8.3/8.4/7.0), grids derived from the same geometry as the plates + boxes.",
        is_sandbox=True,
    ))

    handle = {}
    for i, (lid, _geo, _doors) in enumerate(ROOMS):
        handle[lid] = server.add_location(
            campaign_id=CID, name=lid.replace("_", " ").title(), location_id=lid,
            make_current=(lid == "crypt"),
            description=f"The registered {lid.replace('_', ' ')}.")

    c = server._require(CID)
    for lid, geofile, doors in ROOMS:
        eid = handle[lid]["id"]
        c.locations[eid].connections = [handle[to]["id"] for _cell, to in doors]
        geo = json.loads((GEO / geofile).read_text())
        door_pairs = [{"cell": cell, "to": lid} for cell, _to in doors]  # to= is unused by the builder
        c.locations[eid].scene_grid = build_grid_from_geometry(geo, eid, CID, lid, door_pairs)
        # re-key door_cells to the ENGINE ids in connection order (builder set them from door_pairs order)
    server.save_campaign(c)
    server.start_session(CID, title="Registered walkable world")

    server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=handle["crypt"]["id"])
    print(f"[registered_world] {CID}: 3 rooms seeded (crypt hub -> tavern + throne_hall); "
          f"doors {[(l, d) for l, _g, d in ROOMS]}")


if __name__ == "__main__":
    main()
