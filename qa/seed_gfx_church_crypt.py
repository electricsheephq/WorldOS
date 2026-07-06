#!/usr/bin/env python3
"""seed_gfx_church_crypt.py — a SECOND multi-room-unit space, of DIFFERENT room types, to prove the
composition + live transition (Sprint 3 / M-E) generalize beyond the crypt-only pair.

A thematic descent: a cathedral NAVE (church recipe) linked to its CRYPT UNDERCROFT (crypt recipe),
glued at the shared (6,0) back-wall doorway — the party crosses from the nave DOWN into the crypt.

  [ Cathedral Nave ]  --shared (6,0) doorway-->  [ Crypt Undercroft ]
     church recipe                                   crypt recipe

Same one-source discipline as seed_gfx_crypt_2room.py (each unit's props == its combat obstacles via
impassable_cells; both honor the occlusion near-zone rule — tall props in the back half r<=5). Distinct
ROOM TYPES exercise different recipes/obstacles/painting, so this is a generalization + bug-finding pass.

  WORLDOS_STATE_DIR=<sd> uv run --directory servers/engine python "$PWD/qa/seed_gfx_church_crypt.py" <sd>
Then per unit: export_scene_grid.py <cid> <out> --location <loc_id> -> build_room_greybox.cs ->
generate_room.py --room {church|crypt} ; cross via drive_room_transition-style cross_door(6,0).

Engine = SOLE WRITER. Additive (a new seed/campaign).
"""
import json
import os
import sys

CID = "camp_gfxchurchcrypt01"
GRID_W, GRID_H = 14, 11
DOOR = [6, 0]  # shared back-wall doorway between the nave and the undercroft

# Nave (church recipe): two flanking column rows in the BACK HALF (near-zone rule) + a font off to one side.
NAVE_PROPS = [
    ("column_0", "stone_pillar", [[3, 3]], "tall", "tall fluted cathedral column"),
    ("column_1", "stone_pillar", [[10, 3]], "tall", "tall fluted cathedral column"),
    ("column_2", "stone_pillar", [[3, 5]], "tall", "tall fluted cathedral column"),
    ("column_3", "stone_pillar", [[10, 5]], "tall", "tall fluted cathedral column"),
    ("font", "altar", [[10, 2]], "mid", "carved stone font on a plinth"),
]
NAVE_HERO, NAVE_GOBLIN = [6, 8], [7, 5]

# Crypt undercroft (crypt recipe): sarcophagus + flanking pillars, back half.
UNDERCROFT_PROPS = [
    ("sarcophagus", "sarcophagus", [[6, 3], [7, 3]], "tall", "carved stone sarcophagus, lid ajar"),
    ("pillar_l", "stone_pillar", [[3, 4]], "tall", "ancient cracked stone pillar"),
    ("pillar_r", "stone_pillar", [[10, 4]], "tall", "ancient mossy stone pillar"),
]


def _author_room(loc_id: str, scene_id: str, biome: str, props_spec: list) -> object:
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )
    cols, rows = GRID_W, GRID_H
    cells: list = []
    for c in range(cols):
        if [c, 0] == DOOR:
            cells.append(SceneCell(c=c, r=0, type="door", walkable=True))
        else:
            cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        cells.append(SceneCell(c=c, r=rows - 1, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))
    props: list = []
    for pid, kind, footprint, band, sil in props_spec:
        props.append(SceneProp(id=pid, kind=kind, cells=[(c, r) for (c, r) in footprint],
                               anchor_cell=(footprint[0][0], footprint[0][1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))
    grid = SceneGrid(
        scene_id=scene_id, location_id=loc_id, kind="dungeon", biome=biome,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props, door_cells=[(DOOR[0], DOOR[1])],
        lighting=SceneLighting(key_dir_deg=155, key_color="#eccf94", ambient_color="#26203a",
                               mood="solemn sacred stone, candlelight, deep cool shadows"),
    )
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_church_crypt.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    sd = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = sd
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from scene_grid import impassable_cells  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Cathedral + Crypt (2 room-units)",
                                  summary="A cathedral nave linked to its crypt undercroft — two room types."))
    nave = server.add_location(campaign_id=CID, name="Cathedral Nave", make_current=True,
                               description="A vaulted nave; a doorway at the back descends to the crypt.")
    crypt = server.add_location(campaign_id=CID, name="Crypt Undercroft",
                                description="The crypt beneath the cathedral: a sarcophagus among pillars.",
                                connections=[nave["id"]])
    c = server._require(CID)
    c.locations[nave["id"]].scene_grid = _author_room(
        nave["id"], f"{CID}:nave", "vaulted stone cathedral nave, stained-glass, candlelit", NAVE_PROPS)
    c.locations[crypt["id"]].scene_grid = _author_room(
        crypt["id"], f"{CID}:undercroft", "cold stone crypt undercroft, torchlit", UNDERCROFT_PROPS)
    server.save_campaign(c)
    server.start_session(CID, title="GFX Cathedral+Crypt Demo")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    hero_id = hero["id"]
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]
    nave_grid = c.locations[nave["id"]].scene_grid
    imp = impassable_cells(nave_grid, GRID_W, GRID_H)
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=imp)
    server.place_combatant_at_coords(CID, hero_id, NAVE_HERO[0], NAVE_HERO[1])
    server.place_combatant_at_coords(CID, goblin_id, NAVE_GOBLIN[0], NAVE_GOBLIN[1])

    print(json.dumps({
        "campaign_id": CID, "units": {"nave": nave["id"], "crypt": crypt["id"]},
        "door_cell": DOOR, "connections_wired": crypt["id"] in c.locations[nave["id"]].connections,
        "hero_id": hero_id, "goblin_id": goblin_id, "nave_impassable": len(imp),
    }))


if __name__ == "__main__":
    main()
