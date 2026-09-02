#!/usr/bin/env python3
"""seed_gfx_camp_clearing.py — the CANONICAL rest-camp fixture (backdrop-cadence restart, HV5).

The money-shot fixture campaign camp_637cc4322ef2 (the demo-reel Baldur's Gate canon fixture) is
a CAMP by NAME only — WorldOS has never had an actual camp PLATE. This seed hand-authors a night
campfire clearing scene_grid (bedrolls + campfire + log seats + a loose tree-line boundary, NO
perimeter walls — an open-air clearing, matching the engine's own `_gen_forest` convention of
"no hard perimeter walls") so the room-gen pipeline (export_scene_grid.py -> a headless greybox ->
generate_room.py --layered --room camp_clearing_night) has a camera-pinned base to paint.

Unlike a COMBAT seed (seed_gfx_church.py et al.), this is a REST scene like seed_gfx_rest_tavern.py:
party + 1 present NPC around the fire, NO combat ever started, so /combat-surface's additive `stage`
block naturally reports mode:"rest". The `spawns.npcs` bucket (W1 #1318 convention) gives the rest
viewer projection a deterministic at-rest anchor for the present NPC.

  # uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_camp_clearing.py" <state_dir>

Then: export_scene_grid.py -> (headless greybox render) -> generate_room.py --room camp_clearing_night
--layered -> deploy_room.sh. Engine = SOLE WRITER (writes only via server.* + save_campaign). Additive.
"""
import json
import os
import sys

CID = "camp_gfxcampnight01"
GRID_W, GRID_H = 16, 12
MID_C = GRID_W // 2  # 8


def _author_camp_grid(server, cid: str):
    """Attach a 16x12 open-air campfire-clearing scene_grid. No perimeter walls (outdoor, matches
    scene_grid.py::_gen_forest's convention) — a loose tree-line + boulders bound the clearing
    visually, and the campfire + bedrolls + log seat + supply crates are the impassable set-dressing
    (also the combat obstacles, one source, matching every other seed_gfx_*.py room)."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cells: list = []
    props: list = []

    def _prop(pid: str, kind: str, footprint: list, band: str, sil: str) -> None:
        props.append(SceneProp(id=pid, kind=kind, cells=[(c, r) for (c, r) in footprint],
                               anchor_cell=(footprint[0][0], footprint[0][1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    # Loose tree-line boundary along the back (row 0-1) — 4 gnarled trees, evenly spaced, NOT a
    # hard wall (outdoor clearing, per _gen_forest doc).
    for i, c in enumerate((2, 6, 9, 13)):
        _prop(f"tree_{i}", "large_tree", [(c, 0), (c, 1)], "tall",
              "gnarled night forest tree, dense dark canopy")

    # Two boulders anchoring the sides of the clearing.
    _prop("rock_l", "boulder", [(1, 6)], "mid", "mossy boulder catching firelight on one face")
    _prop("rock_r", "boulder_r", [(14, 6)], "mid", "lichen-covered boulder in cool moon-shadow")

    # The campfire pit — the warm key-light source, dead center.
    _prop("campfire", "campfire_pit", [(8, 6)], "low",
          "a glowing campfire pit ringed with fire-stones, embers drifting up")

    # 3 bedrolls scattered around the fire (not on top of it).
    _prop("bedroll_1", "bedroll", [(7, 7)], "low", "a rolled sleeping bedroll and blanket")
    _prop("bedroll_2", "bedroll_2", [(9, 7)], "low", "a rolled sleeping bedroll near the embers")
    _prop("bedroll_3", "bedroll_3", [(8, 8)], "low", "a bedroll with a pack for a pillow")

    # A fallen-log bench pulled up to the fire for seating.
    _prop("log_seat", "fallen_log", [(6, 6), (6, 7)], "low", "a fallen log dragged up as fireside seating")

    # Travel packs + a supply crate opposite the log seat.
    _prop("supply_crates", "supply_crates", [(10, 6)], "low",
          "stacked travel packs and a supply crate, waterskins hanging off the straps")

    # De-dup (defensive; footprints above don't overlap, but keep the discipline every other seed uses).
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
        "the fire pit": (8, 6),
        "the tree line": (8, 1),
        "the trail": (MID_C, GRID_H - 1),
    }
    exits = [{"cell": [MID_C, GRID_H - 1], "to_location_id": "", "label": "the forest trail"}]
    spawns = {
        "party": [(7, 9), (8, 9), (9, 9)],
        "npcs": [(6, 8), (10, 8)],   # W1 #1318 at-rest anchors — off the bedroll/prop cells.
    }

    # Night — warm ember key from the fire (low, center), deep cool blue-violet moonlit ambient.
    lighting = SceneLighting(
        key_dir_deg=200,
        key_color="#ff8a3d",
        ambient_color="#26305a",
        mood="night campfire clearing, warm ember glow from the central fire vs deep blue-violet "
             "moonlit shadow under the trees",
    )

    grid = SceneGrid(
        scene_id=f"{cid}:camp_clearing", location_id="", kind="forest",
        biome="open forest clearing at night, campfire lit, moonlit tree line",
        grid=SceneGridSpec(cols=GRID_W, rows=GRID_H, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props, zone_anchors=zone_anchors, exits=exits, spawns=spawns,
        lighting=lighting,
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
        print("usage: seed_gfx_camp_clearing.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Camp Clearing Demo",
                                  summary="The canonical rest-camp fixture — a night campfire clearing "
                                          "(backdrop-cadence restart, HV5/#1386 item 1)."))
    loc = server.add_location(
        campaign_id=CID, name="Campfire Clearing (at rest)", make_current=True,
        description="A quiet forest clearing at night: a low campfire ringed with stones, bedrolls "
                     "laid out around the embers, packs stacked against a fallen log. The party has "
                     "stopped here to make camp.",
    )
    loc_id = loc["id"] if isinstance(loc, dict) and "id" in loc else None
    grid = _author_camp_grid(server, CID)

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    ranger = server.create_character(
        campaign_id=CID, name="Wren", kind="player",
        race="elf", class_name="ranger", level=4,
        abilities={"strength": 12, "dexterity": 18, "constitution": 14,
                   "intelligence": 10, "wisdom": 14, "charisma": 8},
        apply_srd_defaults=True, add_to_party=True,
    )
    scout = server.create_character(
        campaign_id=CID, name="Camp Scout", kind="npc", race="human",
        location_id=loc_id, add_to_party=False,
    )
    server.start_session(CID, title="GFX Camp Clearing Demo")

    validation = sg.validate_scene_grid(grid, GRID_W, GRID_H)
    print(json.dumps({
        "campaign_id": CID, "location_id": loc_id,
        "hero_id": hero.get("id"), "ranger_id": ranger.get("id"), "scout_id": scout.get("id"),
        "grid": f"{GRID_W}x{GRID_H}", "impassable_total": len(sg.impassable_cells(grid, GRID_W, GRID_H)),
        "validation_violations": validation,
    }))


if __name__ == "__main__":
    main()
