#!/usr/bin/env python3
"""seed_gfx_hell.py — "the depths of hell": a dramatic HELLISH CAVERN room (the owner's north-star
example of the world's range). Tests the room-gen pipeline on an EXTREME biome (lava / black obsidian /
fire) beyond the proven cool-stone interiors.

Same one-source discipline: one authored 14x11 scene_grid drives BOTH the carved greybox -> img2img
painted hell room (recipe 'hell') AND the combat pathing (impassable_cells: perimeter rock + every prop,
incl. the LAVA channel which is an impassable hazard a token can never cross). Tall occluder props
(obsidian spires) stay in the BACK HALF (r<=5, the near-zone occlusion rule).

  WORLDOS_STATE_DIR=<sd> uv run --directory servers/engine python "$PWD/qa/seed_gfx_hell.py" <sd>
Then: export_scene_grid.py -> build_room_greybox.cs -> generate_room.py --room hell -> deploy_room.sh.
Engine = SOLE WRITER. Additive (new seed/campaign).
"""
import json
import os
import sys

CID = "camp_gfxhell01"
GRID_W, GRID_H = 14, 11
SPIRES = [[3, 3], [10, 3]]      # jagged obsidian spires (tall occluders, back half)
BONES = [[6, 4]]                # a charred bone pile (mid-room focal obstacle)
LAVA = [[9, 5], [9, 6]]         # a molten lava channel — impassable hazard (routes around, never crosses)
OBSTACLES = SPIRES + BONES + LAVA
HERO_CELL = [6, 8]
GOBLIN_CELL = [6, 5]


def _author_hell_grid(server, cid: str):
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )
    cols, rows = GRID_W, GRID_H
    cells: list = []
    for c in range(cols):
        cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        cells.append(SceneCell(c=c, r=rows - 1, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))

    props: list = []

    def _prop(pid: str, kind: str, footprint: list, band: str, sil: str) -> None:
        props.append(SceneProp(id=pid, kind=kind, cells=[(c, r) for (c, r) in footprint],
                               anchor_cell=(footprint[0][0], footprint[0][1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    for i, s in enumerate(SPIRES):
        _prop(f"spire_{i}", "obsidian_spire", [s], "tall", "jagged black obsidian spire")
    _prop("bones", "bone_pile", BONES, "mid", "charred pile of bones and ash")
    _prop("lava", "lava_channel", LAVA, "low", "molten lava channel, glowing orange")

    grid = SceneGrid(
        scene_id=f"{cid}:hell", location_id="", kind="dungeon",
        biome="the depths of hell, volcanic lava cavern, black obsidian, fire and ash",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=140, key_color="#ff6a2b", ambient_color="#2a1416",
                               mood="hellish lava glow, hot orange key, cool ash shadows"),
    )
    grid.art.layout_hash = _layout_hash(grid)
    c = server._require(cid)
    loc = c.locations.get(c.current_location_id)
    grid.location_id = loc.id
    loc.scene_grid = grid
    server.save_campaign(c)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_hell.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Hell Demo",
                                  summary="The depths of hell — a lava cavern (the world's extreme range)."))
    server.add_location(campaign_id=CID, name="The Depths of Hell", make_current=True,
                        description="A cavern of black obsidian and molten lava, wreathed in fire and ash.")
    grid = _author_hell_grid(server, CID)
    server.start_session(CID, title="GFX Hell Demo")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    hero_id = hero["id"]
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]
    impassable = sg.impassable_cells(grid, GRID_W, GRID_H)
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=impassable)
    server.place_combatant_at_coords(CID, hero_id, HERO_CELL[0], HERO_CELL[1])
    server.place_combatant_at_coords(CID, goblin_id, GOBLIN_CELL[0], GOBLIN_CELL[1])

    print(json.dumps({
        "campaign_id": CID, "hero_id": hero_id, "goblin_id": goblin_id,
        "grid": f"{GRID_W}x{GRID_H}", "obstacles": OBSTACLES, "impassable_total": len(impassable),
    }))


if __name__ == "__main__":
    main()
