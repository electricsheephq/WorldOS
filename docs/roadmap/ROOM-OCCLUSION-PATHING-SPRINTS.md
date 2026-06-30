# Rooms that WORK with actors — occlusion, pathing-lanes, room-size, stray-items (sprints)

> Owner feedback (2026-07-01): the generated backdrops look excellent, but as soon as you add actors a few
> things break — (1) the camera-near walls / ceilings / near pillars **occlude** the interior + the actors
> + the pathing; (2) the rooms feel **small/crunched** for actors to move around; (3) **props block
> pathing** (a sarcophagus jammed next to a staircase) — there are no protected lanes / door zones; (4)
> img2img sometimes paints **stray items** (a lantern floating on a pillar). "Now we get into the more
> complex stuff… keep iterating autonomously given the massive plan + issues/sprints."
>
> Decisions below are grounded in a researched ultracode workflow (real iso-CRPGs: PoE1/2, Infinity Engine,
> Disco Elysium, Diablo, Divinity OS2, BG3). The throughline: **fixed camera → static art-time cutaway, not
> runtime transparency; pathing locked BEFORE art (Diablo two-stage); the "crunch" is prop-density/margin,
> not raw grid size.**

## The 5 decisions

1. **Occlusion = static art-time wall omission (cutaway), not a runtime fade.** Our camera is permanently
   fixed (yaw 45, elev 30, ortho 13, at the −x,−z near corner). So OMIT the camera-near wall meshes from the
   greybox: FAR walls = +z (back) + +x (right) keep full detail; NEAR walls = −x (left) + −z (front) are
   omitted (the front was already open; `cutNear=true` now omits −x). Walls stay in `scene_grid` as
   `impassable_cells` for PATHING — only the visual mesh is cut. A BG3-style runtime fade is **deferred
   indefinitely**, gated on a concrete observed gap (a near-side PROP, not a wall, occluding an actor).
   **Status: SHIPPED (Sprint 1, PR #1213).**
2. **Ceilings = never authored, for any interior.** Universal iso-CRPG convention; zero runtime cost. A
   guard tripwire is in `build_room_greybox.cs`. **Status: SHIPPED.**
3. **Room size = compose fixed-size room-units, do NOT widen ortho/grid.** Widening the frame shrinks every
   object's pixel budget (worsens the LoRA-bound paint) and renegotiates the load-bearing `cellToWorld`
   contract. Current 14×11 grids are already at/above the D&D "comfortable" cell floor — the crunch is
   **prop-density + zero camera margin**, fixed by authoring discipline (reserve clear cells + protected
   lanes) and, for genuinely multi-room spaces, composing 2–4 room-units glued at door cells and stitched
   by the existing plate-swap (`_active_combat.txt`/`_active_campaign.txt`). **Status: Sprint 3.**
4. **Pathing = locked before art (Diablo two-stage), authored never derived.** Add to `SceneGrid` (additive,
   `_StrictModel`): `door_cells` (first-class) + `protected_lane_cells`; derive `door_zone_cells` (door +
   Chebyshev-1) + a camera-near `near_zone` (tall props there occlude). Hard rule: **no prop in a door
   zone or a protected lane**; tall props avoid the near zone. A pure `validate_scene_grid()` (BFS
   reachability: every spawn/door/anchor mutually reachable past props; min clear floor) runs **pre-greybox
   as a hard gate** in `export_scene_grid.py`. **Status: Sprint 2 (this PR).**
5. **Stray items = regional ControlNet conditioning, not just negatives.** Emit a greybox depth pass +
   a prop-occupancy mask (high lock on bare wall/floor, relaxed on authored prop footprints) so img2img only
   ENRICHES authored props and can't hallucinate large items into blank regions; add a `stray_item_negative`
   as secondary insurance. **Status: Sprint 4.**

## Sprints (each PR-sized)

- **Sprint 1 — occlusion cutaway (SHIPPED, #1213):** `cutNear` omits the −x wall + pilasters + coursing;
  no-ceiling guard. Proven: `renders/m1_combat_cutnear.png` (actors fully visible, clear floor).
- **Sprint 1b — near-zone tall-prop rule (SHIPPED, #1219):** the cut-near handles the near WALL; tall
  INTERIOR props (columns) in the camera-near band still occluded (the owner's "church near-left pillars",
  verified on a re-render). Dev-start fix (the owner's "don't generate pillars there" — the per-prop fade
  is the deferred Phase-2 layer): tall occluder props stay in the BACK HALF (r ≤ 5). Church + throne
  colonnade pulled r=7→r=5. Proven painted: `renders/church_nearzone_stray_v{1,2}.png` (open foreground).
- **Sprint 2 — pathing-lane + door-zone schema + validator (SHIPPED, #1214):** `SceneGrid.door_cells` +
  `protected_lane_cells`; `door_zone_cells()` / `validate_scene_grid()` in `scene_grid.py` (no prop in a
  door zone/lane, no disconnected floor pocket, no crunch); hard-fail wired into `export_scene_grid.py`;
  `test_scene_grid_validate.py`.
- **Sprint 3 — room-unit composition (OPEN, #1217):** author multi-section spaces (e.g. crypt = stair + tomb)
  as room-units linked at door cells; recipe entries; verify the plate-swap flips on crossing a door cell.
  NOTE: current single rooms already read SPACIOUS post-occlusion-fix (see church renders) — the crunch was
  largely the near walls/pillars closing in the view; composition is for genuinely bigger multi-feature spaces.
- **Sprint 4 — stray-item control (OPEN, #1218):** `stray_item_negative` SHIPPED (#1220 — suppresses
  img2img-invented props like the lantern-on-a-pillar; proven no over-suppression on church renders);
  REMAINING: greybox depth pass + prop-occupancy mask + regional conditioning so img2img only ENRICHES
  authored props (note: our pipeline is img2img not controlnet — validate the regional-conditioning fit).
- **Backlog (deferred, gated on observed need):** per-prop dithered alpha-clip fade for a near-side PROP that
  still occludes an actor after the back-half rule (the owner's "transparency when you walk around them").

## Invariants (unchanged)

Engine = SOLE WRITER; pathing AUTHORED in the `scene_grid`, never derived from painted pixels; all new
`SceneGrid` fields are additive (`extra="forbid"`, default empty == today). The camera contract
(cell 2.0 / ortho 13 / elev 30 / yaw 45) is byte-identical per room-unit.
