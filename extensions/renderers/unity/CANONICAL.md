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
| Room plate | `Assets/painterly/backdrops/crypt_dense_v1.png` (density crypt via the **Gemini polish+populate pass** on a painterly base — crisp de-cloned columns + carved knight effigy + lived-in clutter; **~6.2** on an anchored 5-scorer harsh panel = best STATIC crypt to date, +0.4 over the pre-polish baseline; still <8 — see the ≥8 row + `room_recipes.json:gemini_polish_populate_pass_2026_07_01`) | `generate_room.py` + the polish+populate pass (`extensions/renderers/shared/room_recipes.json`) | `~/worldos-session-notes/renders/gbuf/combat_on_density_crypt.png` (VALIDATED in LIVE combat 2026-07-02) | ✅ canonical (crypt_firelit_v2 "7.4" was inflated vs the harsh panel; crypt_firelit_v2 / crypt_pinned_v1 = prior) |
| Character (hero) | `Assets/painterly/models/hero.fbx` + `hero_albedo.png` (2048²) on a Standard/PainterlyActor mat | `paint_3d_spike.cs` | `Captures-Durable/m10_spike.png` — **Gate-1 PASSED** (textured, lit, grounded 3D actor) | ✅ canonical 3D actor |
| Animator | `HeroAnim_CL.controller` + 9-clip moveset from `meshy_gen.py --moveset` | `ClosedLoopBuilder.cs` + `unity-editor-patterns-m1-combat.md` | — | ⚠ WIP |
| Combat SCENE (LIVE, multi-actor) | **BUILT (P2)** — engine-driven hero+goblin on `crypt_firelit_v2`; actors placed at LIVE `/combat-surface` cells, move routes around painted props (M-B), attack → impact VFX + floating damage, NPC auto-counterattacks | `paint_combat_v1.cs` (LIVE; reads `/combat-surface`) + seed `qa/seed_gfx_combat.py` + driver `qa/drive_gfx_combat.py` | `~/worldos-session-notes/renders/m1_combat_02_attack.png` (full round: move + hit-for-7) | ✅ **P2 DONE** |
| Playable IN-APP (M-D) | **DONE** — the OpenWorlds combat backdrop serves the box-rendered 3D-on-2D frame (`combatFrameScope` → `/image` → `<Img>`); move/attack resolve via `/move`; 2D `CombatGridBoard` = input/fallback | `build_combat_surface` (`combatFrameScope`) + `screen-combat.jsx` + `drive_gfx_combat.py` (delivers to `images/<safe-scope>/`) | `GET /image?scope=…` → 200 image/png 1920×1097 (verified) | ✅ **M-D DONE** |
| Multi-room + registry (M-E) | **3 distinct rooms PLAYABLE** (crypt + tavern + church) from ONE pipeline: `generate_room.py --room <type>` → `qa/deploy_room.sh` writes `_active_combat.txt` → the room-agnostic renderer reads the active plate. Actors resolve via the **asset registry** (`registry.json` by slot → exact OR default-template-on-miss, read inline via MiniJson) — no renderer edit per room/actor | `paint_combat_v1.cs` (plate + registry params) + `generate_room.py` + `qa/deploy_room.sh` | `~/worldos-session-notes/renders/m1_combat_{tavern,church}.png` + `rooms_3playable.png` | ✅ **M-E core** (≥8 quality + demo cast + day/night = remaining) |
| **Authored-pathing room-gen (M-E)** | **PROVEN** — one authored `scene_grid` is the SINGLE source for BOTH the painted room AND the pathing: export geometry → render a camera-pinned **greybox** (floor+walls+a box per prop at the contract camera) → **img2img** the greybox into a painterly room → the painted props land on the SAME cells the combat `impassable_cells()` derives from the SAME scene_grid. Interior obstacles stay on-cell; the LoRA only invents perimeter (already-impassable) decoration. `strength` = the structure-fidelity ↔ paint-quality knob. Answers the owner's "how do we generate a room / how does pathing work." | `qa/export_scene_grid.py` + `build_room_greybox.cs` + `generate_room.py --base-plate` + `qa/gen_room_from_scene_grid.sh` (one command) — see `docs/roadmap/ROOM-GENERATION-AND-PATHING.md` | `~/worldos-session-notes/renders/greybox_to_painted_ALIGNED.png` + `crypt_from_greybox_v1.png` | ✅ **pipeline PROVEN** |
| **Textured-greybox (M-A ≥8 push) — driven to ~7.07, LoRA-bound** | A SCORED loop (qa/scores_db, harsh PoE2 panel) drove the carved-greybox to its empirical ceiling: flat gray greybox **4.75** → procedural stone albedo+normal (textured base) **5.75** → LIT walls (cool fill, no crush) **6.5** → GEOMETRIC masonry coursing on the wall faces **7.07** (denser coursing = no gain). The 7.07 is reached at **LOW img2img strength (0.5)** so the camera-pin holds → props stay on the authored-pathing cells (the 7.4 firelit plate had hand-tuned obstacles; this is authored-pathing-aligned AND repeatable from any scene_grid). **≥8 is LoRA-BOUND** (confirmed `room_recipes.json:textured_greybox_result`): the LoRA paints focal craft (floor flagstones, niches, columns, figural relief) at PoE2-grade but smooths the broad wall FIELDS into value-pass regardless of geometry → ≥8 needs a crisper architectural/stone LoRA, not more geometry. | `build_room_greybox.cs` (procedural stone texture + lit walls + geometric coursing) | `~/worldos-session-notes/renders/crypt14_walledcourse_v1.png` (7.07) + `m1_combat_textured.png` (live combat on it) + `LEVER_progression_6up.png` | ⚠ **≥8 STILL OPEN 2026-07-01.** A "Seedream 4.5 unblocks ≥8" claim was posted then REFUTED: the rigorous 3-scorer panel scored Seedream crypt **4.0**, Seedream hell **5.0**, and the OLD "7.4/7.07" LoRA crypt **4.7** on the SAME panel — all below-bar, Seedream NOT better than the LoRA pipeline (see `room_recipes.json:seedream_v2_2026_07_01`). The "7.07/7.4" numbers were themselves inflated vs this harsh panel. **≥8 is not achieved by any current pipeline; do NOT swap to Seedream.** Reproduced levers to close the gap: WASHOUT/flat-value, REPETITION (cloned tiles/props), SPARSE composition. SCORE every candidate on the ≥2-run panel before claiming a bar. **★ UPDATE 2026-07-02 (5 anchored harsh panels this session):** the img2img/editor/grade/relight ceiling = **~6.0–6.5**, now confirmed across the whole project. Single panels are NOISY (±1.2; the same image scored 7.0 and 5.83) → always use a FIXED in-panel anchor + judge by within-panel delta (`feedback_visual_panel_scoring_variance`). Best repeatable lever found = the **Gemini polish+populate pass** (crisp focal carving + DE-CLONE columns + paint flat greybox caps + ADD pathing-safe edge clutter + deepen voids) → the adopted `crypt_dense_v1.png` (6.2, robust +0.4 anchored; composition lens 4.5→6.2). In-engine relight of a FLAT diffuse = flatter/worse for a STATIC plate (its value is DYNAMIC torch/spell lighting, not the static score). The clean char-free architectural LoRA bet is now **RESOLVED — NOT ADOPTED** (2026-07-02): `worldos-poe2-clean-env-v4` (model_ng6MSaWtyvwKbZyDBMtzv7tA, 26 curated wiki plates trained) was evaluated under the CALIBRATION-CONTROL PROTOCOL — 3 blind 5-scorer panels with disguised real-art controls, incl. a same-greybox/same-prompt/same-strength HEAD-TO-HEAD vs the incumbent painterly LoRA (model_MB22WaRCBLtfhi5R2CRpHoEL): the incumbent beat v4 in BOTH head-to-head panels (unanimous rep rankings; +0.3…+1.5 same-panel median). v4 also lost to crypt_dense_v1 (−1.5 same-panel) and its albedo-painter lane failed (2.8). **Keep the incumbent LoRA as the generate_room default; do NOT re-bet on v4.** v4's one objective win — it natively generates the PoE staging-law value structure (t2i 80–86% near-black / median 0; first LoRA to do so) — keep it ONLY as a dark-staging txt2img ideation tool. Untested residue: the upgraded staging-law PROMPT was never A/B'd vs the recipe prompt (same LoRA both sides) — that's the remaining cheap experiment. Evidence: ~/worldos-session-notes/2026-07-02-lora-v4-eval/. **★★ RECALIBRATION 2026-07-02 (positive control — VISION.md "GATE RECALIBRATED" + visual-critic CALIBRATION-CONTROL PROTOCOL): every absolute number in this table is INSTRUMENT-RELATIVE — blind on the same instrument, REAL shipped PoE plates scored 3.0-4.6 and real BG2EE 4.6-5.6. On the clean instrument the canonical `crypt_dense_v1` scores **6.72/median-7** and BEATS the real-art controls → the recalibrated craft bar is MET; remaining named craft work = brushstroke looseness, wall repetition, relief crispness (the v4 LoRA's targets). The Atelier 3D-composite lane (buffer render + albedo-only paint-over + staging law) is PARKED with tools banked (#1241/#1244) pending sculpted-density content; the STAGING LAW (66-80% near-black, 2-4% lit, tiny pools — measured from real plates) applies to ALL plate generation NOW.** The layered 3-pass pipeline (macro → detail/populate → staging-last) reached 7.6 median on a full multi-room plate (−0.8 from the single-room anchor, 2-panel evidence 2026-07-02) and is banked in `room_recipes.json:layered_pipeline_2026_07_02` + `generate_room.py --layered`. |
| **★ FULL pipeline CLOSED (author→amazing→pathing→play)** | **PROVEN end-to-end on a GENERATED room.** The seed authors a contract-matching **14×11** crypt `scene_grid` (props == combat OBSTACLES) → carved greybox → img2img **carved-stone crypt** (≥8) → deploy → **LIVE 3D combat**: Aldric + Goblin as grounded, scene-lit 3D actors with party/foe rings standing on the painted floor, between the painted pillars that sit at the pathing obstacle cells **by construction** (one source: the scene_grid drives the painted room AND the pathing). Answers the owner's **SYSTEM** question — *author a room → painted room + aligned pathing → play on it* — repeatably (`qa/gen_room_from_scene_grid.sh`). (Backdrop QUALITY is the separate, deprioritized ≥8 row above — current ~5-6, not yet "amazing".) | `qa/seed_gfx_combat.py` (authors 14×11 grid) + `build_room_greybox.cs` + `generate_room.py` + `qa/deploy_room.sh` + `paint_combat_v1.cs` | `~/worldos-session-notes/renders/PIPELINE_author_to_combat.png` (greybox→crypt→combat) + `m1_combat_carved14.png` | ✅ **system CLOSED** (quality = the deprioritized ≥8 lever) |
| **SCALE to a world — 4 distinct rooms @ ~7, across 3 breadth axes** | **MET + extended on 3 axes.** The textured-greybox lever (the ~7 standard) stamps out DISTINCT authored rooms, one-source authored-pathing-aligned, across: **(A) room TYPE/layout** — (1) crypt (pillars + sarcophagus, **7.07**), (2) cathedral nave (columns + apse altar + stained-glass, **6.97**), (3) throne hall (columned aisle + raised dais, ~7), (4) **tavern** (bar + tables + timber posts, ~7); **(B) MATERIAL** — stone (crypt/nave/throne) vs **WOOD** (tavern: `build_room_greybox.cs` switches to plank coursing + warm grain when `export_scene_grid.py` emits `material:wood` from the biome); **(C) TIME** — day/night (`generate_room.py --lighting day|night`, proven on the church). Same pipeline, distinct `scene_grid` per room; shared LoRA-bound cap (focal craft PoE2-grade, broad wall fields softer). | `qa/seed_gfx_{combat,church,bosshall,tavern}.py` + `build_room_greybox.cs` (stone/wood) + `generate_room.py` (`--lighting`) | `~/worldos-session-notes/renders/FOUR_ROOMS_world.png` + `tavern_wood_v1.png` + `DAYNIGHT_church.png` | ✅ **4 rooms @ ~7, 3 axes (type/material/time)** (≥8 = better LoRA, owner-deferred) |
| **Tavern + bosshall plates ADOPTED (2-panel ratified) — DEPLOYED + live-combat VALIDATED** | The layered 3-pass pipeline's `tavern`/`bosshall` staging candidates (`~/worldos-session-notes/2026-07-03-g4-retry/{tavern,bosshall}_gen/room_*_pass3_staging.png`) were 2-panel ADOPT-ratified and deployed as the box's active plates via `qa/deploy_room.sh` (church-naming convention: `tavern_layered_v1.png` / `bosshall_layered_v1.png`), then force-reimported (`AssetDatabase.ImportAsset(..., ForceUpdate)`) and validated in LIVE engine-driven combat (`paint_combat_v1.cs`, 2 actors + damage VFX, non-black gate PASS both). STRUCTURAL IDENTITY confirmed by direct visual read (not just the luma gate): tavern shows a hearth with a roasting pig on a spit, a bar with hanging lanterns + shelved bottles, and multiple floor barrels; bosshall shows a raised throne dais (red-cushioned throne + stairs + winged-angel statue) with hanging red/white/green griffin banners and a brazier/flame bowl at the dais — both unambiguous vs a generic dungeon read. | `qa/deploy_room.sh` (plate swap + `_active_combat.txt`) + `paint_combat_v1.cs` (live render) | `~/worldos-session-notes/renders/tavern_live_combat_v1.png` (mean 0.146/stddev 0.140) + `~/worldos-session-notes/renders/bosshall_live_combat_v1.png` (mean 0.136/stddev 0.151) | ✅ **both plates DEPLOYED + live-combat VALIDATED 2026-07-02** (`_active_combat.txt` restored to `crypt_dense_v1.png` after; the two plate files themselves stay deployed on box as the deliverable) |
| **Wave-1 monster variety — 3 actors IMPORTED to box (not yet registry-wired)** | Monster-variety wave 1 (#1290): **skeleton_archer** + **cultist** — Meshy humanoid, full **9-clip Generic moveset** (idle/walk/run/attack/cast/block/dodge/hit/death; import as `animationType=Generic`, NOT Humanoid — Meshy bone names silently drop clips under Humanoid import) — and **ghoul** — Tripo3D text-to-model → rig-check (auto-detected `biped`) → rig (mixamo spec) → retarget, but only **idle+walk** (Tripo retarget is one-preset-per-call and creature rigs support far fewer preset names than bipeds; combat clips deferred). All three physically present on box at `Assets/chars_v2/{skeleton_archer,cultist,ghoul}/` (rigged.fbx + anim_*.fbx + albedo.jpg) with a matching `Captures-Durable/{name}_grounded_v1.png` per actor. Grounding uses the SAME `paint_combat_v1.cs` spawn logic as the hero/goblin cast — `SkinnedMeshRenderer.BakeMesh` → world-space bounds (NOT the inflated `Renderer.bounds` AABB, whose `min.y` sits below the true feet and floats the actor) → snap `min.y` to 0 + center X/Z on the cell. Scale-multiply + stand-up `Euler(-90, yaw, 0)` applies at import time (FBX memory guidance). **NOT yet done:** none of the three are wired into `registry.json` (`data/asset-registry/registry.json`) under a `monster.<slug>` slot, so `paint_combat_v1.cs`'s asset-registry resolver still falls through to the goblin default-template for any token using these names. | Generated via `meshy_gen.py`/`tripo_gen.py` (asset-gen); imported to box `Assets/chars_v2/`; grounding logic shared with `paint_combat_v1.cs`'s `spawn` func | `Captures-Durable/skeleton_archer_grounded_v1.png`, `cultist_grounded_v1.png`, `ghoul_grounded_v1.png` (box) — local staging copies + full gen manifest at `~/worldos-session-notes/actors-wave1/` (`manifest.json`) | ⚠ **assets IMPORTED, registry-wiring OPEN** — cultist is also the purpose-built #1282 bright-albedo (`light_tint`) probe body; skeleton_archer's prompt read as armored hooded rogue rather than a literal bare-bone skeleton (acceptable as delivered, flag if #1290 wants a literal read) |

## Action-Replay ANIMATED reel (#1303 — /events → motion; renderer-only, sibling driver)
`paint_combat_replay_v1.cs` (sibling to `paint_combat_v1.cs`) consumes the engine **Action-Replay
envelope** from `/events` (`{seq, actor_fk, verb, target_fk, result, anim_hint}`, `docs/roadmap/
contracts/action-replay-envelope.md`), sorts by `seq`, and plays each beat as an ANIMATED presentation
beat on the same registry-resolved actors, capturing a durable multi-frame REEL
(`Captures-Durable/replay_*.png`). **verb→motion:** attack→lunge toward target + VFX at struck cell;
move_to_zone→DOTween-style glide along engine `lastPath`; damage/condition→knockback+tilt flinch + G1
"-N" number + HP-bar drop (off `result.hp_after`/`hpMax`); death→topple+squish+fade; actors face
their heading. Pure read-only projection (engine=sole writer); VFX default-on-miss from the registry
`fx_default_slash` slot via an ADDITIVE shader; captures gate non-black (45 frames, mean ~0.38/stddev
~0.18). **Animation strategy = TRANSFORM MOTION on the clean upright bind pose, NOT live-clip posing:**
the wave-1 Meshy/Generic rigs are Y-up and driving a live skinned Animator in the editor's synchronous
multi-actor capture DESYNCS the GPU skin from the CPU bones (BakeMesh reads upright, but a
transform-mutated skinned actor renders its prone bind pose). `build_combat_animator.cs` still builds
`Assets/Animations/CombatActor.controller` (idle/attack/cast/block/dodge/hit/death) as the clip lane for
a future Play-mode / bake-to-static driver. Validated on a REAL engine attack+move sequence
(camp_gfxdemo01: move→attack(hit-for-9)→counter→miss). Evidence:
`~/worldos-session-notes/felt-frames/1303-replay/EVID_replay_*.png`. ⚠ RESIDUAL: the skinned-render
artifact (a transform-mutated skinned actor renders prone in the synchronous capture despite upright
transforms) — tracked as the #1303 flag for a Play-mode/bake follow-up.

## Actor-integration levers (#1280 — FELT gap, param-gated, default-off)
`paint_combat_v1.cs` reads an OPTIONAL `Assets/painterly/backdrops/_actor_integration.json`
(`shadow_scale`/`shadow_intensity`/`shadow_softness`/`core_shadow`/`core_scale` for stronger plate-blended
contact shadows, `light_tint`/`warmth` to tint actor mats toward the plate's warm firelight, `pose_yaw`/`pose_time`
for pose variety); an absent file leaves every value at today's byte-identical baseline.

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
