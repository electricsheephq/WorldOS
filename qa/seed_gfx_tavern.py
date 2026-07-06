#!/usr/bin/env python3
"""seed_gfx_tavern.py — a WOOD tavern, proving the room-gen MATERIAL axis (gfx M-E breadth).

Crypt / church / throne hall are all STONE. A tavern is WOOD — so the greybox texture switches to wood
planks (warm grain + horizontal plank coursing) via the `material` hint that export_scene_grid.py derives
from the scene_grid biome ("wooden tavern" -> material=wood). Same one-source authored-pathing discipline
(impassable = perimeter walls + every prop). DISTINCT layout — a bar counter, scattered tables, timber
support posts — so it reads as a tavern, not a re-skinned stone room.

  # uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_tavern.py" <state_dir>

Then: export_scene_grid.py (-> material=wood) -> build_room_greybox.cs (wood planks) -> generate_room.py
--room tavern -> deploy. Engine = SOLE WRITER. Additive (a new seed/campaign).
"""
import json
import os
import sys


CID = "camp_gfxtavern01"
GRID_W, GRID_H = 14, 11
BAR = [[2, 1], [3, 1], [4, 1]]          # a bar counter along the back-left
TABLES = [[7, 5], [10, 6]]              # scattered tables
POSTS = [[4, 4], [9, 4]]               # timber support posts
OBSTACLES = BAR + TABLES + POSTS
HERO_CELL = [6, 9]    # by the door
GOBLIN_CELL = [8, 6]  # mid-room


def _author_tavern_grid(server, cid: str):
    """Attach a 14x11 WOOD tavern scene_grid whose PROP footprints ARE the obstacles (one source)."""
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

    _prop("bar", "bar", BAR, "mid", "long timber bar counter")
    for i, t in enumerate(TABLES):
        _prop(f"table_{i}", "table", [t], "low", "round wooden tavern table")
    for i, p in enumerate(POSTS):
        _prop(f"post_{i}", "pillar", [p], "tall", "heavy timber support post")

    grid = SceneGrid(
        scene_id=f"{cid}:tavern", location_id="", kind="tavern",
        biome="warm wooden tavern, timber posts and plank floor, hearth-lit",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=210, key_color="#f0a64a", ambient_color="#241a14",
                               mood="cosy tavern, warm hearth + lantern glow, soft amber shadow"),
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
        print("usage: seed_gfx_tavern.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Tavern Demo",
                                  summary="A WOOD room — a timber tavern (material-axis breadth)."))
    server.add_location(campaign_id=CID, name="The Wooden Tavern", make_current=True,
                        description="A warm timber tavern: a bar counter, tables, plank floor, a hearth.")
    grid = _author_tavern_grid(server, CID)
    server.start_session(CID, title="GFX Tavern Demo")

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

    impassable = sg.impassable_cells(grid, GRID_W, GRID_H)
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=impassable)
    server.place_combatant_at_coords(CID, hero_id, HERO_CELL[0], HERO_CELL[1])
    server.place_combatant_at_coords(CID, goblin_id, GOBLIN_CELL[0], GOBLIN_CELL[1])

    print(json.dumps({
        "campaign_id": CID, "hero_id": hero_id, "goblin_id": goblin_id,
        "grid": f"{GRID_W}x{GRID_H}", "prop_obstacles": OBSTACLES, "impassable_total": len(impassable),
        "hero_cell": HERO_CELL, "goblin_cell": GOBLIN_CELL,
    }))


if __name__ == "__main__":
    main()
