# W6 "The Living Stage" — architecture (epic #1457)

**Date:** 2026-07-10 · **Trigger:** owner playtest #3 — "a 2D image with 3D models walking on it; nothing
dynamic; collision wrong (walking over logs); what matters most is how actors interact with the sets; the
evals should have caught this." · **Evidence:** 4-agent ground-truth sweep (journal wf_3cbd3bf9-87d);
full digests preserved there — the file:line citations below come from it.

## The reframe the evidence forced

W6 was scoped as "keep the greybox alive under the paint." The sweep showed most of the stage layer
ALREADY EXISTS as engine-authored data that the shipped player ignores:

1. **Collision truth exists; the wire drops it.** `scene_grid.impassable_cells()`
   (servers/engine/scene_grid.py:846-917) correctly derives blocked cells from geometry + prop footprints,
   and `rest_blocked_cells()` (server.py:4631-4693) computes the exact rest-mode set — but
   `build_combat_surface`'s `impassable` field (viewer/server.py:3763) has **no rest-mode branch**, and
   CombatSurfaceClient has **no rest-mode movement path at all** (HandleClick always POSTs the combat verb).
   The owner's walking-over-logs is THIS, not bad data.
2. **Occlusion exists; the runtime never reads it.** The engine ships `occluders` ({cells,band}) on
   /combat-surface (viewer/server.py:3490-3522); paint_combat_v1.cs:487-533 builds invisible
   WorldOS/OccluderDepth boxes from it — but only as a one-time EDITOR bake saved into the scene. The
   runtime client never consumes the field, so occlusion is frozen at last-editor-save and dies on any
   room swap. (Bonus real bug: IntegrationBuilder.cs:283 + ClosedLoopBuilder.cs:683 Shader.Find
   "WorldOS/OccluderDepthOnly" — wrong name, never resolves, falls back to VISIBLE black boxes.)
3. **The delivery channel exists.** registry.json + StreamingAssets packaging
   (BuildMacOSPlayer.EnsurePackaged) + the client's Bundle()/LoadRegistry loaders + an unconditional
   per-frame Update block = everything a per-plate stage manifest and animated layers need.
4. **The greybox is transient BY DESIGN and that is fine** — room_geometry.json is exported FROM the
   engine's scene_grid (qa/export_scene_grid.py), i.e. the geometry that matters (cells, prop footprints,
   bands) is already the engine's single source of truth. We do NOT need to persist Unity scenes; we need
   the runtime to consume the engine's geometry. (Camp's greybox was never a 3D scene at all —
   qa/greybox_render_headless.py.)

## The workstreams (ordered; each independently shippable + additive)

- **W6.1 Runtime occluder proxies** (renderer): CombatSurfaceClient reads the surface `occluders` field →
  spawns/rebuilds invisible OccluderDepth boxes (port of paint_combat_v1.cs:487-533 into a runtime
  builder), invalidating on location change. Includes the OccluderDepthOnly shader-name fix and a
  PainterlyActor-vs-occluder ZTest check (transparent 2-pass depth-prime interplay is unverified).
  KILLS: actors drawing over set pieces they stand behind.
- **W6.2 Rest-mode collision + movement** (engine/viewer + renderer): rest-mode branch in
  build_combat_surface surfacing rest_blocked_cells() (mirror the _derive_grid_from_scene auto-wiring
  pattern, server.py:4135-4168); client rest-mode click → walk_to wiring with the same pre-validation the
  combat path got in #1446. KILLS: walking over logs.
- **W6.3 Paint-drift eval gates** (QA): (a) durable per-room logical_cell→screen_bbox manifest (versioned,
  not incident folders); (b) a deterministic repaint gate — any canonical_plate replacement re-validates
  painted prop positions against the authored cells via the #1396 camera-reprojection recipe; (c) rest
  fixture pinned red-first like qa/test_seed_gfx_camp.py pins combat. KILLS: the eval blindness the owner
  called out.
- **W6.4 Stage manifest + animated layers** (renderer, default-off): per-plate stage.json in
  StreamingAssets ({fire_anchors, light_flicker, vfx}), a Perlin light-flicker component on the fire key,
  a glow quad at the hearth anchor (procedural fallback; Hovl VFX only if verified on the box). KILLS:
  "nothing dynamic".
- **W6.5 Silhouette-fit occluders** (later): AABB-per-cell-group is crude for tall/irregular props;
  refine only after W6.1 lands and the panel shows edge artifacts.

## Invariants
Engine stays SOLE WRITER (all stage data is engine-authored or manifest-static); every piece additive +
default-off where it touches the shipped render path; text tier untouched; evals gate promotion (W6.3
gates ship before W6.1/W6.2 close).

## AMENDMENT (2026-07-10, deep-research verdict: AMEND ~80% — full evidence: wf_46db77c8-a30)

Owner asked "are we overlooking existing engines/assets/AI methods?" Research (5 angles + adversarial
judge, source-verified) says YES — two dormant, already-proven in-repo capabilities are the highest-
leverage fixes, and the current W6 headline aims at the wrong axis of the 3.2-vs-8.7 panel gap:

1. **W6.0 UNIFIED LIGHT STAGE (new LEAD workstream).** The backdrop quad is flat Unlit/Texture while
   actors get a separate hand-tuned rig — plate and actors CANNOT be lit coherently; that mismatch IS
   the cohesion failure. Fix: wire the dormant `WOSRelight.shader` (plate-GI relight, PR #1236, zero
   refs) onto the backdrop quad, fed by the already-captured `room_greybox_{depth,normal}.png`
   sidecars, with ONE shared light rig driving plate AND actors. This is the exact Obsidian/PoE bake
   pipeline (4-pass background: albedo/depth/normal + 2-light rig + bg-color-sample pseudo-GI —
   eternity.obsidian.net update #79; Disco Elysium GDC 2020 variant). **Metal/built-in-RP spike on the
   GEX44 build FIRST** (Metal shader gaps are a documented risk). Greybox depth → SV_DEPTH per-pixel
   occlusion later obsoletes W6.5's silhouette work; AABB proxies stay as the collision/nav proxy.
2. **W6.3b DRIFT PREVENTION AT SOURCE.** generate_room.py imports CONTROLNET_PATH but never calls it —
   plates are UNCONDITIONED img2img, which is where paint-vs-grid drift is born. Add a --controlnet
   path (scenario_gen._cmd_controlnet, proven 2026-06-22) with the greybox as depth/canny control:
   locks paint to geometry, makes the depth/normal sidecars valid for W6.0, and preempts most
   after-the-fact gating. W6.3's gates remain as the regression net (SAM2 segmentation usable as a
   drift-DETECTION signal only — collision stays engine-authored, invariant upheld).
3. **Resequencing:** W6.4 flicker DEPENDS on W6.0 (flickering actors over a static-bright flat plate
   increases incoherence — one shared key color drives both). Beautify 3 (owned, unwired) = final
   tone/grade unification AFTER lighting+texture cohesion, never the stylization layer (screen-space
   stylization = confirmed dead end, twice). Offline AI texture re-bake of actor albedos (E-track,
   probing now) is the complementary cohesion lever — zero runtime cost, animation-safe.
4. **Status corrections:** W6.1 SHIPPED (#1460 → PR #1464, runtime RebuildOccluders + the
   OccluderDepthOnly shader-name fix). W6.2 (rest-mode collision) is the one open correctness item and
   is unaffected by this amendment.
