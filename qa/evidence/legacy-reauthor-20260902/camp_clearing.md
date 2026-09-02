# camp_clearing — geometry re-authored to the painted plate (2026-09-02)

Plate `camp_v3_registered.png`, ortho 13, 16x12. Method: `qa/reauthor_legacy_room.md`.
Projection proven on every frame below — `overlay_collision --verify` residual **0.064, 0.082 cell**.

Baseline overlay (before): `artifacts/camp_ol_00.png` · after: `artifacts/v_camp_ov.png`

| prop | before | after | why (frame) |
|---|---|---|---|
| `campfire` | (4,8),(5,8) | (4,8),(5,8),**(4,9),(5,9)** | the painted stone ring circles (4,9) and laps (5,9); the hero walked into the flames at (4,9). `z_fire.png` |
| `firewood` | (7,8),(8,8),(8,9) | (7,8),(8,8),**(7,9),(6,9)**, −(8,9) | the split-log pile covers (6,9)/(7,9); (8,9) is bare dirt beside it. `z_logs.png` |
| `crate_l` | (2,2),(3,2),(3,3),(2,4) | + **(2,3),(1,3)** | the pile's two lower-left crates were open floor. `t_crates.png` |
| `crate_c` | (8,3),(8,4) | **deleted** | bare dirt — the party's own start cell area. `z_mid.png` |
| `crate_wall` | (6,3) | **deleted** | bare dirt on the path. `z_mid.png` |
| `crate_r` | (9,10),(10,10),(10,11) | **deleted** | bare dirt SE of the fire; the ruin's stones are further right. `z_ruinbase.png` |
| `bedroll_l` | (1,8),(2,8),(2,9),(3,9) | **deleted** | four invisible blockers on bare firelit dirt. `camp_ol_bl.png` |
| `bedroll_r` | (5,10),(6,10),(6,11) | **(10,6),(11,6)** | re-pointed from bare dirt to the rolled bedroll in front of the lean-to. `t_lean.png` |
| `wall_bl` | (5,2),(6,2),(7,3) | (5,2),(6,2) | (5,2)/(6,2) sit on the root-and-rock mass at the clearing's north edge; (7,3) is open dirt. `z_left.png` |
| `wall_br` | (10,5),(10,6),(11,6) | **deleted** | no stone wall is painted there — it is the lean-to's ground, now `shelter` + `bedroll_r`. `t_lean.png` |
| `shelter` | (12,2),(12,3),(13,3),(13,4),(14,4) | **(10,4),(11,4),(12,4),(10,5),(11,5),(12,5)** | the old cells trace the lean-to's ROOF; the structure stands ~1.7 cells down-screen. Hero used to stand on the painted bedrolls at (11,5)/(12,5). `t_lean.png`, `z_leanground.png` |
| `ruin_wall_head` | — | **(12,6),(12,7)** (new, `stone_wall`) | the ruin's low wall starts here and was walkable — the hero stood inside the lean-to's leg post. `t_camp_new.png` |
| `wall_br2`,`wall_br3`,`ruin_tower1/2`,`ruin_link`,`ruin_rubble_pocket` | — | unchanged | verified on the ruin's painted stone. `z_ruin.png` |
| `(14,1)` "lean-to roof" | walkable | **unchanged (walkable)** | the collision lens called this roof paint. REFUTED on the contract projection (residual 0.07 cell): the hero's ring lands on the dark forest floor BEHIND the shelter — the roof planks start at (13,2)/(14,2) and run down-right of him. Blocking it would re-create the phantom-blocker class. `camp_clearing/felt_14_1.png` |

**Counts:** props 17 → 13 · blocked cells 50 → 43 · +13 cells, −20 cells. Doors unchanged ((8,0), (0,6)).

**Affordance follow-ups (NOT changed here):** door (0,6) "To Crypt" has no painted opening — its ground
point is bare dirt below the crate pile, and the label glyphs are drawn over the crates. Door (8,0)
"To Tavern Snug" sits on the forest path but its label projects off-grid (r ≈ −3). Both are label/hotspot
work, not geometry.
