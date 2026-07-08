# #1441 (W5d) — Player interactivity evidence

Owner playtest of W5c (2026-07-09) reported: actors (1) FLOAT after moving, (2) TELEPORT instead
of walking, (3) no walk/idle anims during movement, (4) no collision (stack on props/actors).

## Fix (extensions/renderers/unity/scripts/CombatSurfaceClient.cs)

### 1. Grounded reposition (float fix)
- BEFORE: `PlaceActor` re-centered X/Z only and PRESERVED the actor's raw `position.y` — never
  applied spawn's `FloorY - bb.min.y` grounding. Any actor whose pivot Y wasn't already grounded
  (baked actors; post-idle-retarget bounds shifts) floated after a move.
- AFTER: `UpdateActor` → `GroundSnap`/`GroundedPivot` re-grounds feet→FloorY via the SAME BakeMesh
  `Measure` math as `SpawnActor`, on every reposition and at glide-end.

### 2. Engine-confirmed glide (teleport fix)
- BEFORE: `PlaceActor` set position instantly (a pop).
- AFTER: `GlideTo` tweens cell→cell at `GlideSpeed` (6 u/s), following the engine-confirmed
  `lastPath` polyline (straight-line fallback when absent). Rings/AO follow every frame.
- INVARIANT: renderer animates ONLY engine-confirmed cells — the client never moves an actor
  before the `/move` response (`HandleClick`→POST→`ApplySurf(resp.combat)`). A poll reporting the
  same cell is a no-op, so it never interrupts an in-flight glide.

### 3. Walk clips
- `GlideTo` plays a walk animation during the tween: the actor's OWN embedded walk/run clip
  (`FindOwnClip` via `_fbxOf`, `SampleAnimation`), else a humanoid `DonorWalk` (goblin.fbx)
  retarget graph, else glide-with-no-clip (non-humanoid fallback). Returns to idle at rest via
  `PoseIdle` (the same idle SpawnActor establishes).

### 4. Click pre-validation
- `HandleClick` rejects clicks on `_impassable` (engine grid_impassable, parsed from the surface)
  or `_occupied` (token cells) with a brief red ring flash (`FlashReject`) — no doomed POST. Foe
  cell still routes to attack. UX pre-filter ONLY; engine stays authoritative.

### 5. Engine rejection verdict (investigate-only)
- The engine ALREADY rejects moves onto occupied/impassable cells: `move_to_coords`
  (servers/engine/server.py) routes via `combat_grid.shortest_path`, which returns `None` when
  `goal in occupied or goal in impassable` (combat_grid.py:243-244) → `move_blocked`, nothing
  mutated. Unity sends no explicit `path`, so routing always runs in seed_gfx_combat. NO engine
  issue filed. (Trusted the conclusive code path per coordinator; no curl smoke needed.)

### Height reconciliation
- Named constants `ActorHeightFoe=4.2` / `ActorHeightChar=3.2` — the paint_combat_v1.cs
  #1418-calibrated LIVE baked-scene heights (what the client repositions). paint_combat_replay_v1.cs
  still carries a stale pre-#1418 character height of 5.0 (editor reel, out of scope) — flagged.

## Validation
- Compile: clean (0 errors); `CombatSurfaceClient` component compiled + attached (instanceID 312418).
- Editor sanity: `1441_editor_sanity.png` — painterly camp scene, two grounded actors (feet on
  floor, selection rings beneath), props intact, no error materials.
- Rebuild: `Tools/WorldOS/Build/macOS Player (Universal)` → WorldOSPlayer.app.zip → scp to
  ~/worldos-session-notes/w5a-build/ (overwrite).
- Orchestrator + owner re-playtest locally (live engine drive of glide/reject/grounding).
