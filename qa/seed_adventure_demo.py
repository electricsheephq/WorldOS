#!/usr/bin/env python3
"""Seed the Diablo-1 ADVENTURE demo: a complete quest-loop world from certified rooms (A-series A0.2).

The ONE-CALL fixture both adventure-eval lanes (text-arc + GUI-walked) run against — a five-room
quest loop wired from EXISTING certified geometries (qa/room_geometries/), seeded through the SAME
build_grid_from_geometry + full static gate the dwing fixture uses (qa/seed_gfx_dwing_sandbox.py):

  camp_clearing (hub, party start)
     |  door [8,0] <-> tavern_snug [5,0]  (Keeper Maera, the quest giver)
     |                    tavern_snug [11,4] <-> shop [12,4]  (Merchant Oswin)
     |  door [0,6] <-> crypt [7,0]         (2-3 goblins — the dungeon)
                          crypt [15,5]  <-> throne_hall [8,11]  (the Goblin Boss)

No declared-but-unwired seams remain (ALLOWED_UNWIRED is empty): the throne hall's side passage (15,6)
opened onto the painted arcade wall and was retired to wall on 2026-09-02, and the shop's seam moved
from the back-wall cell (6,0) to (12,4) the same day
(qa/reauthor_legacy_room.md): the shop plate paints a shelf unit across (6,0)'s landing and its ONLY
painted opening is the archway beside (12,5), so (6,0) was retired to plain wall rather than left as a
door into a bookshelf.

camp_clearing has no authored doorway in the certified greybox (an outdoor clearing), so this fixture
carries its own qa/room_geometries/camp_clearing_geometry.json — the true-greybox camp plus the two
hub door cells and one ruin-rubble prop closing the ruins' orphan pocket (11,10). Every other room is
an unmodified certified geometry.

Usage (set WORLDOS_STATE_DIR to override the state dir; default current_room = camp_clearing):
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python /ABS/PATH/WorldOS/qa/seed_adventure_demo.py <state_dir> [current_room]
  # in the sandbox lane:
  WORLDOS_PLAYER_APP=/tmp/WorldOSPlayer_hotload.app qa/qa_sandbox.py up --run adventure \
      --campaign adventure_demo_v1 \
      --seed-cmd "uv run --directory servers/engine python /ABS/PATH/WorldOS/qa/seed_adventure_demo.py {state}"

Prints the campaign_id on the LAST line (the contract other harnesses read).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CID = "adventure_demo_v1"
GEO = HERE / "room_geometries"

# (location_id, geometry_file, [(door_cell, to_location_id), ...]) — door_cells[i] <-> connections[i]
# in PAIR ORDER (the cross_door contract). camp_clearing is the hub + the party's start room.
ROOMS = [
    ("camp_clearing", "camp_clearing_geometry.json", [([8, 0], "tavern_snug"), ([0, 6], "crypt")]),
    ("tavern_snug", "tavern_snug_geometry.json", [([5, 0], "camp_clearing"), ([11, 4], "shop")]),
    ("shop", "shop_geometry.json", [([12, 4], "tavern_snug")]),
    ("crypt", "crypt_v36_geometry.json", [([7, 0], "camp_clearing"), ([15, 5], "throne_hall")]),
    ("throne_hall", "throne_hall_geometry.json", [([8, 11], "crypt")]),
]
# Every authored door is wired (qa/reauthor_legacy_room.md, 2026-09-02): the two cells that used to sit
# here — the shop's back-wall (6,0) and the hall's side passage (15,6) — were retired to WALL because
# neither plate paints an opening there, so no plate paints an arch that does nothing.
ALLOWED_UNWIRED: set = set()

# The dungeon cast (crypt) and the boss room (throne_hall). SRD names: "Goblin" -> Goblin Warrior.
N_GOBLINS = 3
BOSS_NAME = "Goblin Boss"


def open_floor(geo: dict, door_cells: list, cell_verdicts: dict | None = None) -> list:
    """Sorted open interior floor cells clear of walls/props and the door landing ring — the same
    walkable truth choose_spawns uses, so every NPC/monster placement is walkable by construction.

    When `cell_verdicts` (a paint-coherence report, #1647) is supplied, the list is further restricted to
    cells the player SEES as OPEN floor, via the SAME `_prefer_open` discipline the party spawn uses — so a
    staged NPC/monster is never placed on a grid-open cell that sits under painted furniture (Keeper Maera
    in the snug, the boss on a throne dais). Absent verdicts ⇒ geometry-only, byte-identical to before."""
    from seed_gfx_town import _prefer_open  # noqa: PLC0415  (shared open-preference + fallback discipline)

    cols, rows = int(geo["cols"]), int(geo["rows"])
    doors = {tuple(d) for d in door_cells}
    props = {tuple(c) for p in geo.get("props", []) if p.get("kind") != "wall_run"
             for c in p.get("cells", [])}
    walls = ({tuple(c) for c in geo.get("walls", [])} | props) - doors
    ring = {(dc + dx, dr + dy) for (dc, dr) in doors for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    cells = [(c, r) for r in range(1, rows - 1) for c in range(1, cols - 1)
             if (c, r) not in walls and (c, r) not in doors and (c, r) not in ring]
    if cell_verdicts is not None and cells:
        cells = _prefer_open(cells, cell_verdicts)
    return cells


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_adventure_demo.py <state_dir> [current_room]", file=sys.stderr)
        sys.exit(2)
    current = sys.argv[2] if len(sys.argv) > 2 else "camp_clearing"
    room_ids = {r[0] for r in ROOMS}
    if current not in room_ids:
        print(f"unknown current_room {current!r} (rooms: {sorted(room_ids)})", file=sys.stderr)
        sys.exit(2)
    os.environ["WORLDOS_STATE_DIR"] = sys.argv[1]
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent / "servers" / "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    from seed_gfx_town import DEFAULT_COHERENCE_DIR, build_grid_from_geometry, load_cell_verdicts  # noqa: PLC0415
    from walk_static import check_geometry, validate_seed_doors, validate_world  # noqa: PLC0415

    # --- full static gate (validate_world + validate_seed_doors + check_geometry) BEFORE seeding ---
    rooms_spec = [(lid, [(cell, to) for cell, to in doors]) for lid, _g, doors in ROOMS]
    geometries = {lid: json.loads((GEO / geofile).read_text()) for lid, geofile, _d in ROOMS}
    fails = validate_world(rooms_spec)
    fails += validate_seed_doors(rooms_spec, geometries, allowed_unwired=ALLOWED_UNWIRED)
    for lid, geofile, _doors in ROOMS:
        fails += check_geometry(geofile, geometries[lid])
    if fails:
        for f in fails:
            print(f"[adventure_demo] STATIC GATE RED: {f}", file=sys.stderr)
        sys.exit(1)

    # --- world graph -------------------------------------------------------------------------------
    server.save_campaign(Campaign(
        id=CID, title="The Crypt Below (Diablo-1 adventure demo)",
        summary="A five-room quest loop: camp hub <-> tavern (Keeper Maera) <-> shop, and camp <-> "
                "crypt (goblins) <-> throne hall (the Goblin Boss). The A-series adventure-eval fixture.",
        is_sandbox=True,
    ))
    handle = {}
    for lid, _g, _d in ROOMS:
        handle[lid] = server.add_location(
            campaign_id=CID, name=lid.replace("_", " ").title(), location_id=lid,
            make_current=(lid == current),
            description=f"The {lid.replace('_', ' ')} (adventure-demo fixture).")
    c = server._require(CID)
    for lid, _geofile, doors in ROOMS:
        eid = handle[lid]["id"]
        c.locations[eid].connections = [handle[to]["id"] for _cell, to in doors]
        door_pairs = [{"cell": cell, "to": to} for cell, to in doors]
        c.locations[eid].scene_grid = build_grid_from_geometry(
            geometries[lid], eid, CID, lid, door_pairs,
            coherence_reports_dir=DEFAULT_COHERENCE_DIR)  # #1647: party spawns on OPEN floor, not painted furniture
    server.save_campaign(c)
    server.start_session(CID, title="The Crypt Below")

    # --- party PC (spawns at the current room, default camp_clearing) ------------------------------
    server.create_character(
        campaign_id=CID, name="Aidan", kind="player", race="human", class_name="fighter", level=3,
        abilities={"strength": 15, "dexterity": 13, "constitution": 14,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=handle[current]["id"])

    # --- NPCs: Keeper Maera (quest giver) in the tavern; Merchant Oswin in the shop -----------------
    # #1647: stage NPCs on cells the player SEES as OPEN, same coherence discipline as the party spawn.
    tavern_open = open_floor(geometries["tavern_snug"], [d for d, _t in ROOMS[1][2]],
                             cell_verdicts=load_cell_verdicts(DEFAULT_COHERENCE_DIR, "tavern_snug"))
    shop_open = open_floor(geometries["shop"], [d for d, _t in ROOMS[2][2]],
                           cell_verdicts=load_cell_verdicts(DEFAULT_COHERENCE_DIR, "shop"))
    maera = server.create_character(
        campaign_id=CID, name="Keeper Maera", kind="npc", race="human", location_id=handle["tavern_snug"]["id"],
        biography="The keeper of the snug tavern by the camp — she has watched the crypt's goblins "
                  "creep closer and holds a reward for whoever clears them out.", add_to_party=False)
    oswin = server.create_character(
        campaign_id=CID, name="Merchant Oswin", kind="npc", race="human", location_id=handle["shop"]["id"],
        biography="A travelling merchant who set up his stall beside the tavern.", add_to_party=False)

    # --- boss + dungeon goblins (spawn_monster mints them; anchor + place them afterward) -----------
    boss = server.spawn_monster(campaign_id=CID, name=BOSS_NAME, count=1)
    if "error" in boss:
        print(f"[adventure_demo] boss spawn failed: {boss}", file=sys.stderr)
        sys.exit(1)
    goblins = server.spawn_monster(campaign_id=CID, name="Goblin", count=N_GOBLINS)
    if "error" in goblins:
        print(f"[adventure_demo] goblin spawn failed: {goblins}", file=sys.stderr)
        sys.exit(1)

    # spawn_monster leaves monsters location-less; anchor the boss in the throne hall and the goblins
    # in the crypt, each on an open floor cell (walkable by construction) via the stage_cell field.
    crypt_open = open_floor(geometries["crypt"], [d for d, _t in ROOMS[3][2]],
                            cell_verdicts=load_cell_verdicts(DEFAULT_COHERENCE_DIR, "crypt"))
    throne_open = open_floor(geometries["throne_hall"], [d for d, _t in ROOMS[4][2]],
                             cell_verdicts=load_cell_verdicts(DEFAULT_COHERENCE_DIR, "throne_hall"))
    c = server._require(CID)
    c.characters[maera["id"]].stage_cell = tavern_open[len(tavern_open) // 2]
    c.characters[oswin["id"]].stage_cell = shop_open[len(shop_open) // 2]
    c.characters[boss["spawned"][0]["id"]].location_id = handle["throne_hall"]["id"]
    c.characters[boss["spawned"][0]["id"]].stage_cell = throne_open[len(throne_open) // 2]
    for i, g in enumerate(goblins["spawned"]):
        c.characters[g["id"]].location_id = handle["crypt"]["id"]
        # spread the goblins across the crypt's open floor (distinct, walkable cells)
        c.characters[g["id"]].stage_cell = crypt_open[(i * len(crypt_open)) // len(goblins["spawned"])]
    server.save_campaign(c)

    # --- the quest: giver = Keeper Maera, anchored to the crypt, four objectives -------------------
    reward = "Ring of Protection"
    server.add_item(CID, maera["id"], item_name=reward)  # staged on the giver, handed over on return
    server.add_quest(
        CID, "The Crypt Below",
        description=f"Goblins have overrun the old crypt beneath the camp and a boss musters them for "
                    f"worse. Keeper Maera will part with a {reward} to whoever clears them out.",
        giver_id=maera["id"], location_id=handle["crypt"]["id"],
        objectives=["Speak with Keeper Maera",
                    "Clear the crypt of goblins",
                    "Slay the goblin boss",
                    "Return to Maera for the reward"])

    print(f"[adventure_demo] seeded: 5 rooms, hub=camp_clearing, current={current}; "
          f"NPCs Keeper Maera(tavern)+Merchant Oswin(shop); {BOSS_NAME}(throne_hall)+{N_GOBLINS} "
          f"goblins(crypt); quest 'The Crypt Below' (4 objectives)")
    print(CID)


if __name__ == "__main__":
    main()
