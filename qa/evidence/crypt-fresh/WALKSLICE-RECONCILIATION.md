# FRESH-CRYPT — walkslice / combat-grid reconciliation (report only; seed NOT edited, per #1559)

The `crypt_fresh` plate is painted around a NEW authored geometry
(`author_crypt_fresh`, `qa/evidence/crypt-fresh/crypt_fresh_geometry.json`). The engine's
canonical combat crypt grid (`seed_gfx_combat._build_crypt_grid`, which the walkslice reuses —
`servers/engine/tests/test_walkslice_seed_grid.py`) is UNCHANGED in this lane. This table is the
exact delta between the two impassable sets, so a follow-up lane can decide whether to align the
engine seed to the fresh geometry. **No seed/`seed_gfx_*.py` edit is made here** (the sibling
tests `test_walkslice_crypt_*` still pin the canonical props byte-for-byte).

## Totals
| set | impassable cells |
|---|---|
| canonical combat grid (perimeter + pillars + 12-cell sarcophagus) | 62 |
| fresh geometry (camera-fit, wall_run band, 2x2 coffin, +11 ornament props) | 68 |
| shared (identical in both) | 52 |

## A. Engine-BLOCKED but FRESH-FREE (10 cells) — the fresh plate treats these as clear floor
| cause | cells |
|---|---|
| doors opened (walkslice already punches these; combat perimeter is solid) | (6,0) camp seam, (13,4) tavern seam |
| **sarcophagus shrink** — the canonical 12-cell drift blob (cols3-7 x rows6-8) reduced to the TRUE 2x2 coffin; these 8 cells are freed | (3,7),(4,6),(5,6),(6,6),(6,7),(6,8),(7,6),(7,7) |

The fresh 2x2 coffin `(4,7),(5,7),(4,8),(5,8)` is a strict SUBSET of the canonical 12-cell
footprint — it introduces NO new blocking on the tomb; it only frees the 8 over-large drift cells.

## B. FRESH-BLOCKED but ENGINE-FREE (16 cells) — the fresh plate paints an object where the seed authors clear floor
| fresh prop | cells | class |
|---|---|---|
| effigy_niche_l | (2,1),(3,1) | tall back-band ornament |
| torch_door_l | (5,1) | back-band torch (flanks camp door) |
| torch_door_r | (7,1) | back-band torch (flanks camp door) |
| niche_back_r | (10,1),(11,1) | tall back-band niche / 2nd tomb slab |
| pilaster_arch | (12,3) | engaged column beside tavern door |
| torch_near_l | (1,4) | near/left-wall torch |
| torch_far_r | (12,6) | far/right-wall torch |
| rubble_bl | (1,1),(1,2) | low corner clutter |
| broken_slabs | (1,6),(1,7) | low left-wall clutter |
| skull_pile | (2,9),(3,9) | low front-left clutter |
| urn_spill | (11,9) | low front-right clutter |

## Pillars — IDENTICAL (0 delta)
`pillar_l (3,3),(3,4)` and `pillar_r (8,9),(9,9)` are imported verbatim from the seed and match
the canonical combat grid exactly.

## Follow-up (NOT this lane)
To make the engine collision agree with the fresh plate pixel-for-pixel, a future lane would:
(1) shrink `SARCOPHAGUS_CELLS` to the 2x2 `(4,7),(5,7),(4,8),(5,8)`, and (2) add the 16 ornament
cells above as impassable — re-running the walkslice grid tests. Until then the fresh plate is a
**visual upgrade registered to its own greybox** (edge-recall 0.975); the door zones and central
circulation ring remain walkable in the fresh geometry (flood-fill CONNECTED, both doors reachable),
so the plate is safe to ship as a backdrop while the engine keeps the canonical grid.

---

# ADDENDUM — CRYPT-ALIGN-V2 (M-ALIGN, 2026-07-15): geometry realigned to the PAINT

The "Follow-up (NOT this lane)" above is now DONE, but the paint truth turned out different from what
the pre-adoption reconciliation assumed. Fit-camera overlay forensics (ortho=10.5224 via
`qa/greybox_render_headless._fit_ortho_size(14,11)`, CENTER convention ±0.5;
`qa/evidence/1540/after-align-v2/overlay_v2_fit.png`) proved flux depth-CN **RELOCATED** the interior
furniture during the style pass — the plate does NOT paint the authored props where they were authored:

| authored (v1 seed) | PAINTED on crypt_fresh_v1 | v2 action |
|---|---|---|
| sarcophagus 2×2 `(4,7),(5,7),(4,8),(5,8)` | MONUMENTAL 6×2 tomb, base **cols 7-12 × rows 3-4** (lid/effigy silhouette rises up-screen over rows 2-3); the authored 2×2 cells are painted **open floor** | sarcophagus → **cols 7-11 × rows 3-4** (5×2; see trim) |
| pillar_l `(3,3),(3,4)` | plinth painted at **(4,2),(4,3)**; the authored cells are painted floor | pillar_l → **(4,2),(4,3)** |
| pillar_r `(8,9),(9,9)` | INVISIBLE — renders behind the wall_height=5 cutaway's south wall band; authored cells painted clear floor | **DELETED** |
| skull_pile `(2,9),(3,9)` | painted OUTSIDE the walls on the non-playable exterior apron | **DELETED** |
| urn_spill `(11,9)` | painted OUTSIDE the walls on the exterior apron | **DELETED** |

**Why edge-recall (0.96/0.975) missed it:** the recall metric is dominated by walls/extent and is
structurally insensitive to small/low props (the #1491 class). Only `check_grid_paint_coherence` (waved
off as "diagnostic only") and the post-hoc visual sweep (13 flags, waved off as instrument lag) measured
prop-level registration. Both were RIGHT.

**The one forced deviation from pure paint truth — the coffin east-end trim:** the tavern doorway is at
`(13,4)`; a sarcophagus PROP on `(12,3)/(12,4)` trips `validate_scene_grid`'s door-zone rule (props must
keep a doorway's Chebyshev-1 landing clear). So BOTH the geometry coffin AND the seed coffin are trimmed
one cell at the east end to **cols 7-11**. The plate's 1-cell paint overhang at col 12 is an ACCEPTED,
DOCUMENTED residual — NOT silently exempted. (In the definitive fit-aware sweep it did not even flag —
`(12,4)` fell below the outlier bar — but the trim is recorded as the honest cause of any col-12 flag.)

**Arrival seeding proven:** trimming the coffin narrows the tavern-door Chebyshev-1 walkable ring to
`(12,4)/(12,5)`. `servers/engine/tests/test_crypt_arrival_seed_v2.py` proves a full 4-seat party arriving
FROM the tavern still seeds on 4 distinct walkable, non-prop cells (the arrival BFS floods outward), with
at least one member beside the tavern door. Reciprocal-door round-trips stay green
(`test_arrival_reciprocal_door.py`).

**Geometry and seed now AGREE** (single source): `author_crypt_fresh` references `seed_gfx_combat`'s
`PILLAR_L_CELLS`/`SARCOPHAGUS_CELLS`, so the derived manifest occlusion exempts the live seed props by
construction. Combat hero moved off the tomb: `HERO_CELL (11,3) → (11,8)`.
