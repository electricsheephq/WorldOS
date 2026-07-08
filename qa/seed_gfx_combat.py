#!/usr/bin/env python3
"""seed_gfx_combat.py — deterministic hero-vs-goblin GRID combat on camp_gfxdemo01 (gfx P2).

The playable PoE2 3D-on-2D combat-demo seed. Pins the well-known campaign id the box
renderer hardcodes (paint_combat_v1.cs / paint_backdrop_p0.cs CID="camp_gfxdemo01"),
seats a fighter PC + a goblin on a 14x11 grid whose OBSTACLE cells match the crypt's
painted-prop occluders on the deployed `crypt_dense_v1.png` plate (pillarL(2,4) /
pillarR(9,9) / sarcophagus(3,8)+(4,8) — see #1396), places hero@(6,6) / goblin@(9,5),
and starts combat. After this, GET /combat-surface?campaign=camp_gfxdemo01 returns real
engine cells (positionAuthority='grid') for the READ-ONLY renderer to consume + the M-B
bridge routes movement around exactly the painted props.

  # NOTE: uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_combat.py" <state_dir>

#1396 content fix (2026-07-08): the ORIGINAL cells here — pillarL(2,3) / pillarR(11,3) /
sarcophagus(7,1) — matched the OLDER `crypt_firelit_v2` greybox-conditioned plate
(`paint_backdrop_p0.cs` OCC_ cells), but the currently-deployed `crypt_dense_v1.png` went
through a later Gemini polish+populate repaint pass (CANONICAL.md) that recomposed the
room WITHOUT re-deriving prop cells from the greybox — so the engine's impassable set
drifted from the paint (#1396: "actor on the sarcophagus"). The cells below were
RE-CALIBRATED against the deployed plate: the `qa/export_scene_grid.py`-style contract
camera (orthographic, size=13, `Euler(30,45,0)`, `cellToWorld`) was replayed against the
real capture frames in `qa/evidence/1397/` + `qa/evidence/1408/` (their logical_cell ->
screen_bbox/floor_y_px manifests reproduce EXACTLY, pixel-for-pixel, confirming the
camera model), then the SAME projection was grid-overlaid on `qa/evidence/1392/*.jpg`
(live captures of `crypt_dense_v1.png`) to read off where the pillars/sarcophagus
actually sit in the paint. Engine untouched; this is a content-only re-authoring.

Engine = SOLE WRITER: writes only via server.* engine calls + save_campaign. Additive
(a new seed; touches no existing seed/contract).
"""
import json
import os
import sys


CID = "camp_gfxdemo01"
GRID_W, GRID_H = 14, 11
# Prop footprints re-calibrated to the DEPLOYED crypt_dense_v1.png plate (#1396). Each entry is a
# footprint (list of [c, r] cells); OBSTACLES flattens them for the printed summary below.
PILLAR_L_CELLS = [[2, 4]]
PILLAR_R_CELLS = [[9, 9]]
SARCOPHAGUS_CELLS = [[3, 8], [4, 8]]  # ~2-cell footprint under the painted sarcophagus box
OBSTACLES = PILLAR_L_CELLS + PILLAR_R_CELLS + SARCOPHAGUS_CELLS
HERO_CELL = [6, 6]
GOBLIN_CELL = [9, 5]


def _build_crypt_grid(cid: str, location_id: str = ""):
    """Pure grid builder (no server dependency) — a 14x11 crypt scene_grid whose PROP footprints
    match the painted crypt_dense_v1.png plate (#1396), so the pathing and the paint agree. Kept
    separate from `_author_crypt_grid` so it's directly unit-testable (validate_scene_grid +
    impassable_cells) without spinning up a full campaign/server."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp, SceneLighting, _layout_hash,
    )

    cols, rows = GRID_W, GRID_H
    cells: list = []
    # solid perimeter walls (impassable, and the greybox encloses the room).
    for c in range(cols):
        cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        cells.append(SceneCell(c=c, r=rows - 1, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))

    props: list = []

    def _prop(pid: str, kind: str, footprint: list, band: str, sil: str) -> None:
        anchor = footprint[0]
        props.append(SceneProp(id=pid, kind=kind, cells=[(c0, r0) for (c0, r0) in footprint],
                               anchor_cell=(anchor[0], anchor[1]), occluder=True,
                               height_band=band, silhouette=sil))
        for (c0, r0) in footprint:
            cells.append(SceneCell(c=c0, r=r0, type="prop", walkable=False, prop_ref=pid))

    # the THREE obstacle props — footprints == PILLAR_L_CELLS/PILLAR_R_CELLS/SARCOPHAGUS_CELLS
    # (kept in lock-step with set_grid below via OBSTACLES).
    _prop("pillar_l", "stone_pillar", PILLAR_L_CELLS, "tall", "ancient cracked stone pillar")
    _prop("pillar_r", "stone_pillar", PILLAR_R_CELLS, "tall", "ancient mossy stone pillar")
    _prop("sarcophagus", "sarcophagus", SARCOPHAGUS_CELLS, "tall", "carved stone sarcophagus, lid ajar")

    grid = SceneGrid(
        scene_id=f"{cid}:crypt", location_id=location_id, kind="dungeon",
        biome="ancient stone crypt, flickering brazier light",
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
        lighting=SceneLighting(key_dir_deg=200, key_color="#e8823a", ambient_color="#1a2040",
                               mood="dim torchlit crypt, warm brazier glow, cold blue shadow fill"),
    )
    grid.art.layout_hash = _layout_hash(grid)
    return grid


def _author_crypt_grid(server, cid: str):
    """Attach a 14x11 crypt scene_grid (see `_build_crypt_grid`) to the campaign's current location.
    Replaces the auto-generated dungeon grid (14..17 wide -> mismatched the fixed 14x11 combat contract)."""
    c = server._require(cid)
    loc = c.locations.get(c.current_location_id)
    grid = _build_crypt_grid(cid, loc.id)
    loc.scene_grid = grid
    server.save_campaign(c)
    return grid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_combat.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    # Pin the well-known id the box renderer hardcodes (create_campaign auto-ids, so build directly).
    server.save_campaign(Campaign(id=CID, title="GFX Combat Demo",
                                  summary="Playable PoE2 3D-on-2D combat demo on crypt_firelit_v2."))
    server.add_location(campaign_id=CID, name="Crypt", make_current=True,
                        description="A firelit stone crypt: pillars, a sarcophagus, braziers, deep shadows.")

    # Author a CONTRACT-MATCHING 14x11 scene_grid whose PROP cells == the combat OBSTACLES, so the
    # greybox (build_room_greybox.cs) -> img2img painted room has its props on the EXACT combat pathing
    # cells (authored-by-construction; replaces the auto-generated 16x11 grid which mismatched the 14x11
    # combat). One source: this scene_grid drives BOTH the painted room AND the obstacles below.
    grid = _author_crypt_grid(server, CID)

    server.start_session(CID, title="GFX Combat Demo")

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
    # ONE source: combat obstacles = the FULL scene-grid impassable set (perimeter WALLS + props), not a
    # props-only subset — else a token could end on a cell the room art paints as wall.
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
