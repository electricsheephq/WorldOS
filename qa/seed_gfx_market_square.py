#!/usr/bin/env python3
"""seed_gfx_market_square.py — a daylight town-square REST/social hub (backdrop-cadence restart, HV5).

The second new environment for the HV5 cadence restart. Chosen over "travel/wilderness road" and
"dungeon entrance" because a market square is the highest-frequency REST-CAPABLE social hub in actual
play (shopping, downtime banter, NPC hooks) AND the engine's `scene_grid.py::_gen_town` generator
already ships the W1 (#1318) `spawns.npcs` at-rest bucket for exactly this kind — this hand-authored
fixture mirrors that generator's shape (building-facade walls, a central well, flanking stalls) but
ALSO carves one explicit shop DOOR cell (with its Chebyshev-1 landing kept clear of props) so the
door_cells / door-zone-protection contract (validate_scene_grid) gets exercised by this batch, which
`_gen_town`'s own walls-only layout does not.

Daylight lighting (bright midday sun) — a REST scene like seed_gfx_rest_tavern.py: party + 1 present
merchant NPC, NO combat ever started, so /combat-surface's `stage` block reports mode:"rest".

  # uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_market_square.py" <state_dir>

Then: export_scene_grid.py -> (headless greybox render) -> generate_room.py --room market_square
--layered --lighting daylight -> deploy_room.sh. Engine = SOLE WRITER. Additive.
"""
import json
import os
import sys

CID = "camp_gfxmarket01"
GRID_W, GRID_H = 17, 13
MID_C = GRID_W // 2  # 8
DOOR_CELL = (MID_C, 0)


def _author_market_grid(server, cid: str):
    """Attach a 17x13 daylight town-square scene_grid: building-facade back wall (with one shop DOOR
    gap + kept-clear landing) + partial side walls, a central well, two flanking market stalls, and a
    merchant's cart — all impassable props (== the combat obstacles, one source)."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cols, rows = GRID_W, GRID_H
    cells: list = []
    walls: set = set()

    # Building-front back wall (row 0) with ONE shop-door gap at MID_C — the door cell itself is
    # type="door" (walkable), NOT a wall; its Chebyshev-1 landing ring is left clear of any prop below.
    for c in range(cols):
        if c == DOOR_CELL[0]:
            continue
        walls.add((c, 0))
    # Partial side walls (building facades), matching _gen_town's side_depth convention.
    side_depth = 3
    for r in range(1, side_depth):
        walls.add((0, r))
        walls.add((cols - 1, r))
    for (c, r) in sorted(walls):
        cells.append(SceneCell(c=c, r=r, type="wall", walkable=False))
    cells.append(SceneCell(c=DOOR_CELL[0], r=DOOR_CELL[1], type="door", walkable=True))

    props: list = []

    def _prop(pid: str, kind: str, footprint: list, band: str, sil: str) -> None:
        props.append(SceneProp(id=pid, kind=kind, cells=[(c, r) for (c, r) in footprint],
                               anchor_cell=(footprint[0][0], footprint[0][1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    well_r = 6
    _prop("well", "stone_well", [(MID_C - 1, well_r), (MID_C, well_r)], "tall",
          "a stone well with a rope and bucket, plaza centerpiece")

    stall_r = well_r - 1
    _prop("stall_l", "market_stall", [(MID_C - 4, stall_r), (MID_C - 3, stall_r)], "mid",
          "a canvas market stall, herbs and bread on display")
    _prop("stall_r", "market_stall_r", [(MID_C + 2, stall_r), (MID_C + 3, stall_r)], "mid",
          "a canvas market stall, bolts of cloth for sale")

    cart_r = rows - 4
    _prop("cart", "merchants_cart", [(MID_C - 1, cart_r), (MID_C, cart_r)], "low",
          "a wooden merchant's cart, parked")

    # De-dup.
    seen: set = set()
    deduped: list = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    zone_anchors = {
        "the well": (MID_C, well_r),
        "the market stalls": (MID_C, stall_r),
        "the plaza entrance": (MID_C, rows - 1),
        "the shop door": (DOOR_CELL[0], DOOR_CELL[1]),
    }
    exits = [{"cell": [MID_C, rows - 1], "to_location_id": "", "label": "the main street"}]
    spawns = {
        "party": [(MID_C - 1, rows - 3), (MID_C, rows - 3), (MID_C + 1, rows - 3)],
        "npcs": [(MID_C - 4, well_r), (MID_C + 3, well_r)],   # W1 #1318 — merchants near their stalls.
    }

    # Daylight — bright overhead midday sun from the upper-right, clear-sky ambient.
    lighting = SceneLighting(
        key_dir_deg=120,
        key_color="#f5e8c8",
        ambient_color="#98b8d0",
        mood="bright town square midday, warm overhead sun from the upper-right, clear-sky ambient",
    )

    grid = SceneGrid(
        scene_id=f"{cid}:market_square", location_id="", kind="town",
        biome="outdoor daylight town square, open plaza with market stalls and a stone well",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props, zone_anchors=zone_anchors, exits=exits, spawns=spawns,
        door_cells=[list(DOOR_CELL)], lighting=lighting,
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
        print("usage: seed_gfx_market_square.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Market Square Demo",
                                  summary="A daylight town-square rest/social hub (backdrop-cadence "
                                          "restart, HV5/#1386 item 2)."))
    loc = server.add_location(
        campaign_id=CID, name="The Market Square (at rest)", make_current=True,
        description="A sunlit town square: a stone well at the center, canvas market stalls to either "
                     "side, a merchant's cart parked near the street. The party lingers here to trade "
                     "and rest between errands.",
    )
    loc_id = loc["id"] if isinstance(loc, dict) and "id" in loc else None
    grid = _author_market_grid(server, CID)

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    mage = server.create_character(
        campaign_id=CID, name="Wizard", kind="player",
        race="elf", class_name="wizard", level=4,
        abilities={"strength": 8, "dexterity": 14, "constitution": 12,
                   "intelligence": 18, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    merchant = server.create_character(
        campaign_id=CID, name="Market Merchant", kind="npc", race="human",
        location_id=loc_id, add_to_party=False,
    )
    server.start_session(CID, title="GFX Market Square Demo")

    validation = sg.validate_scene_grid(grid, GRID_W, GRID_H)
    print(json.dumps({
        "campaign_id": CID, "location_id": loc_id,
        "hero_id": hero.get("id"), "mage_id": mage.get("id"), "merchant_id": merchant.get("id"),
        "grid": f"{GRID_W}x{GRID_H}", "impassable_total": len(sg.impassable_cells(grid, GRID_W, GRID_H)),
        "door_cells": [list(DOOR_CELL)], "validation_violations": validation,
    }))


if __name__ == "__main__":
    main()
