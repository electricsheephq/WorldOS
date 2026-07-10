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

It walks the generated `Dungeon`: `AllTiles` → rooms (world AABB + tags + main-path), `Connections` →
doorways (world position + forward), child `MeshFilter`s → props (transform + world bounds + a **shape
class**). DunGen member access is reflection-guarded so a 3.x minor version rename degrades with a log
rather than throwing mid-export.

**Verified against the installed DunGen 3.x source on the GEX44 box (2026-07-11):**
`RuntimeDungeon.Generator` / `.Generate(request=null)`; `DungeonGenerator.{DungeonFlow, Seed,
ShouldRandomizeSeed, CurrentDungeon, Status, GenerateAsynchronously=false}` (synchronous in edit mode,
creates its own Root); `Dungeon.{AllTiles, MainPathTiles, Connections}` (ReadOnlyCollections);
`Tile.{Bounds, Placement, Tags, AllDoorways}`; `TilePlacementData.{Bounds (world), LocalBounds,
IsOnMainPath}`; `DoorwayConnection.{A, B}` (Doorway); `Doorway.{Tile, transform, Tags}`. The exporter
generates via the `RuntimeDungeon.Generate()` path (not a bare generator) so Root + the default request
are set up for us.

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

## Box-phase results (2026-07-11, GEX44)

One real dungeon generated end-to-end and pushed through the full pipeline:

- **Generation:** DunGen Basic Sample flow, seed 12345, `LengthMultiplier 1.0` → **26 tiles, 25 doorways,
  407 props, status=Complete**. Run in-editor via `execute_code` (reflection; `qa/evidence/dungen-spike/
  dungen_generate_and_export.cs`) — no Assets/*.cs deploy, no editor restart, scene self-cleaned.
- **★ Load-bearing API finding:** `DungeonGenerator.DungeonFlow / Seed / ShouldRandomizeSeed` are
  **DEPRECATED (DunGen 2.19)** — the pipeline reads `Generator.Settings` (a `DungeonGeneratorSettings`).
  Setting only the top-level fields fails with `[ArchetypeValidator] No Dungeon Flow is assigned`. Fix:
  set `Generator.Settings.DungeonFlow` (+ Seed/ShouldRandomizeSeed). The committed exporter now sets
  BOTH the modern `Settings` and the legacy fields.
- **Converter → fixture:** 84×94 cell grid, 2247 floor cells, 407 props, 25 doors. The whole-dungeon
  `dungen_basic.scenegrid.json` **validates against the engine `SceneGrid` model** (6512 cells, 7804
  impassable). Per-room crop `room_1` = 12×7 → greybox rendered through the real headless path.
- **Registered plate** (flux depth-CN, crypt recipe, on the DunGen room greybox):
  - `control_strength 0.70` → edge-recall **0.7418** (richly firelit-painted; below the 0.95 masonry gate)
  - `control_strength 0.95` → edge-recall **0.9894** (PASSES the gate; but under-painted/dark)
  - the documented alignment↔painterly-quality tradeoff — the adopted pipeline's Gemini **style-pass
    (step 2) reconciles it, but that step is NOT yet wired to a CLI flag** (only step-1 base ran).
- **5-scorer blind panel** (candidate-only, uncalibrated — no in-band real-art control for an ad-hoc
  room): median **2.0/10**, unanimous house-style "lesser", zero character defects. The bare DunGen room
  is structurally valid but under-dressed; a single flux base pass reads as abstract vs the PoE2 bar.

## DunGen vs Tessera comparison rubric

The Tessera arm (`TesseraLayoutExporter.cs`, #1513) **is merged** and emits the SAME layout-json shape, so
`tools/dungen_to_fixtures.py` consumes it with **no schema fork** — verified here by converting #1513's
committed `qa/evidence/tessera-spike/synth_tessera_layout.json` → a valid fixture + geometry (the key
architectural claim: ONE converter, both generators). Tessera IS imported on the box (`Assets/Tessera`,
ready sample scenes). A **real** Tessera box generation + plate + panel is a scoped follow-up (a full
second lap; the plate quality is generator-independent, so it would show the same recipe-side tradeoff as
DunGen). Tessera adds one field, `rooms[].cell_positions` (non-rectangular WFC "big tiles"); the converter
tolerates it via the bounds AABB today — consuming it for exact footprints is a future refinement.

| Criterion | DunGen (real box run) | Tessera Pro |
|---|---|---|
| Programmatic layout export | ✅ `Dungeon.AllTiles`/`Connections`/child meshes → json | ✅ exporter merged (#1513); box run = follow-up |
| Exporter approach | reflection (no asmdef ref); 1 deprecated-field trap (fixed) | direct refs vs Tessera 6 docs; +`cell_positions` |
| Generation entry (headless) | `RuntimeDungeon.Generate()`, synchronous in edit mode | `TesseraGenerator` (sample scenes ready) — TBD |
| Rooms / doors / props | 26 / 25 / 407 (Basic flow) | TBD (box run) |
| Same layout-json → same converter | ✅ | ✅ **converts to a valid fixture (proven on synth layout)** |
| Converts to valid engine SceneGrid | ✅ validates against the model | ✅ (synth layout) |
| Registration achievable (masonry gate) | ✅ 0.9894 @ cs0.95 | expected same (plate is generator-independent) |
| Bare-room plate panel quality | 2.0/10 (under-dressed room; recipe-side) | expected same (recipe-side, not generator-side) |
| Verdict | **ADOPT for structure; iterate the plate recipe** | ADOPT-compatible; run a real box gen to confirm |
