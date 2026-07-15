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
