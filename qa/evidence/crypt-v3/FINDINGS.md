# CRYPT V3 — the design-gated arc through the unified pipeline (2026-07-15, playtest-#9 response)

## The design gate (NEW hard step — greybox critiqued as a GAME SPACE before any paint spend)
- v3.0 geometry REJECTED at the gate (0 CU): tomb read as a wall segment; grid-symmetric pillar
  pairs clumped on-screen from the contract camera. The gate catches "AI slop" BEFORE paint.
- v3.1 redesign PASSED: stepped 2-tier monument centered off the south wall; pillar DIAMOND framing
  the tomb chamber (screen-space rhythm, not grid symmetry); brazier-gated processional; sealed-arch
  flavor; collapsed-corner story; negative space preserved.

## The paint chain, measured (all overlays committed here)
| stage | CU | alignment (box-overlay verdict) | beauty |
|---|---|---|---|
| v3.1 flux base @0.70 | 9 | small props ON (0.07-0.10); 1-cell pillars drift ~0.7 | flat seed |
| v3.1 + Gemini | 20 | pillars drift ~0.7-1.0; SW pillar DROPPED; false door softened | excellent |
| v3.2 flux base @0.85 + FAT cue volumes (pillar pw 1.6→2.4, brazier 0.8→1.2) | 9 | IRON — all 4 pillars in-volume, tomb/braziers/niches in-volume | stiff (expected) |
| v3.2 + Gemini | 20 | big masses HOLD; 1-cell columns re-sculpted/shifted; SW pillar dropped AGAIN; 2 apron torches added (outside playable) | best-yet, PoE2-adjacent |

## THE MEASURED RULE (codify in the room runbook + design gate)
**Freestanding 1-cell props do not survive the style pass** (2/2 Gemini passes moved or dropped
them, even from an iron-aligned 0.85 base with fattened cue volumes). Masses ≥2 cells hold through
the full chain. → Design rooms with freestanding props at ≥2-cell footprints (a real crypt column
IS ≥5ft), or attach 1-cell items to larger masses (wall torches, tomb-side urns), or accept
post-paint cell realignment per prop.

## Status: CANDIDATE, NOT ADOPTED
v3.2-styled is the beauty candidate. Before adoption: v3.3 geometry (pillars → 1×2 footprints per
the rule) + one paint cycle + full gates (sweep pairing, neutral panel vs the camp 9/10 bar, truth
overlay in-player). Nothing wired; canonical manifest untouched.

## v3.3 — THE RULE CONFIRMED (2026-07-15 ~14:10)
Pillars re-authored as 1×2 piers (the measured rule) + fat cue volumes + 0.85 control: **all four
piers survived the Gemini style pass inside their volumes** (styled_v33_overlay.png) — first full
chain where every collision-relevant mass held end-to-end. Tomb/braziers/torch/urn in-volume, lit.
Residual cosmetics only: re-invented arched windows on the SW inner wall face (impassable cells —
no walkable lie; the v3.2 prompt correction was dropped in v3.3's pass, re-add it) + apron debris
outside the playable floor. **crypt_v33_CANDIDATE.png = the adoption candidate**; remaining gates:
targeted window re-roll (optional), sweep pairing, neutral panel vs the camp 9/10 bar, in-player
truth overlay. Arc totals: 3 geometry iterations (1 gate-rejected free), 3 bases, 3 style passes,
87 CU. Run total 188/300.

## v3.4 — MOLDED FORMS (owner: "everything is squares" → fixed at the source, 2026-07-15 ~15:00)
build_room_unified.cs grew a SHAPE VOCABULARY: cylindrical column shafts w/ plinths+capitals,
curved tomb-lid ridges (horizontal half-cylinders), brazier pedestal+bowl composites, arched niche
headers, and ARCHED DOOR FRAMES (jambs + arch cylinder + lintel) auto-generated at every door gap.
The depth map now carries CURVES → flux paints round knotwork columns, a rounded effigy lid, arched
stone portals, ironwork fire bowls (crypt_v34_MOLDED_CANDIDATE.png). Same geometry as v3.3 (same
collision cells); only the render vocabulary changed. Overlay (styled_v34_overlay.png): all volumes
hold; one soft spot — the SW column paints weakly behind its NW neighbor at this camera (watch in
panel). Cost: 29 CU (base 9 @0.85 + Gemini 20). Arc total 116 CU; run 217/300.
NEXT GATES unchanged: sweep pairing + neutral panel vs camp bar + in-player truth overlay.

## v3.5 — OWNER PUNCH LIST (2026-07-15 ~17:00): corners+capitals+spacing FIXED
Builder: wall runs extended 0.6 past ends (corners now OVERLAP — seams gone); slim chamfer plinth +
thin ring collar replaces the fat plinth/capital slabs. Geometry: 16x12 (was 14x11), >=1-cell
breathing ring audited in code (urn adjacency = intentional grave goods), bones consolidated inside.
crypt_v35_SPACED_CANDIDATE.png: corners continuous, columns clean, chamber breathes. OPEN: bones
still painted on the apron (flux prior, 3/3 — root-cause queued in PROCEDURAL-SCORECARD.md), right
portal paints weaker than the gate. Scorecard row recorded. 29 CU; run 246/300.

### v3.5 alignment measurement (axis 5) — MEASURED, not eyeballed
Numeric solve (brazier-flame blobs vs projected bowl centers at the sidecar ortho 11.7851):
NE gate brazier err 3px (0.05 cells) | wall torch err 3px (0.04 cells) | greybox render itself
fits ortho 11.7050 +/- blob bias == the stamp (2.1px residual) — the C# camera, the JSON sidecar,
and the Python projection agree; UNIFY-THE-FRAMES registration holds to the PIXEL on the grown room.
Defects: SW gate brazier painted ~106px (~1.6 cells) SE of its volume (the recurring SW-corner
weak-paint class — 3rd sighting; root-cause queued: SW corner is the darkest conditioning region);
urn (10,7) painted as a burning fire pot (kind drift, position held). Scorecard axis 5 = 8.
LESSON (instrument): floor apron-skirt boxes in a whole-scene overlay READ as global misalignment —
always confirm with the numeric blob solve before diagnosing camera drift (my first eyeball wrongly
suspected a client cameraPin override; the fit refuted it).

### v3.5 blind panel (axis 8) — 6.2 vs incumbent 8.2 vs PoE2 control 8.8 (in-band, valid)
ITERATE verdict, but the split is diagnostic gold: candidate WINS composition/readability (7.2 vs
6.5) — the whole-room-legible unified layout beats the incumbent's cropped vignette — and loses on
(1) the dead-black apron vignette read (framing/presentation, not paint), (2) carving fidelity
(#1538 ceiling class), (3) flat lighting drama from evenly-spread braziers (the breathing-room fix
overshot into uniformity — cluster lights asymmetrically), (4) niches painted as a wall-clipping
dome. Levers 1+3+4 are geometry/prompt-level and FREE to iterate; 2 is the known LoRA/detail-pass
question (owner-gated). Full verdict: crypt_v35_blind_panel.json.

## v3.6 (panel-lever iteration) — geometry SHIPPED, paint = honest negative that KILLED a bad rule
Geometry (free, design-gate PASSED): focal grave-flame pair flanking the tomb + single gate accent +
side torch (the panel's flat-drama fix), niches REMOVED (wall-clip fix + consolidation), 115
walkable connected. Flux base (asset_d585bv1FJe7jzEazEqb1mUDB, cs0.85 seed12345 single-shot):
STYLE COLLAPSE — chunky clay/low-poly blocks, 3 chess-piece pillars, 1 flame of 4, E door missing.
Same params that produced the strong v3.5 base, same depth-rich room class.
★ RULE FALSIFIED: "depth-rich rooms are safe single-shot" (written earlier TODAY) — 1/1 failure on
exactly that class. Flux draw variance is UNIVERSAL; best-of-N + edge-recall selection is mandatory
for EVERY room, not just flat interiors. Secondary suspect (untestable without spend): the longer
v3.6 prompt ("crisp carved stone relief", block-wall emphasis) may bias toward the chunky prior —
next cycle A/Bs the v3.5 prompt verbatim against the v3.6 prompt across the 3-draw set.
CU: 9. Run total 273/300 — remaining 27 banked for next session's 3-draw chain.
