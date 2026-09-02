#!/usr/bin/env python3
"""seed_gfx_forest_road.py — the FOREST ROAD AT DUSK fixture (PLATE SPRINT Phase 2, lane FOREST-ROAD).

The generalization test on an UNSEEN structure class: an OUTDOOR LINEAR dirt road threading through
dense forest at dusk. Where seed_gfx_camp_clearing.py authors an OPEN clearing, this authors a
CORRIDOR — a central walkable dirt road (cols 5-10) flanked by dense impassable forest (cols 0-4 and
11-15: tall trees behind a road-facing skirt of roadside boulders + fallen logs), with a few obstacles
intruding onto the road itself. NO perimeter walls (open-air, matches scene_grid.py::_gen_forest's
"no hard perimeter walls" convention).

★ LOAD-BEARING WALKABILITY CONTRACT (the owner's #1 complaint class): every painted tree / boulder /
fallen log is an IMPASSABLE pathing obstacle (prop footprint -> walkable=False), and the walkable road
is prop-free floor — so the greybox the plate paints matches exactly what the engine lets you walk over.
One source (the scene_grid), same as every other seed_gfx_*.py room.

  # uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_forest_road.py" <state_dir>

Then: export_scene_grid.py -> greybox_render_headless.py -> (ARM-B flux+controlnet base -> Gemini style
pass) -> plate_loop.py gates+panel. Engine = SOLE WRITER (writes only via server.* + save_campaign).
Additive.
"""
import json
import os
import sys

CID = "forest_gfxroad01"
GRID_W, GRID_H = 16, 12
MID_C = GRID_W // 2  # 8


def build_forest_road_grid(cid: str, location_id: str = ""):
    """Build (pure, no server) the 16x12 dusk-forest-road scene_grid: a central walkable dirt road
    (cols 5-10) flanked by dense impassable forest (tall trees + a road-facing skirt of roadside
    boulders/fallen logs), plus a few obstacles intruding onto the road. No perimeter walls (outdoor).
    The forest props are the impassable set (also the combat obstacles — one source). Returns the
    SceneGrid; callers attach it to a location. Split out so the walkability contract is unit-testable
    without a running server (mirrors seed_gfx_camp._build_camp_grid)."""
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

    def _rect(c0: int, c1: int, r0: int, r1: int) -> list:
        return [(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]

    # ── Deep forest: tall-tree clusters (2 cols x 3 rows each) filling cols 0-3 (left) and 12-15
    # (right) — every cell covered so no walkable pocket is trapped behind the tree line.
    t = 0
    for side_c0 in (0, 2, 12, 14):            # 2-wide column groups
        for r0 in (0, 3, 6, 9):               # 3-tall row groups (0-2, 3-5, 6-8, 9-11)
            _prop(f"tree_{t}", "large_tree", _rect(side_c0, side_c0 + 1, r0, r0 + 2), "tall",
                  "gnarled dusk-forest tree, dense dark canopy, deeply grooved bark")
            t += 1

    # ── Roadside skirt: the road-facing forest column (4 left, 11 right) is a LOW ledge of boulders
    # and fallen logs (a depth step down from the tall canopy behind to the road) — alternating,
    # 2-cell footprints, fully covering the column so the road edge is a hard walkability boundary.
    s = 0
    for col in (4, 11):
        for i, r0 in enumerate((0, 2, 4, 6, 8, 10)):
            if i % 2 == 0:
                _prop(f"skirt_boulder_{s}", "boulder", _rect(col, col, r0, r0 + 1), "mid",
                      "rounded moss-covered roadside boulder, weathered granite")
            else:
                _prop(f"skirt_log_{s}", "fallen_log", _rect(col, col, r0, r0 + 1), "low",
                      "a mossy fallen log along the road edge, split-grain broken end")
            s += 1

    # ── Obstacles intruding onto the road itself (storytelling + the road is never a sterile lane):
    # a fallen log fallen partway across the road, and two boulders at the road margins. Each removes
    # a road cell but the road stays connected (every row keeps >=4 open cells of the 6-wide corridor).
    _prop("road_log", "fallen_log", [(5, 4), (6, 4)], "low",
          "a large fallen log fallen partway across the dirt road, moss and bracket-fungus on the bark")
    _prop("road_boulder_r", "boulder", [(10, 7)], "mid",
          "a boulder shouldering onto the road from the right verge")
    _prop("road_boulder_l", "boulder", [(5, 2)], "mid",
          "a boulder at the left road edge in the distance")

    # De-dup (defensive; footprints above don't overlap, but keep the discipline every seed uses).
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
        "the road": (MID_C, 6),
        "the near bend": (MID_C, 10),
        "the road ahead": (MID_C, 1),
    }
    exits = [
        {"cell": [MID_C, 0], "to_location_id": "", "label": "the road ahead"},
        {"cell": [MID_C, GRID_H - 1], "to_location_id": "", "label": "back down the road"},
    ]
    spawns = {
        "party": [(7, 10), (8, 10), (9, 10)],
        "npcs": [(7, 8), (9, 9)],   # W1 #1318 at-rest anchors — on road cells, off the obstacle cells.
    }

    # Dusk — a low warm amber sun raking through the trunks vs cool blue dusk shadow under the canopy.
    # Kept adjacent to the proven firelit/night regime (warm directional key + deep cool ambient) so the
    # generalization read isolates STRUCTURE, not lighting.
    lighting = SceneLighting(
        key_dir_deg=235,
        key_color="#e8934a",
        ambient_color="#2b3355",
        mood="dusk forest road, a low amber sun raking through the trunks vs deep cool blue-violet "
             "dusk shadow under the dense canopy",
    )

    grid = SceneGrid(
        scene_id=f"{cid}:forest_road", location_id=location_id, kind="forest",
        biome="a dirt road winding through deep forest at dusk, low amber sun through the trees",
        grid=SceneGridSpec(cols=GRID_W, rows=GRID_H, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props, zone_anchors=zone_anchors, exits=exits, spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def _author_road_grid(server, cid: str):
    """Build the forest-road grid and attach it to the campaign's current location."""
    grid = build_forest_road_grid(cid)
    c = server._require(cid)
    loc = c.locations.get(c.current_location_id)
    grid.location_id = loc.id
    loc.scene_grid = grid
    server.save_campaign(c)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_forest_road.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Forest Road Demo",
                                  summary="The forest-road-at-dusk fixture — a linear dirt road through "
                                          "deep forest (PLATE SPRINT Phase 2, lane FOREST-ROAD, #1481)."))
    loc = server.add_location(
        campaign_id=CID, name="Forest Road (at dusk)", make_current=True,
        description="A rutted dirt road winds through deep forest as the light fails: a low amber sun "
                     "rakes between the trunks, roadside boulders and fallen logs crowd the verge, and "
                     "the dense canopy closes overhead into cool blue dusk shadow.",
    )
    loc_id = loc["id"] if isinstance(loc, dict) and "id" in loc else None
    grid = _author_road_grid(server, CID)

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
        campaign_id=CID, name="Road Scout", kind="npc", race="human",
        location_id=loc_id, add_to_party=False,
    )
    server.start_session(CID, title="GFX Forest Road Demo")

    validation = sg.validate_scene_grid(grid, GRID_W, GRID_H)
    print(json.dumps({
        "campaign_id": CID, "location_id": loc_id,
        "hero_id": hero.get("id"), "ranger_id": ranger.get("id"), "scout_id": scout.get("id"),
        "grid": f"{GRID_W}x{GRID_H}", "props": len(grid.props),
        "impassable_total": len(sg.impassable_cells(grid, GRID_W, GRID_H)),
        "validation_violations": validation,
    }))


if __name__ == "__main__":
    main()
