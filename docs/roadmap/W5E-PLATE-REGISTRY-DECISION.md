# Decision: W5e Runtime Plate Registry — how the player build swaps backdrops per room

**Status: DECIDED (2026-07-10). Owner-delegated to the architect; part of the WALKABLE-SLICE-V1
build lane.** Resolves the "how does crossing a doorway change the painted backdrop" seam for the
first owner-playable slice (walk the crypt → talk to an NPC → cross a door INTO camp → start a
fight in place).

## Context

The macOS standalone player (`CombatSurfaceClient.cs`, the RENDER-DELIVERY-DECISION option (c)
surface) is a **pure consumer**: it polls `/combat-surface`, repositions the engine's authoritative
tokens, and POSTs move/walk/parley/cross intents. Until now the painted backdrop ("plate") was
BAKED into the shipped scene by the editor capture path (`paint_combat_v1.cs`) at scene-save time —
ONE room per built scene. Crossing a doorway (`cross_door`) relocates the party to a new engine
location, but the player kept rendering the old crypt plate. The milestone requires the backdrop to
visibly become the **camp** when the party crosses into it.

Two plates ship in this slice:
- **crypt** — the ADOPTED registered plate `room_crypt_armb_iter3_styled_20260710` (`library/rooms`).
- **camp** — `camp_clearing_night_v2` (`qa/room_manifests/camp_clearing_night_v2.cells.json`).

## Options considered

- **(a) Per-room baked scenes + scene load on cross.** One Unity scene per room, `SceneManager`
  swap on `cross_door`. Highest fidelity per scene, but: a scene per room does not scale (the
  "adding a room" cost is a full editor bake + rebuild), and a mid-session `LoadScene` tears down
  the live `CombatSurfaceClient` (poll loop, spawned actors, QA channel) — fragile for a walkable
  slice. Rejected.
- **(b) Addressables-packed plates, addressable-load on cross.** Content-addressed plate assets,
  async load by address. Correct at large scale, but pulls in the Addressables package + a build
  pipeline step for a 2-room slice, and is heavier than the problem. Deferred (see Reversibility).
- **(c) Runtime plate registry — ONE persistent scene, backdrop material texture swapped at runtime
  from a StreamingAssets manifest keyed by engine location.** No scene reload, no per-room bake, no
  Addressables. A plate is runtime DATA (a PNG + a plane sizing), not scene CONTENT. **CHOSEN.**

## Decision

**ONE persistent Unity scene. The backdrop plate is resolved AT RUNTIME by the engine's location
id/slug via a StreamingAssets manifest, `plates_manifest.json`.** On a surface location change (the
`location.id` on `/combat-surface` differs from the last-applied, which is exactly what a
`cross_door` response drives), the client does a brief fade, `Texture2D.LoadImage`-swaps the plate
onto the backdrop material, reapplies plane sizing + camera pin from the manifest entry, and
repositions the cast via the existing grounded placement. **Walkable/impassable truth and occluders
stay ENGINE-side** — they already arrive on the surface (`impassable`, `occluders`, `grid`); the
manifest carries ONLY presentation data (plate file + plane size + camera pin), so this never
becomes a second writer of game state (the SOLE-WRITER invariant holds).

### `plates_manifest.json` schema (StreamingAssets)

```jsonc
{
  "version": 1,
  "plates": {
    "<location_slug>": {
      "plate": "plates/<file>.png",   // path under StreamingAssets
      "planeSize": [W, H],             // world units of the backdrop quad (matches the plate aspect × scene scale)
      "cameraPin": { "ortho": 13.0, "pitch": 30.0, "yaw": 45.0 },  // optional; omitted => leave the scene camera as-is
      "stage": "stage.json"            // optional; reuse the W6.4 stage-manifest (fire flicker/glow) for this room
    }
  }
}
```

- **Key** = the engine location slug. The surface already carries `location.id`
  (`viewer/server.py _session_location`); the client keys on it (the same value it already caches as
  `_occLocId` for occluder rebuilds). Unknown/absent key ⇒ no swap (keep the current plate) — a
  new room is invisible-but-safe until its manifest entry lands, never a crash.
- **Grid dims + occluders are NOT in the manifest** — they are engine truth on the surface. The
  runtime swap reuses the existing W6.1 `RebuildOccluders` path and the W6.4 `stage.json` layer.

### Client trigger

`ParseSurfaceExtras` already extracts `location.id`. A new `_plateLocId` tracks the last-APPLIED
plate location; when the parsed location differs and the manifest has an entry, `ApplyPlate(slug)`
runs (fade → `LoadImage` → resize plane → reposition). This fires naturally on the `cross_door`
re-fetch (the response carries the new `location`), so no new plumbing on the cross path.

### Packaging

`BuildMacOSPlayer.EnsurePackaged()` (which already stages `registry.json`, the actor bundle, and the
optional `stage.json`) additionally copies `plates_manifest.json` + every referenced `plates/*.png`
into `StreamingAssets/` at build time. Absent manifest ⇒ today's single-baked-plate behavior
(byte-identical), so the change is additive.

## The repeatable-process condition (why this scales)

**Adding a room later = add ONE manifest entry + drop the plate PNG.** No editor bake, no scene per
room, no rebuild of the renderer. That is the explicit bar this decision was chosen to meet.

## Reversibility

The manifest is consumed behind a single `ApplyPlate(slug)` loader. A future move to Addressables
(option b) — if plate count or memory ever justifies it — changes ONLY that loader (address-load
instead of `LoadFromFile`+`LoadImage`); the manifest key contract and the pure-consumer boundary are
unchanged. This decision is a one-file reversal, not a re-architecture.

## Invariants honored

- **Renderer stays a pure consumer** — the manifest is presentation data; game-state truth
  (walkable cells, occluders, token cells, combat) stays on the engine surface.
- **Engine untouched** — no `servers/engine/` change; the plate registry is entirely client-side
  data + loader.
- **Additive / default-safe** — absent manifest or unknown location ⇒ the current single-plate
  behavior, byte-identical.
