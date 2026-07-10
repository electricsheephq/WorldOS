# WALKABLE-SLICE-V1 — CRYPT-COHERENCE re-verify (v2 frames)

Fix for the orchestrator eyeball defect: the player's crypt was a pale, unstyled render on a
layout that did NOT match the canonical crypt (the seed authored its own crypt grid + the manifest
bundled a non-adopted plate — the #1396 scene-grid coherence defect class).

## What changed
- `qa/seed_gfx_walkslice.py` — the walkslice crypt now REUSES the canonical combat crypt grid
  (`seed_gfx_combat._build_crypt_grid`: 14x11, sarcophagus cols3-9 x rows3-7, pillars (2,4)/(9,9),
  #1386) with ONE addition — a back-center doorway `(6,0)` the party crosses to the camp. Party +
  Mira spawn on front-floor cells (r=8), in front of the mid-height tomb, clear of the sarcophagus
  and both tall pillars.
- `extensions/renderers/unity/plates_manifest.json` — crypt entry now points at the ADOPTED plate
  `plates/crypt_armb_iter3_v1.png` (library/rooms `room_crypt_armb_iter3_styled_20260710`, warm
  dense firelit crypt) instead of the non-adopted `plates/crypt.png`. The PNG was staged into the
  box Unity project `plates/` + `Assets/StreamingAssets/plates/`.

## Frames (GEX44, Unity 6000.5.1f1, direct-camera RenderTexture, 2560x1440, non-black gated)
- `1_crypt_rest_idle_v2.png` — crypt rest on the WARM adopted plate; Aldric + Mira idle+grounded on
  the floor in front of the tomb, nobody standing on the sarcophagus or a pillar. (mean 0.23 / std 0.17)
- `2_camp_after_cross_v2.png` — after `cross_door(6,0)`: the plate visibly swapped to the night camp
  clearing; the party re-staged grounded beside the campfire. (mean 0.13 / std 0.14)

## Coherence note
An initial pass placed Mira at (9,8) — directly screen-in-front of the tall painted pillar at (9,9),
which made a floor-grounded actor read as standing ON the column. Relocated to (4,8) (in front of the
mid-height tomb, like the party) where she grounds cleanly. No conflict found between the adopted
plate's painted geometry and the canonical grid's impassable set (the plate-drift gate already
passed the adopted plate: sarcophagus/pillar NCC 0.945/0.9615/0.968).
