#!/usr/bin/env python3
"""seed_gfx_camp.py — deterministic hero-vs-goblin GRID combat on camp_gfxdemo01, COHERENT with the
camp_clearing_night plate (#1441 Phase 2: scene<->grid coherence).

Sibling of qa/seed_gfx_combat.py (same shape: pinned campaign id, hero + goblin combat, surprise
seeding) but the scene_grid is the camp_clearing_night AUTHORED grid, not the crypt's. The felt bug
this fixes: the player's WorldOSPlayer.app build BAKES the camp_clearing_night plate (fire pit, log
seat, bedrolls, supply crates, boulders, tree line — extensions/renderers/shared/room_recipes.json's
"camp_clearing_night" entry, adopted tier=stable 2026-07-08), but the T3 harness (qa/ui_playtest_
player.sh) seeded camp_gfxdemo01 with seed_gfx_combat.py's CRYPT grid (14x11, pillars + sarcophagus).
Those painted camp props were never in the engine's impassable set, so actors could walk onto/through
the fire pit and log seat — the owner's "stacking on everything" report. This seed re-authors
camp_gfxdemo01 on the SAME campfire-clearing scene_grid room_recipes.json cites as the plate's
source of truth (qa/seed_gfx_camp_clearing.py::_author_camp_grid, verbatim prop layout — one source
between the painted plate and the combat obstacles), so the painted logs/fire/crates/bedrolls/rocks/
trees are pathing-solid and the T3 harness renders a coherent scene.

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
# Prop footprints — VERBATIM from qa/seed_gfx_camp_clearing.py::_author_camp_grid, the authored grid
# extensions/renderers/shared/room_recipes.json's "camp_clearing_night" entry cites as its source of
# truth. Keeping this list in lock-step (rather than importing the function) keeps this seed's own
# OBSTACLES summary self-contained and directly diffable against the recipe's `_doc` field.
TREE_CELLS = [[2, 0], [2, 1], [6, 0], [6, 1], [9, 0], [9, 1], [13, 0], [13, 1]]
ROCK_L_CELLS = [[1, 6]]
ROCK_R_CELLS = [[14, 6]]
CAMPFIRE_CELLS = [[8, 6]]
BEDROLL_CELLS = [[7, 7], [9, 7], [8, 8]]
LOG_SEAT_CELLS = [[6, 6], [6, 7]]
SUPPLY_CRATE_CELLS = [[10, 6]]
OBSTACLES = (TREE_CELLS + ROCK_L_CELLS + ROCK_R_CELLS + CAMPFIRE_CELLS + BEDROLL_CELLS
             + LOG_SEAT_CELLS + SUPPLY_CRATE_CELLS)
# Combat spawns — the recipe's own party/npc zone anchors (clear of every prop footprint above).
HERO_CELL = [7, 9]
GOBLIN_CELL = [10, 8]


def _build_camp_grid(cid: str, location_id: str = ""):
    """Pure grid builder (no server dependency) — a 16x12 open-air campfire-clearing scene_grid, NO
    perimeter walls (outdoor clearing; matches scene_grid.py::_gen_forest's convention and the
    camp_clearing_night recipe), whose prop footprints are the painted fire pit / log seat / bedrolls
    / supply crates / boulders / tree line. Kept separate from `_author_camp_grid` so it's directly
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

    for i, pair in enumerate((TREE_CELLS[0:2], TREE_CELLS[2:4], TREE_CELLS[4:6], TREE_CELLS[6:8])):
        _prop(f"tree_{i}", "large_tree", pair, "tall", "gnarled night forest tree, dense dark canopy")
    _prop("rock_l", "boulder", ROCK_L_CELLS, "mid", "mossy boulder catching firelight on one face")
    _prop("rock_r", "boulder_r", ROCK_R_CELLS, "mid", "lichen-covered boulder in cool moon-shadow")
    _prop("campfire", "campfire_pit", CAMPFIRE_CELLS, "low",
          "a glowing campfire pit ringed with fire-stones, embers drifting up")
    _prop("bedroll_1", "bedroll", [BEDROLL_CELLS[0]], "low", "a rolled sleeping bedroll and blanket")
    _prop("bedroll_2", "bedroll_2", [BEDROLL_CELLS[1]], "low", "a rolled sleeping bedroll near the embers")
    _prop("bedroll_3", "bedroll_3", [BEDROLL_CELLS[2]], "low", "a bedroll with a pack for a pillow")
    _prop("log_seat", "fallen_log", LOG_SEAT_CELLS, "low", "a fallen log dragged up as fireside seating")
    _prop("supply_crates", "supply_crates", SUPPLY_CRATE_CELLS, "low",
          "stacked travel packs and a supply crate, waterskins hanging off the straps")

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
