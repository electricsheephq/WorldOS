#!/usr/bin/env python3
"""seed_gfx_walkslice.py — WALKABLE-SLICE-V1 smoke fixture: a REST-mode crypt linked by a doorway to
a camp clearing, with a present NPC to talk to and a lurking goblin to fight. Reuses the crypt
``_author_room`` (door_cells) from seed_gfx_crypt_2room and the camp grid from seed_gfx_camp — ONE
grid source each. NO combat is started (rest mode), so the surface's ``stage`` carries the walk /
parley / door affordances the player consumes. ``start_combat`` (item 4) then opens the fight in place.

Engine = SOLE WRITER (writes only via server.* + save_campaign). Additive: a new seed/campaign, no
existing seed touched.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/seed_gfx_walkslice.py <state_dir>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CID = "walkslice_smoke01"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_walkslice.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(HERE, "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    import seed_gfx_crypt_2room as crypt  # noqa: PLC0415  (reuse _author_room + DOOR — ONE crypt grid source)
    import seed_gfx_camp as camp  # noqa: PLC0415  (reuse the camp_clearing_night grid — ONE camp source)

    server.save_campaign(Campaign(
        id=CID, title="Walkable Slice smoke",
        summary="Rest-mode crypt -> camp doorway + present NPC + a lurking goblin (WALKABLE-SLICE-V1).",
        is_sandbox=True,
    ))
    # Pin STABLE, meaningful location ids (add_location honors a caller-chosen id) so they double as the
    # plate-registry keys — the runtime plate swap keys on surface.location.id, and stable ids make the
    # plates_manifest.json durable + deterministic across re-seeds (add_location ids are otherwise random).
    crypt_loc = server.add_location(
        campaign_id=CID, name="Crypt Antechamber", location_id="crypt", make_current=True,
        description="A cold torchlit crypt with a doorway to the night camp beyond.")
    camp_loc = server.add_location(
        campaign_id=CID, name="Campfire Clearing", location_id="camp_clearing_night",
        description="A camp clearing under the night sky.", connections=[crypt_loc["id"]])

    c = server._require(CID)
    # crypt carries the door_cells scene_grid; wire crypt -> camp so cross_door(6,0) leads to the camp.
    crypt_grid = crypt._author_room(
        crypt_loc["id"], f"{CID}:crypt",
        "cold stone crypt antechamber, doorway to a night camp", crypt.STAIR_PROPS)
    # REST-mode spawns: WHERE the party + present NPC stand when the room renders inhabited (the stage
    # projects party onto spawns["party"], present NPCs onto spawns["npcs"]). Front-of-room floor cells,
    # clear of the back-half props (STAIR_PROPS at r<=4) and the (6,0) doorway.
    crypt_grid.spawns = {"party": [(6, 8), (5, 8)], "npcs": [(8, 6)]}
    c.locations[crypt_loc["id"]].scene_grid = crypt_grid
    if camp_loc["id"] not in c.locations[crypt_loc["id"]].connections:
        c.locations[crypt_loc["id"]].connections.append(camp_loc["id"])
    camp_grid = camp._build_camp_grid(CID, camp_loc["id"])
    camp_grid.spawns = {"party": [(8, 9), (7, 9)], "npcs": [(9, 7)]}
    c.locations[camp_loc["id"]].scene_grid = camp_grid
    server.save_campaign(c)
    server.start_session(CID, title="Walkable Slice Demo")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=crypt_loc["id"])
    hero_id = hero["id"]
    # a present NPC in the crypt (the talk target — rest_role "npc" on the stage).
    npc = server.create_character(
        campaign_id=CID, name="Mira the Keeper", kind="npc", race="human", class_name="commoner",
        level=1, apply_srd_defaults=True, add_to_party=False, location_id=crypt_loc["id"])
    npc_id = npc["id"]
    # a lurking goblin in the CAMP (the foe for start_combat "start a fight in place" — the milestone's
    # fight happens after crossing INTO camp). spawn_monster has no location_id param, so anchor it at the
    # camp directly (engine snapshot, still under save_campaign).
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]
    c = server._require(CID)
    c.characters[goblin_id].location_id = camp_loc["id"]
    # TEST-ONLY force_hit (double-guarded by is_sandbox + WORLDOS_COMBAT_TEST env, per
    # _combat_test_mode_enabled) so the smoke's attack step lands deterministically once the fighter is
    # adjacent — same discipline as seed_gfx_camp_smoke. Damage is still rolled normally (hp really drops).
    c.house_rules.force_hit = True
    server.save_campaign(c)

    print(json.dumps({
        "campaign_id": CID, "crypt_id": crypt_loc["id"], "camp_id": camp_loc["id"],
        "door_cell": crypt.DOOR, "hero_id": hero_id, "npc_id": npc_id, "goblin_id": goblin_id,
        "crypt_connections": list(server._require(CID).locations[crypt_loc["id"]].connections),
    }))


if __name__ == "__main__":
    main()
