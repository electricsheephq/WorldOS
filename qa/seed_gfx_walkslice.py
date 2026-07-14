#!/usr/bin/env python3
"""seed_gfx_walkslice.py — WALKABLE-SLICE-V1 smoke fixture, now a THREE-ROOM WORLD: a REST-mode crypt
HUB linked by doorways to a camp clearing AND a brand-new firelit tavern, with a present NPC to talk to
and a lurking goblin to fight. The crypt reuses the CANONICAL crypt grid
(``seed_gfx_combat._build_crypt_grid``: the 14x11 fixture whose sarcophagus floor footprint cols3-7 x
rows6-8 + pillars (3,3)/(3,4) and (8,9)/(9,9) match the adopted ``crypt_armb_iter3_v1`` plate,
owner-playtest-#5 collision-coherence re-measurement) with TWO additions — a back-center doorway (6,0)
to the camp and a left-wall doorway (0,5) to the tavern — so the player renders the SAME crypt as the
combat demo instead of a divergent hand-authored grid (the #1396 scene-grid coherence defect class). The
camp grid comes from seed_gfx_camp; the tavern grid (``build_tavern_grid``) is authored HERE from the
SAME world-true geometry as its greybox / registered plate / DERIVED manifest (NEW-ROOM-TAVERN, epic
#1508) — ONE grid source each. NO combat is started (rest mode), so the surface's ``stage`` carries the
walk / parley / door affordances the player consumes. ``start_combat`` (item 4) then opens the fight in
place.

Engine = SOLE WRITER (writes only via server.* + save_campaign). Additive: a new seed/campaign, no
existing seed touched.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/seed_gfx_walkslice.py <state_dir>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CID = "walkslice_smoke01"
# back-center doorway punched into the reused canonical crypt grid — cross_door(6,0) leads to the camp.
# Its Chebyshev-1 landing ring (rows 0-1) stays clear of the sarcophagus (rows 6-8) and both pillars,
# so the door zone is prop-free.
DOOR = [6, 0]
# THREE-ROOM WORLD (NEW-ROOM-TAVERN, epic #1508): a SECOND crypt doorway leads to the brand-new
# firelit tavern. #1534 (door cells must sit at PAINTED doorways): the original (0,5) left-wall cell
# had NO doorway painted on the plate — the label/glow floated over open floor. Re-measured against
# crypt_armb_iter3 with the cell-lattice overlay (qa/evidence/1534/grid_crypt.png): the plate's BIG
# right archway bases at (13,4) on the NE wall edge. Its landing ring (cols 12-13 x rows 3-5) is clear
# of the sarcophagus (cols 2-7) and both pillars ((3,3)/(3,4), (8,9)/(9,9)). The crypt is the hub:
# camp <-> crypt <-> tavern. (The camp door (6,0) was re-measured too — it already sits at the painted
# left archway; unchanged.)
TAVERN_DOOR = [13, 4]
# The camp's RETURN doorway back to the crypt (SHIP-MORNING smoke's "known gap": the camp grid had no
# authored door_cells, so the camp was a DEAD END for a real player — the smoke could only leave via the
# QA-side travel_to primitive). Top-edge (5,0) sits in the painted gate-post gap on the north path; the
# camp's rows 0-1 carry no prop footprint, so the door cell and its Chebyshev-1 landing ring
# (cols 4-6 x rows 0-1) are prop-free by construction.
CAMP_DOOR = [5, 0]


def build_crypt_grid(loc_id: str):
    """The walkslice crypt scene_grid = the CANONICAL combat crypt (``seed_gfx_combat._build_crypt_grid``:
    14x11, sarcophagus floor footprint cols2-7 x rows7-9, pillars (2,4)/(9,9) — matched to the adopted
    plate, #1386 corrected by #1505) with ONE addition: a back-center DOORWAY the party crosses to the
    camp. Reuses the canonical grid verbatim (same cells/props/impassable) so the player renders the
    SAME crypt as the combat demo. Pure (no server) — directly unit-testable, mirroring
    ``_build_crypt_grid``'s own split rationale.

    REST-mode ``spawns`` are WHERE the party + present NPC stand when the room renders inhabited (the
    stage projects party onto ``spawns['party']``, present NPCs onto ``spawns['npcs']``): the open
    flagstone floor between the left pillar and the tomb (cols 3-4, rows 5-6), clear of the tomb
    footprint (rows 7-9), the pillars, and the doorway zone."""
    import scene_grid as sg  # noqa: PLC0415
    import seed_gfx_combat as combat  # noqa: PLC0415  (reuse the CANONICAL crypt grid — ONE crypt source)

    grid = combat._build_crypt_grid(CID, loc_id)
    doors = [DOOR, TAVERN_DOOR]  # back-center -> camp, left-wall -> tavern (three-room world)
    for cell in grid.cells:  # punch each wall cell into a walkable doorway
        if [cell.c, cell.r] in doors:
            cell.type, cell.walkable = "door", True
    grid.door_cells = [(DOOR[0], DOOR[1]), (TAVERN_DOOR[0], TAVERN_DOOR[1])]
    # party + Mira on the OPEN flagstone floor between the left pillar and the tomb (cols 3-4, rows
    # 5-6) — clear painted floor, off the corrected tomb footprint (cols3-7 x rows6-8) and both
    # re-measured pillars ((3,3)/(3,4) and (8,9)/(9,9)), owner-playtest-#5 collision-coherence.
    grid.spawns = {"party": [(3, 5), (4, 5)], "npcs": [(3, 6)]}
    grid.art.layout_hash = sg._layout_hash(grid)  # layout changed (added door) — refresh the hash
    return grid


def build_camp_grid(loc_id: str):
    """The walkslice camp = the CANONICAL camp_clearing_night grid (``seed_gfx_camp._build_camp_grid``
    — ONE camp source, #1396 coherence class) plus the walkslice's world topology: the RETURN doorway
    to the crypt at ``CAMP_DOOR`` (the painted gate-post gap, top edge) and rest-mode ``spawns`` on
    clear ground by the fire. Two defects fixed here (ship-morning frames, orchestrator eyeball +
    data-verified): (1) the camp had NO door_cells — a dead end for a clicking player; (2) the old
    party spawn (8,9) collided with the firewood footprint after CAMP-TUNE (#1526) extended it to
    [[7,8],[8,8],[8,9]] — the hero rendered standing ON the woodpile. Pure (no server) — directly
    unit-testable, mirroring ``build_crypt_grid``."""
    import scene_grid as sg  # noqa: PLC0415
    import seed_gfx_camp as camp  # noqa: PLC0415  (reuse the camp_clearing_night grid — ONE camp source)

    grid = camp._build_camp_grid(CID, loc_id)
    # the camp has no perimeter wall cells (outdoor clearing; walkable floor by default), so the door
    # only needs an explicit door-typed cell + the door_cells registration for the renderer's glow/label.
    grid.cells.append(sg.SceneCell(c=CAMP_DOOR[0], r=CAMP_DOOR[1], type="door", walkable=True))
    grid.door_cells = [(CAMP_DOOR[0], CAMP_DOOR[1])]
    # party on the clear open ground between the fire ((4-5,8-9)) and the firewood ((7,8)-(8,9)):
    # (6,9)/(7,9) touch no footprint; NPC (9,7) clear of the back-right wall run ((10,5)...).
    grid.spawns = {"party": [(6, 9), (7, 9)], "npcs": [(9, 7)]}
    grid.art.layout_hash = sg._layout_hash(grid)  # layout changed (door + spawns) — refresh the hash
    return grid


# The tavern is a BRAND-NEW room (NEW-ROOM-TAVERN, epic #1508) — no separate combat seed exists, so its
# grid is authored HERE from the SAME world-true 12x10 geometry the greybox / registered plate / DERIVED
# manifest were built from. TAVERN-FIT2 ADOPTION (M-ALIGN wave-2 close, ruling on #1557): the canonical
# tavern is now the DENSITY-LAW fit2 room (tools/author_room_geometry.py tavern_fit2 ->
# qa/room_manifests/tavern_fit2.cells.json). Prop footprints are VERBATIM from that geometry, so pathing
# and paint agree by construction.
TAVERN_W, TAVERN_H = 12, 10
# The tavern's door back to the crypt. #1534/#1535 durable fix, now LANDED by fit2: the incumbent
# truegrey plate painted NO doorway on the (8,0) back wall (blank wall between hearth and bar), so the
# door cell had to move to (0,0) beside an out-painted west opening. The fit2 geometry AUTHORS a real
# door gap in the north wall_run at (8,0) and the fit2 plate PAINTS its doorway there — so the door cell
# and the painted opening now COINCIDE exactly. The door returns to the authored back-wall cell (8,0).
TAVERN_BACK_DOOR = [8, 0]
# (id, kind, footprint, height_band, silhouette) — correct 5-ft-grid scale, off the two near walls.
# TAVERN-FIT2: the 6-prop truegrey interior grown to 14 REAL collision props (the density law, #1557/
# #1559). Footprints VERBATIM from tools/author_room_geometry.py author_tavern_fit2. The 8 added props
# are all off row 8 (the party/npc spawn row) and off the (8,0) door zone.
_TAVERN_PROPS = [
    ("hearth", "hearth", [(5, 1), (6, 1)], "tall", "stone hearth with glowing embers"),
    ("bar_counter", "bar_counter", [(9, 2), (9, 3), (9, 4), (9, 5)], "mid", "carved wooden bar counter"),
    ("table_nw", "table", [(3, 3), (4, 3), (3, 4), (4, 4)], "low", "round wooden tavern table"),
    ("table_ne", "table", [(6, 3), (7, 3), (6, 4), (7, 4)], "low", "round wooden tavern table"),
    ("table_s", "table", [(5, 6), (6, 6), (5, 7), (6, 7)], "low", "round wooden tavern table"),
    ("barrels", "barrel", [(2, 6), (3, 6), (2, 7), (3, 7)], "low", "stacked ale barrels"),
    # --- DENSITY-LAW additions (fit2): 8 props -> 14 interior (16 new impassable cells) ---
    ("woodpile", "fallen_log", [(3, 1), (4, 1)], "low", "stacked firewood logs beside the hearth"),
    ("bench_nw", "fallen_log", [(3, 5), (4, 5)], "low", "wooden bench flanking the NW table"),
    ("bench_ne", "fallen_log", [(6, 5), (7, 5)], "low", "wooden bench flanking the NE table"),
    ("bench_s", "fallen_log", [(7, 6), (7, 7)], "low", "wooden bench flanking the S table"),
    ("stools_bar", "fallen_log", [(8, 3), (8, 4)], "low", "patron stools at the bar"),
    ("shelf_bar", "supply_crates", [(10, 2), (10, 3)], "mid", "back-bar shelf behind the counter"),
    ("casks_bar", "barrel", [(10, 4), (10, 5)], "mid", "ale casks stacked behind the bar"),
    ("barrels_corner", "barrel", [(10, 7), (10, 8)], "low", "a barrel pair in the SE corner"),
]


def build_tavern_grid(location_id: str = ""):
    """Pure 12x10 firelit-tavern scene_grid (no server) — the three-room world's THIRD room, authored
    from the SAME world-true geometry as its greybox / registered plate / derived manifest so pathing and
    paint agree. A back-wall doorway (TAVERN_BACK_DOOR) returns to the crypt. Directly unit-testable
    (validate_scene_grid + impassable_cells), mirroring ``seed_gfx_combat._build_crypt_grid``."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cols, rows = TAVERN_W, TAVERN_H
    cells: list = []
    for c in range(cols):  # solid perimeter (enclosed hall; the greybox uses a cutaway wall height)
        cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        cells.append(SceneCell(c=c, r=rows - 1, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))

    props: list = []
    for pid, kind, footprint, band, sil in _TAVERN_PROPS:
        anchor = footprint[0]
        props.append(SceneProp(id=pid, kind=kind, cells=[(c0, r0) for (c0, r0) in footprint],
                               anchor_cell=(anchor[0], anchor[1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c0, r0) in footprint:
            cells.append(SceneCell(c=c0, r=r0, type="prop", walkable=False, prop_ref=pid))

    for cell in cells:  # punch the back-wall doorway back to the crypt
        if [cell.c, cell.r] == TAVERN_BACK_DOOR:
            cell.type, cell.walkable = "door", True

    grid = SceneGrid(
        scene_id=f"{CID}:tavern", location_id=location_id, kind="tavern",
        biome="firelit tavern hall, hearth fire and hanging iron lanterns",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=200, key_color="#e8823a", ambient_color="#1a2040",
                               mood="warm firelit tavern, hearth glow, deep blue-violet corners"),
    )
    grid.door_cells = [(TAVERN_BACK_DOOR[0], TAVERN_BACK_DOOR[1])]
    # party + present NPC stand on the open near-half floor (row 8), clear of every prop and the door zone.
    grid.spawns = {"party": [(4, 8), (5, 8)], "npcs": [(7, 8)]}
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_walkslice.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(HERE, "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(
        id=CID, title="Walkable Slice smoke",
        summary="Rest-mode crypt -> camp doorway + present NPC + a lurking goblin (WALKABLE-SLICE-V1).",
        is_sandbox=True,
    ))
    # Pin STABLE, meaningful location ids (add_location honors a caller-chosen id) so they double as the
    # plate-registry keys — the runtime plate swap keys on surface.location.id, and stable ids make the
    # plates_manifest.json durable + deterministic across re-seeds (add_location ids are otherwise random).
    crypt_loc = server.add_location(
        campaign_id=CID, name="Crypt Antechamber", location_id="crypt", make_current=True,
        description="A cold torchlit crypt with a doorway to the night camp beyond.")
    camp_loc = server.add_location(
        campaign_id=CID, name="Campfire Clearing", location_id="camp_clearing_night",
        description="A camp clearing under the night sky.", connections=[crypt_loc["id"]])
    # THREE-ROOM WORLD: the brand-new firelit tavern, the crypt's OTHER neighbour (via the left-wall door).
    tavern_loc = server.add_location(
        campaign_id=CID, name="Firelit Tavern Hall", location_id="tavern",
        description="A warm firelit tavern hall — a hearth, a bar counter, tables, and a doorway back "
                    "to the crypt.", connections=[crypt_loc["id"]])

    c = server._require(CID)
    # crypt carries the CANONICAL grid + TWO door_cells; wire crypt -> camp (6,0) and crypt -> tavern (0,5).
    crypt_grid = build_crypt_grid(crypt_loc["id"])
    c.locations[crypt_loc["id"]].scene_grid = crypt_grid
    for neighbour in (camp_loc["id"], tavern_loc["id"]):
        if neighbour not in c.locations[crypt_loc["id"]].connections:
            c.locations[crypt_loc["id"]].connections.append(neighbour)
    # the camp carries the canonical grid + its RETURN door to the crypt (no more dead end) + spawns
    # moved off the extended firewood footprint — see build_camp_grid.
    camp_grid = build_camp_grid(camp_loc["id"])
    c.locations[camp_loc["id"]].scene_grid = camp_grid
    # the tavern carries its own world-true grid + a back-wall door_cell returning to the crypt.
    tavern_grid = build_tavern_grid(tavern_loc["id"])
    c.locations[tavern_loc["id"]].scene_grid = tavern_grid
    server.save_campaign(c)
    server.start_session(CID, title="Walkable Slice Demo")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True, location_id=crypt_loc["id"])
    hero_id = hero["id"]
    # a present NPC in the crypt (the talk target — rest_role "npc" on the stage).
    npc = server.create_character(
        campaign_id=CID, name="Mira the Keeper", kind="npc", race="human", class_name="commoner",
        level=1, apply_srd_defaults=True, add_to_party=False, location_id=crypt_loc["id"])
    npc_id = npc["id"]
    # a lurking goblin in the CAMP (the foe for start_combat "start a fight in place" — the milestone's
    # fight happens after crossing INTO camp). spawn_monster has no location_id param, so anchor it at the
    # camp directly (engine snapshot, still under save_campaign).
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]
    c = server._require(CID)
    c.characters[goblin_id].location_id = camp_loc["id"]
    # TEST-ONLY force_hit (double-guarded by is_sandbox + WORLDOS_COMBAT_TEST env, per
    # _combat_test_mode_enabled) so the smoke's attack step lands deterministically once the fighter is
    # adjacent — same discipline as seed_gfx_camp_smoke. Damage is still rolled normally (hp really drops).
    c.house_rules.force_hit = True
    server.save_campaign(c)

    print(json.dumps({
        "campaign_id": CID, "crypt_id": crypt_loc["id"], "camp_id": camp_loc["id"],
        "tavern_id": tavern_loc["id"], "door_cell": DOOR, "tavern_door_cell": TAVERN_DOOR,
        "hero_id": hero_id, "npc_id": npc_id, "goblin_id": goblin_id,
        "crypt_connections": list(server._require(CID).locations[crypt_loc["id"]].connections),
    }))


if __name__ == "__main__":
    main()
