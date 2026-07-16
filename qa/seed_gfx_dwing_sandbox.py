#!/usr/bin/env python3
"""Seed the DWING walk-gate sandbox world: the two ADOPTED generated rooms (epic #1581, Step 6).

The generated-room fixture (sibling of qa/seed_gfx_snug_sandbox.py), used by the #1596 sandbox lane:

  WORLDOS_PLAYER_APP=/tmp/WorldOSPlayer_hotload.app qa/qa_sandbox.py up --run dwinggate \
      --campaign dwing_sandbox_v1 \
      --seed-cmd "uv run --directory servers/engine python qa/seed_gfx_dwing_sandbox.py {state}"
  qa/walk_test.py --room dwing_room_0 --engine http://127.0.0.1:8866 --qa http://127.0.0.1:8972

Door graph: dwing_room_0 (11,6) <-> dwing_room_1 (0,3) — the layout's real doorway. Declared unwired
seam: dwing_room_1 (11,3) (led to dwing_room_2, parked as the run's honest negative).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CID = "dwing_sandbox_v1"
GEO = HERE / "room_geometries"

ROOMS = [
    ("dwing_room_0", "dwing_room_0_geometry.json", [([11, 6], "dwing_room_1")]),
    ("dwing_room_1", "dwing_room_1_geometry.json", [([0, 3], "dwing_room_0")]),
]
ALLOWED_UNWIRED = {("dwing_room_1", (11, 3))}


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_dwing_sandbox.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "servers" / "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    from seed_gfx_town import build_grid_from_geometry  # noqa: PLC0415
    from walk_static import check_geometry, validate_seed_doors, validate_world  # noqa: PLC0415

    rooms_spec = [(lid, [(cell, to) for cell, to in doors]) for lid, _g, doors in ROOMS]
    geometries = {lid: json.loads((GEO / geofile).read_text()) for lid, geofile, _d in ROOMS}
    fails = validate_world(rooms_spec)
    fails += validate_seed_doors(rooms_spec, geometries, allowed_unwired=ALLOWED_UNWIRED)
    for lid, geofile, _doors in ROOMS:
        fails += check_geometry(geofile, geometries[lid])
    if fails:
        for f in fails:
            print(f"[dwing_sandbox] STATIC GATE RED: {f}", file=sys.stderr)
        sys.exit(1)

    server.save_campaign(Campaign(
        id=CID, title="Dwing walk-gate sandbox",
        summary="dwing_room_0 <-> dwing_room_1 — the GENERATED-room fixture (Step 6, epic #1581).",
        is_sandbox=True,
    ))
    handle = {}
    for lid, _g, _d in ROOMS:
        handle[lid] = server.add_location(
            campaign_id=CID, name=lid.replace("_", " ").title(), location_id=lid,
            make_current=(lid == "dwing_room_0"),
            description=f"The {lid.replace('_', ' ')} (generated walk-gate fixture).")
    c = server._require(CID)
    for lid, _geofile, doors in ROOMS:
        eid = handle[lid]["id"]
        c.locations[eid].connections = [handle[to]["id"] for _cell, to in doors]
        door_pairs = [{"cell": cell, "to": lid} for cell, _to in doors]
        c.locations[eid].scene_grid = build_grid_from_geometry(geometries[lid], eid, CID, lid, door_pairs)
    server.save_campaign(c)
    server.start_session(CID, title="Dwing walk-gate sandbox")
    server.create_character(
        campaign_id=CID, name="Sable", kind="player", race="human", class_name="rogue", level=3,
        abilities={"strength": 10, "dexterity": 16, "constitution": 12,
                   "intelligence": 12, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=handle["dwing_room_0"]["id"])
    print(f"[dwing_sandbox] {CID}: dwing_room_0(12x13, party spawned) <-> dwing_room_1(12x7) seeded")


if __name__ == "__main__":
    main()
