# PROCEDURAL ROOM SCORECARD (owner directive, 2026-07-15 post-v3.4: "generate, iterate, scorecard,
# refine until it's generating them well")

Every generated room candidate is graded 0-10 on EIGHT axes before any adoption talk. Grades come
from: (1) the orchestrator's design-gate eyeball on the greybox (axes 1-4, FREE — before paint),
(2) the box-overlay + coherence tooling (axis 5), (3) a blind comparative panel vs a real PoE2
control (axes 6-8). A candidate ships only at ≥7 on every axis.

| # | Axis | Measured by | v3.4 | v3.5 |
|---|------|-------------|------|------|
| 1 | Door readability (framed arches, no phantom doors) | design gate + paint eyeball | 8 | 7 (right portal weak) |
| 2 | Architectural logic (rhythm, focal point, negative space) | design gate | 8 | 9 |
| 3 | Silhouette vocabulary (molded, no box salad) | design gate | 8 | 8 |
| 4 | Spacing/breathing room (≥1 cell around freestanding masses) | geometry audit (scripted) | 5 | 9 |
| 5 | Alignment (paint-vs-volumes overlay; masses in-volume) | overlay + coherence | 8 | 8 (2 props 0.05c; SW brazier 1.6c) |
| 6 | Corner/seam integrity (walls meet cleanly) | paint eyeball | 5 | 9 |
| 7 | Containment (nothing painted outside playable; no invented objects) | overlay eyeball | 6 | 6 (bones on apron — flux prior, open defect) |
| 8 | PoE2-family beauty (vs real-art control) | blind panel | pending | 6.2 (incumbent 8.2, control 8.8 in-band) |

## The refinement loop (per room, ~30 min + 29-47 CU/cycle)
author geometry → scripted spacing audit → box greybox render → DESIGN GATE (axes 1-4; REJECT is
free) → flux base numImages=3 + PICK BY EDGE-RECALL vs greybox (the promoted-recipe selection gate
— single-shot has no variance absorber; tavern cycle 1 failed 2/2 single-shots) → Gemini
structure-lock pass → overlay (axis 5) +
eyeball (axes 6-7) → blind panel (axis 8) → grade the row → fix the LOWEST axis at its root
(geometry/builder/prompt — never chase paint with paint) → repeat until all ≥7.

## Known open defect classes (root-cause the generator, not the instance)
- Bones/debris drift to the exterior apron (flux prior; 3/3 crypts). RULED OUT (2026-07-15): masking
  the apron in the conditioning depth — it is ALREADY far-black there (zero CN signal is exactly why
  flux decorates it freely). Live levers: (a) an explicit additions-lock clause in BOTH pass prompts
  ("the exterior apron outside the walls stays bare ground — no bones, debris, or objects"), (b) the
  best-of-N edge-recall selection naturally discards apron-heavy draws.
- Ring collars on columns read as flat plates from the dimetric camera — try torus-approx (two
  stacked thin cylinders) or drop collars entirely.
- 1-cell wall-mounted items ~0.7-1.6 cell drift (measured v3.1/v3.2) — keep ≥2-cell or wall-attached.

## Scale-out (after crypt + tavern both pass): forest road, DunGen dungeon room, then the
## 2-environments-a-night cadence (HV5) with this scorecard as the promotion gate.

## Cycle log
| date | room | cycle | verdict | CU |
|------|------|-------|---------|----|
| 2026-07-15 | crypt | v3.5 | ITERATE: axes 1-7 ~8 but panel 6.2 (vignette framing, carving softness, flat light drama); WINS readability vs incumbent 7.2>6.5 | 29 |
| 2026-07-15 | tavern | 1 | process-refined honest negative: molded table/bar kinds + cue-mass rule (1.33h top = 0 depth delta, raised to 2.0) landed; base registration fails flat-interior class at cs0.85 single-shot -> best-of-3 selection gate next | 18 |

## Defect classes (appended)
- FLAT-INTERIOR conditioning: rooms whose tallest furniture is ~2 units give flux compositional
  freedom (displacement, count drift, invented arches, soft finish). Levers: best-of-3 + edge-recall
  selection (proven, promoted recipe), cs 0.9-0.95 probe, taller architectural masses (chimney
  breast over hearth) so the depth carries structure.
- Dead-black apron vignette: a room that doesn't fill the frame reads as an unshipped asset to
  every judge (panel 2026-07-15). Levers: raise CAMERA_FIT_FILL for squarer rooms; prompt the apron
  as faintly-lit bare ground (both passes), never void; Gemini must not shrink the room (exact-size
  resize check already in the chain).
- Even light spacing = flat drama: breathing-room spacing must not equalize LIGHT sources — cluster
  braziers/candles asymmetrically around the focal point (panel lens: lighting 6.0 vs incumbent 8.4).
