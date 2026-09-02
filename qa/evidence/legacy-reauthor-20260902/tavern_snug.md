# tavern_snug — geometry re-authored to the painted plate (2026-09-02)

Plate `tavern_snug_v1_registered.png`, ortho 9.2597, 12x10. `overlay_collision --verify` residual
**0.068, 0.067 cell**. Baseline overlay `artifacts/tav_ol.png`, proposal `artifacts/tt_final.png`.

| prop | before | after | why (frame) |
|---|---|---|---|
| `bar_snug` | (4,2),(5,2),(6,2),(7,2) | **(7,1),(7,2),(7,3),(7,4),(7,5)** | the painted counter runs down-screen (+r) at c=7, not across (+c) at r=2. FELT: at (7,5) the hero's ring lands on the counter's front panel — he is inside the bar. `tz_7_5.png` |
| `candle_bar` | (8,2) | **deleted** | the bar lantern stands on the counter (now `bar_snug`); a 1-cell prop there would duplicate a SceneCell. |
| `barrel_e` | — | **deleted** (was briefly authored at (9,5), then (8,6)) | the painted barrel at the bar's end stands on **(8,6)** — FELT: at (8,6) the hero's ring is centred in the barrel's base, he is inside it (`probe_tav_86.png`); the earlier (9,5) reading was the floor BEHIND it (a cell behind tall paint makes the hero look like he is standing on top — the trap this method warns about, and at (9,6) he is clearly beside the barrel, `felt_9_6.png`). But **(8,6) is the ONLY throat into the area behind the bar** — blocking it orphans 16 cells including the shop door's landing (10,4) (`walk_static` RED). Same constraint as (8,5) below: left walkable, and the barrel goes to the plate lane. |
| `hearth_east` | (10,2),(10,3) | **(9,7),(9,8),(10,6),(10,7),(10,8)** | the old cells are up on the crockery shelf. The fireplace's jambs, chimney breast, mantel and the FIRE itself were walkable — the hero stood in the flames at (9,8). `tz_hearth.png` |
| `woodpile_e` | (10,6),(10,7) | **deleted** | those cells are the fireplace (above); the painted woodpile resolves to r ≈ 10 — outside the 12x10 grid. `tz_hearth.png` |
| `table_sw` | (3,5),(4,5),(3,6),(4,6) | **(1,4),(2,4),(1,5),(2,5)** | the big oval table with the lantern and mugs. It was fully walkable — the hero stood on the tabletop. `tt_tables.png` |
| `table_s` | (7,6),(8,6),(7,7),(8,7) | **(4,4),(5,4),(4,5)** | the small round table. The old cells are bare floorboards. `tt_tables.png` |
| `kegs_back` | (1,1),(1,2) | (1,1),**(2,1),(3,1)**, −(1,2) | the painted barrel stack covers (2,1)/(3,1); (1,2) is floor below it. `tt_tables.png` |
| `post_w` | (3,3),(3,4) | **(4,3)** | the timber post's stone plinth. `tt_tables.png` |
| `post_e` | (8,4),(8,5) | **deleted** | the painted pillar's base resolves to r ≈ 10.1 — outside the grid. `tt_right.png` |
| `bench_sw` | (5,5),(5,6) | **deleted** | bare floorboards; the painted bench is off-grid at the lower left. |
| `barrels_sw` | (2,7),(2,8) | **deleted** | bare floorboards; the painted barrels are `kegs_back`. |

**Counts:** props 17 → 12 · blocked cells 65 → 59 · +15 cells, −21 cells. Doors unchanged ((5,0), (11,4)).

**Deliberately NOT blocked:** the barrel at (8,6) (above) and the bar's short arm at (8,5). It reads as part of the counter, but blocking
it orphans 13 cells behind the bar *including the shop door's landing (10,4)* — `walk_static` RED. The bar
is authored as its long arm and the player walks around its open end.

**Follow-ups (NOT changed here):** neither door has a painted opening — (5,0) is plain timber wall and
(11,4) plain plaster. The outer wall ring (col 0, row 9) is drawn a cell inside the visible floorboards,
and the painted bench / woodpile / main pillar all resolve outside the grid: plate-vs-grid registration,
for the plate lane.
