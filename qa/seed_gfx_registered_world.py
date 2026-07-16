#!/usr/bin/env python3
"""Seed the registered walkable world — THE 5-ROOM SPINE: crypt <-> tavern <-> shop <-> snug (+throne).

Each room's grid is built DIRECTLY from its shipped geometry (qa/room_geometries/*.json) via the
generic build_grid_from_geometry — so the walkable grid, the occluder boxes sidecar, and the painted
plate are all derived from ONE geometry (UNIFY-THE-FRAMES). This is the crypt-escape fix at the world
level: no 14x11-grid-under-a-16x12-plate scale mismatch. Combat (seed_gfx_combat) is UNTOUCHED —
this is a separate campaign for the rendered rest-world the owner walks.

Door graph (crypt = hub):
  crypt (7,0)  <-> tavern (7,0)        crypt (15,5) <-> throne_hall (8,11)
  tavern (13,5) <-> shop (6,0)         shop (12,5)  <-> tavern_snug (5,0)
Remaining declared seams: throne_hall (15,6), tavern_snug (11,4).
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
# THE 5-ROOM SPINE (epic #1581/#1588 scale-out, 2026-07-16): crypt hub <-> tavern <-> shop <-> snug,
# plus crypt <-> throne. Wiring tavern (13,5) -> shop FIXES the previously-dead painted doorway the
# 3-room world left unwired (validate_seed_doors had it allowlisted; now it's a real door).
ROOMS = [
    ("crypt", "crypt_v36_geometry.json",
     [([7, 0], "tavern"), ([15, 5], "throne_hall")]),
    ("tavern", "tavern_v2_geometry.json",
     [([7, 0], "crypt"), ([13, 5], "shop")]),
    ("throne_hall", "throne_hall_geometry.json",
     [([8, 11], "crypt")]),
    ("shop", "shop_geometry.json",
     [([6, 0], "tavern"), ([12, 5], "tavern_snug")]),
    ("tavern_snug", "tavern_snug_geometry.json",
     [([5, 0], "shop")]),
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
    from walk_static import check_geometry, validate_seed_doors, validate_world  # noqa: PLC0415

    # ★ STATIC GATE AT THE SEED BOUNDARY (epic #1581): an invalid world never enters a campaign —
    # door reciprocity + per-room geometry checks (blocked landings, orphan pockets) + seed-vs-geometry
    # door agreement REFUSE the seed. KNOWN UNWIRED SEAMS (explicit, not silent): throne_hall (15,6)
    # and tavern_snug (11,4) — the remaining town seams for future rooms. (tavern (13,5) was here
    # until the 5-room spine wired it to the shop.)
    rooms_spec = [(lid, [(cell, to) for cell, to in doors]) for lid, _g, doors in ROOMS]
    geometries = {lid: json.loads((GEO / geofile).read_text()) for lid, geofile, _d in ROOMS}
    fails = validate_world(rooms_spec)
    fails += validate_seed_doors(rooms_spec, geometries,
                                 allowed_unwired={("throne_hall", (15, 6)), ("tavern_snug", (11, 4))})
    for lid, geofile, _doors in ROOMS:
        fails += check_geometry(geofile, geometries[lid])
    if fails:
        for f in fails:
            print(f"[registered_world] STATIC GATE RED: {f}", file=sys.stderr)
        sys.exit(1)

    server.save_campaign(Campaign(
        id=CID, title="Registered walkable world",
        summary="The 5-room spine: crypt hub <-> tavern <-> shop <-> tavern_snug, plus throne_hall — "
                "every room panel-in-band AND walk-certified (epic #1581); grids derived from the "
                "same geometry as the plates + boxes.",
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
    print(f"[registered_world] {CID}: {len(ROOMS)} rooms seeded (the 5-room spine); "
          f"doors {[(l, d) for l, _g, d in ROOMS]}")


if __name__ == "__main__":
    main()
