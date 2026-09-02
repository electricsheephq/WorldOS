#!/usr/bin/env python3
"""Seed the TAVERN_SNUG walk-gate sandbox world: crypt <-> snug (epic #1581, issue #1588).

The variant-proof fixture (sibling of qa/seed_gfx_shop_sandbox.py), used by the #1596 sandbox lane:

  qa/qa_sandbox.py up --run snuggate --campaign snug_sandbox_v1 \
      --seed-cmd "uv run --directory servers/engine python qa/seed_gfx_snug_sandbox.py {state}"
  qa/walk_test.py --room tavern_snug --engine http://127.0.0.1:8866 --qa http://127.0.0.1:8972

Door graph: crypt (7,0) <-> snug (5,0). Declared unwired seams: crypt (15,5) (the throne seam in this
fixture) and snug (11,4) (the east town seam) — validated explicitly via validate_seed_doors.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CID = "snug_sandbox_v1"
GEO = HERE / "room_geometries"

ROOMS = [
    ("crypt", "crypt_v36_geometry.json", [([7, 0], "tavern_snug")]),
    ("tavern_snug", "tavern_snug_geometry.json", [([5, 0], "crypt")]),
]
ALLOWED_UNWIRED = {("crypt", (15, 5)), ("tavern_snug", (11, 4))}


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_snug_sandbox.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "servers" / "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    from seed_gfx_town import DEFAULT_COHERENCE_DIR, build_grid_from_geometry  # noqa: PLC0415
    from walk_static import check_geometry, validate_seed_doors, validate_world  # noqa: PLC0415

    rooms_spec = [(lid, [(cell, to) for cell, to in doors]) for lid, _g, doors in ROOMS]
    geometries = {lid: json.loads((GEO / geofile).read_text()) for lid, geofile, _d in ROOMS}
    fails = validate_world(rooms_spec)
    fails += validate_seed_doors(rooms_spec, geometries, allowed_unwired=ALLOWED_UNWIRED)
    for lid, geofile, _doors in ROOMS:
        fails += check_geometry(geofile, geometries[lid])
    if fails:
        for f in fails:
            print(f"[snug_sandbox] STATIC GATE RED: {f}", file=sys.stderr)
        sys.exit(1)

    server.save_campaign(Campaign(
        id=CID, title="Snug walk-gate sandbox",
        summary="crypt <-> tavern_snug — the Room Readiness Pipeline VARIANT fixture (#1588).",
        is_sandbox=True,
    ))
    handle = {}
    for lid, _g, _d in ROOMS:
        handle[lid] = server.add_location(
            campaign_id=CID, name=lid.replace("_", " ").title(), location_id=lid,
            make_current=(lid == "tavern_snug"),
            description=f"The {lid.replace('_', ' ')} (walk-gate fixture).")
    c = server._require(CID)
    for lid, _geofile, doors in ROOMS:
        eid = handle[lid]["id"]
        c.locations[eid].connections = [handle[to]["id"] for _cell, to in doors]
        door_pairs = [{"cell": cell, "to": lid} for cell, _to in doors]
        c.locations[eid].scene_grid = build_grid_from_geometry(geometries[lid], eid, CID, lid, door_pairs,
            coherence_reports_dir=DEFAULT_COHERENCE_DIR)  # #1647: spawn on OPEN floor (no-op without a report)
    server.save_campaign(c)
    server.start_session(CID, title="Snug walk-gate sandbox")
    server.create_character(
        campaign_id=CID, name="Pint", kind="player", race="halfling", class_name="bard", level=3,
        abilities={"strength": 8, "dexterity": 14, "constitution": 12,
                   "intelligence": 12, "wisdom": 10, "charisma": 16},
        apply_srd_defaults=True, add_to_party=True, location_id=handle["tavern_snug"]["id"])
    print(f"[snug_sandbox] {CID}: tavern_snug(12x10, party spawned) <-> crypt(16x12) seeded")


if __name__ == "__main__":
    main()
