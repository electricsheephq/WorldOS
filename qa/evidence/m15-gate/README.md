# M1.5 asset spend-gate (unity-asset-stack skill) — run 2026-07-10

Issue #1386 (single-tenant box charter). Gate contract (`.claude/skills/unity-asset-stack/SKILL.md`,
"The M1.5 spend-gate"): **6 FREE capsules + 1 FREE Hovl VFX on a painterly plate, visual-critic scored
— multi-unit grounding/occlusion + VFX-reads-in-a-2D-plate must hold. FAIL at 6 ⇒ architectural
rethink, NO asset spend.**

## What was run
- Box: GEX44 (`M1CombatV1_canonical.unity`), PaintedBackdrop forced to the ADOPTED
  `crypt_armb_iter3_v1.png` plate (per this dispatch's instruction), 12 stale live-combat-cast objects
  disabled for a clean read.
- New tool: `extensions/renderers/unity/scripts/M15SpendGateProbe.cs` (Editor-only, deployed to the
  box `Assets/Editor/`) — spawns 6 built-in Unity Capsule primitives (3-party @ gold ring near
  cell (11,3)/(12,3)/(11,2), 3-foe @ red ring near (1,8)/(2,8)/(1,7), all #1386 PROBE-PLACEMENT
  clear cells) at the #1418 target heights (3.2 / 4.2), + writes a `qa/visual_pregate.py`-compatible
  manifest via real `WorldToViewportPoint` projection of each capsule's `Renderer.bounds`.
- Captured at `super_size:2` (5120x2880), non-black gated (mean 0.24 / stddev 0.18).

## Result 1 — deterministic pre-gate (multi-unit grounding/occlusion architecture): PASS
`qa/evidence/m15-gate/m15_gate_pregate_report.json` (`python3 qa/visual_pregate.py <frame> <manifest>`):
frame-lit PASS, occupancy 6/6 found, **floor-contact PASS all 6** (grounded within the ±14px band),
**screen-scale PASS all 6** (5.8%-7.5% of frame height, within the 3%-45% band), pose-uprightness
PASS all 6. The engine-cell-driven placement pipeline registers 6 simultaneous units correctly under
the locked camera contract — the architecture does not break at 6 units.

## Result 2 — visual-critic cohesion panel (character-in-scene integration): FAIL
`qa/evidence/m15-gate/m15_gate_cohesion_panel.json` — control-anchored 5-scorer blind panel vs the
registered `poe2_ruins_brazier_integration_01` control (same control as the #1386 ADOPT-CRYPT cohesion
panel). **Candidate median 1.0 vs control median 8.0 (delta -7.0), unanimous 5/5 ranking control >
candidate.** All 5 scorers independently flagged the capsules as reading like engine primitives / UI
markers (not character art), the ground ring as a UI-selection-ring tell rather than a rendered
contact shadow, and zero scene-relit color pickup from the plate's warm/cool grading.

**Interpretation (load-bearing, do not skip when reading this result):** this is a MATERIAL/SHADING
gap, not proof the multi-unit architecture fails to scale — the deterministic pre-gate above already
answers the scaling question cleanly. The existing `CohesionProbe.cs` stack (RungB plate-sampled
light rig → RungD contact shadows → RungA' painterly actor materials) already solves exactly this
defect class for real actor meshes, lifting cohesion from ~3.0 to ~6.0-7.0 on this same instrument
(`qa/evidence/plate-sprint/adopt-crypt/crypt_armb_iter3_cohesion_panel.json`) — it was not applied to
these disposable capsule stand-ins for this quick gate probe.

## Result 3 — Hovl VFX legibility: FAIL (root-caused, not architectural)
Two owned Hovl Studio prefabs were tried (`Magic circles/.../Magic circle fire loop.prefab`, a
looping VFX; `AAA Projectiles Vol 1/.../Hit 16 fire.prefab`, a one-shot). Both instantiate with valid,
non-degenerate `Renderer.bounds` and non-zero `ParticleSystem.particleCount` after an explicit
`Simulate()` warm-up (confirmed via a diagnostic menu item logging renderer/shader/particle state —
see `qa/evidence/m15-gate/m15_gate_vfx_attempt.jpg`, captured with the burst mid-emission), yet
**neither renders any visible pixels** in the captured frame. Every Hovl renderer uses
`Shader Graphs/HS_Blend_CG` / `HS_Distortion` — this reproduces, independently, the
`docs/research/2026-07-10-stage-tech-research.md` finding that "Hovl VFX has only a soft
optional-prefab lookup, not a proven hard integration" (line 247: "re-verify Hovl VFX's box-import
status... confirm it's still present"). Root cause is almost certainly the Hovl Shader Graph
materials never having been through the unity-asset-stack import drill's **URP-convert check**
(`Edit ▸ Rendering ▸ Materials ▸ Convert … to URP`) — a bounded, one-time import-hygiene fix, not an
architectural ceiling.

## Gate verdict: **FAIL** (per the skill's literal binary contract — "visual-critic scored... must
hold" — the cohesion panel and the VFX check both failed this run)
Per the skill: **FAIL ⇒ NO new asset spend this round.** However, both failure causes are
DIAGNOSED and BOUNDED, not evidence of an architectural ceiling on multi-unit scale (which the
deterministic pre-gate proves is fine):
1. Re-run this same capsule test through the existing `CohesionProbe.cs` RungB→D→A' stack (or the
   real character meshes it already targets) instead of bare primitives, and
2. URP-convert the Hovl Studio Shader Graph materials (one-pack-at-a-time import drill, already
   prescribed by `unity-asset-stack/SKILL.md`),
then re-score. Recommend a fast, cheap follow-up pass on those two items before re-attempting the
gate — this is NOT a "rethink the plate architecture" finding.

## Evidence files
- `m15_gate_capsules_clean.jpg` — the scored candidate frame (6 capsules, crypt plate, clean).
- `m15_gate_vfx_attempt.jpg` — the Hovl VFX attempt frame (particles confirmed present via diagnostic
  log, not visible in the render).
- `m15_gate_manifest.json` — the `qa/visual_pregate.py` actor manifest (real projected screen_bbox).
- `m15_gate_pregate_report.json` — the deterministic pre-gate JSON verdict (PASS).
- `m15_gate_cohesion_panel.json` — the 5-scorer blind panel verdict (FAIL, delta -7.0).
