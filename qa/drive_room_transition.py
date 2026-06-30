#!/usr/bin/env python3
"""drive_room_transition.py — the LIVE door-crossing transition (occlusion/pathing Sprint 3, M-E scene gate).

Drives the party from the STAIR room-unit to the TOMB room-unit of the 2-room crypt
(qa/seed_gfx_crypt_2room.py):

  win the stair fight -> end_combat -> travel_to(Crypt Tomb)  [engine co-locates the whole party]
  -> a NEW fight in the tomb (a skeleton ambush) -> place the cast on the tomb's OWN authored pathing.

The renderer is then pointed at the tomb plate (deploy_room.sh crypt2room_tomb.png) and re-run, so the
SAME hero renders in the tomb unit. transition_00_stair.png vs transition_01_tomb.png IS the live room
transition — one campaign, two linked room-units, the party crossing the shared (6,0) doorway.

  WORLDOS_STATE_DIR=<sd> uv run --directory servers/engine python "$PWD/qa/drive_room_transition.py" <sd>

Engine = SOLE WRITER (mutates only via server.*). One-source pathing: the tomb obstacles are the tomb
scene_grid's impassable_cells (NOT re-derived from pixels).
"""
import json
import os
import sys

CID = "camp_gfxcrypt2room01"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: drive_room_transition.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    sd = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = sd
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from scene_grid import impassable_cells  # noqa: PLC0415

    c = server._require(CID)
    tomb = next(loc for loc in c.locations.values() if loc.name == "Crypt Tomb")
    tomb_id = tomb.id
    hero_id = next(cid for cid, ch in c.characters.items() if getattr(ch, "kind", "") == "player")

    # 1) the stair fight is won -> end combat (so the party can travel).
    if c.combat.active:
        server.end_combat(CID, resolution="The party cuts down the goblin in the stair hall.")

    # 2) CROSS THE DOORWAY via the authored door primitive: cross_door validates (6,0) is a door cell
    #    of the current (stair) room + finds the linked unit from Location.connections, then travel_to
    #    co-locates the whole party. (6,0) = the shared back-wall doorway authored in seed_gfx_crypt_2room.
    tr = server.cross_door(CID, 6, 0)

    # 3) a NEW encounter in the tomb: a skeleton rises from the sarcophagus -> a fresh fight.
    sk = server.spawn_monster(CID, name="Skeleton", count=1)
    skel_id = sk["spawned"][0]["id"]
    server.start_combat(CID, [hero_id, skel_id], surpriser_ids=[skel_id])

    # 4) place the cast on the TOMB's OWN authored pathing (one source: the tomb scene_grid).
    c = server._require(CID)
    tomb = c.locations[tomb_id]
    imp = impassable_cells(tomb.scene_grid, 14, 11)
    server.set_grid(CID, width=14, height=11, obstacles=imp)
    server.place_combatant_at_coords(CID, hero_id, 6, 9)   # hero enters at the tomb mouth
    server.place_combatant_at_coords(CID, skel_id, 6, 5)   # skeleton mid-tomb

    print(json.dumps({
        "crossed_to": tomb.name, "current_location": c.current_location_id, "is_tomb": c.current_location_id == tomb_id,
        "hero": hero_id, "skeleton": skel_id, "tomb_impassable": len(imp),
        "party_relocated": tr.get("party_relocated"), "combat_active": c.combat.active,
    }))


if __name__ == "__main__":
    main()
