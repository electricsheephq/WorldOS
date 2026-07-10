#!/usr/bin/env python3
"""seed_gfx_camp.py — deterministic hero-vs-goblin GRID combat on camp_gfxdemo01, COHERENT with the
camp_clearing_night plate (#1441 Phase 2: scene<->grid coherence).

Sibling of qa/seed_gfx_combat.py (same shape: pinned campaign id, hero + goblin combat, surprise
seeding) but the scene_grid is the camp_clearing_night grid, not the crypt's. The felt bug this fixes
(owner playtest #5, 2026-07-10): the player's WorldOSPlayer.app build BAKES the DEPLOYED
camp_clearing_night_v2.png plate (extensions/renderers/unity/plates_manifest.json), but the engine's
impassable set was authored for the OLDER greybox/v1 composition — a completely different layout — so
the fire pit / firewood / crates / bedrolls / walls the v2 plate paints were NOT pathing-solid and the
owner walked straight through them (his "essentially open grid" report). This seed re-authors
camp_gfxdemo01's prop footprints against the v2 plate itself (see the constants above — each solid's
floor-contact cells measured off the deployed plate), so the painted fire/firewood/crates/bedrolls/
stone-walls/posts/shelter are pathing-solid AND the read-only renderer's invisible occluder proxies
(derived from these SAME occluder-prop footprints) sit on the true silhouettes — one source between the
painted plate, the combat obstacles, and the occluders.

  # NOTE: uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_camp.py" <state_dir>

Engine = SOLE WRITER: writes only via server.* engine calls + save_campaign. Additive (a new seed;
touches no existing seed/contract — seed_gfx_combat.py is untouched and stays available for when the
player build reverts to the crypt plate).
"""
import json
import os
import sys


CID = "camp_gfxdemo01"  # the id seed_gfx_combat.py + the box renderer pin (unchanged; grid swaps)
GRID_W, GRID_H = 16, 12
# OWNER PLAYTEST #5 COLLISION-COHERENCE RE-MEASUREMENT (2026-07-10): the whole camp impassable set was
# re-derived from the DEPLOYED camp_clearing_night_v2.png plate (extensions/renderers/unity/plates_manifest
# .json). The prior footprints (fire pit(8,6), log seat(6,6)/(6,7), etc.) matched the OLDER greybox/v1
# composition, NOT the v2 plate the player actually renders — a whole DIFFERENT layout (fire pit painted
# front-center, a diagonal ruined stone wall, a timber lean-to, crates + bedrolls all elsewhere). So the
# engine blocked open ground while every painted solid was walkable: the owner's "walks THROUGH the
# campfire / over bedrolls / crates / logs — essentially open grid" report. (The circular
# qa/room_manifests/camp_clearing_night_v2.cells.json was derived from THIS seed, not the plate, so it
# echoed the old cells — the manifest-derivation fix is a separate lane.)
#
# Re-derived by projecting the 16x12 grid's grounded cell centers onto the deployed 1344x768 plate with
# the verified `greybox_render_headless` world_to_screen basis (<1e-3 vs Unity Euler(30,45,0)) and
# eyeball-confirming each painted solid's floor-contact cells against the plate. Footprints kept DISJOINT
# (no cell claimed by two props). Each entry is a footprint (list of [c, r] cells).
CAMPFIRE_CELLS = [[4, 8], [5, 8], [4, 9], [5, 9]]        # the central fire pit + fire-stone ring
FIREWOOD_CELLS = [[7, 8], [8, 8]]                        # the stacked firewood logs right of the fire
CRATE_L_CELLS = [[2, 4], [3, 4], [3, 5]]                 # the left stacked supply crates
CRATE_C_CELLS = [[8, 3], [8, 4]]                         # the crate by the shelter entrance
CRATE_WALL_CELLS = [[6, 3]]                              # the lone box atop the back wall
CRATE_R_CELLS = [[9, 10], [10, 10], [10, 11]]           # the crate front-right
WALL_BL_CELLS = [[5, 2], [6, 2], [7, 3]]                 # the ruined low stone wall, back-left run
WALL_BR_CELLS = [[10, 5], [10, 6], [11, 6], [11, 7], [11, 8],
                 [12, 8], [12, 9], [11, 9], [12, 10]]    # the thick stone wall, back-right compound edge
POST_CELLS = [[3, 2], [4, 2]]                            # the two stone gate posts, far left
SHELTER_CELLS = [[12, 3], [13, 4], [14, 5]]             # the timber lean-to's ground-contact base
BEDROLL_L_CELLS = [[1, 8], [2, 8], [2, 9], [3, 9]]      # the two bedrolls, front-left
BEDROLL_R_CELLS = [[5, 10], [6, 10], [6, 11]]           # the bedroll, front-right
OBSTACLES = (CAMPFIRE_CELLS + FIREWOOD_CELLS + CRATE_L_CELLS + CRATE_C_CELLS + CRATE_WALL_CELLS
             + CRATE_R_CELLS + WALL_BL_CELLS + WALL_BR_CELLS + POST_CELLS + SHELTER_CELLS
             + BEDROLL_L_CELLS + BEDROLL_R_CELLS)
# Combat spawns — open dirt near the fire (clear of every prop footprint above), re-verified vs the plate.
HERO_CELL = [7, 9]
GOBLIN_CELL = [10, 8]


def _build_camp_grid(cid: str, location_id: str = ""):
    """Pure grid builder (no server dependency) — a 16x12 open-air campfire-clearing scene_grid, NO
    perimeter walls (outdoor clearing; matches scene_grid.py::_gen_forest's convention and the
    camp_clearing_night recipe), whose prop footprints are the painted fire pit / firewood / supply
    crates / bedrolls / ruined stone walls / gate posts / timber lean-to on the deployed v2 plate (owner
    playtest #5 re-measurement — see the module constants). Kept separate from `_author_camp_grid` so it's directly
    unit-testable (validate_scene_grid + impassable_cells) without spinning up a full campaign/server."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cells: list = []
    props: list = []

    def _prop(pid: str, kind: str, footprint: list, band: str, sil: str) -> None:
        anchor = footprint[0]
        props.append(SceneProp(id=pid, kind=kind, cells=[(c0, r0) for (c0, r0) in footprint],
                               anchor_cell=(anchor[0], anchor[1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c0, r0) in footprint:
            cells.append(SceneCell(c=c0, r=r0, type="prop", walkable=False, prop_ref=pid))

    # Prop occluder height bands (task C, owner playtest #5): tall = columns / lean-to; mid = crate stacks
    # + low ruined walls; low = fire, firewood, bedrolls (so the invisible depth proxies the read-only
    # renderer builds from these footprints match each painted silhouette and never over-hide actors
    # standing behind a low prop).
    _prop("campfire", "campfire_pit", CAMPFIRE_CELLS, "low",
          "a glowing campfire pit ringed with fire-stones, embers drifting up")
    _prop("firewood", "fallen_log", FIREWOOD_CELLS, "low", "a stack of split firewood logs beside the fire")
    _prop("crate_l", "supply_crates", CRATE_L_CELLS, "mid", "stacked wooden supply crates, iron-banded")
    _prop("crate_c", "supply_crates", CRATE_C_CELLS, "mid", "a supply crate by the shelter mouth")
    _prop("crate_wall", "supply_crates", CRATE_WALL_CELLS, "mid", "a lone crate set atop the low wall")
    _prop("crate_r", "supply_crates", CRATE_R_CELLS, "mid", "a supply crate at the camp's front edge")
    _prop("wall_bl", "stone_wall", WALL_BL_CELLS, "mid", "a low ruined stone wall, back-left")
    _prop("wall_br", "stone_wall", WALL_BR_CELLS, "mid", "the thick stone compound wall, back-right")
    _prop("post_l", "stone_pillar", POST_CELLS, "tall", "a pair of weathered stone gate posts")
    _prop("shelter", "timber_frame", SHELTER_CELLS, "tall", "the timber lean-to's post-and-beam frame")
    _prop("bedroll_l", "bedroll", BEDROLL_L_CELLS, "low", "two rolled sleeping bedrolls, front-left")
    _prop("bedroll_r", "bedroll_2", BEDROLL_R_CELLS, "low", "a rolled bedroll with a pack for a pillow")

    grid = SceneGrid(
        scene_id=f"{cid}:camp_clearing", location_id=location_id, kind="forest",
        biome="open forest clearing at night, campfire lit, moonlit tree line",
        grid=SceneGridSpec(cols=GRID_W, rows=GRID_H, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=200, key_color="#ff8a3d", ambient_color="#26305a",
                               mood="night campfire clearing, warm ember glow from the central fire "
                                    "vs deep blue-violet moonlit shadow under the trees"),
    )
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def _author_camp_grid(server, cid: str):
    """Attach a 16x12 camp_clearing_night-coherent scene_grid (see `_build_camp_grid`) to the
    campaign's current location. Replaces the crypt grid seed_gfx_combat.py authored for this same
    CID — the #1441 P2 fix — so the impassable set matches the CAMP plate the player build renders."""
    c = server._require(cid)
    loc = c.locations.get(c.current_location_id)
    grid = _build_camp_grid(cid, loc.id)
    loc.scene_grid = grid
    server.save_campaign(c)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_camp.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    # Pin the well-known id the box renderer hardcodes (create_campaign auto-ids, so build directly).
    server.save_campaign(Campaign(id=CID, title="GFX Camp Demo",
                                  summary="Playable PoE2 3D-on-2D combat demo on camp_clearing_night."))
    server.add_location(campaign_id=CID, name="Campfire Clearing", make_current=True,
                        description="A moonlit forest clearing: a low campfire, bedrolls, a fallen-log "
                                    "seat, supply crates, boulders, and a loose tree-line boundary.")

    # Author a CONTRACT-MATCHING 16x12 scene_grid whose PROP cells == the painted camp_clearing_night
    # plate's fire pit / log seat / bedrolls / crates / boulders / trees. One source: this scene_grid
    # is the SAME layout room_recipes.json's "camp_clearing_night" entry cites (qa/seed_gfx_camp_
    # clearing.py), so the combat obstacles below match exactly what the player renders.
    grid = _author_camp_grid(server, CID)

    server.start_session(CID, title="GFX Camp Demo")

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
    import scene_grid as sg  # noqa: PLC0415
    # ONE source: combat obstacles = the FULL scene-grid impassable set (perimeter WALLS [none, this
    # is an open-air clearing] + props), not a props-only subset — else a token could end on a cell
    # the room art paints as prop.
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
