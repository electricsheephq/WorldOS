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
| 5 | Alignment (paint-vs-volumes overlay; masses in-volume) | overlay + coherence | 8 | pending |
| 6 | Corner/seam integrity (walls meet cleanly) | paint eyeball | 5 | 9 |
| 7 | Containment (nothing painted outside playable; no invented objects) | overlay eyeball | 6 | 6 (bones on apron — flux prior, open defect) |
| 8 | PoE2-family beauty (vs real-art control) | blind panel | pending | pending |

## The refinement loop (per room, ~30 min + 29 CU/cycle)
author geometry → scripted spacing audit → box greybox render → DESIGN GATE (axes 1-4; REJECT is
free) → flux base @0.85 + fat/molded volumes → Gemini structure-lock pass → overlay (axis 5) +
eyeball (axes 6-7) → blind panel (axis 8) → grade the row → fix the LOWEST axis at its root
(geometry/builder/prompt — never chase paint with paint) → repeat until all ≥7.

## Known open defect classes (root-cause the generator, not the instance)
- Bones/debris drift to the exterior apron (flux prior; 3/3 crypts) — candidate fixes: place bone
  piles adjacent to interior wall faces w/ a wall-side backdrop volume; or mask the apron in the
  conditioning depth (paint it far-black).
- Ring collars on columns read as flat plates from the dimetric camera — try torus-approx (two
  stacked thin cylinders) or drop collars entirely.
- 1-cell wall-mounted items ~0.7-1.6 cell drift (measured v3.1/v3.2) — keep ≥2-cell or wall-attached.

## Scale-out (after crypt + tavern both pass): forest road, DunGen dungeon room, then the
## 2-environments-a-night cadence (HV5) with this scorecard as the promotion gate.
