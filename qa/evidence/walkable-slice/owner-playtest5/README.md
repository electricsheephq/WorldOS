# Owner playtest #5 — collision-coherence proof (PR #1507)

Projection-overlay evidence that the crypt+camp collision now matches the painted plates. Each grid
cell's grounded center is projected onto the deployed 1344x768 plate with the verified
`greybox_render_headless` `world_to_screen` basis (<1e-3 vs Unity Euler(30,45,0)) — the SAME
ground-truth registration the runtime uses. Impassable prop footprints are shaded.

- `crypt_OLD-footprints-drifted.png` — the pre-fix canonical cells: the sarcophagus block (red) sits
  down-right of the coffin, covering the OPEN lit floor to the tomb's right (owner "cannot walk right
  of the tomb"); both pillar cells (blue/magenta) miss their painted bases entirely (walk-through).
- `crypt_NEW-footprints-on-paint.png` — corrected: pillars on their 2-cell bases, sarcophagus on the
  coffin body (cols3-7 x rows6-8); floor right of the tomb freed. Green = walkslice rest spawns (clear).
- `camp_NEW-footprints-on-paint.png` — the fully re-derived camp set on the DEPLOYED
  camp_clearing_night_v2 plate (fire / firewood / 4 crates / 2 stone walls / gate posts / lean-to /
  2 bedrolls = 39 disjoint cells). Blue = spawns (clear). The pre-fix seed blocked open ground for a
  DIFFERENT (greybox/v1) composition, so every painted solid was walkable — the "essentially open grid".

Occluders derive from these SAME occluder-prop footprints (viewer `_combat_occluders` ->
`CombatSurfaceClient.RebuildOccluders`), so the cell fixes re-seat them onto the true silhouettes.
