# WALKSLICE-CRYPT-ALIGN (#1565) — evidence

Aligns the canonical crypt combat grid (`qa/seed_gfx_combat._build_crypt_grid`, reused by the
walkslice) to the adopted **crypt_fresh** geometry, per `qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md`.

## Grid delta (verified by `make_grid_overlay.py`)
- old impassable **62** → new impassable **70** (= 62 − 8 freed + 16 added). This equals the fresh
  geometry's 68 impassable **plus** the 2 door cells `(6,0)`/`(13,4)`: the fresh geometry treats those as
  walkable door gaps, while the combat perimeter keeps them solid and the walkslice punches them at render.
- **freed 8** (sarcophagus shrink, reconciliation §A): `(3,7)(4,6)(5,6)(6,6)(6,7)(6,8)(7,6)(7,7)` — the
  12-cell drift blob → true 2×2 coffin `(4,7)(5,7)(4,8)(5,8)`.
- **added 16** (fresh-plate ornaments, reconciliation §B): `(1,1)(1,2)(1,4)(1,6)(1,7)(2,1)(2,9)(3,1)(3,9)(5,1)(7,1)(10,1)(11,1)(11,9)(12,3)(12,6)`.
  The 3 door-flanking wall-mounted cells `(5,1)(7,1)(12,3)` are authored as impassable **wall** cells
  (not free-standing props) so they never trip the walkslice door-zone gate.

## Visual sweep (`journey_visual_sweep.py`, scratch viewer port 8781, fresh plate + aligned grid)
`report.json` / `gallery.html` (per-step frames in `frames/`):

| room | CLEAN% | notes |
|---|---|---|
| **crypt** | **82.8%** | 15/86 flags = up-screen occlusion-silhouette false-positives |
| camp_clearing_night | 95.6% | |
| tavern | 100.0% | |
| **overall** | **92.1%** | reciprocal-door failures **0** (all 5 crossings land ≤ Cheb-2 of the correct return door) |

The crypt **82.8%** is squarely in the expected ~82–85 band for the current detector. The 15 residual
crypt flags all sit on floor cells **up-screen of a tall prop** (coffin lid/effigy, back-wall niches +
effigy, pillars) whose silhouette rises into the cell quad under the ortho=13 contract camera (the
crypt_fresh plate was authored camera-fit at ortho≈10.52; box deploy + camera reconciliation deferred by
#1565). These are a detector-precision artifact, **not** a collision mismatch.

**Hero-position: 4 of 5 steps pass.** Steps 0–3 pass (feet in the authored floor quad, off every painted
object). Step 4 (tavern → crypt return) lands the hero at **(12,4)**, the correct threshold beside the
tavern door (13,4) — its **reciprocal-door check PASSES** (Chebyshev-1 of the return door). The
hero-position check nonetheless flags it because (12,4) is one of the inverse-coherence cells: it sits
one cell down-screen of the tall `pilaster_arch` at (12,3), whose painted archway architecture bleeds
into (12,4)'s floor quad — the **same up-screen-silhouette / painted-doorway false-positive class** as the
15 crypt floor flags, not a ground-collision defect (the arrival is functionally correct). NOTE: the
sweep's rolled-up `findings_by_class.hero_position_failures` counter reports 0 here while the step-4
`hero_check.pass` is False — a known aggregation quirk in the instrument; this evidence cites the
authoritative per-step `hero_check`. The occlusion-precision fix (and this counter) are a separate lane.

## Files
- `report.json`, `gallery.html`, `frames/` — the sweep output.
- `grid_overlay_before.png` / `grid_overlay_after.png` / `grid_overlay_before_after.png` — the delta on the plate.
- `make_grid_overlay.py` — regenerates the overlays (contract camera, reuses `impassable_cells`).
- scores.db row: `crypt-align-52ce418a` (surface=visual, scene=crypt, milestone=M-ALIGN).
