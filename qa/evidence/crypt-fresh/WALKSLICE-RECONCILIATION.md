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
