#!/usr/bin/env python3
"""seed_gfx_crypt_2room.py — a MULTI-ROOM-UNIT crypt (occlusion/pathing Sprint 3, #1217).

The owner's "crunch" example: "the crypt got a staircase, but then you have the tomb really crunched in
there." The fix is NOT a bigger grid (the 14x11 contract is fixed, and research shows it's already at the
D&D floor) — it's COMPOSITION: author the crypt as TWO linked room-units, each its own spacious 14x11
scene_grid + greybox + painted plate, glued at a DOOR cell:

  [ Crypt Stair ]  --shared (6,0) back-wall doorway-->  [ Crypt Tomb ]

Both units link at the SAME door cell (6,0) — the back (+z) wall, which the cut-near occlusion KEEPS
(the near front/left walls are cut, so a VISIBLE doorway must sit on a kept wall). The party crosses
(6,0) in one unit and re-enters just inside (6,1) of the other.

- Each unit is a full room (own scene_grid, own greybox -> img2img plate), so neither is crunched.
- The units are linked in the engine by Location.connections (the existing adjacency model) — the door
  cell on each grid is where the party crosses; crossing swaps the active plate (the existing
  _active_combat.txt room-agnostic renderer). This PR ships the AUTHORING + render foundation; the live
  door-crossing scene-swap is the follow-up increment.
- Both units honor the occlusion rules: cut-near walls + no ceiling (build_room_greybox.cs), and tall
  occluder props stay in the BACK HALF (r<=5) so the near third is clear.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_crypt_2room.py" <state_dir>
Then per unit: export_scene_grid.py <cid> <out> --location <loc_id> -> build_room_greybox.cs -> generate_room.py.

Engine = SOLE WRITER (writes only via server.* + save_campaign). Additive (a new seed/campaign).
"""
import json
import os
import sys


CID = "camp_gfxcrypt2room01"
GRID_W, GRID_H = 14, 11
DOOR = [6, 0]  # back-wall-center doorway, shared link between the two units (a gap in the FAR wall)

# Each unit: spacious, props in the BACK HALF (r<=5), clear of the door zone (DOOR + Chebyshev-1 ring).
STAIR_PROPS = [
    ("staircase", "descending_stairs", [[3, 2], [3, 3]], "tall", "broad descending stone staircase, worn steps"),
    ("pillar_r", "stone_pillar", [[10, 3]], "tall", "ancient cracked stone pillar"),
]
STAIR_HERO, STAIR_GOBLIN = [6, 8], [8, 5]

TOMB_PROPS = [
    ("sarcophagus", "sarcophagus", [[6, 3], [7, 3]], "tall", "carved stone sarcophagus, lid ajar"),
    ("pillar_l", "stone_pillar", [[3, 4]], "tall", "ancient mossy stone pillar"),
    ("pillar_r", "stone_pillar", [[10, 4]], "tall", "ancient cracked stone pillar"),
]


def _author_room(loc_id: str, scene_id: str, biome: str, props_spec: list) -> object:
    """Author a 14x11 scene_grid with a back-center DOORWAY (gap in the far wall) + the given props.
    Props are obstacles by construction; the door cell is walkable (it is the link to the other unit)."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cols, rows = GRID_W, GRID_H
    cells: list = []
    # perimeter walls — EXCEPT the door cell, which is a walkable gap (the doorway to the other unit).
    for c in range(cols):
        if [c, 0] == DOOR:
            cells.append(SceneCell(c=c, r=0, type="door", walkable=True))  # the doorway opening
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
        cells=cells, props=props,
        door_cells=[(DOOR[0], DOOR[1])],  # the doorway cell (also the cross-unit link point)
        lighting=SceneLighting(key_dir_deg=150, key_color="#e8b878", ambient_color="#241c30",
                               mood="cold stone crypt, single torch, deep blue-violet shadows"),
    )
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_crypt_2room.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from scene_grid import impassable_cells  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Crypt (2 room-units)",
                                  summary="A crypt composed of two linked room-units: a stair hall + a tomb."))
    # unit A = the stair hall (current); unit B = the tomb, wired adjacent via connections.
    stair = server.add_location(campaign_id=CID, name="Crypt Stair", make_current=True,
                                description="A descending stone staircase; a doorway at the back leads deeper.")
    tomb = server.add_location(campaign_id=CID, name="Crypt Tomb",
                               description="A tomb chamber: a carved sarcophagus flanked by pillars.",
                               connections=[stair["id"]])

    c = server._require(CID)
    c.locations[stair["id"]].scene_grid = _author_room(
        stair["id"], f"{CID}:stair", "cold stone crypt stair hall, descending steps, torchlit", STAIR_PROPS)
    c.locations[tomb["id"]].scene_grid = _author_room(
        tomb["id"], f"{CID}:tomb", "cold stone crypt tomb chamber, sarcophagus, torchlit", TOMB_PROPS)
    server.save_campaign(c)
    server.start_session(CID, title="GFX Crypt 2-room Demo")

    # combat seeded in the STAIR unit (the entrance) for the first render.
    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    hero_id = hero["id"]
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]
    stair_grid = c.locations[stair["id"]].scene_grid
    impassable = impassable_cells(stair_grid, GRID_W, GRID_H)
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=impassable)
    server.place_combatant_at_coords(CID, hero_id, STAIR_HERO[0], STAIR_HERO[1])
    server.place_combatant_at_coords(CID, goblin_id, STAIR_GOBLIN[0], STAIR_GOBLIN[1])

    print(json.dumps({
        "campaign_id": CID, "units": {"stair": stair["id"], "tomb": tomb["id"]},
        "door_cell": DOOR, "connections_wired": tomb["id"] in c.locations[stair["id"]].connections,
        "hero_id": hero_id, "goblin_id": goblin_id, "stair_impassable": len(impassable),
    }))


if __name__ == "__main__":
    main()
