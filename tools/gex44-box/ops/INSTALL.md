# WorldOSPlayer — install & run

## What is new in this build (FRESH CRYPT — brand-new crypt through the amended pipeline, rebuilt 2026-07-15)
- **The canonical crypt is now the brand-new `crypt_fresh_v1.png`** (#1565 / #1566, adopted at
  neutral-anchor parity 7.0) — it replaces the earlier `crypt_armb_iter3_v1.png`. Dense authored
  geometry: knotwork-carved pillars, a stone sarcophagus with a recumbent effigy, a carved altar,
  arched wall alcoves, a lit wall torch, scattered bones + a funerary urn.
- **Paint == collision**: the engine walkslice grid was aligned to the fresh crypt geometry
  (#1566, WALKSLICE-CRYPT-ALIGN) — what is painted is what collides; two walkable doorways, the
  canonical props preserved.
- Data-only change: this build ships the new plate + updated `plates_manifest.json`
  (`crypt -> plates/crypt_fresh_v1.png`). No client C# change since Box Cycle 1
  (`CombatSurfaceClient.cs` unchanged; the engine remains the sole writer).

## What is new in this build (TAVERN-FIT2 adoption, the truthful tavern — rebuilt 2026-07-14)
- **The canonical tavern is now the density-law `fit2` plate** (`tavern_fit2_v1.png`, PR #1562,
  playability-first ruling on #1557) — it replaces the earlier `tavern_truegrey_v1.png`. It is a
  **truthful** room: **what is painted is what collides**. 14 authored collision props at
  world-true scale (hearth, round tables, benches, barrels, crates, bar counter, stools, woodpile),
  coherence-perfect (edge-recall 0.98, **zero invented furniture**), camera-fit extent.
- **Doorway painted at the authored (8,0)** — the tavern door is rendered in the plate at grid cell
  **(8,0)**, matching the walkslice collision grid exactly, so the door affordance/glow sits on the
  real painted opening (no phantom/mismatched door).
- Data-only change: this build ships the new plate + updated `plates_manifest.json`
  (`tavern -> plates/tavern_fit2_v1.png`). No client C# change since M-ALIGN wave-1
  (`CombatSurfaceClient.cs` unchanged; the engine remains the sole writer).

## What is new in this build (M-ALIGN wave-1, transition + navigation polish — rebuilt 2026-07-11)
- **No greybox flash / black gap on door-cross** (PR #1551, fixes #1544): crossing a door now reads
  `old room -> black -> fade to finished new room`. An opaque black cover is raised the instant a
  room change is detected, BEFORE the destination room's proxies/occluders rebuild behind it — so the
  un-textured grey proxy boxes and the disconnected black frame the owner saw in playtest #8 are gone.
- **Rest-walk follows the engine path** (PR #1551): a rest-mode walk now glides along the engine's
  authoritative route (parsed from the `walk_to_cell` response `path`) and routes **around** props
  instead of straight-lining through tables/firepits ("walking through tables"). Combat glide
  (`lastPath`) is byte-identical/unchanged.
- **Reciprocal-door arrivals** (PR #1550, engine-side): you now arrive at the door you came through —
  the engine's arrival contract lands the party at the reciprocal door cell of the destination room.
  This lives in the viewer/engine (NOT baked into this player build) — run the engine at the matching
  main SHA for the reciprocal-door behavior.
- Client change is a pure consumer (`CombatSurfaceClient.cs`); the engine stays the sole writer. The
  visual journey instrument (PR #1552) is a QA-side sweep, not part of the player — listed for context.

## What is new in this build (SHIP-MORNING, three-room world + fire + tuned collision — rebuilt 2026-07-11)
- **Three-room world**: `crypt <-> camp_clearing_night <-> tavern` — the brand-new firelit tavern
  (PR #1531 NEW-ROOM-TAVERN, epic #1508) now ships its own true-greybox plate
  (`tavern_truegrey_v1.png`) alongside the existing crypt + camp rooms, registered in
  `plates_manifest.json` (3 plate entries). This is the tavern's first-ever player render.
- **Fire**: the camp firepit's animated flame (VFX-ANCHORS) carries forward unchanged; the
  tavern's hearth glow is painted into the plate for this build (an animated hearth fire is a
  follow-up, mirroring the camp `effects` entry).
- **Tuned collision**: camp + crypt collision/walkability continue to reflect the true-greybox
  geometry adopted in SHIP-CAMP/RING-COHESION; no collision regressions expected from adding the
  tavern room (additive plate-registry entry only).

## What is new in this build (VFX-ANCHORS, animated campfire — rebuilt 2026-07-11)
- The camp firepit now has an **animated fire** flickering over the painted pit
  (`camp_clearing_night`, cell [5,8]) — a Synty painterly flame spawned by the new per-plate
  **effects layer**. `plates_manifest.json` plate entries carry an optional `effects` array;
  `effects_registry.json` maps each effect `type` to a prefab. Same mechanism is ready for
  subtle embers/fireflies later (one manifest line). Pure presentation — no gameplay change;
  a plate with no `effects` renders exactly as before.

## What is new in this build (SHIP-CAMP, true-greybox camp plate — rebuilt 2026-07-11)
- `camp_clearing_night`'s backdrop now ships the **true-greybox** camp plate
  (`camp_clearing_night_truegrey_v1.png`, PR #1518/#1519) instead of the legacy
  `camp_clearing_night_v2.png` — correct-scale 3D-authored geometry (fixes the ~25% legacy
  scale drift; fire pit shrunk from a 2x2 blob to the correct 2x1 ring) with paint registered
  to that geometry, so collision/occlusion/scale for the camp room are right by construction.
  Crypt is unchanged this build (`crypt_armb_iter3_v1.png`, iteration-2 restyle did not clear
  the adopt gate — see #1518).

## What is new in this build (ANIM-PACK, #1408 permanent T-pose fix — rebuilt 2026-07-11)
- All HUMANOID actors now play REAL animations from a shared humanoid AnimatorController
  (Explosive LLC RPG Character Mecanim pack, retargeted): a live **idle**, a **walk/run** cycle
  while gliding cell-to-cell, and **attack / hit-reaction / death** during combat — driven from
  the engine surface (Speed float + Attack/Hit/Death triggers). No more T-posed/frozen actors.
- Non-humanoid rigs (giant_spider, dire_rat) and the generic hero template keep the prior
  per-frame animation fallback automatically.

# WorldOS Player — Walkable Slice (macOS)

Universal macOS build (Intel + Apple Silicon), scene `M1CombatV1_canonical` with the
runtime plate registry + rest-stage actor rendering.

This build ships the **owner playtest #4** fixes (PR #1505):
- **Crypt tomb footprint corrected** — the sarcophagus impassable footprint is now the coffin`s
  actual FLOOR cells (cols2-7 x rows7-9), not its silhouette (the old cols3-9 x rows3-7 sat on the
  open floor *behind* the coffin), so actors no longer stand on the painted tomb and the party
  spawns on clear floor, not snug against it.
- **Door affordance** — door cells show a pulsing gold glow + a floating "To <Room>" label so you
  can find how to change rooms.
- **Walkability overlay default-OFF** in normal play (a QA aid now); press **G** to toggle it, or
  set `WORLDOS_WALK_OVERLAY=1`.

## Requirements
- macOS (Intel or Apple Silicon).
- The WorldOS engine/viewer running locally, serving `/combat-surface` + `/move` for a campaign.
- Re-seed `walkslice_smoke01` from this build`s engine so the corrected crypt grid is served.

## Run
1. Unzip `WorldOSPlayer.app.zip`.
2. Start the WorldOS viewer/engine for your campaign and note its base URL
   (the walkable-slice smoke seeds `walkslice_smoke01`; the viewer typically listens on
   `http://127.0.0.1:8790`).
3. Launch the player with the engine URL + campaign id via the launch-contract env vars
   (read at startup by `CombatSurfaceClient.Start`):

   ```sh
   WORLDOS_ENGINE_BASE_URL="http://127.0.0.1:8790" \
   WORLDOS_CAMPAIGN_ID="walkslice_smoke01" \
   WORLDOS_ONBOARD=1 \
   ./WorldOSPlayer.app/Contents/MacOS/WorldOSPlayer
   ```

   (Or export those three vars, then `open WorldOSPlayer.app`.)

## What you`ll see (the walkable slice)
- Your party + the present NPC rendered as 3D actors — **idle + grounded** on the painterly plate,
  on clear floor (never on the tomb or a pillar).
- **Click a floor cell** to walk your character (the actor glides there).
- **Click the NPC** to open a parley.
- **The glowing doorway** — click the pulsing gold door cell (labelled "To <Room>") to cross to the
  next location; the backdrop **plate swaps** (crypt -> campfire clearing) and the cast respawns.
- **Press `F`** to start a fight in place.
- **Press `G`** to toggle the walkability overlay (off by default in normal play).

## Launch-contract env vars
| Var | Meaning |
|-----|---------|
| `WORLDOS_ENGINE_BASE_URL` | Engine/viewer base URL (**required**). |
| `WORLDOS_CAMPAIGN_ID` | Campaign to render (**required**). |
| `WORLDOS_ONBOARD=1` | Name plates + onboarding hint + door affordance (recommended for a first run). |
| `WORLDOS_WALK_OVERLAY=1` | Force the walkability grid overlay ON (QA/debug; default OFF). |
| `WORLDOS_PLAYTEST=1` | Playtest defaults (also enables the overlay). |

## Gatekeeper
The app is unsigned. If macOS blocks it: right-click -> **Open**, or
`xattr -dr com.apple.quarantine WorldOSPlayer.app`.
