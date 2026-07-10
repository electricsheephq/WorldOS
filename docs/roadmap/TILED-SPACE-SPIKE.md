# TILED-SPACE spike — composing larger spaces from seamlessly-pieced plates

**Status: DRAFT-FOR-ORCHESTRATOR-REVIEW.** *The decision (which architecture the town-scale path adopts)
is NOT finalized here — this memo reports the spike evidence and recommends; the orchestrator/owner decides.*

**Date:** 2026-07-11 · **Lane:** TILED-SPACE (epic #1508 extension — "the road to towns") · **Budget:** API-side,
no box, ~60 CU of ~250 spent.

## The question

The plate system paints ONE dimetric room from a 3D greybox (flux.1-dev depth ControlNet). Can a space
**larger than one plate** be composed from seamlessly-pieced tiles? Two candidate architectures, tested on the
**same greybox pair** — a war-camp clearing that opens eastward into a forest path (the canonical
`camp_clearing_night` + `forest_road` geometries, merged):

- **ARM A — SLICE-ONE-BIG.** Author ONE wide greybox, render ONE wide depth control, generate ONE plate at the
  largest width flux sustains, then SLICE it into tiles. Seams perfect by construction; the cost to measure is
  the quality hit of spreading one generation across a bigger area, and the practical size ceiling.
- **ARM B — EDGE-CONTINUATION.** Generate tile 1 normally; generate tile 2 from its own depth control but
  conditioned for continuity — flux **img2img** with an init canvas whose left overlap strip is tile 1's finished
  paint (+ tile 2 depth as `controlImage`), then overlap-**feather** the join. Measure the seam.

**Reproduce:** `qa/tiled_space_spike.py {build,initb,compose}` (offline geometry/stitch/metrics) +
`extensions/renderers/godot/tools/scenario_gen.py controlnet` (flux depth ControlNet; gained `--init-image`/
`--strength` this lane for Arm B img2img). Evidence: `qa/evidence/tiled-space/` (gallery.html + composites +
seam close-ups + seam_metrics.json). The **fairness trick**: tile controls are exact left/right crops of the ONE
wide depth control (unit-tested `np.array_equal`), so an Arm-A slice and the baseline/Arm-B tiles share
identical control pixels — the only variable is generation width/conditioning.

## What flux actually sustains (the hard numbers)

- **flux.1-dev caps at 2048 px/side** (schema max; multiple of 16). It supports **img2img + depth ControlNet
  together** — Arm B's true edge-conditioning is in-toolchain, not a research gap.
- Holding the native vertical rig (768 px tall, ortho 13 → **64 px/col**), a 2048-wide frame reaches
  **32 cols ≈ 2 rooms ≈ 68 world-units at ZERO per-cell density loss.** So the *pixel* ceiling for one
  native-density generation is **~2 rooms wide.** Cost: **9 CU** at 2048×768, ~5 CU at 1024×768.

## ARM A findings — SLICE-ONE-BIG

- **Seam: perfect by construction.** The wide plate is one image; the x=1024 slice is invisible
  (`seam_excess` **1.28** ≈ ordinary-texture noise). Beyond "no color seam," a single generation
  **guarantees one consistent projection, ground-plane, horizon and lighting across the whole width** — a
  property the tiling arms cannot get for free (see Arm B).
- **Cost: painterly richness collapses as the frame widens.** The clearest datum in the spike: the camp tile
  **sliced from the 2048 wide plate** and the **standalone 1024 baseline** were generated from *identical* control
  pixels, recipe and seed — yet the baseline is a rich Deadfire-grade scene (dramatic campfire, warm pooled
  firelight, palisade, bedrolls) while the wide-context slice is **dim, flat, low-contrast, and reads as literal
  dark greybox cubes** (ember-fire, lost detail). flux spends a roughly fixed contrast/detail budget per
  generation; spreading it over 2048 px halves the local dynamic range.
- **Escalation (3 rooms / 48 cols @ 0.67× density) degrades hard** — small props resolve as literal cubes; the
  plate reads as an abstract blocky diorama. Confirms the fall-off is monotonic and steep.
- **Ceilings:** *pixel* ceiling ≈ 2 rooms / 2048 px; the **quality** ceiling is lower — richness already visibly
  drops from 1024→2048, so the painterly sweet-spot is **~1 room per generation**, 2 rooms is
  usable-but-diminished, ≥3 rooms is unacceptable without a per-tile re-paint.

## ARM B findings — EDGE-CONTINUATION

- **Naive butt-join is an obvious seam** (`seam_excess` **2.66**; the join column jumps 2.7× the texture floor):
  a hard vertical line where a bright dimetric camp meets a dark forest — both a value break and a projection break.
- **Edge-continuation heals the *tonal* seam.** img2img-seeding tile 2 with tile 1's overlap strip + a feather
  blend drops the join to `seam_excess` **0.51** — *below* the ordinary-texture floor (i.e. smoother than the
  interior). Visually there is **no hard line**; palette, value and lighting flow across the join, and the
  firelight even bleeds convincingly into the near forest.
- **But edge-continuation does NOT enforce spatial coherence.** The independently-generated forest tile drifted
  to a **different camera/projection** (near eye-level trees vs the left's dimetric bird's-eye). So the seam is
  invisible *in tone* while the *ground plane bends* at the join. Color-continuous, projection-discontinuous.
  Feathering hides the line; it does not lock the horizon.
- **Implication:** edge-continuation is a viable **style-seam healer** but needs a **structural constraint** to be
  a space-builder — i.e. a shared full-span depth/greybox control (so both tiles inherit one dimetric ground
  plane) *plus* the tonal conditioning. That is a **hybrid** (Arm A's shared structure + Arm B's per-tile
  native-density paint + feather).

## Panel (3 blind scorers, PoE2 bar) — `qa/evidence/tiled-space/panel/`

3 independent blind sonnet scorers, PoE2 Deadfire bar (raw scores → median):

| image | what it is | scores | median |
|-------|-----------|--------|--------|
| 1 | baseline single-room camp @1024 (native density) | 6,7,7 | **7** |
| 2 | Arm A camp tile **sliced from the 2048 wide plate** (identical control as #1) | 2,2,3 | **2** |
| 3 | Arm B naive butt-join | 4,6,6 | **6** |
| 4 | Arm B edge-continuation (img2img-seed + feather) | 4,6,6 | **6** |

- **Arm A quality cost = a 5-point gap** (7 → 2) from *identical* control pixels — the single loudest result in
  the spike. Spreading one generation to 2048 px halves the dynamic range and detail; the wide slice reads as
  "blocky untextured greybox cubes" to all three scorers.
- **Arm B: 3/3 scorers see a VISIBLE seam on BOTH the naive and the edge-continued composite** — and all three
  independently name the **projection mismatch** (isometric camp vs eye-level/vanishing-point forest) + a palette
  jump as the dominant break, *not* a hard tonal line. **The deterministic tonal metric (seam_excess 0.51) says
  the feather heals the seam; the human panel says it does not, because the ground plane bends.** Tone is healed;
  space is not. This is the load-bearing finding: **per-tile generation needs a shared-structure projection lock,
  which the feather/img2img conditioning alone does not provide.**

## Seam metrics (deterministic — `seam_metrics.json`)

| Arm | stitch | `seam_excess` (1.0=invisible) | `grad_ratio` | verdict |
|-----|--------|------------------------------|--------------|---------|
| A — slice-one-big | one plate, sliced | **1.28** | 1.30 | seamless by construction |
| B — edge-continuation | img2img-seed + feather | **0.51** | 0.42 | tonal seam invisible; projection drifts |
| B — naive (neg. control) | independent butt-join | **2.66** | 0.45 | hard visible seam |

## Recommendation (DRAFT — orchestrator/owner decides)

**Headline: neither pure arm is the town-scale answer; a HYBRID is — Arm A's shared structure for
within-a-space continuity, Arm B's per-tile native-density paint + feather for richness, and the LAYOUT
graph (tessera) with occluded/door-cross boundaries for town scale.**

1. **Within one contiguous space bigger than a plate** (a plaza, a large clearing, a hall): use **one shared
   wide depth/greybox control** to lock a single dimetric ground plane, but **paint it as per-tile
   native-density (~1 room/1024 px) generations, then feather-stitch** (Arm B's tonal conditioning across the
   shared structure). This keeps Arm A's projection guarantee AND Arm B's per-tile richness — avoiding the wide
   plate's richness collapse. A **global style pass over the finished composite** is the cheapest universal
   seam-healer (trivially seamless for the slice case, palette-unifying for the stitch case) and should be the
   standard post-stitch step.
2. **Pure SLICE-ONE-BIG is acceptable only up to ~2 rooms** and only when the richness cost is tolerable (e.g. a
   dark transition corridor). Do not push one generation past 2048 px / 2 rooms.
3. **Town scale is a LAYOUT problem, not a single-paint problem.** Compose districts at the room-graph layer
   (`TesseraLayoutExporter` / scenegrid) and **hide most district boundaries behind natural occluders**
   (walls, tree-lines, elevation, gateways) so seams never need to be invisible. Reserve edge-continuation
   (door-cross stitching) for the few boundaries that must read as one continuous space.
4. **Greybox prop primitives need work for the town path:** tall thin boxes (trees) render inconsistently under
   depth control (literal cubes at high strength). Town greyboxes want better organic primitives and/or lower
   control strength + a style pass.

## Risks / caveats for review

- Arm B's continuity was tested via **img2img-seed + feather** (the most controllable in-toolchain path); a true
  **inpaint/outpaint** endpoint is not wired — that is the untested upper bound on tonal continuity.
- Plates here are the flux depth-ControlNet **base only** (no Gemini/z-image style pass); the real pipeline adds a
  style pass, which would further homogenize seams (an argument FOR the hybrid's post-stitch style pass).
- Projection-drift under low control strength is the key structural risk for any per-tile approach; the hybrid's
  shared-structure control is the proposed mitigation but is itself unbuilt (next spike).

---
## ORCHESTRATOR RULING (2026-07-12, ratified with amendments)
The spike's evidence is accepted. Architecture ruling for large spaces and towns:
1. **The room/plate remains the atomic unit at native painting density.** Measured: quality collapses 7→2
   when one generation is stretched past ~1 room (flux 2048px cap). Never widen the frame to grow a space.
2. **Towns and larger spaces are a LAYOUT problem, not a painting problem.** Default path: generator graphs
   (DunGen/Tessera, epic #1508) of room-scale districts connected by door-cross transitions and visually
   MASKED boundaries (gates, walls, alleys, tree lines) — boundaries the paint never has to reconcile.
3. **The hybrid seam recipe** (shared wide depth control for projection lock + per-tile native-density paint
   + feather + post-stitch style pass) is ratified as the SPECIAL-CASE tool for genuinely continuous wide
   vistas (e.g. a market square spanning two tiles) — used sparingly, panel-gated per vista.
4. Amendment to the spike's projection finding: with one shared wide depth render the projection mismatch is
   locked out by construction; the residual hybrid risk is the >2048px post-stitch style pass — accept the
   per-vista cost or skip the global pass when the feather suffices.
This ruling extends docs/roadmap/PLATE-RECIPE-DECISION.md and epic #1508 (stage: LARGE SPACES).
