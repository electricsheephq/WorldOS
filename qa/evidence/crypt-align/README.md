# WALKSLICE-CRYPT-ALIGN (#1565) — evidence

Aligns the canonical crypt combat grid (`qa/seed_gfx_combat._build_crypt_grid`, reused by the
walkslice) to the adopted **crypt_fresh** geometry, per `qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md`.

## Grid delta (verified by `make_grid_overlay.py`)
- old impassable **62** → new impassable **70** (= 62 − 8 freed + 16 added; the fresh geometry's 68 minus
  the 2 door cells the combat grid keeps solid and the walkslice punches).
- **freed 8** (sarcophagus shrink, reconciliation §A): `(3,7)(4,6)(5,6)(6,6)(6,7)(6,8)(7,6)(7,7)` — the
  12-cell drift blob → true 2×2 coffin `(4,7)(5,7)(4,8)(5,8)`.
- **added 16** (fresh-plate ornaments, reconciliation §B): `(1,1)(1,2)(1,4)(1,6)(1,7)(2,1)(2,9)(3,1)(3,9)(5,1)(7,1)(10,1)(11,1)(11,9)(12,3)(12,6)`.
  The 3 door-flanking wall-mounted cells `(5,1)(7,1)(12,3)` are authored as impassable **wall** cells
  (not free-standing props) so they never trip the walkslice door-zone gate.

## Visual sweep (`journey_visual_sweep.py`, scratch viewer port 8781, fresh plate + aligned grid)
`report.json` / `gallery.html` (per-step frames in `frames/`):

| room | CLEAN% | hero-pos | notes |
|---|---|---|---|
| **crypt** | **82.8%** | 1/1 | 15/86 flags = up-screen occlusion-silhouette false-positives |
| camp_clearing_night | 95.6% | 1/1 | |
| tavern | 100.0% | 1/1 | |
| **overall** | **92.1%** | | reciprocal-door failures **0**, hero-position failures **0** |

The crypt **82.8%** is squarely in the expected ~82–85 band for the current detector. The 15 residual
crypt flags all sit on floor cells **up-screen of a tall prop** (coffin lid/effigy, back-wall niches +
effigy, pillars) whose silhouette rises into the cell quad under the ortho=13 contract camera (the
crypt_fresh plate was authored camera-fit at ortho≈10.52; box deploy + camera reconciliation deferred by
#1565). These are a detector-precision artifact, **not** a collision mismatch — the hero-position check
passes (the hero never stands inside a painted object) and both doors round-trip. The occlusion-precision
fix is a separate lane.

## Files
- `report.json`, `gallery.html`, `frames/` — the sweep output.
- `grid_overlay_before.png` / `grid_overlay_after.png` / `grid_overlay_before_after.png` — the delta on the plate.
- `make_grid_overlay.py` — regenerates the overlays (contract camera, reuses `impassable_cells`).
- scores.db row: `crypt-align-52ce418a` (surface=visual, scene=crypt, milestone=M-ALIGN).
