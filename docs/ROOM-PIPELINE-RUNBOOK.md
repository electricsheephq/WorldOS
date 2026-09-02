# ROOM PIPELINE RUNBOOK — author a room end-to-end (cold-agent bootstrap)

> **You were told "make a room" and given this repo. This page is the whole pipeline.** Read it top
> to bottom, then execute — you do not need any prior conversation's context. Every step names its
> exact file/command and the decision record that ratified it. This is the TRUE-GREYBOX lane (epic
> #1508): geometry-first, registration by construction, no paint-vs-grid drift.

> **★ RESUME PROTOCOL (cold start / after compaction — read in this order):** (1) epic #1581 + its
> OPEN sub-issues (the queue of what's left) → (2) `VISION.md` → "The Room Readiness Pipeline" +
> the WALKABILITY / GEOMETRY-IS-GROUND-TRUTH doctrine → (3) THIS runbook → (4) the active plan's
> STATE block → then run `qa/room_pipeline.py --resume`. The goal/queue/process/state live in the
> repo + GitHub, never only in an agent's head — that is the fix for the #1 failure (a process that
> lived in context died at compaction and regressed a whole feature, 2026-07-15).
>
> **★ THE SHIP GATE (non-negotiable): a room ships only when BOTH gates are green — the BEAUTY panel
> AND the automated WALKABILITY test (`qa/walk_test.py`, step 11).** Beauty ≠ walkable: we shipped
> 3 panel-8+ rooms on 2026-07-15 that were unwalkable (walk-through-walls, spawn-in-barrel, no
> occlusion) because a walkability gate did not exist as automation. `qa/room_pipeline.py` exits
> non-zero without walk-green; a human "ship it" no longer substitutes for it.

**Preflight (do this before any command below):** read `docs/OPERATIONS.md` first — it's the
general cold-start bootstrap (worktree discipline, box claim etiquette, the Universal Run Contract)
this page specializes for room authoring. Verify `pwd` before running anything: you should be inside
a real checkout of this repo (`/Users/m1/WorldOS` or an approved same-disk worktree per
OPERATIONS.md's worktree-discipline section) — never a bare scratch directory. Any step below that
touches the live Unity project (step 9's `plates_manifest.json`, step 10's player rebuild) requires
the canonical checkout or box path named in that step; don't improvise a different location.

## The one invariant every step below honors

The Python engine (`servers/engine/`) is the **sole writer** of level-structure truth (walkable
cells, occluders, pathing). Every artifact below — greybox, plate, manifest, VFX anchor — is a
**presentation-layer derivation** of engine-authored geometry, never a second writer of it. If a
step you're adding would let art or a generator invent grid truth independently, it's wrong by
construction — see `docs/roadmap/GENERATOR-EXPORT-CONTRACT.md`'s framing of this same rule for the
generator arms.

## Scale convention — 1 cell = 5 ft ≈ 1 human

The engine cell is **5 ft**. The greybox renderer's world scale is **2.0 world-units per cell**
(`greybox_render_headless.cell_to_world`), so `2 world-units == 1 cell == 5 ft`. Size every
authored prop against that ruler — **a standing human is ~1 cell wide**, so a prop's footprint
should read as a multiple of "how many people could stand there," not an eyeballed blob. This
convention is what makes cross-tool math (author → derive → DunGen/Tessera world-units-per-cell)
compose without a unit-conversion bug; see "Scale mapping" in `GENERATOR-EXPORT-CONTRACT.md`.

## The RICHNESS PRINCIPLE (load-bearing lesson, PR #1528 / CRYPT-REPLICATE)

**Paint richness follows GEOMETRY richness.** CRYPT-REPLICATE spent a full style-pass iteration
loop trying to out-paint a drift-rich incumbent plate (best honest result: 7.1, vs the incumbent's
7.8) and could not close the gap with cosmetic style-pass levers alone. The honest path past a
richer incumbent is **authoring a denser greybox** — more prop volumes = richer paint AND richer
collision, simultaneously, because both are derived from the same geometry. Do not reach for "one
more style-pass iteration" to fix a plate that reads as sparse; reach for `author_room_geometry.py`
and add prop volumes instead. (Full note: `docs/RUNBOOK-INDEX.md` LARGE SPACES row / PR #1528.)

---

## Worked example — a room from scratch, start to finish

**NEW-ROOM-TAVERN (epic #1508, merged 2026-07-11) ran every step below, in order, on a room with no
prior plate or seed** (proving the method generalises beyond regenerating an existing incumbent):
geometry (`author_room_geometry.py tavern`, 6 props, world-true 12×10) → cutaway greybox + sidecars
(coherence-green 6/6) → derived manifest → registered flux depth-CN base (edge-recall 0.980 ≥ 0.95
masonry gate) → Gemini-noref layered style pass (best-of-3, seed123 adopted) → blind 5-scorer panel
(candidate median 7.0 vs the registered PoE2 control 9.0, Δ−2.0 in-band ⇒ `tier=stable`) →
`promote.py` (automated PASS) → `room_recipes.json` + `plates_manifest.json` wiring, ready for the
box's next rebuild. Productive wall-clock: ~40 min. If a step below is ambiguous, that PR's diff and
evidence (`qa/evidence/new-tavern/`) is the concrete ground truth to check against.

## The pipeline, step by step

### 1. Author geometry — `tools/author_room_geometry.py`

Emits a `room_geometry.json` (the `export_scene_grid.py`-shaped input every downstream tool
consumes) directly from a room's own prop constants, at CORRECT WORLD SCALE — each prop sized as a
kind's proxy volume (height from `greybox_render_headless._KIND_SPECS`) extruded on cells sized to
the true 5-ft-grid object, not an eyeballed/drifted footprint.

```bash
EVIDENCE_ID=1234   # this PR/issue's number — write geometry under it, NOT /tmp
mkdir -p "qa/evidence/${EVIDENCE_ID}/"
python3 tools/author_room_geometry.py crypt -o "qa/evidence/${EVIDENCE_ID}/crypt_geometry.json"
python3 tools/author_room_geometry.py camp  -o "qa/evidence/${EVIDENCE_ID}/camp_geometry.json"
```
**Write outputs under the repo, not `/tmp`:** `derive_room_manifest.py` (step 2) stamps the derived
manifest's `source_geometry` field by resolving your `-o` path repo-relative (`tools/derive_room_manifest.py:_repo_relative`);
a path outside the repo (e.g. `/tmp/...`) falls through to being stored verbatim as a transient
absolute path, breaking the derived manifest's single-source/reproducibility promise the moment that
`/tmp` file is cleaned up. Use a committed evidence/artifact path for anything you intend to derive
a manifest from.

**Shape-appropriate proxies (PR #1495 lesson):** box-shaped trees read as buildings to depth
models. Route each prop through the kind that matches its true silhouette (cylinder → pillar, cone
→ tree, box → crate/rubble/masonry) — the generator-export converter (`tools/dungen_to_fixtures.py`)
applies the same shape→kind rule for generator-sourced props (see step 8a).

**ENCLOSED ROOMS need a CUTAWAY greybox (tavern finding, PR #1531; codified from the CRYPT-RICH
cold-agent run, PR #1538):** full-height near walls poison the interior's depth/lighting read (NCC
collapses, the frame goes mostly black). Author enclosed interiors with `wall_height ≈ 5` on the
near/south walls (the camera-facing side stays open, the back walls keep height). Two artifacts to
avoid, both measured in #1538's panels:
- **Crenellation:** authoring wall bands as PER-CELL boxes gives the depth map toothed/castellated
  wall-tops → the depth-CN paints a "tiled/gamey" motif panels penalize. Author each wall run as
  ONE CONTINUOUS box spanning its cells, not a stack of cell-sized boxes.
- **Over-dark frames:** a cutaway that still leaves >~40% of the canvas as unlit wall/void reads
  as a black-margin defect at panel time — check `visual_pregate.py`'s frame-lit gate on the BASE,
  not just the styled result.

**Geometry schema** (mirrors `qa/export_scene_grid.py`):
```
{cols, rows, material, cell_default_walkable, walls, props:[{id, kind, cells}],
 impassable, door_cells, protected_lane_cells}
```
`walls` is every non-walkable cell (true perimeter + every prop footprint cell, conflated —
`greybox_render_headless` dedupes prop cells out of the wall boxes for rendering).

Deterministic, offline, read-only w.r.t. engine state.

### 2. Derive the manifest — `tools/derive_room_manifest.py`

**Owner playtest #5 architecture decision: the greybox geometry is the single source of truth for
a room's FOOTPRINT + OCCLUSION + WALKABLE; manifests are DERIVED from it, never hand-authored.**
This is what kills paint-vs-grid drift at the source instead of patching it downstream.

```bash
python3 tools/derive_room_manifest.py "qa/evidence/${EVIDENCE_ID}/crypt_geometry.json" \
    -o qa/room_manifests/crypt.cells.json --room crypt --recipe-key crypt
```

Per prop this computes:
- **footprint** — the impassable FLOOR cells (collision + `check_grid_paint_coherence.py`'s
  correctness check).
- **occlusion** — the screen-space SILHOUETTE cells (a tall prop's silhouette rises up-screen off
  its floor footprint — strictly contains but is offset from the footprint).
- **screen_bbox** — the footprint reprojected under the contract camera.

**Footprint-vs-occlusion is the distinction CAMP-TUNE's defect #5 turned on** (see the recall table
below, `qa/evidence/journey-eval-first-run/RECALL.md`): a per-prop occlusion hull computed as the
bounding box over a whole multi-cell footprint can blanket far more of the room than the prop
actually occupies (a 9-cell L-shaped wall produced a 48-cell occlusion hull covering the room's
exit). **Keep individual prop runs SHORT** (2-4 cells) specifically so each one's occlusion hull
stays tight to itself — this is now a standing authoring rule, not just a one-off fix.

Manifests are stamped `derivation: "derived"` + their source geometry — distinguishing them from
legacy `measured` manifests (`qa/build_room_manifest.py` reconstructions) that exist only until a
room's geometry JSON is authored.

### 3. Greybox render (the shaded base + optional depth/normal sidecars)

The shaded greybox render is BOTH the plate's visual base AND the ControlNet `controlImage` for
step 4 — one artifact, two consumers, guaranteeing base and control agree pixel-for-pixel:

```bash
python3 qa/greybox_render_headless.py "qa/evidence/${EVIDENCE_ID}/crypt_geometry.json" "qa/evidence/${EVIDENCE_ID}/crypt_greybox.png"
```

This is the verified camera rig (`greybox_render_headless` — the #1396 recipe, <1e-3 vs Unity;
dimetric, elevation 30°, yaw 45, `cell_size 2.0`, `ortho_size 13`).

**Optional depth+normal sidecars** (`qa/greybox_sidecars_headless.py`) — a pure-PIL analog of the
box `CohesionProbe.cs` G-buffer, co-registered pixel-for-pixel with the greybox render:
```bash
python3 qa/greybox_sidecars_headless.py "qa/evidence/${EVIDENCE_ID}/crypt_geometry.json" \
    "qa/evidence/${EVIDENCE_ID}/crypt_depth.png" "qa/evidence/${EVIDENCE_ID}/crypt_normal.png"
```
**Scope note (a PLATE SPRINT finding, not a live dependency):** the ADOPTED recipe (step 4) does
**not** consume these sidecars — Scenario derives the depth control server-side from the shaded
greybox `controlImage` directly. These sidecars exist for parity with the crypt relight-lane
artifact and as a reproducible no-box path for any future relight; issue #1481 concluded the
WOSRelight lane that DID consume them should **stop** (shared-greybox sidecars stamped
vertical-banding seams onto warm plates — only a per-plate sidecar would be safe). Don't wire a new
recipe to these unless you've re-read #1481 first.

### 3a. THE EXTENT CONTRACT — the painted room must equal the playable grid (#1543, M-ALIGN)

**The defect (owner playtest #8):** the fixed ortho=13 rig leaves canvas margins around a small room,
and the style pass out-paints those margins into "more room" — the tavern painted a room LARGER than
its authored 12×10 grid (unreachable painted floor, invisible walls at the grid edges, edge cells on
painted furniture). Two amendments kill it at the recipe level, both **strictly opt-in** (a room
without them renders byte-identical — the registration/coherence instruments share the fixed rig):

- **CAMERA-FIT** — render the greybox with `--camera-fit` (or a geometry field `"camera_fit": true`):
  `greybox_render_headless` computes the ortho SCALE from the room's own grid extent so the grid
  diamond + perimeter wall band fills the frame edge-to-edge — no margin left to out-paint. Only the
  ortho scale changes; the dimetric basis (`cell_to_world`/`world_to_screen`) is identical.
  ```bash
  python3 qa/greybox_render_headless.py <geometry.json> <greybox.png> --camera-fit --wall-height 5
  ```
- **PERIMETER WALL BAND** — for ENCLOSED rooms, author the perimeter as continuous wall RUNS
  (`author_room_geometry._perimeter_wall_run_props`, kind `wall_run`: one box per edge run, split at
  doors — the #1539 no-crenellation rule), so painted walls sit ON impassable cells by construction
  and the door gap stays walkable. `tavern_fit` is the reference room emitting both (`camera_fit` +
  the wall band); keep `tavern` as-is (its geometry is pinned by the engine-grid tests).

**HOTFIX for already-painted plates** — you can't un-paint an out-painted plate, but you can mask it:
`tools/mask_plate_extent.py <plate> <geometry.json> -o <masked.png>` feathers everything OUTSIDE the
grid diamond + wall band to a dark vignette (deterministic, PIL-only), so the room fades to darkness
instead of showing unreachable painted floor. Evidence: `qa/evidence/1543/` (masked tavern +
before/after). Adopting a masked plate into `plates_manifest.json` is a separate, deliberate step.

### 4. Registered base — flux depth-ControlNet (`docs/roadmap/PLATE-RECIPE-DECISION.md`)

**Adopted pipeline (DECIDED 2026-07-10, supersedes the implicit `model_z-image` img2img default):**
1. **flux.1-dev + depth-ControlNet base** from the room greybox — registration by construction (the
   paint is conditioned directly on the authored geometry, so it cannot drift from it).
2. Via `extensions/renderers/godot/tools/generate_room.py --controlnet depth` (or
   `qa/plate_loop.py`'s `generate.controlnet` config field) — the greybox is `--base-plate`, Scenario
   resolves it to `controlImage` with `controlModality=depth`.

### 5. Coherence gate — `qa/check_grid_paint_coherence.py`

The ABSOLUTE grid↔paint coherence gate (#1462/#1491) — this is what would have caught the
sarcophagus incident (engine cells legal, but the paint sat ~3/4 cell off the grid footprint;
actor stood on the authored impassable cell while the painted prop was elsewhere).

- **Why `check_plate_drift.py` (relative) doesn't catch this:** that gate only asserts a regen
  keeps a prop where a KNOWN-GOOD baseline had it — a prop that has ALWAYS been off-grid passes
  forever. This gate is **absolute**: no baseline needed, it checks the paint against the grid's
  OWN authored footprint.
- **Method:** regenerate the grid's structural signature from the manifest FOOTPRINT via the same
  contract greybox rig; build an edge template per prop (mean-subtracted, L2-normalised silhouette
  edges — the modality-invariant bridge greybox↔paint); localise it in the plate's edge map via
  normalised cross-correlation; any prop whose peak offset exceeds `MAX_OFFSET_CELLS` (0.5) →
  **INCOHERENT, fail loud.**
- **Reliability scope:** hard-silhouette props (pillars, sarcophagi, walls, altars) localise
  reliably. Tall organic props (tree foliage) present a poor box-silhouette match — reported as a
  diagnostic, not a blocking CI signal, for that class.

Run it against every registered candidate before it goes to the panel.

**Scope note (from the #1538 cold-agent run):** this gate is a *diagnostic instrument*, not the
promotion floor — `promote.py`'s panel-delta gate is the floor. Organic/legacy-styled incumbents
(including the deployed 8.0 crypt) fail cells of this gate identically; a cold agent should not
read a coherence FLAG on the candidate as blocking when the incumbent FLAGs the same cells. What
IS blocking: the candidate regressing coherence on cells the incumbent passes.

### 6. Style pass — the reference-images LAW + structure/dimetric locks

**Gemini instruction-edit** (`model_google-gemini-3-1-flash`) over the flux depth-CN base, with two
mandatory prompt clauses. **★ No single reusable CLI wraps this exact step today — don't confuse it
with two DIFFERENT, already-wired mechanisms that also touch Gemini:**
- `generate_room.py --style-pass <json>` is a **z-image + LoRA img2img** pass (model/loras/
  lorasScale/strength) — not Gemini at all.
- `generate_room.py --layered` chains its OWN two Gemini instruction-edit passes
  (`_run_gemini_pass`, pass2 detail/populate + pass3 staging-last) — a different pipeline stage,
  for a different purpose, than the base-registration style pass described here.
- The adopted camp-armB / crypt-replicate style pass (the one `PLATE-RECIPE-DECISION.md` documents)
  was run as a direct Scenario Gemini instruction-edit job against the flux depth-CN base, using the
  prompt text committed at e.g. `qa/evidence/plate-sprint/camp-armB/style_pass_prompt_winning.txt` —
  reproduce it by submitting that same prompt (STRUCTURE-LOCK + DIMETRIC-LOCK clauses intact) against
  your new base via the Scenario API/MCP, not via `--style-pass`/`--layered`. Wiring this into a
  first-class `generate_room.py` flag is open follow-up work, not yet done.

Two mandatory prompt clauses, whichever way you submit the edit:

- **STRUCTURE-LOCK** — "every wall, pillar, archway, doorway, staircase, tree, boulder, road edge,
  and prop must stay in EXACTLY its current position, size, and shape... only the paint and
  lighting treatment changes" (verbatim pattern used across every adopted style-pass prompt, e.g.
  `qa/evidence/plate-sprint/camp-armB/style_pass_prompt_winning.txt`).
- **ADDITIONS-LOCK (#1542, owner playtest #8)** — "add NO new furniture, props, objects, walls,
  or blocking elements of any kind; enrich only the SURFACES of what already exists (materials,
  wear, lighting, small loose scatter like straw/pebbles that no one could collide with)".
  STRUCTURE-LOCK protects what's authored; it never forbade *inventing* — the tavern's painted
  benches had no cells, so players walked through solid-looking furniture. Every style-pass prompt
  MUST carry this clause. Enforcement today: the visual journey's inverse-coherence check (#1540,
  qa/journey_visual_sweep.py) FLAGS painted-object edges on authored-walkable cells and they count
  against the room's CLEAN% (the M-ALIGN bar); wiring those flags into promote.py as a hard
  no-promote condition is tracked on #1542/#1553 — until that lands, a flagged room is a human
  reject-by-policy, not a machine-blocked one.
- **DIMETRIC-LOCK** — an explicit camera-angle-preservation clause. Needed because dropping
  `referenceImages` also drops an *implicit* camera pin that a reference image otherwise supplied
  (`qa/evidence/plate-sprint/camp-armB/findings.json` finding 3) — but test it per-room: on a base
  whose camera was never drifting, adding dimetric-lock wording measurably made results WORSE
  (added prompt complexity/stochastic risk without fixing an actual defect). Don't cargo-cult it.

**★ THE REFERENCE-IMAGES LAW (`PLATE-RECIPE-DECISION.md`):** Gemini `referenceImages` hijack
CONTENT toward the reference, not just style. A reference is safe **only if its composition already
matches the room greybox** (e.g. an anchor minted FROM that same greybox). For a room with no
greybox-aligned anchor: use **no** `referenceImages` — text style description + scene-content
grounding instead. Measured: no-ref registration recall 0.9439 vs same-room-ref 0.81-0.84 (PR #1492).
A `STRUCTURE-LOCK EXCEPTION` clause is the sanctioned escape hatch for a specific, named artifact
region (e.g. an unwanted concentric-ring pattern in ground texture) that the general lock would
otherwise force Gemini to preserve — scope it tightly to the one region, never generally.

**Clarification (from the #1538 cold-agent run):** the LAW governs *external anchors*. Gemini
img2img mechanically requires the image being edited in `referenceImages[0]` — passing the
registered BASE you are styling (which was minted from this room's own greybox) is the sanctioned,
required case, not a violation. "No referenceImages" means: no SECOND image, no external style
anchor, no other room's plate.

**Registration gate:** edge-recall ≥0.95 for hard-edge/masonry rooms; for organic rooms edge-recall
is ADVISORY (content-blind and class-dependent — issue #1491) — use the greybox-edge overlay as
primary evidence instead.

### 7. Blind panel — the in-band control recipe

**`qa/plate_loop.py`** is the one-command conductor: generate → deterministic registration/pre-gate
→ STAGE the panel packet → (orchestrator scores it) → ingest verdict → scores_db row + gallery row.

```bash
# Phase 1 — generate + deterministic gates + stage the panel
python3 qa/plate_loop.py --room crypt --config cfg.json --out-dir out/ --gallery gallery.html
# ... orchestrator runs the 5 blind scorers per <out-dir>/panel/prompts.json ...
# Phase 2 — ingest the verdict
python3 qa/plate_loop.py --panel-verdict verdict.json --out-dir out/ --gallery gallery.html
```

**★ THE PANEL IS AGENT-WORK, NOT SCRIPT-WORK** — `plate_loop.py` never calls an LLM. It stages
blind slots (candidate / incumbent / disguised real-art control), writes the blind
slot→A/B/C mapping OUTSIDE the panel image dir (scorers Read adjacent files), and the
**orchestrator** runs the 5-scorer blind panel (the visual-critic skill recipe).

**5-scorer blind panel composition:** candidate + incumbent canonical + a **disguised in-band
real-art control** (validity band 6.8-9.2 on our instrument; out-of-band ⇒ advisory, re-run once).
Best-of-N (N≥3) generations per iteration (measured run-to-run variance). **Never cite an absolute
score as a quality verdict** — real shipped PoE2/BG2 art scores 3.0-5.6 on this same instrument; the
control's presence is what makes a panel result citable at all (`docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md`
formalizes this as the promotion gate's delta-anchored strategy for the "room" artifact class).

### 8. Promote — `tools/library/promote.py --batch`

The HV3 eval-gated promotion pipeline (Act II §4c, #1325) — the **sole writer** of the repo-root
`library/` pack, additive by default (empty nomination queue ⇒ byte-identical library).

```bash
python3 tools/library/promote.py --batch \
    --library library/ --nominations qa/nominations.jsonl --db qa/scores.db
python3 tools/library/library_lint.py   # validate the result
```

**Two gate strategies by class** (`docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md`):
- **TEXT** classes (quest/npc/location/encounter) → absolute threshold gate (overall ≥4.0, every
  dim ≥3.0, control-valid).
- **VISUAL** ("room") → **delta-anchored, never absolute**: deterministic pre-gate HARD FLOOR
  passed + the panel's control landed in-band + candidate-minus-control delta ≥ -1.2 (noise law).

`tier=canonical` is human curation ONLY — `promote.py` never assigns it. **Bootstrap note:** until
HV5's auto-nominator exists, hand-author `qa/nominations.jsonl` — one JSON line per `artifact_id`.
**Room nominations MUST declare BOTH `"class": "room"` AND `"source_path"`** (the control-anchored
panel JSON the visual gate reads) — a visual score lives in that panel JSON, not the `artifacts` DB
table, and `promote.py` fails the nomination outright (`"visual nomination has no 'source_path'"`)
without it; `class` alone is not sufficient.

### 8a. The generator path — DunGen / Tessera export → converter (structure-source alternative)

For a room sourced from a **generator layout** rather than hand-authored constants, the geometry
step (1) is replaced by an export+convert hop; steps 2-8 above are unchanged downstream. Full
contract: `docs/roadmap/GENERATOR-EXPORT-CONTRACT.md` (supersedes `DUNGEN-EXPORT-CONTRACT.md`,
renamed when the Tessera Pro arm landed).

```
DunGen scene   ──[DunGenLayoutExporter.cs]───▶ dungen_layout.json   ──┐
Tessera scene  ──[TesseraLayoutExporter.cs]──▶ tessera_layout.json ──┴─[dungen_to_fixtures.py]──▶
    (a) <name>.scenegrid.json      — engine SceneGrid fixture (the sole-writer truth)
    (b) <name>_geometry.json       — greybox geometry json (feeds step 3 directly)
    (b') <name>_<room>_geometry.json  — --room: one cropped room = the registered-plate input
```

- Both Unity-Editor exporters (`extensions/renderers/unity/scripts/Editor/{DunGen,Tessera}LayoutExporter.cs`)
  emit the **same top-level layout shape** (`generator`/`bounds`/`rooms`/`doorways`/`props`), so
  `tools/dungen_to_fixtures.py` is a single converter for both arms — no schema fork. Tessera's
  gaps (no native doorway object, no `is_main_path`) are additive-empty fields the converter already
  tolerated before the Tessera arm landed.
- **Scale mapping is identical to the hand-authored path:** `--world-units-per-cell 2.0` (2 Unity
  units = 1 five-ft cell), same convention as step 0 above — this is what keeps the whole chain
  unit-consistent regardless of which structure-source produced the layout.
- Box drive recipe (when a live Unity session is available): `qa/evidence/dungen-spike/BOX-DRIVE-RECIPE.md`
  — deploy the exporter → generate+export via unity-mcp `execute_code` → convert → **derive the
  manifest from the converter's `<name>_<room>_geometry.json` output (step 2 above —
  `tools/derive_room_manifest.py`; do not skip this even though the box-drive recipe's own steps
  jump straight to greybox-render — the manifest is what `check_grid_paint_coherence.py` and runtime
  validation depend on)** → greybox-render → continue at step 4 of this runbook.
- **DunGen-vs-Tessera verdict (both arms run for real on the box, 2026-07-11 —
  `docs/roadmap/GENERATOR-EXPORT-CONTRACT.md` "Box comparison RESULTS"): ADOPT DunGen as the primary
  dungeon/room STRUCTURE generator.** DunGen's native `Doorway`/`Connection` objects + room-graph flow
  map directly onto the engine `SceneGrid` (25 doorways exported cleanly on a 26-tile sample); Tessera
  Pro exported 0 doorways on its 396-tile castle run — no native connection object, so rooms come out
  as disconnected floor islands — the decisive gap for THIS pipeline. **Keep Tessera Pro for
  tile-dense WFC set-dressing/exterior fields** (walls, terrain) where connectivity isn't the point.
  Both arms still feed the identical `tools/dungen_to_fixtures.py` with no schema fork — that
  architecture win holds regardless of which generator you pick per-room.
- **Architecture boundary — see the ruling appended to `docs/roadmap/TILED-SPACE-SPIKE.md`** (dated
  2026-07-12 in that file; confirm it has actually landed if you're reading this before that date —
  treat it as the documented owner direction either way): the room/plate stays the atomic unit at
  native painting density — never widen a single generation to grow a space (measured: quality
  collapses 7→2 stretching one generation past ~1 room). **Towns/larger spaces are a LAYOUT problem**
  solved at the generator-graph layer (room-scale districts + door-cross transitions + visually
  MASKED boundaries), not a painting problem. A shared-wide-depth-control + per-tile-paint + feather
  **hybrid** is that ruling's special-case tool for genuinely continuous wide vistas (e.g. a market
  square spanning two tiles) — used sparingly, panel-gated per vista, never as the default
  town-building path.

### 9. `plates_manifest.json` + `effects[]` anchors (runtime backdrop + VFX)

**Runtime plate registry** (`docs/roadmap/W5E-PLATE-REGISTRY-DECISION.md`, DECIDED 2026-07-10): ONE
persistent Unity scene; the backdrop is resolved AT RUNTIME by the engine's location slug via a
StreamingAssets `plates_manifest.json` — no scene reload, no per-room bake, no Addressables.

**★ Edit this at its real committed path, the Unity PROJECT root, not the repo root:**
`extensions/renderers/unity/plates_manifest.json` (+ `extensions/renderers/unity/effects_registry.json`)
— `BuildMacOSPlayer.EnsurePackaged` resolves both relative to `Application.dataPath`'s parent (the
Unity project dir), so a copy dropped at the repo root is silently never packaged. Referenced plate
PNGs live under `extensions/renderers/unity/plates/`.

```jsonc
{
  "version": 1,
  "plates": {
    "<location_slug>": {
      "plate": "plates/<file>.png",
      "planeSize": [W, H],
      "cameraPin": { "ortho": 13.0, "pitch": 30.0, "yaw": 45.0 },
      "effects": [ { "type": "fire_medium", "cell": [5, 8], "scale": 1.5 } ]
    }
  }
}
```
**No per-plate `stage` field** — `CombatSurfaceClient.LoadPlateManifest` only parses `plate`,
`planeSize`, `cameraPin`, and `effects` per entry (`CombatSurfaceClient.cs` `PlateEntry`/
`LoadPlateManifest`). Stage data (fire flicker/glow anchors, #1463 W6.4) is a SEPARATE, single global
`StreamingAssets/stage.json` copied verbatim by the same packaging step — it is not keyed per-plate
and adding a `"stage"` key to a plate entry above is a silent no-op.

**★ BOX BUILD PRE-FLIGHT (measured gap — stale manifests shipped in Box Cycles 2 AND 3, 2026-07-15):**
- The pre-flight sync target is the Unity project ROOT (the `EnsurePackaged` source), never `Assets/StreamingAssets`; verify with `packaged_pins` after every build.
the GEX44 box's Unity project (`/home/unity/worldos-unity/`) carries its OWN copies of these data
files, and `BuildMacOSPlayer.EnsurePackaged` packages the BOX copies verbatim. A lane that deploys
only its changed `.cs`/shader files ships whatever manifest the box happened to have. Before EVERY
box `BuildMacOSPlayer`, sync the renderer data files from the repo main being built against:
`plates_manifest.json`, `effects_registry.json`, `stage.json`, `registry.json`, `plates/*.png` AND
`boxes/*.json` (the occluder sidecars — omitted from this list until 2026-09-02, which is how a build shipped the
pre-kit chunky sidecars under a green stamp) → the Unity project root. The box copy is NEVER the source of truth; the repo is. (Both cycle regressions were caught by
the post-install pin check `python3 -c "...print cameraPin per plate..."` on the installed app —
keep running that check after every install.)

**`effects[]` is the additive VFX-anchor mechanism** (PR #1525, VFX-ANCHORS): on plate load/swap,
`CombatSurfaceClient.SpawnPlateEffects` despawns prior effect instances and spawns each entry's
mapped prefab (via `effects_registry.json`, abstract `type` → prefab path — single source of truth
for both the runtime resolver and the build) at the cell's world position, scaled, ParticleSystems
warmed, parented under `_EffectsRoot`. **Pure presentation — no engine/gameplay contact.** Absent
`effects`/registry/bundle-prefab ⇒ nothing spawns (byte-identical to the pre-VFX plate). The box
renders **Built-in RP**; Hovl Shader-Graph particle materials get re-pointed to Legacy Particles at
runtime (`RepointHovlMaterials`, reuses PR #1515) — a no-op for Synty/GAPH shaders.

**Grid dims + occluders are NEVER in the manifest** — they stay engine truth on the `/combat-surface`
poll; the manifest carries ONLY presentation data (plate file, plane size, camera pin, effects). This
is what keeps the renderer a pure consumer (the sole-writer invariant, restated for this seam).
`BuildMacOSPlayer.EnsurePackaged` copies `plates_manifest.json` + every referenced plate PNG +
`effects_registry.json` into `StreamingAssets/` at build time.

### 10. Player pickup — hot-load for ITERATION, box rebuild for SHIP

**★ Hot-load (empirically confirmed 2026-07-16, dwing gates):** on macOS, `StreamingAssets` is
plain disk files inside the built .app that Unity reads at RUNTIME — so for ITERATION (sandbox walk
gates on candidate plates) you do NOT need a box rebuild. Copy the installed app, inject the
candidate artifacts into the COPY, and gate against it:

```
cp -R ~/Applications/WorldOSPlayer.app /tmp/WorldOSPlayer_hotload.app
SA=/tmp/WorldOSPlayer_hotload.app/Contents/Resources/Data/StreamingAssets
cp <plate>.png "$SA/plates/"; cp <boxes>.json "$SA/boxes/"    # + edit $SA/plates_manifest.json
WORLDOS_PLAYER_APP=/tmp/WorldOSPlayer_hotload.app qa/qa_sandbox.py up ...
```

Proof: the dwing gates read `camOrtho 10.5224` (the hot-loaded pin) exactly. Caveats: NEVER edit the
owner's installed app (copies only — editing breaks the code seal; local non-notarized copies still
launch); the hot-patch is never the durable state — the repo manifest + a SHIP-time box rebuild must
still land or the next build regresses.

**Box rebuild (ship time only)** — the distributed app's `StreamingAssets` are baked at build:

```
Tools/WorldOS/Build/macOS Player (Universal)   # Unity Editor menu item, on the box
```

**★ ALWAYS-INCLUDED SHADERS pre-flight (#1674 — walk-behind silhouette shipped disabled, 2026-07-22):**
`WorldOS/ActorSilhouette` (walk-behind mask) and `WorldOS/OccluderDepth` (occluder proxies) are resolved
at runtime via `Shader.Find` and referenced by NO asset, so a player build strips them unless they are in
Graphics → Always-Included Shaders. `BuildMacOSPlayer.Build()` now bakes both in itself (via
`EnsureAlwaysIncludedShaders()`, no longer only the manual `Tools/WorldOS/W5b`), and records the resolved
list on the report's `alwaysIncludedShaders=` line. Run the repo pre-flight BEFORE the box rebuild — it
FAILS if the build source stops guaranteeing a required shader:

```
python3 qa/check_always_included_shaders.py                                   # source guarantee (pre-build)
python3 qa/check_always_included_shaders.py --build-report BuildOutput/build-report.txt   # post-build confirm
```

Sync the shader files (`extensions/renderers/unity/shaders/ActorSilhouette.shader`,
`OccluderDepth.shader`) to the box project's `Assets/Shaders/` before the rebuild; then `player_cert`'s
silhouette assert flips GREEN and `WORLDOS_SILHOUETTE=0` reproduces the RED as the deliberate-regression proof.

### 10b. The sandbox hot-load GATE LOOP (the proven per-room iteration cycle, 2026-07-16)

```
cp -R ~/Applications/WorldOSPlayer.app /tmp/WorldOSPlayer_hotload.app       # once per app version
# inject candidate plate/boxes + manifest entry into the COPY's StreamingAssets (see 10 above)
WORLDOS_PLAYER_APP=/tmp/WorldOSPlayer_hotload.app python3 qa/qa_sandbox.py up --run <qa-run-id> \
    --campaign <fixture-campaign> --seed-cmd "uv run --directory servers/engine python \
    /ABS/PATH/qa/seed_gfx_<fixture>.py {state} [current_room]"
python3 qa/walk_test.py --room <room> --engine http://127.0.0.1:8866 --qa http://127.0.0.1:8972 \
    --visual 5 --out qa/evidence/walk-<room>
```

**★ The rig is WINDOWED and refuses to launch over you (#1672 — incident 2026-09-02).** `up` exits
**75** with `SANDBOX-DEFERRED (owner active)` if you touched the Mac in the last 120 s
(`FORCE_PLAYER_QA=1` overrides; `WORLDOS_PLAYER_IDLE_THRESHOLD` retunes). The player launches
`-screen-fullscreen 0 -screen-width 1280 -screen-height 700` (a Unity RESOLUTION, i.e. **backing
pixels** — on this 2x host, 1512x835 points / 3024x1670 pixels, that is a 640x382 point window), is
watchdogged against fullscreen / offscreen from the window server's own bounds on every readiness
poll, and hands keyboard focus back **by pid** afterwards. Run `qa/qa_sandbox.py watchdog --run
<run>` periodically during a long sweep: a fullscreen verdict kills the rig player and exits **3**.
Size knobs: `WORLDOS_PLAYER_WIN_W` / `_WIN_H` (fit-clamped; never below the display's aspect, or
sample cells crop out of the ortho frame). The rig gets its own `CFFIXED_USER_HOME=<rundir>/home`, so
its `/shot` frames cannot collide with the owner instance's. `down` leak-checks the SHARED
`com.worldos.WorldOSPlayer` plist and restores only keys that still hold the value this rig writes
(everything else is reported, never rewritten) into `<rundir>/prefs_leak.json`; `qa/qa_sandbox.py
orphans` finds a rig stranded by a crashed run. The rig's window TITLE is still `WorldOSPlayer` until the
Phase 2 badge lands — `docs/qa/QA-RIG-WINDOW-BADGE.md`.

TRAPS (each cost a real debugging round):
- **Gate the room the party is IN.** `walk_test --room B` while the fixture spawned the party in
  room A silently measures room A (identical counts, wrong camera). Multi-room fixtures take a
  `current_room` argv — cycle the sandbox per room.
- **`uv run --directory` resolves relative script paths under servers/engine** — always pass the
  seed script by ABSOLUTE path.
- **Verdicts are read from walk_report.json** (tri-state GREEN/RED/ERROR; ERROR = harness, never a
  room verdict) and adjudicated per qa/PANEL-PROTOCOL.md's blinded-adjudication rule.
- **Owner-preview hygiene (#1620):** the owner only ever sees a DEDICATED run id containing
  gate-passed states. The QA-churn sandbox (mid-iteration plates, un-gated candidates) is never
  announced — a half-iterated state reaching the owner manufactures alarm, true and false.


Verify with `qa/player_smoke.sh` (free, ~30-60s, every player rebuild — `docs/RUNBOOK-INDEX.md`
"player smoke" row) before treating a rebuild as done. A location whose manifest key is unknown ⇒
current plate kept (invisible-but-safe), never a crash — check `plates_manifest.json` if a room you
just promoted doesn't appear in the player.

### 11. WALKABILITY gate — `qa/walk_test.py` (THE ship gate; automated; no room ships without green)

The beauty panel is BLIND to whether you can actually walk the room. After the built player is running
(step 10) with the QA channel armed (`WORLDOS_QA_INPUT=1`, port 8971), drive it cell-by-cell and assert
— with NO human:

```
qa/walk_test.py --room <id>          # drives :8971 /click + reads the client actor screen-pos + /shot
```

- **Actor alignment** — every reachable floor cell: `/click` it → the engine token cell must equal the
  click AND the client's reported actor screen position (`Camera.main.WorldToScreenPoint` of the grounded
  actor, exposed on the QA `/debug` channel) must be within N px of `world_to_screen(CellToWorld(cell),
  ortho)`. This is the projection check that catches the camera-rig class (an offset here = the character
  renders on a tomb / through a wall even though the engine has it on the right cell).
- **Impassable rejection** — every wall/prop cell: `/click` → the move must REJECT (token unchanged).
- **Doors (cosmetic)** — every `door_cell` projects onto the painted arch; `cross_door` lands on the
  reciprocal door, not a wall/prop.
- **Occlusion** — walk behind each tall occluder → `/shot` → the character region is partially masked.
- **No orphan paint** — no painted walkable-looking space lacks a backing grid cell.

Emits `walk_report.json` (pass/fail per cell/door/occlusion) + a `/shot` contact sheet. **GREEN =
walkable = shippable.** The geometric asserts are CU-free (no LLM); images are only for the occlusion
spot-check + human evidence.

**The STATIC half runs long before this step** — `qa/walk_static.py` (manifest lint, the
manifest==sidecar==fit-math ortho triple-check, orphan-pocket/door-landing checks, seed↔geometry door
agreement) runs in CI on every PR and at the seed boundary (seed scripts refuse invalid worlds), so
most walkability defect classes never reach a live player. **The LIVE half must run in the #1596
sandbox lane** (`qa/qa_sandbox.py up …` — cloned state, second engine :8866, second player :8972);
small probe sets on the owner instance are acceptable, full sweeps are NOT — the owner's campaign is
not a test rig, and walk_test returns the party home when done. Use `--visual N` to also measure the
rendered actor against the plate-correct projection with no client instrumentation (pixel-diff
localization; glide-settled sampling). Root-cause reference for the failure class this gate catches:
`CombatSurfaceClient.ApplyPlate` must reproduce `build_room_unified`'s full camera rig (Euler(30,45,0),
pos=-(rot·fwd)·80) whenever `cameraPin.ortho` is set — not only when pitch/yaw are present (epic #1581,
issue #1583). GEOMETRY IS GROUND TRUTH: collision/occlusion come from the grid+boxes; the plate is
cosmetic, so a door "misalignment" is a label/arrival-cell fix, never a collision fix.

---

## Cross-linked decision records (read before deviating from this runbook)

| Decision | What it settled |
|---|---|
| `docs/roadmap/PLATE-RECIPE-DECISION.md` | The adopted flux depth-CN + Gemini style-pass recipe; the reference-images law; outdoor-class rejected-approaches register |
| `docs/roadmap/TILED-SPACE-SPIKE.md` | Room = atomic paint unit; towns are a layout problem; the ratified hybrid seam recipe for special-case wide vistas |
| `docs/roadmap/GENERATOR-EXPORT-CONTRACT.md` | The DunGen + Tessera Pro export contract; the additive schema; shape-appropriate proxy routing |
| `docs/roadmap/W5E-PLATE-REGISTRY-DECISION.md` | Runtime plate registry (`plates_manifest.json`); why per-room baked scenes and Addressables were rejected for this slice |
| `docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md` | Why the "room" class gates on control-relative delta, never an absolute score |
| `docs/research/2026-07-10-stage-tech-research.md` | REJECTED-APPROACHES register cross-linked from the plate recipe decision |
| `docs/RUNBOOK-INDEX.md` | The run-type registry — every run in this pipeline has a row (runner, tier, evidence, scores surface) |

## Standing instruments that validate a room after it ships

See `docs/OPERATIONS.md` "Journey-eval + the coherence gate — standing instruments" — the coherence
gate (`check_grid_paint_coherence.py`) and journey-eval (`qa/journey_eval.py`) both run against a
shipped room; `qa/evidence/journey-eval-first-run/RECALL.md` documents the current instrument gap
(the legal-path blind spot, #1523) so you know what journey-eval does and does NOT yet catch. Both
runners have rows in `docs/RUNBOOK-INDEX.md` "Visual / render".

## ★ EVIDENCE CITATION RULE (owner-caught failure class, 3× on 2026-07-15)
Anything cited to the owner in chat MUST exist at the cited path at the moment of citation:
- **Cite `~/worldos-session-notes/...` or a GitHub URL** (PR Files-changed view) — both are
  branch-independent and survive checkouts.
- **NEVER cite a repo working-tree path for an unmerged branch** — switching back to main removes
  the folder from disk and the owner finds a dead path. Repo paths become citable ONLY after merge.
- Mirror every owner-facing frame into `~/worldos-session-notes/morning-frames-*/` BEFORE citing.

## ★ DESIGN GATE (playtest-#9, hardened after the crypt arc) — run on the GREYBOX before ANY paint
Critique the greybox as a GAME SPACE, not a prop list. REJECT (free, before CU spend) unless:
1. Doors read as doorways (framed/arched, not bare gaps under a floating label).
2. Structural logic: pillars/piers placed as architecture (screen-space rhythm at the contract
   camera, not grid symmetry — grid-symmetric pairs CLUMP on screen, measured v3.0).
3. One focal point with negative space around it; clutter placed with narrative intent.
4. **Silhouette vocabulary: molded forms, not all boxes** (owner, post-v3.3): round shafts,
   arched headers, stepped/curved lids. An all-cube greybox paints as "squares, squares, squares".
5. Freestanding props ≥2-cell footprints (1-cell props do not survive the style pass — measured
   2/2 drops in the v3 arc; see qa/evidence/crypt-v3/FINDINGS.md).

## PAINT CALLS: THE PINNED PATH + THE DIAGNOSTIC LADDER (2026-07-15 slot-bug postmortem)
⛔ NEVER freehand a Scenario generation call. ALL room paints go through `qa/paint_room.py`
(prompts/params in `qa/unified_paint_recipes.json`; the depth goes in **controlImage** — the
`image` slot is img2img and silently ignores ControlNet params; a compacted agent swapping these
cost an afternoon and a false vendor-blame doc, see qa/evidence/gemini-restyle/FLUX_ROOTCAUSE.md).
Every paint_room run logs each job's SUBMITTED body beside its output.

**Provenance-attached-to-PR rule:** any PR that adds/changes a plate embeds its paint_room
`report.json` (job ids, seeds, recalls, selected draw) in the evidence dir — a plate without a
reproducible recipe trail is not mergeable.

**When generation output looks wrong, climb this ladder — in order, before ANY vendor theory:**
1. `job_get verbose` the failing job AND a known-good job → diff the SERVICE-RECORDED inputs
   field-by-field (param slots, not just values).
2. Same-seed determinism probe: re-submit the known-good job's recorded input verbatim; a
   byte-identical output (md5) exonerates the provider entirely.
3. Only if 1-2 pass and output still differs: provider-side theories (deployment, subprocessor
   routing — note LoRA-attached flux jobs route via Replicate, bare flux via Modal).
