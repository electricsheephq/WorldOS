#!/usr/bin/env python3
"""seed_gfx_church.py — a SECOND distinct playable room (a church nave) proving the carved room-gen
pipeline scales to a world (gfx M-E "scale" gate: >=2 distinct rooms, each authored + carved + playable).

Same one-source discipline as the crypt: one authored 14x11 scene_grid drives BOTH the carved greybox
(-> img2img painted church) AND the combat pathing — the combat obstacles are the FULL scene-grid
impassable set (perimeter WALLS + every prop), via impassable_cells(), so a token can never stop on a
cell painted as wall or furniture. Distinct LAYOUT from the crypt — a long nave flanked by two rows of
columns, an altar at the apse — so the painted room reads as a church, not a re-skinned crypt.

  # NOTE: uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_church.py" <state_dir>

Then: export_scene_grid.py -> build_room_greybox.cs -> generate_room.py --room church -> deploy_room.sh.
Engine = SOLE WRITER (writes only via server.* + save_campaign). Additive (a new seed/campaign).
"""
import json
import os
import sys


CID = "camp_gfxchurch01"
GRID_W, GRID_H = 14, 11
# a church nave: two rows of flanking COLUMNS + a 2-cell ALTAR at the back-center apse == the obstacles.
# NEAR-ZONE OCCLUSION RULE (the occlusion-sprint dev-start, owner 2026-07-01): tall occluder props
# (columns/pillars) stay in the BACK HALF (r <= 5 on an 11-row grid) so the camera-near third (r>=6,
# where actors enter + fight) is never occluded by a foreground column. The cut-near WALL (build_room_
# greybox.cs cutNear) handles the near walls; this keeps tall INTERIOR props out of the near band too
# (the per-prop "see-through on approach" fade is the deferred Phase-2 layer). So the front colonnade
# pair is pulled from r=7 to r=5 — a 2-row colonnade in the mid/back nave, open near approach.
COLUMNS = [[3, 3], [10, 3], [3, 5], [10, 5]]
ALTAR = [[6, 1], [7, 1]]
OBSTACLES = COLUMNS + ALTAR
HERO_CELL = [6, 8]   # entrance end of the nave
GOBLIN_CELL = [7, 5]  # mid-nave


def _author_church_grid(server, cid: str) -> None:
    """Attach a 14x11 church scene_grid whose PROP footprints ARE the combat OBSTACLES (one source)."""
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

    for i, col in enumerate(COLUMNS):
        _prop(f"column_{i}", "stone_pillar", [col], "tall", "tall fluted cathedral column")
    _prop("altar", "altar", ALTAR, "mid", "carved stone altar with candles")

    grid = SceneGrid(
        scene_id=f"{cid}:church", location_id="", kind="dungeon",
        biome="vaulted stone cathedral nave, candlelit, stained-glass glow",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=160, key_color="#f0d9a0", ambient_color="#2a2440",
                               mood="solemn cathedral, warm candlelight, cool stained-glass fill"),
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
        print("usage: seed_gfx_church.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Church Demo",
                                  summary="A second distinct carved room — a cathedral nave."))
    server.add_location(campaign_id=CID, name="Cathedral Nave", make_current=True,
                        description="A vaulted stone nave: flanking columns, a candlelit altar at the apse.")
    grid = _author_church_grid(server, CID)
    server.start_session(CID, title="GFX Church Demo")

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

    import scene_grid as sg  # noqa: PLC0415
    # ONE source: the combat obstacles are the FULL scene-grid impassable set (perimeter WALLS + every
    # prop), not a props-only subset — so a token can never end on a cell the room art paints as wall or
    # furniture. (A props-only override would silently drop the perimeter walls the greybox/img2img paint.)
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
