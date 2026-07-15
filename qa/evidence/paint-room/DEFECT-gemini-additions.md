# Gemini addition habit — the ONE remaining paint defect class (3/3 paint_room runs, 2026-07-15)
Pattern: the flux base follows the depth (recalls 0.92-0.96); the Gemini style pass then INVENTS
architecture in the EMPTY DARK regions — staircases + phantom columns (crypt), staircases (tavern),
a full second-story arcade gallery + 4 staircases (throne). The structure-lock prose alone does not
hold against void space.
Fix queue (next cycle):
1. DETERMINISTIC MASK-COMPOSITE (preferred): project the room volume (walls+floor diamond from the
   boxes sidecar) into screen space, feather ~12px, and composite styled-inside/base-outside. The
   apron and above-wall headroom then come from the REGISTERED base by construction — the whole
   invented-architecture class dies without prompt-fighting. Implement in qa/paint_room.py.
2. Prompt clause: "the empty dark space above the cutaway walls and outside the room must remain
   EMPTY dark background — no upper story, galleries, balconies, staircases" (belt+braces).
3. Adoption gate unchanged: inverse-coherence NET-NEW on clear floor must be 0.

## Composite fix v1 verdict (measured on the throne, 2026-07-15)
qa/overlay_boxes.py --composite (styled-inside-envelope / base-outside, feathered): KILLS the
outside-envelope class (right staircase gone, apron clean) but CANNOT kill restyles painted on
legitimate wall-face screen area (the invented gallery lives on the real far wall's projection).
Verdict: composite = standing guard for outside-envelope; wall-face inventions remain the
structure-lock prompt clause + the inverse-coherence NET-NEW adoption gate's job. Also note the
BASE's own apron content leaks through the composite (flux grid-lines) — pick clean-apron draws
or darken the base apron in-composite (queued).
