# TAVERN through the molded unified pipeline — scorecard cycle 1 (2026-07-15)

## What landed (generator improvements, all measured)
- Molded `table` kind (pedestal + wide oval top) + `bar` kind (body + light overhang countertop)
  in build_room_unified.cs — gate v1 showed stone_well reads as a TROUGH and a plain bar box
  edge-on reads as a FLOOR STRIPE. v3 greybox passes the design gate (axes 1-4: 8/7/8/9).
- CUE-MASS RULE QUANTIFIED: a 1.33-high tabletop = ~0 grey delta in the remapped depth (measured;
  flux dropped/moved every table at cs0.85). Raised to 2.0-surface chunky tables / 2.86 bar —
  matching the h2.0 flat proxies that survived in the PROMOTED truegrey tavern. v5 base: 4/4
  tables painted (v3 base: 3/4 present, all displaced).
- Depth remap tightened to the real scene ceiling (wallH+0.5, was max(wallH,8)+1) — small gain;
  the range is dominated by the room's ~30-unit horizontal view span.
- Barrel albedo lightened (dark-on-dark barrels vanished at the v1 gate).

## Honest negative: base registration still FAILS for the flat-interior class
v5 base (asset_qGf6a4smjzCVMx7w8oBCRr1p, cs0.85 seed12345): tables 4/4 but drifted into a flux-
preferred diagonal; candelabra tripled + displaced; hearth painted as a well/stove; an INVENTED
arch door on the NE face (2/2 bases); soft/blurry finish (2/2 — crypt at same params is crisp).
NOT sent to Gemini (structure-lock would freeze the relocations in).

## Root cause + next-cycle levers (the registry recipe holds the answer)
The PROMOTED tavern/crypt recipe (room_recipes.json) ran cs**0.7 best-of-3 with edge-recall
SELECTION** (adopted gen 0.960) — single-shot at any strength has no variance absorber. Next cycle:
(1) numImages=3 + pick by edge-recall vs greybox (the proven selection gate, wire into the loop);
(2) cs probe 0.9/0.95 for flat interiors; (3) hearth: brighter/hotter cue volume (fire glow in the
greybox albedo does nothing — depth is the only channel; consider a taller chimney breast mass so
the hearth reads architecturally); (4) candelabra: same 2-cell/fat-cue treatment as crypt piers.
CU this cycle: 18 (2 flux). Run total ~264/300 — Gemini slot deliberately preserved.
