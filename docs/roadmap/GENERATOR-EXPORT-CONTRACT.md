# Generator export contract (epic #1508 — DunGen + Tessera Pro arms)

**Date:** 2026-07-11 · **Status:** SPIKE, repo-side complete for both arms. Renamed from
`DUNGEN-EXPORT-CONTRACT.md` (PR #1509) once the Tessera Pro arm landed — this is now the shared contract
for BOTH generator-comparison arms the owner asked for: DunGen (room-graph, PR #1509, verified against
the installed source on the GEX44 box) and Tessera Pro (tile-WFC, this PR, verified only against the
public docs — no box access, no vendored source in this repo). The box session referenced throughout
(`qa/evidence/dungen-spike/BOX-DRIVE-RECIPE.md`) drives BOTH arms and fills in the comparison rubric at
the bottom of this doc.

## The invariant this honors

The Python engine is the **sole writer** of level-structure truth. Neither generator runs at game
runtime; each *proposes* a layout at authoring time. We export that proposal to JSON and bake it into
engine fixtures. The baked `*.scenegrid.json` is the authored fixture the engine loads — neither DunGen
nor Tessera ever becomes a second writer of grid state.

## The two hops (shared by both arms)

```
DunGen scene   ──[DunGenLayoutExporter.cs]───▶ dungen_layout.json   ──┐
                                                                       ├─[dungen_to_fixtures.py]──▶
Tessera scene  ──[TesseraLayoutExporter.cs]──▶ tessera_layout.json ──┘
    (a) <name>.scenegrid.json   (engine SceneGrid fixture — the sole-writer truth)
    (b) <name>_geometry.json     (greybox geometry json — greybox_render_headless + derive_room_manifest)
    (b') <name>_<room>_geometry.json  (--room: one cropped room = the registered-plate input)
```

Both exporters emit the **same top-level layout-json shape** (`generator` / `bounds` / `rooms` /
`doorways` / `props`) so `tools/dungen_to_fixtures.py` (hop 2) is a **single converter for both arms** —
no schema fork, no generator-specific branch in the Python. Where Tessera's tile-WFC model doesn't map
1:1 onto DunGen's continuous room-graph, the schema is extended **additively** (new optional fields the
converter tolerates when absent) rather than forked — see "The additive schema" below.

---

## Arm 1 — DunGen (room-graph)

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

---

## Arm 2 — Tessera Pro (tile-WFC)

### Hop 1 — `TesseraLayoutExporter.cs` (Unity Editor, C#)

`extensions/renderers/unity/scripts/Editor/TesseraLayoutExporter.cs`. Written repo-side, no box access
and no vendored Tessera source in this repo. Two entry points mirroring DunGen's:
- Menu `WorldOS/Tessera ▸ Export Active Tessera Layout` — finds the first `TesseraGenerator` in the open
  scene and generates + exports.
- Static `TesseraLayoutExporter.Export(generatorObjectName, outPath, seed)` — `generatorObjectName` may
  be empty to fall back to the first `TesseraGenerator` found, or named to disambiguate a scene that hosts
  BOTH a DunGen `RuntimeDungeon` and a Tessera generator side by side (the likely shape of the box
  session's comparison scene). Callable from unity-mcp `execute_code`.

Unlike DunGen's `ExportActive` (which reads an already-generated `CurrentDungeon`), both Tessera entry
points always call `Generate()` themselves — `TesseraGenerator` does not document an accessible "last
completion" property to read back from, so re-generating deterministically via `seed` was the only
verifiable path.

**API verification status** (checked against the public docs, `boristhebrave.com/docs/tessera/6/api/`,
version 6 — the current documented major as of 2026-07-11 — NOT against the installed package):

| Status | Member |
|---|---|
| ✅ Verified (docs page confirms signature) | `TesseraGenerator.{bounds (Bounds), cellSize (Vector3), Generate(TesseraGenerateOptions=null) → TesseraCompletion, GetGrid() → IGrid}` |
| ✅ Verified | `TesseraGenerateOptions.seed (int?)` |
| ✅ Verified | `TesseraCompletion.{success (bool), tileInstances (IList<TesseraTileInstance>), contradictionLocation (Vector3Int?), contradictionReason (string)}` |
| ✅ Verified | `TesseraTileInstance.{Cell (Vector3Int), Cells (Vector3Int[]), Position (Vector3, WORLD), Rotation (Quaternion, WORLD), CellRotation, Tile (TesseraTileBase)}` |
| ✅ Verified | Default instantiation parents spawned tile copies as **children of the generator's own transform** (docs: "`TesseraGenerator` will instantiate copies of all the tiles as child objects") |
| 🚩 **Flagged — unverified beyond docs** | Whether the spawned child's world position/rotation is set to **exactly** `TesseraTileInstance.Position`/`.Rotation`. The exporter's prop scan associates each tile instance with its spawned GameObject by **nearest-position match** against the generator's direct children — if the real default instantiate offsets differently, props will silently come up empty for that tile (rooms/tiles/bounds are unaffected — those come straight off `TesseraCompletion`, not the matched GameObject) |
| 🚩 **Flagged — out of scope** | Anisotropic / non-square grids (hex, triangle, deformed Sylves grids). This exporter assumes a plain square/rectangular grid (`cellSize.x == cellSize.z`), matching DunGen's own scale-mapping assumption. Flag to the box session if the comparison scene uses a hex/triangle Tessera generator |
| 🚩 **Flagged — genuine capability gap, not a bug** | `PathConstraint` "on critical path" membership is not exposed on `TesseraCompletion`/`TesseraTileInstance` in anything documented; `is_main_path` is always emitted `false` for the Tessera arm. Score this under "constraint expressiveness" below, don't fake it |
| 🚩 **Flagged — genuine capability gap** | Tessera has no native doorway/connection object (WFC connects tiles by face-matching, not an explicit `Doorway` type). `doorways` is always emitted empty. A tagged "door" child prop, if the tile prefab has one, still round-trips through the generic props path like any other `kind_hint` |

**`tessera_layout.json`** (all coords Unity WORLD units, same top-level shape as `dungen_layout.json` plus
the additive fields below):
```json
{
  "generator": { "kind": "tessera_wfc", "seed": 99, "world_units_per_cell": 2.0, "tile_count": 2 },
  "bounds":    { "min": [x,y,z], "max": [x,y,z] },
  "rooms":     [ { "id", "tags": [], "is_main_path": false,
                   "tile_name", "cell_rotation",
                   "bounds": { "min":[…], "max":[…] },
                   "cell_positions": [[x,y,z], …] } ],
  "doorways":  [],
  "props":     [ { "id", "room", "shape_class":"box"|"cylinder"|"cone", "kind_hint",
                   "position":[x,y,z], "bounds":{ "min":[…], "max":[…] } } ]
}
```

### The additive schema

One tile instance = one `rooms[]` entry (keeps DunGen's 1:1 tile=room mapping). `props` is byte-identical
in shape to DunGen's. Two fields are **additive** because Tessera's tile-WFC model has no 1:1 DunGen
analog:

- **`rooms[].cell_positions`** — the WORLD-space center of every grid cell the tile instance occupies
  (length 1 for a normal single-cell tile, >1 for a Tessera "big tile"). This closes the one genuine
  1:1-mapping gap: a multi-cell WFC tile's true footprint can be **non-rectangular** (an L-shape, say);
  rasterizing its bounding AABB (DunGen's original approach) would over-carve a cell that was never
  actually part of the tile. `tools/dungen_to_fixtures.py`'s `_room_footprint()` helper prefers
  `cell_positions` when present and carves the exact set; it falls back to AABB rasterization when the
  field is absent, so **DunGen layouts (which never carry this field) are byte-identical to the
  pre-Tessera behaviour** — regression-pinned in `qa/test_tessera_to_fixtures.py`. `rooms[].bounds` is
  still always ALSO emitted (the AABB across `cell_positions`) for backward-compat with any bounds-only
  consumer.
  ⚠ **Scoping the "exact" claim:** `TesseraLayoutExporter.cs` computes each cell's world position as
  `Position + (Cells[i] - Cell) * cellSize` — an axis-aligned, **unrotated** approximation (documented as
  FLAGGED (1) in the exporter's header). For an un-rotated big tile this is exact; for a **rotated**
  Tessera big tile, the true world footprint can diverge from this approximation, and the converter has
  no way to detect that from `cell_positions` alone — it consumes whatever the exporter emitted as ground
  truth. `qa/test_tessera_to_fixtures.py`'s "exact non-rectangular footprint" assertions are exact
  relative to this unrotated model, not a guarantee against the real installed package. **The box session
  must re-verify footprint fidelity specifically for a rotated big tile** before the "export fidelity"
  rubric row below is scored for Tessera.
- **`rooms[].tile_name`, `rooms[].cell_rotation`** — purely descriptive (the source `TesseraTile`'s name
  and the placed rotation), ignored by the converter, useful for the comparison rubric / box-session
  debugging.
- **`generator.kind`** — informational tag (`"tessera_wfc"`), ignored by the converter.
- **`doorways: []`** — Tessera has no native doorway object; `tools/dungen_to_fixtures.py` already
  tolerated a missing/empty `doorways` list before this PR (`layout.get("doorways", [])`), so **no
  converter change was needed** for this gap specifically — only `cell_positions` required one.

⚠ **`qa/evidence/tessera-spike/synth_tessera_layout.json` is schema-valid but NOT pathing-valid.** Its two
rooms are disconnected floor islands (no doorway carves a path between them, matching the "Tessera has no
native doorway object" gap above) — this fixture only proves the conversion's schema/footprint correctness
(`SceneGrid(**fixture)` validates, `qa/test_tessera_to_fixtures.py` covers it), NOT that a Tessera export
produces a connected, walkable dungeon. It would fail the engine's own `validate_scene_grid` connectivity
gate (`servers/engine/scene_grid.py`) if that gate were ever run over it. Don't cite this fixture as
evidence of connectivity — that property depends on the actual Tessera scene / constraints used on the box.

### Hop 2 — `dungen_to_fixtures.py` (unchanged file, one additive change)

`tools/dungen_to_fixtures.py`. The only change for the Tessera arm is the `_room_footprint()` helper
described above; everything else (prop kind-mapping, doorway/exit derivation, SceneGrid/geometry
building) is untouched and shared by both arms. `qa/test_tessera_to_fixtures.py` covers: the additive
footprint carving (exact vs AABB), the DunGen-shape fallback (regression pin), an end-to-end synthetic
Tessera layout through `convert()`/`build_scenegrid()`/`build_geometry()` with a non-rectangular big tile
and zero doorways, and validation against the engine's own `SceneGrid` model.

## Scale mapping (world units → 5-ft cells)

The engine cell is **5 ft**. The greybox renderer already uses **2.0 world-units-per-cell**
(`greybox_render_headless.cell_to_world` multiplies by 2.0). So the converter default
`--world-units-per-cell 2.0` makes *2 Unity units = one 5-ft cell* and keeps the whole chain
unit-consistent for either arm. Cell indexing mirrors the greybox back→front convention (row 0 = max
world-Z, col 0 = min world-X):

```
col = round((wx − min_x) / upc)
row = round((max_z − wz) / upc)
cols = round((max_x − min_x) / upc) + 1 ,  rows = round((max_z − min_z) / upc) + 1
```

Retune `--world-units-per-cell` per generator/tileset (DunGen's Synty POLYGON Dungeon vs whatever tileset
the Tessera comparison scene uses) — it is the one knob, unchanged by this PR.

## Shape-appropriate proxies (PR #1495 lesson)

Box-trees read as buildings to depth models. Both exporters classify each prop `box|cylinder|cone`; the
converter routes masonry boxes → `crate`/`rubble`, cylinders → `pillar`, cones → `large_tree`, unless the
prop's `kind_hint` already names a known greybox kind (which wins). The emitted `kind` always lands in
`greybox_render_headless._KIND_SPECS`, so a shape-right proxy (height + color) is drawn. NOTE: the headless
renderer draws colored *boxes* per kind; true cylinder/cone volumes are the greybox renderer's job
(`build_room_greybox.cs` can honor them) — the converter's responsibility is the correct shape→kind route.

## Engine-fixture correctness

`<name>.scenegrid.json` validates against the engine `SceneGrid` model (`extra='forbid'`): exterior is
solid rock (`cell_default = void`, non-walkable); carved room/corridor cells are explicit walkable
`floor`; doorways are `door` cells (also listed in `door_cells`); an unconnected doorway becomes a level
`exit`; prop footprints are impassable with `occluder`/`height_band` for the Tier-2 depth proxies. This
holds for both arms — verified per-arm in `qa/test_dungen_to_fixtures.py` and
`qa/test_tessera_to_fixtures.py`.

---

## Comparison rubric (for the box session — `qa/evidence/dungen-spike/BOX-DRIVE-RECIPE.md`)

Score each dimension for BOTH arms once a real scene has been generated, exported, converted, and
greybox-rendered on the box. This section is the template the box session fills in; it is intentionally
empty of verdicts here (repo-side has no box access to produce a real generation to score).

| Dimension | DunGen | Tessera Pro | Notes |
|---|---|---|---|
| **Export fidelity** — does the exported json faithfully reproduce what was generated in-editor (room shapes, prop placement, no silently-dropped data)? | _(fill in on the box)_ | _(fill in on the box — pay particular attention to the flagged prop-association heuristic above; if props come up empty, that's the signal the position-match assumption didn't hold. ALSO specifically re-verify a **rotated** big tile's `cell_positions` against the real generated footprint — the exporter's unrotated-approximation caveat above means the repo-side test suite cannot prove fidelity for that case)_ | |
| **Door/connection handling** | DunGen has native `Doorway`/`Connection` objects — exported directly, world position + forward. | No native doorway object; `doorways` is always empty. Any door-like geometry only surfaces if it round-trips as a generic prop via `kind_hint`. | This is Tessera's clearest structural gap vs DunGen for this pipeline — confirm on the box whether Tessera tile prefabs in the comparison set tag doors as child objects at all |
| **Constraint expressiveness** — how much authorial control over the result (paths, symmetry, tile budgets, region tagging)? | DunGen: `DungeonFlow` graph (tile sets, branching, length) + `IsOnMainPath`. | Tessera Pro: `PathConstraint`, `BorderConstraint`, `MirrorConstraint`, `CountConstraint` (Pro-only) — richer constraint vocabulary per the docs, but `is_main_path`-equivalent data isn't exposed through the export path built here (see flagged gap above) | Score the GENERATOR's actual expressiveness, not just what this exporter currently surfaces — note where the exporter itself is the limiting factor vs. Tessera itself |
| **Generation speed** — wall-clock for a comparable-size layout, editor vs headless | _(fill in on the box)_ | _(fill in on the box)_ | Use the SAME seed/tile-budget/room-count target for both arms if possible |

## Box comparison RESULTS — DunGen vs Tessera (2026-07-11, GEX44, both arms real)

Both arms generated, exported, converted, greybox-rendered, plated, and paneled on the box. DunGen: Basic
Sample flow, 26 tiles/25 doorways/407 props. Tessera: Castle sample (native 9×5×9, `size.y=1` was
unsolvable — castle tiles have vertical-adjacency constraints), seed 12345 → 396 tiles. Tessera API
verified against the installed Tessera 6 package (matches `TesseraLayoutExporter.cs`'s VERIFIED assumptions
exactly: `TesseraGenerator.{cellSize,bounds,Generate}` → `TesseraCompletion.{success,tileInstances}`;
`TesseraTileInstance.{Position,Cell,Cells,Tile,CellRotation}`).

| Dimension | DunGen | Tessera Pro | Winner |
|---|---|---|---|
| **Export fidelity** | 26 tiles + 25 doorways + **407 props** exported; rooms=bounds+tags, props from child `MeshFilter`s. | 396 tiles + `cell_positions` + bounds exported cleanly; **props = 0** — the nearest-child-by-`Position` prop-association heuristic (FLAGGED-1) matched nothing on the castle tileset (tile geometry isn't a direct positioned child within 0.25·cellSize). Tiles/bounds/`cell_positions` faithful; props need a box-verified association fix. | **DunGen** (props) |
| **Door/connection handling** | Native `Doorway`/`Connection` → **25 doorways** (position+forward) → `door` cells + exits in the fixture. | **0 doorways** — Tessera has no native connection object (WFC face-matching). Rooms become disconnected floor islands unless a `PathConstraint` + tagged door-prop is used. | **DunGen** (decisive) |
| **Constraint expressiveness** | `DungeonFlow` graph (tile sets, branching, length) + `IsOnMainPath` (level flow exported). | Richer raw constraint vocab (`Path`/`Border`/`Mirror`/`Count`Constraint, Pro-only), but `is_main_path`-equivalent isn't exposed through the export path → always `false`. Great for tile-field coherence, weaker for exported "level flow". | DunGen for level flow; Tessera for tile coherence |
| **Generation speed** | 26 tiles, synchronous in edit mode, one `execute_code` call (sub-second effective). | 396 tiles, synchronous in edit mode, one `execute_code` call (sub-second effective; opened+restored a sample scene). | Tie (both fast/headless) |
| **Converts via the ONE converter** | ✅ `tools/dungen_to_fixtures.py` → 84×94 fixture, validates against `SceneGrid` model. | ✅ **same converter, no schema fork** (`cell_positions` path) → 10×10 fixture, validates against `SceneGrid` model. | **Tie — the architecture win** |
| **Registration (masonry gate 0.95)** | 0.9894 @ cs0.95 (PASS). | 0.9226 @ cs0.95 (just below; the scattered castle-wall pattern is harder to lock). | DunGen |
| **Bare-structure plate panel** | median 2.0/10. | median 2.0/10 (3-scorer). | Tie — **plate quality is generator-independent** (recipe-side) |

### DunGen-vs-Tessera verdict for #1508 stage 1
**ADOPT DunGen as the primary dungeon/room STRUCTURE generator.** For a room-graph pipeline whose fixture
needs walkable floor + **door cells** + occluder props, DunGen's native doorways/connections + room-graph
flow + clean prop export map directly onto the engine `SceneGrid`; Tessera's lack of a native connection
object is the decisive gap (rooms export as disconnected islands). **Keep Tessera Pro for tile-dense WFC
set-dressing / exterior fields** (walls, terrain, buildings) where its constraint vocabulary shines and
connectivity is not the point. The load-bearing win is architectural: **both generators feed the identical
`tools/dungen_to_fixtures.py`** with no schema fork — the generator is a swappable authoring front-end,
the engine stays the sole writer. (Plate painterly quality — 2.0/10 for both bare structures — is a
recipe-side concern, not a generator differentiator; wire the Gemini style-pass step 2.)
