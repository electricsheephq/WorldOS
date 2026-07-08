# Decision: closing the actor-in-painting cohesion seam (the PoE2 gap)

**Date:** 2026-07-09 · **Owner directive:** apply the scarce frontier budget to the hardest problem —
"aligning Pillars of Eternity's visuals with the backend… animations, the system, integrating everything
cohesively." · **Status:** DECIDED — probe ladder executing; runtime port staged behind the probe verdict.

## Context

Frames from the shipped macOS player read as "3D model pasted on a painting" even after scale, grounding,
materials, collision and walk-clip fixes (#1418–#1451). Three mismatches stack:
- **Style:** smooth PBR-ish Meshy meshes vs the plate's visible brushwork.
- **Lighting:** plates carry warm-core/cool-periphery chiaroscuro; actors get a fixed studio rig
  (KeyLight (1,.73,.44)/FillLight (.36,.44,.64) + flat ambient, baked per capture by paint_combat_v1.cs:95-104)
  that knows nothing about the plate.
- **Integration:** a fake radial blob AO quad; actor shadow-casters have **no receiving floor** to land on;
  zero post-processing (no LUT/grade/vignette anywhere in the runtime).

## The ground-truthed finding (2026-07-09 evidence sweep, 4 agents + design pass)

**The cohesion tech already exists in this codebase — offline only.** The ClosedLoopBuilder capture lane
(graphics fork, 2026-06-23, r2–r11 iteration cycle) built and TUNED:
- `WorldOS/PainterlyActor` shader — scene-driven painterly relight (plate-key/ambient colors, key-dir,
  rim restraint) + real-time paint pass (Kuwahara region-flattening, value posterize, brush grain,
  edge-feather, palette-snap, max-luma clamp, scene-color contamination, atmospheric depth wash) —
  per-fragment on the live skinned mesh, so it survives Mecanim animation (composes with the #1451
  PlayableGraph walk path).
- `WorldOS/ContactShadow` + BuildContactShadow — directional cast-shadow ellipses thrown AWAY from the
  plate's key light, near-end fused to the feet (ClosedLoopBuilder.cs:1218-1354), replacing the radial blob.
- A plate-matched light rig (SetupLighting, :1357-1374) with the key-dir-sign recipe (measure displayed
  warmth L-vs-R after import settles → set _KeyDir.x sign).

Every parameter carries the critic finding it fixed ("the L4 maquette-ceiling breaker", "the #1 pasted-sprite
tell"). **None of it is consumed by CombatSurfaceClient (the shipped player), which uses Standard shader +
blob AO + whatever lights the editor bake left in the scene.** The seam is a PORTING/WIRING gap, not an R&D
gap — this is why a month of effort didn't close it: the tech kept being validated in the offline lane while
the player stayed a bare consumer. PainterlyActor/PainterlyBackdrop/ContactShadow lived ONLY on the GEX44 box
until this PR (now rescued into extensions/renderers/unity/shaders/).

## Options considered

- **A. Port the ClosedLoopBuilder stack into the live player** — biggest single delta; highest port surface.
- **B. Plate-sampled per-scene light rig** (key/fill/ambient from the plate's warm-core/cool-periphery medians)
  — kills the lighting tell alone; 1-1.5 sessions.
- **C. Full-frame OnRenderImage post** (plate-derived LUT + vignette; optional subtle painterly filter) —
  palette unification; built-in-RP blit, no URP Volume.
- **D. Shadow-catcher/directional contact shadows matched to plate key** — kills the grounding tell.
- **E. AI-stylized actor textures (Scenario img2img, style-ref from plate)** — kills the style tell at the
  texture source; offline, compounds with everything.

## Decision

**Probe first, port second.** One box session runs the cumulative ladder **baseline → +B → +D → +A′
(PainterlyActor with the CL-tuned params, plate-sampled colors) → +C (image-side LUT for the probe)** on the
canonical combat scene, one capture per rung, scored in ONE panel against the house anchor + a verified-clean
control. Then the winning rung-set is ported into CombatSurfaceClient as the runtime cohesion stack, params
sourced per-plate (sampled at spawn) with the CL manifest values as defaults. E runs as a parallel offline
track (one actor first).

KILL condition (from the design pass): if baseline→B+D misses the panel parity delta, the plate-derived-
lighting premise is dead and A′/E won't rescue it — stop and redesign.

## Counter-arguments considered

- *"Lead with A — it's the proven look."* The CL captures were scored on the ATELIER scene with hand-tuned
  manifests; the canonical combat scene + arbitrary plates need the parameter-sourcing to generalize. The
  ladder isolates how much each layer buys before committing the biggest port.
- *"Full-frame Kuwahara will smear the plate."* C is scoped to LUT+vignette first; the painterly filter is
  actor-side (A′ per-fragment) where it belongs.
- *"Naive plate sampling washes to mud."* Median-of-region sampling (bright-quartile core vs dim periphery),
  never mean; the CL key-dir-sign recipe is retained.

## Risks accepted

- PainterlyActor is Transparent-queue (2-pass depth-prime) — overdraw cost on the shipped player must be
  profiled on Apple Silicon before the port ships as default-on.
- Per-plate light metadata doesn't exist yet; sampling at spawn adds a one-time CPU cost per plate load.
- CL params were tuned on interiors; outdoor plates (camp v2) may need a second tuning round via the panel.

## Reversibility

Every rung is additive and behind scene-mutation or material swap — the runtime port ships as a config-gated
path (default read from the plate manifest; `cohesion:off` restores today's Standard+blob rendering exactly).

## Verification

Panel-scored ladder frames (5 scorers, house anchor crypt_dense_v1 + verified-clean control per the #1452
cadence guard), evidence committed under qa/evidence/cohesion-probe/, scores_db rows per rung, verdict folded
back into this record.
