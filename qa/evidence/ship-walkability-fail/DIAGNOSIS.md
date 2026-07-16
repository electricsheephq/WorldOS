# SHIP WALKABILITY FAILURE — diagnosis (2026-07-15, owner playtest)

## The process failure (root of it all)
The blind panel gates BEAUTY (crypt 8.3/tavern 8.4/throne 7.0). It is BLIND to WALKABILITY. I shipped
on the beauty gate alone — never drove the character through the rooms. qa/playtest_drive.sh is the
missing gate (drive :8971 /click + /shot, read each frame). It reproduced every owner complaint in 3
clicks. RULE: no room ships until playtest_drive shows the character landing ON the clicked floor cell,
occluded behind foreground pillars, and impassable cells rejected.

## What's actually broken (engine is FINE; all client/data)
Engine has Aldric at the EXACT clicked cell every time (clicked (12,2) → engine token x=12,y=2;
collision/move-resolution correct). Every symptom is client render + seed data:
1. NAIVE SPAWNS (seed): build_grid_from_geometry drops the party at the first free cells (1,1),(4,1)
   = back-wall corner, next to tavern barrels → "spawn inside a barrel". Fix: author sensible open-floor
   spawns per room (the hand-walkslice did; the generic builder must too).
2. DOOR-CELL vs PAINTED-DOORWAY MISMATCH (#1534 class): tavern door_cell (7,0) is BEHIND the bar, but
   flux/Gemini painted the archway on the LEFT wall → "To Crypt" + the cross-door spawn land on the bar.
   Fix: re-measure each door_cell against where the plate PAINTED the doorway (cell-lattice overlay),
   OR condition the paint so the doorway lands at the authored cell.
3. OCCLUSION REGRESSION (client): no character masking behind pillars (worked on the OLD rooms). OLD
   rooms used the legacy _occRaw footprint path; NEW rooms carry a boxes sidecar → the _plateBoxes path
   gated by #1575's `_plateBoxesLocId == _locId`. SUSPECT: that gate rejects the sidecar (or the
   sidecar boxes project at the wrong scale) → RebuildOccluders clears → no masking. VERIFY next: check
   whether the surface `occluders` legacy set is populated for these rooms, and whether _plateBoxesLocId
   is set when ApplyPlate runs for a reseeded room.
4. ACTOR PROJECTION drift (client): the character renders offset from the plate-correct screen position
   of its cell (world_to_screen(cell,ortho) vs where it draws). SUSPECT: the manifest cameraPin has
   ONLY `ortho` (no pitch/yaw), so ApplyPlate (CombatSurfaceClient.cs:3275) does NOT reset the camera
   rotation/position to build_room_unified's rig (Euler(30,45,0), pos -(rot*fwd)*80 aiming at origin) —
   the camera keeps a stale aim, so a 16x12 room's origin-centered grid doesn't line up with the plate.
   FIX to test: add pitch:30, yaw:45 to each cameraPin so the full rig is reproduced.
5. THRONE TWO-STORY (paint): Gemini invented an upper gallery + staircases (the documented additions
   class) that have no geometry/collision → walk-through-walls up there. Fix: mask-composite
   (qa/overlay_boxes.py --composite) to keep only the styled room-volume + a tighter additions-lock, OR
   drop throne from the shipped set until cycle-2.

## Fix order (each VERIFIED by playtest_drive before re-ship)
(1) camera pitch/yaw in the 3 cameraPins → re-drive, confirm actor lands on the clicked cell.
(2) occluder path: confirm _plateBoxes builds for reseeded rooms → confirm masking behind pillars.
(3) sensible spawns in the seed builder.
(4) door_cell↔painted-doorway reconciliation per room.
(5) throne additions (mask-composite) or defer throne.
Client changes need a box rebuild + reinstall; gate on playtest_drive BEFORE pushing to the owner player.
