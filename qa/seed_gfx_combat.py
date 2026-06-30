#!/usr/bin/env python3
"""seed_gfx_combat.py — deterministic hero-vs-goblin GRID combat on camp_gfxdemo01 (gfx P2).

The playable PoE2 3D-on-2D combat-demo seed. Pins the well-known campaign id the box
renderer hardcodes (paint_combat_v1.cs / paint_backdrop_p0.cs CID="camp_gfxdemo01"),
seats a fighter PC + a goblin on a 14x11 grid whose OBSTACLE cells match the crypt's
painted-prop occluders (pillarL(2,3) / pillarR(11,3) / sarcophagus(7,1)), places
hero@(6,6) / goblin@(9,5), and starts combat. After this, GET /combat-surface?campaign=
camp_gfxdemo01 returns real engine cells (positionAuthority='grid') for the READ-ONLY
renderer to consume + the M-B bridge routes movement around exactly the painted props.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/seed_gfx_combat.py <state_dir>

Engine = SOLE WRITER: writes only via server.* engine calls + save_campaign. Additive
(a new seed; touches no existing seed/contract).
"""
import json
import os
import sys


CID = "camp_gfxdemo01"
GRID_W, GRID_H = 14, 11
OBSTACLES = [[2, 3], [11, 3], [7, 1]]  # == paint_backdrop_p0.cs OCC_ cells == engine impassable
HERO_CELL = [6, 6]
GOBLIN_CELL = [9, 5]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_combat.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    # Pin the well-known id the box renderer hardcodes (create_campaign auto-ids, so build directly).
    server.save_campaign(Campaign(id=CID, title="GFX Combat Demo",
                                  summary="Playable PoE2 3D-on-2D combat demo on crypt_firelit_v2."))
    server.add_location(campaign_id=CID, name="Crypt", make_current=True,
                        description="A firelit stone crypt: pillars, a sarcophagus, braziers, deep shadows.")
    server.start_session(CID, title="GFX Combat Demo")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    hero_id = hero["id"]

    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]

    # Hero surprises the goblin so the PLAYER acts first (a surprised NPC skips its first turn);
    # leading NPC turns don't auto-resolve headless (no DM), so the demo drive loop needs a PC current.
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=OBSTACLES)
    server.place_combatant_at_coords(CID, hero_id, HERO_CELL[0], HERO_CELL[1])
    server.place_combatant_at_coords(CID, goblin_id, GOBLIN_CELL[0], GOBLIN_CELL[1])

    print(json.dumps({
        "campaign_id": CID, "hero_id": hero_id, "goblin_id": goblin_id,
        "grid": f"{GRID_W}x{GRID_H}", "obstacles": OBSTACLES,
        "hero_cell": HERO_CELL, "goblin_cell": GOBLIN_CELL,
    }))


if __name__ == "__main__":
    main()
