# throne_hall — geometry re-authored to the painted plate (2026-09-02)

Plate `throne_hall_v1_registered.png`, ortho 11.7851, 16x12. `overlay_collision --verify` residual
**0.069, 0.072 cell**. Baseline overlay `artifacts/throne_ol.png`.

| prop | before | after | why (frame) |
|---|---|---|---|
| `throne` | (7,1),(8,1) | **(10,6),(10,7),(11,6)** | the declared throne sat on the grand staircase at the back left. FELT: at (10,6) the hero's ring lands on the throne's seat — he sits in it. `hz_106.png`, `ht_throne.png` |
| `dais_brazier_l` | (5,2) | **(9,5)** | FELT: at (9,5) the hero stands inside the brazier bowl. `hz_95.png` |
| `dais_brazier_r` | (10,2) | **(9,9)** | the second lit brazier, beside the crypt door's step. `ht_throne.png` |
| `dais` | (6,1),(7,1),(8,1),(9,1),(6,2),(7,2),(8,2),(9,2) | **deleted → `grand_stair`** | those 8 cells are correct as a blocker but they are the GRAND STAIRCASE, not a dais. The painted dais platform is raised FLOOR and is now walkable. `hz_tl.png`, `ht_left.png` |
| `grand_stair` | — | **(6,1),(7,1),(8,1),(9,1),(6,2),(7,2),(8,2),(9,2)** (new, `stone_wall`) | same cells, honest kind. `ht_left.png` |
| `col_nw` | (4,4),(4,5) | **(7,3)** | the painted north pillar's base. The old cells are open mosaic floor. `ht_left.png` |
| `col_ne` | (11,4),(11,5) | **deleted** | painted dais top / throne back — walkable. `ht_throne.png` |
| `col_sw` | (4,8),(4,9) | **deleted** | open mosaic floor; no pillar is painted there. `ht_left.png` |
| `col_se` | (11,8),(11,9) | **deleted → `colonnade_e`** | folded into the east band below. |
| `offering` | (7,4),(8,4) | **(5,8),(6,8)** | the stone altar box on the mosaic floor. `ht_left.png` |
| `urn_l` | (2,3) | unchanged | verified on the west pillar's base. `ht_left.png` |
| `urn_r` | (13,3) | **deleted → `arcade_e`** | inside the exterior band. |
| `arcade_e` | — | **c 12,13,14 × r 1..10** (30 cells, new `stone_wall`) | THE out-of-bounds band. FELT: at (13,8) the hero stands halfway up the arcade wall; at (12,9) he floats on top of a column. Nothing outside the colonnade is floor. `hz_138.png`, `hz_129.png`, `ht_band.png` |
| `colonnade_e` | — | **(11,1),(11,2),(11,3),(11,7),(11,8),(11,9),(11,10)** (new, `stone_pillar`) | the colonnade line itself — wall and column bases either side of the throne. `ht_band.png` |
| `wall_e_0` | (15,1)..(15,5) | + **(15,6)** | seals the retired side passage. |

**Door change.** `door_cells` (8,11),(15,6) → **(8,11)** only; `protected_lane_cells` drops (14,6).
The authored side passage (15,6) opens onto the painted arcade wall, and its landing (14,6) is inside the
out-of-bounds band, so it was retired to wall rather than kept as a future seam into stone.
`ALLOWED_UNWIRED` is now empty in `qa/seed_adventure_demo.py`.

**Counts:** props 17 → 15 (5 deleted, 3 added) · blocked cells 72 → 105 · +43 cells, −10 cells · 1 door retired.

**Follow-ups (NOT changed here):** the crypt door (8,11) still lands on the dais's lower step beside a
brazier and the hall's four painted archways are inert — label/hotspot work. The two foreground columns
and the lower stair resolve to r ≈ 13-15, outside the 16x12 grid (plate/grid registration).
