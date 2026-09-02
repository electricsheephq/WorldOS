#!/usr/bin/env python3
"""Seed the SHOP walk-gate sandbox world: crypt <-> shop (epic #1581, issue #1588).

The scale-proof fixture: a 2-room world whose grids are built FROM the authored geometries
(build_grid_from_geometry — UNIFY-THE-FRAMES), used by the #1596 sandbox lane to walk-gate the NEW
shop class without touching the owner's campaign:

  qa/qa_sandbox.py up --run shop1 --campaign shop_sandbox_v1 \
      --seed-cmd "uv run --directory servers/engine python qa/seed_gfx_shop_sandbox.py {state}"
  qa/walk_test.py --room shop --engine http://127.0.0.1:8866 --qa http://127.0.0.1:8972

Door graph: crypt (7,0) <-> shop (12,4). The 2026-09-02 re-author retired the shop's back-wall cell
(6,0) and the (12,5) seam to WALL — the plate paints exactly one opening, the archway, and (12,4) is
its only legal perimeter door (qa/reauthor_legacy_room.md), so the shop now has a single door.
door_cells[i] <-> connections[i] ORDER is the engine cross_door contract (servers/engine/server.py).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CID = "shop_sandbox_v1"
GEO = HERE / "room_geometries"

ROOMS = [
    ("crypt", "crypt_v36_geometry.json", [([7, 0], "shop")]),
    # (6,0) was retired to wall by the 2026-09-02 re-author — the shop's only authored door is the
    # painted archway's perimeter cell (12,4). See qa/reauthor_legacy_room.md.
    ("shop", "shop_geometry.json", [([12, 4], "crypt")]),
]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_shop_sandbox.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "servers" / "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    from seed_gfx_town import DEFAULT_COHERENCE_DIR, build_grid_from_geometry  # noqa: PLC0415

    server.save_campaign(Campaign(
        id=CID, title="Shop walk-gate sandbox",
        summary="crypt <-> shop — the Room Readiness Pipeline scale-proof fixture (#1588).",
        is_sandbox=True,
    ))
    handle = {}
    for lid, _g, _d in ROOMS:
        handle[lid] = server.add_location(
            campaign_id=CID, name=lid.replace("_", " ").title(), location_id=lid,
            make_current=(lid == "shop"),
            description=f"The {lid.replace('_', ' ')} (walk-gate fixture).")
    c = server._require(CID)
    for lid, geofile, doors in ROOMS:
        eid = handle[lid]["id"]
        c.locations[eid].connections = [handle[to]["id"] for _cell, to in doors]
        geo = json.loads((GEO / geofile).read_text())
        door_pairs = [{"cell": cell, "to": lid} for cell, _to in doors]
        c.locations[eid].scene_grid = build_grid_from_geometry(geo, eid, CID, lid, door_pairs,
            coherence_reports_dir=DEFAULT_COHERENCE_DIR)  # #1647: spawn on OPEN floor (no-op without a report)
    server.save_campaign(c)
    server.start_session(CID, title="Shop walk-gate sandbox")
    server.create_character(
        campaign_id=CID, name="Gauge", kind="player", race="human", class_name="rogue", level=3,
        abilities={"strength": 10, "dexterity": 16, "constitution": 12,
                   "intelligence": 13, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=handle["shop"]["id"])
    print(f"[shop_sandbox] {CID}: shop(13x10, party spawned) <-> crypt(16x12) seeded")


if __name__ == "__main__":
    main()
