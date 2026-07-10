# DunGen export contract (epic #1508 stage-1 spike)

**Date:** 2026-07-11 · **Status:** SPIKE. DunGen (Unity Asset Store #15682, owner-purchased 2026-07-11)
proves out as an authoring-time STRUCTURE generator whose layout exports into the WorldOS pipeline.

## The invariant this honors

The Python engine is the **sole writer** of level-structure truth. DunGen runs only at authoring time; it
*proposes* a room-graph. We export that proposal to JSON and bake it into engine fixtures. Nothing in
DunGen runs at game runtime, and the baked `*.scenegrid.json` is the authored fixture the engine loads —
DunGen never becomes a second writer of grid state. (Same fence as Tessera Pro, the WFC follow-up.)

## The two hops

```
DunGen scene ──[DunGenLayoutExporter.cs]──▶ dungen_layout.json ──[dungen_to_fixtures.py]──▶
    (a) <name>.scenegrid.json   (engine SceneGrid fixture — the sole-writer truth)
    (b) <name>_geometry.json     (greybox geometry json — greybox_render_headless + derive_room_manifest)
    (b') <name>_<room>_geometry.json  (--room: one cropped room = the registered-plate input)
```

### Hop 1 — `DunGenLayoutExporter.cs` (Unity Editor, C#)

`extensions/renderers/unity/scripts/Editor/DunGenLayoutExporter.cs`. Two entry points:
- Menu `WorldOS/DunGen ▸ Export Active Dungeon Layout` — exports the `RuntimeDungeon` already generated
  in the open scene.
- Static `DunGenLayoutExporter.Export(flowAssetPath, outPath, seed)` — builds a `DungeonGenerator` from a
  `DungeonFlow` asset, generates deterministically, exports. Callable from unity-mcp `execute_code` for
  the headless GEX44 box drive loop.

It walks the generated `Dungeon`: `AllTiles` → rooms (world AABB `Placement.Bounds` + `Tags` +
`OnMainPath`), `Connections` → doorways (world position + forward), child `MeshFilter`s → props
(transform + world bounds + a **shape class**). DunGen member access is reflection-guarded so a 3.x minor
version rename (`Placement.Bounds`, `Tags`, `UsedDoorways`) degrades with a log rather than throwing
mid-export.

**`dungen_layout.json`** (all coords Unity WORLD units):
```json
{
  "generator": { "seed": 4242, "world_units_per_cell": 2.0, "tile_count": 3 },
  "bounds":   { "min": [x,y,z], "max": [x,y,z] },
  "rooms":    [ { "id", "tags":[…], "is_main_path": bool, "bounds": { "min":[…], "max":[…] } } ],
  "doorways": [ { "id", "room_a", "room_b", "position":[x,y,z], "forward":[x,y,z] } ],
  "props":    [ { "id", "room", "shape_class":"box"|"cylinder"|"cone", "kind_hint",
                  "position":[x,y,z], "bounds":{ "min":[…], "max":[…] } } ]
}
```

### Hop 2 — `dungen_to_fixtures.py` (Python, stdlib-only, deterministic)

`tools/dungen_to_fixtures.py`. Snaps world coords to the 5-ft cell grid and emits the two fixtures. No
schema fork: the geometry json is exactly what `qa/greybox_render_headless.py` and
`tools/derive_room_manifest.py` (lane/eval-upgrade) already consume — verified against the committed
`forest_road_geometry.json` model (`cell_default_walkable: true`; `walls` lists every non-floor cell, so
the derived walkable set == the carved floor).

## Scale mapping (DunGen world units → 5-ft cells)

The engine cell is **5 ft**. The greybox renderer already uses **2.0 world-units-per-cell**
(`greybox_render_headless.cell_to_world` multiplies by 2.0). So the converter default
`--world-units-per-cell 2.0` makes *2 Unity units = one 5-ft cell* and keeps the whole chain
unit-consistent. Cell indexing mirrors the greybox back→front convention (row 0 = max world-Z, col 0 =
min world-X):

```
col = round((wx − min_x) / upc)
row = round((max_z − wz) / upc)
cols = round((max_x − min_x) / upc) + 1 ,  rows = round((max_z − min_z) / upc) + 1
```

For a real purchased tileset (Synty POLYGON Dungeon), retune `--world-units-per-cell` to that tileset's
authored grid pitch — it is the one knob.

## Shape-appropriate proxies (PR #1495 lesson)

Box-trees read as buildings to depth models. The exporter classifies each prop `box|cylinder|cone`; the
converter routes masonry boxes → `crate`/`rubble`, cylinders → `pillar`, cones → `large_tree`, unless the
prop's `kind_hint` already names a known greybox kind (which wins). The emitted `kind` always lands in
`greybox_render_headless._KIND_SPECS`, so a shape-right proxy (height + color) is drawn. NOTE: the headless
renderer draws colored *boxes* per kind; true cylinder/cone volumes are the greybox renderer's job
(`build_room_greybox.cs` can honor them) — the converter's responsibility is the correct shape→kind route.

## Engine-fixture correctness

`<name>.scenegrid.json` validates against the engine `SceneGrid` model (`extra='forbid'`): exterior is
solid rock (`cell_default = void`, non-walkable); carved room/corridor cells are explicit walkable
`floor`; doorways are `door` cells (also listed in `door_cells`); an unconnected doorway becomes a level
`exit`; prop footprints are impassable with `occluder`/`height_band` for the Tier-2 depth proxies.
