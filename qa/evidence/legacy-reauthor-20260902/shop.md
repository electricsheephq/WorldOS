# shop — geometry re-authored to the painted plate (2026-09-02)

Plate `shop_v1_registered.png`, ortho 9.6806, 13x10. `overlay_collision --verify` residual
**0.073, 0.008 cell**. Baseline overlay `artifacts/shop_ol.png`, proposal `artifacts/st_final.png`.

| prop | before | after | why (frame) |
|---|---|---|---|
| `counter` | (5,3),(6,3),(7,3),(8,3) | **(4,4),(5,4),(6,4),(7,4),(8,4),(9,4)** | one row off: r=3 is the walkway BEHIND the counter (its painted top surface), r=4 is the counter itself. FELT: at (5,4) the hero's ring is on the counter's front panel. `sz_54.png` |
| `brazier_counter` | (9,3) | **(5,7)** | declared on the counter top; the actual lit standing brazier is at (5,7) and accepted the walk. `sz_counter.png`, `st_final.png` |
| `display_table` | (7,6),(8,6),(7,7),(8,7) | **(1,7),(1,8),(2,8)** | the tool bench. FELT: at (1,8) the hero stands inside it. `sz_18.png` |
| `bench_front` | (5,5),(6,5) | **deleted** | invisible bench across open floorboards between counter and brazier. `shop_ol.png` |
| `barrels_e` | (10,4),(10,5) | **(8,8),(9,8)** | the old cells are the archway approach; the two painted barrels stand at (8,8)/(9,8). `sz_right.png` |
| `shelves_back` | (2,1),(3,1),(4,1),(5,1) | **(5,1),(6,1),(7,1)** | the bottle/cloth shelf unit. (7,1) was the ARRIVAL cell from the tavern — the player landed on top of the shelving. `sz_left.png` |
| `crates_sw` | (2,6),(3,6),(2,7),(3,7) | **(1,1),(1,2),(2,2)** | the painted crate stack is at the back left, not mid-floor. `sz_left.png` |
| `till_table` | (10,1),(11,1) | **(2,4),(3,4)** | the till table with the cash box. `sz_left.png` |
| `barrel_w` | — | **(3,2)** (new, `barrel`) | the barrel beside the shelves. `sz_left.png` |
| `sacks_se` | (10,7),(11,7) | **deleted** | rolled into `exterior_e` (same cells, honest kind). |
| `exterior_e` | — | **(10,6),(10,7),(10,8),(11,6),(11,7),(11,8)** (new, `stone_wall`) | the plate paints panelled wall and the exterior stone stair landing here — it is not shop floor. `sz_right.png` |
| `shelves_e` | — | **(8,1),(9,1),(8,2)** (new, `shelf`) | the second bookshelf right of the first — FELT: the party's arrival landed on it. `s_back.png` |
| `barrel_back` | — | **(9,2)** (new, `barrel`) | FELT: the first arrival after the door move put the hero standing on top of this painted barrel. `v_shop_arr.png` |
| `wall_back_e` | — | **(10,1),(11,1),(10,2),(11,2),(11,3),(11,5)** (new, `stone_wall`) | painted plaster wall and the archway's frames — not shop floor. `s_back.png` |
| `wall_n_0` | (0,0)..(5,0) | + **(6,0)** | seals the retired door cell into the back wall. |

**Door move.** `door_cells` (6,0),(12,5) → **(12,4)** only; `qa/seed_adventure_demo.py` now wires
`("shop", [([12,4], "tavern_snug")])` and `ALLOWED_UNWIRED` is empty. `wall_e_0`/`wall_e_1` re-split so
(12,4) is the gap and (12,5) is wall.

The shop's only painted opening is the archway. Its floor cell is the INTERIOR cell (10,5) and a door must
be on the perimeter (`check_geometry`), so the seam went to the perimeter cell whose LANDING is inside the
painted arch: **(12,4) → landing (11,4)**. FELT: the party now stands in the archway's threshold
(`f_shop114.png`). The first attempt, (12,5) → landing (11,5), put them on the wall panel beside the arch
(`f_shop115.png`) — kept as the counter-example, and (11,5) is now wall.
(6,0)'s landing (6,1) is mid-shelf in the paint, so (6,0) was sealed into `wall_n_0` rather than left as a
door into a bookshelf. `sz_arch.png`

**Counts:** props 15 → 18 (2 deleted, 5 added) · blocked cells 65 → 78 · +36 cells, −22 cells · 1 door move + 1 door retired.
