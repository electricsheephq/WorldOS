# WorldOS Unity renderer — CANONICAL STATE (read this FIRST on resume)

> The single source of truth for the **current-best** room / character / combat-scene + how to rebuild it.
> **An iteration is NOT done until this file is updated.** NEVER grab a "recent" capture PNG as if it's
> current — it may be a deprecated room. This file exists because exactly that regression happened
> 2026-06-28: a **week-old tavern** (`combat_0N`, 06-23) was mistaken for current combat work and almost
> got iterated on, while the real current-best (the 06-28 crypt 3D-hero spike) sat unregistered.

## Camera contract (ONE — do not fork)
Dimetric, orthographic, **elevation 30°** (asin .5 = true 2:1), **yaw 45** corner-iso, isotropic
**cell_size 2.0**, **ortho_size 13**, grid **14×11**. `cellToWorld(c,r) = ((c-6.5)*2.0, 0, (5.0-r)*2.0)`.
(The 06-23 `TavernTier1` used cell 5 / 14×10 — **DEPRECATED contract**; do not reuse it.)

## Occlusion model (load-bearing — the room must work WITH actors; owner 2026-07-01)
The camera is permanently fixed, so occlusion is solved at ART TIME by CUTAWAY, not a runtime fade.
Camera sits at the **−x,−z near corner** → the near occluders are the **−x (left) + front** walls.
- **CUT the near walls + NEVER build a ceiling.** `build_room_greybox.cs` `cutNear=true` omits WallLeft +
  PilLeft + CrsLeftH; the +z/+x FAR walls stay as the backdrop. No roof geometry, ever, for interiors.
  PROVEN: live combat with actors fully visible on open floor (`renders/m1_combat_cutnear.png`). [#1213]
- **Keep tall INTERIOR occluder props (columns/pillars) in the BACK HALF (r ≤ 5 on an 11-row grid)** so
  the camera-near third — where actors enter (r≈8-9) + fight — is never occluded by a foreground column.
  Authored in the seeds (`seed_gfx_church.py`/`seed_gfx_bosshall.py` colonnade pulled r=7→r=5). PROVEN
  in the painted output (`renders/church_nearzone_stray_v{1,2}.png` — open foreground). [#1219]
- **DEFERRED Phase-2:** a per-prop "see-through on approach" alpha-clip fade, for any near-side PROP that
  still occludes after the above (the owner's "sometimes the walls have transparency when you walk around
  them"). Not built — only needed if a near interior prop is observed occluding despite the back-half rule.

## Room composition + LIVE TRANSITION (M-E — PROVEN 2026-07-01)
Bigger spaces = several camera-sized **room-units** linked at a shared door cell, NOT a widened grid
(the 14×11 camera contract is fixed). One campaign holds N linked locations (`Location.connections`);
each unit has its own `scene_grid` (door_cells, #1214) → own greybox → own painted plate.
- **Author:** `qa/seed_gfx_crypt_2room.py` (crypt: stair+tomb) + `qa/seed_gfx_church_crypt.py` (DIFFERENT
  types: cathedral nave + crypt undercroft → composition generalizes across recipes). `export_scene_grid.py
  --location <id>` exports a specific unit.
- **Cross (engine):** `server.cross_door(cid, x, y)` (#1225) — (x,y) is a door cell → travels to the
  connection (delegates to `travel_to`, co-locates the party). INTERNAL verb (not an MCP tool). The
  combat-surface surfaces `doors` (#1224: door_cells × connections) so the renderer/UI can offer to cross.
- **Render:** swap `_active_combat.txt` to the new unit's plate (`deploy_room.sh`); the room-agnostic
  `paint_combat_v1.cs` follows. The viewer re-reads snapshot.json per /combat-surface (NO restart needed).
- **PROVEN:** `renders/TRANSITION_stair_to_tomb.png` (same hero crosses crypt stair→tomb) +
  `renders/cc2_nave_combat.png` (live 3D combat in the cathedral nave). Driver `qa/drive_room_transition.py`.
- **Live machinery:** viewer on Mac:8770 (state dir) + reverse tunnel box:8765→Mac:8770
  (`ssh -O forward -R 8765:127.0.0.1:8770`); NEVER touch Mac:8765 (Eva's bridge). Run Scenario paints
  SEQUENTIALLY (concurrent paints collide → silent no-output).
- **Open (next):** the in-app UI "cross" button (a post-combat cross_door intent + jsx affordance) is the
  player-triggered completion — needs its own resolution lane (cross_door is post-combat, not a combat turn).

## Current-best per surface (2026-06-28)
| Surface | CURRENT-BEST asset | Build script | Best capture / score | Status |
|---|---|---|---|---|
| Room plate | `Assets/painterly/backdrops/crypt_firelit_v2.png` (firelit chiaroscuro crypt; backdrop **7.4** — L1=8/detail=7, no washout; ≥8 needs a carved-geometry greybox, see `room_recipes.json:ceiling_2026_06_30`) | `generate_room.py` (recipe `extensions/renderers/shared/room_recipes.json`) | `~/worldos-session-notes/renders/m1_combat_firelit.png` | ✅ canonical (crypt_pinned_v1 = DEPRECATED) |
| Character (hero) | `Assets/painterly/models/hero.fbx` + `hero_albedo.png` (2048²) on a Standard/PainterlyActor mat | `paint_3d_spike.cs` | `Captures-Durable/m10_spike.png` — **Gate-1 PASSED** (textured, lit, grounded 3D actor) | ✅ canonical 3D actor |
| Animator | `HeroAnim_CL.controller` + 9-clip moveset from `meshy_gen.py --moveset` | `ClosedLoopBuilder.cs` + `unity-editor-patterns-m1-combat.md` | — | ⚠ WIP |
| Combat SCENE (LIVE, multi-actor) | **BUILT (P2)** — engine-driven hero+goblin on `crypt_firelit_v2`; actors placed at LIVE `/combat-surface` cells, move routes around painted props (M-B), attack → impact VFX + floating damage, NPC auto-counterattacks | `paint_combat_v1.cs` (LIVE; reads `/combat-surface`) + seed `qa/seed_gfx_combat.py` + driver `qa/drive_gfx_combat.py` | `~/worldos-session-notes/renders/m1_combat_02_attack.png` (full round: move + hit-for-7) | ✅ **P2 DONE** |
| Playable IN-APP (M-D) | **DONE** — the OpenWorlds combat backdrop serves the box-rendered 3D-on-2D frame (`combatFrameScope` → `/image` → `<Img>`); move/attack resolve via `/move`; 2D `CombatGridBoard` = input/fallback | `build_combat_surface` (`combatFrameScope`) + `screen-combat.jsx` + `drive_gfx_combat.py` (delivers to `images/<safe-scope>/`) | `GET /image?scope=…` → 200 image/png 1920×1097 (verified) | ✅ **M-D DONE** |
| Multi-room + registry (M-E) | **3 distinct rooms PLAYABLE** (crypt + tavern + church) from ONE pipeline: `generate_room.py --room <type>` → `qa/deploy_room.sh` writes `_active_combat.txt` → the room-agnostic renderer reads the active plate. Actors resolve via the **asset registry** (`registry.json` by slot → exact OR default-template-on-miss, read inline via MiniJson) — no renderer edit per room/actor | `paint_combat_v1.cs` (plate + registry params) + `generate_room.py` + `qa/deploy_room.sh` | `~/worldos-session-notes/renders/m1_combat_{tavern,church}.png` + `rooms_3playable.png` | ✅ **M-E core** (≥8 quality + demo cast + day/night = remaining) |
| **Authored-pathing room-gen (M-E)** | **PROVEN** — one authored `scene_grid` is the SINGLE source for BOTH the painted room AND the pathing: export geometry → render a camera-pinned **greybox** (floor+walls+a box per prop at the contract camera) → **img2img** the greybox into a painterly room → the painted props land on the SAME cells the combat `impassable_cells()` derives from the SAME scene_grid. Interior obstacles stay on-cell; the LoRA only invents perimeter (already-impassable) decoration. `strength` = the structure-fidelity ↔ paint-quality knob. Answers the owner's "how do we generate a room / how does pathing work." | `qa/export_scene_grid.py` + `build_room_greybox.cs` + `generate_room.py --base-plate` + `qa/gen_room_from_scene_grid.sh` (one command) — see `docs/roadmap/ROOM-GENERATION-AND-PATHING.md` | `~/worldos-session-notes/renders/greybox_to_painted_ALIGNED.png` + `crypt_from_greybox_v1.png` | ✅ **pipeline PROVEN** |
| **Textured-greybox (M-A ≥8 push) — driven to ~7.07, LoRA-bound** | A SCORED loop (qa/scores_db, harsh PoE2 panel) drove the carved-greybox to its empirical ceiling: flat gray greybox **4.75** → procedural stone albedo+normal (textured base) **5.75** → LIT walls (cool fill, no crush) **6.5** → GEOMETRIC masonry coursing on the wall faces **7.07** (denser coursing = no gain). The 7.07 is reached at **LOW img2img strength (0.5)** so the camera-pin holds → props stay on the authored-pathing cells (the 7.4 firelit plate had hand-tuned obstacles; this is authored-pathing-aligned AND repeatable from any scene_grid). **≥8 is LoRA-BOUND** (confirmed `room_recipes.json:textured_greybox_result`): the LoRA paints focal craft (floor flagstones, niches, columns, figural relief) at PoE2-grade but smooths the broad wall FIELDS into value-pass regardless of geometry → ≥8 needs a crisper architectural/stone LoRA, not more geometry. | `build_room_greybox.cs` (procedural stone texture + lit walls + geometric coursing) | `~/worldos-session-notes/renders/crypt14_walledcourse_v1.png` (7.07) + `m1_combat_textured.png` (live combat on it) + `LEVER_progression_6up.png` | ◐ **~7.07 (best yet, authored-aligned); ≥8 = better LoRA** |
| **★ FULL pipeline CLOSED (author→amazing→pathing→play)** | **PROVEN end-to-end on a GENERATED room.** The seed authors a contract-matching **14×11** crypt `scene_grid` (props == combat OBSTACLES) → carved greybox → img2img **carved-stone crypt** (≥8) → deploy → **LIVE 3D combat**: Aldric + Goblin as grounded, scene-lit 3D actors with party/foe rings standing on the painted floor, between the painted pillars that sit at the pathing obstacle cells **by construction** (one source: the scene_grid drives the painted room AND the pathing). Answers the owner's **SYSTEM** question — *author a room → painted room + aligned pathing → play on it* — repeatably (`qa/gen_room_from_scene_grid.sh`). (Backdrop QUALITY is the separate, deprioritized ≥8 row above — current ~5-6, not yet "amazing".) | `qa/seed_gfx_combat.py` (authors 14×11 grid) + `build_room_greybox.cs` + `generate_room.py` + `qa/deploy_room.sh` + `paint_combat_v1.cs` | `~/worldos-session-notes/renders/PIPELINE_author_to_combat.png` (greybox→crypt→combat) + `m1_combat_carved14.png` | ✅ **system CLOSED** (quality = the deprioritized ≥8 lever) |
| **SCALE to a world — 4 distinct rooms @ ~7, across 3 breadth axes** | **MET + extended on 3 axes.** The textured-greybox lever (the ~7 standard) stamps out DISTINCT authored rooms, one-source authored-pathing-aligned, across: **(A) room TYPE/layout** — (1) crypt (pillars + sarcophagus, **7.07**), (2) cathedral nave (columns + apse altar + stained-glass, **6.97**), (3) throne hall (columned aisle + raised dais, ~7), (4) **tavern** (bar + tables + timber posts, ~7); **(B) MATERIAL** — stone (crypt/nave/throne) vs **WOOD** (tavern: `build_room_greybox.cs` switches to plank coursing + warm grain when `export_scene_grid.py` emits `material:wood` from the biome); **(C) TIME** — day/night (`generate_room.py --lighting day|night`, proven on the church). Same pipeline, distinct `scene_grid` per room; shared LoRA-bound cap (focal craft PoE2-grade, broad wall fields softer). | `qa/seed_gfx_{combat,church,bosshall,tavern}.py` + `build_room_greybox.cs` (stone/wood) + `generate_room.py` (`--lighting`) | `~/worldos-session-notes/renders/FOUR_ROOMS_world.png` + `tavern_wood_v1.png` + `DAYNIGHT_church.png` | ✅ **4 rooms @ ~7, 3 axes (type/material/time)** (≥8 = better LoRA, owner-deferred) |

## DEPRECATED (do NOT resume from these)
- `TavernTier1.unity` + `Captures/combat_0[1-4]_*.png` (06-23) — week-old tavern; floating dark actors,
  no rings/VFX/goblin. Visual-critic 2026-06-28: **L5=3.5 CRITICAL, L2=4.5/L1=5.5 HIGH (~overall 4-5)**.
  Superseded by the crypt 3D-actor pipeline.
- 8-facing **billboard** hero sprites (`Assets/painterly/sprites/hero/hero_*.png`) — the billboard approach
  (capped ~4/10 "pasted sticker"); superseded by the real 3D actor (the PoE2 pivot).

## Rebuild the current-best (deterministic)
```bash
# on the GEX44 box (gex44-unity-host skill); scripts live in this dir, deploy then:
~/.local/bin/unity-mcp code execute --no-safety-checks -f paint_3d_spike.cs
#   -> /home/unity/worldos-unity/Captures-Durable/m10_spike.png  (crypt + textured/lit/grounded 3D hero)
```

## ITERATION DISCIPLINE — an iteration is NOT done until ALL of these
1. **Persist:** the build script ends with `EditorSceneManager.SaveScene(...)` (not render-and-forget) AND
   is committed here in `extensions/renderers/unity/scripts/`.
2. **Capture + score:** durable PNG in `Captures-Durable/` + logged to `qa/scores_db.py` (surface=visual, milestone).
3. **Register:** update THIS file — add the new current-best row, mark the superseded one DEPRECATED.
4. **Save off-box:** commit to the WorldOS repo (version control) + periodic box tarball
   (`worldos-unity-SAVE-<date>.tgz`, excl. Library/Temp).
5. **On RESUME:** read THIS file FIRST. Never infer "current" from a recent capture PNG.

## Build-script inventory (`extensions/renderers/unity/scripts/`)
- **canonical:** `paint_3d_spike.cs` (crypt 3D-hero spike), `paint_backdrop_p0.cs` (camera-pinned plate),
  `ClosedLoopBuilder.cs` (combat scene builder), `CombatSurfaceDemo.cs` (engine-cell positioning + rings/shadow),
  `CombatBeatDriver.cs` (beat sequencer), `RTCapture.cs` (render capture), `SetupPainterlyScene.cs`.
- **deprecated/proof (billboard-era or one-off):** `IsoSpriteRenderer.cs`, `IsoHeroRender.cs`, `AnimProof.cs`,
  `DungeonPathingProof.cs`, `PathingTestDriver.cs`, `TavernTier1Builder.cs`, `CharsV3Import.cs`, `AddWalkState.cs`.
