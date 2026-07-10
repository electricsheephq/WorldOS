# M1.5 asset spend-gate — RE-RUN (#1503 re-verdict) — 2026-07-11

Lane HOVL-URP (branch `fix/hovl-urp-render`, off main incl. #1507/#1510). Re-runs the two
BOUNDED failures the #1503 gate flagged, on the GEX44 box (`M1CombatV1_canonical`, PaintedBackdrop
forced to the ADOPTED `crypt_armb_iter3_v1.png`). Tooling: additive `M15SpendGateProbe.cs` menu items
(6 real-mesh painterly actors, 2-Hovl-VFX populate, pipeline diag, pipeline-aware Hovl fix).

## Root cause — the Hovl "zero visible pixels" bug (DECISIVELY resolved)
- The box Unity project runs the **Built-in Render Pipeline**, not URP. Runtime-confirmed via the new
  `diag - Pipeline + Hovl shader support` menu item: `currentRenderPipeline = NULL`, and the project
  manifest has **no `com.unity.render-pipelines.universal`** (only `com.unity.shadergraph` 17.5.0).
- Every Hovl renderer's material uses a Shader-Graph shader (`Shader Graphs/HS_Blend_CG`,
  `HS_Distortion` — the packs ship `.shadergraph` assets). **Shader Graph shaders render ONLY under
  URP/HDRP.** In Built-in RP their SG passes carry URP `LightMode` tags the Built-in forward renderer
  never executes → the particles emit valid geometry (`isSupported=True`, non-zero `particleCount`,
  valid `Renderer.bounds`) but draw **zero pixels**. This is exactly the #1503 signature.
- NOTE: the `unity-asset-stack` skill's "the project is URP" line is STALE for this box — the empirical
  pipeline check is the ground truth.

## Conversion path taken
The skill's "URP-convert check" applied to the ACTIVE pipeline: the `3 - Fix Hovl VFX` menu item
detects `currentRenderPipeline == null` (Built-in RP) and **re-points each Hovl SG particle material to
Unity's built-in `Legacy Shaders/Particles/Additive` (`/Alpha Blended` for distortion), preserving the
emissive texture + tint** (`_TintColor` set warm to avoid additive blow-out over the warm plate). No
Hovl third-party file is edited in place — the fix is a runtime material re-point driven by the WorldOS
probe (repo vendors no Hovl assets; they live only on the box). Under URP the tool would instead log the
Opaque/Depth-texture / reimport guidance and NOT re-point.

## Item (ii) — Hovl VFX visibility: **PASS**
Two owned effects (`AAA Projectiles Vol 1/.../Hit 16 fire`, `Magic circles/Loop version/Magic circle
fire loop`) went from **0 visible pixels -> clearly visible** over the crypt plate after the fix.
- `m15_rerun_vfx_prefix.png` — pre-fix: both spawned, ZERO visible particles (SG shaders in Built-in RP).
- `m15_rerun_vfx_both.png` — post-fix: warm fire/spark burst renders in front of the sarcophagus.
- `m15_rerun_vfx_circle_only.png` — fire disabled, isolates the magic-circle effect rendering alone.
- 296 particles across 12 renderers confirmed warmed + rendering (diag log).

## Item (i) — 6-unit character-in-scene cohesion: **FAIL** (improved + diagnosed)
The 6 gate units are now REAL OWNED MESHES (3x fighter party + 3x goblin foe, `Actor_M15_*`) put through
the existing CohesionProbe stack (RungB plate-sampled light rig -> RungD contact shadows -> RungA'
PainterlyActor materials) — fixing the #1503 defect that the units were bare capsules the stack never
touched. Control-anchored 5-scorer blind panels vs `poe2_ruins_brazier_integration_01` (anchor 8.0,
band [6.8, 9.2]):
- **Faithful (rings-on):** candidate median **2.0** vs control **8.0** (delta -6.0, 5/5 control>cand).
  `m15_rerun_cohesion_panel.json`. Unanimous #1 flaw: the team-ring ground decals read as GAME-ENGINE
  UI SELECTION RINGS (a gameplay overlay carried by the baseline spawn, not character art).
- **Rings-off diagnostic:** candidate median **4.0** vs control **8.0** (delta -4.0).
  `m15_rerun_cohesion_panel_noring.json`. Removing the UI decal is worth **+2.0** — but 4.0 is still
  below the 6.8 band. Residual: combat-distance party figures render dark/cool (RungA' warmth is
  fire-proximity-gated; the formation cells sit far from the plate's hearth), faint grounding at combat
  scale, small on-screen size.
- The painterly stack lifts cohesion over bare capsules (1.0 -> 4.0, +3.0) but not into band at the
  6-unit combat framing.

## Deterministic pre-gate (grounding/occlusion architecture): PASS (unchanged from #1503)
Same 6 cells + target heights (3.2 party / 4.2 foe); the #1503 `m15_gate_pregate_report.json` verdict
(floor-contact + screen-scale + pose all PASS, 6/6) carries over — this is an architecture-scale check
the cohesion panel does not re-litigate.

## GATE RE-VERDICT: **FAIL** -> NO new asset spend this round
Per the skill's binary contract (visual-critic cohesion MUST hold): the cohesion panel is below band, so
the gate does not pass. Hovl visibility is now FIXED and the architecture pre-gate holds — the sole
remaining blocker is 6-unit cohesion, with bounded, no-new-asset fixes in `CohesionProbe.cs` / the spawn
convention:
1. Rework the team ring as a subtle rendered contact decal (not a bright UI selection ring) — worth ~+2.0.
2. Give combat-distance units a plate-ambient WARM FLOOR independent of hearth proximity (kill the dark/cool silhouettes).
3. Strengthen RungD contact-shadow density at combat scale (units read as floating without the ring).
4. Consider a tighter combat camera or larger actor target-height for the multi-unit read.

Because the gate does NOT pass, there is no purchase to authorize; asset spend stays BLOCKED pending a
cohesion pass on the above fixes.

## Evidence files
- `m15_rerun_vfx_prefix.png` / `m15_rerun_vfx_both.png` / `m15_rerun_vfx_circle_only.png` — Hovl before/after/isolated.
- `m15_rerun_cohesion_6actors.png` (rings-on) / `m15_rerun_cohesion_6actors_noring.png` (rings-off) — 6 painterly actors.
- `m15_rerun_cohesion_panel.json` / `m15_rerun_cohesion_panel_noring.json` — the two 5-scorer panels.
