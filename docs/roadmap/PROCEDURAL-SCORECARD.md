# PROCEDURAL ROOM SCORECARD (owner directive, 2026-07-15 post-v3.4: "generate, iterate, scorecard,
# refine until it's generating them well")

Every generated room candidate is graded 0-10 on NINE axes before any adoption talk. Grades come
from: (1) the orchestrator's design-gate eyeball on the greybox (axes 1-4, FREE — before paint),
(2) the box-overlay + coherence tooling (axis 5), (3) a blind comparative panel vs a real PoE2
control (axes 6-8), (4) **the automated WALKABILITY gate (axis 9 — PASS/FAIL, not a score;
epic #1581)**. SHIP BAR: axes 1-7 target ≥7; **axis 8 is CONTROL-ANCHORED, never absolute** — it
passes when the candidate's same-panel median is within the band (Δ ≥ −2.0) of the embedded
real-art control (the 2026-07-02 positive-control recalibration, VISION.md: real shipped PoE plates
score 3.0-5.6 on this instrument, so an absolute ≥7/≥8 is unattainable BY CONSTRUCTION); AND axis 9
must be GREEN. Precedent: tavern v1 shipped at 7.0-vs-9.0 Δ−2.0 (#1531), shop v1 at 6-vs-8 Δ−2.0.

| # | Axis | Measured by | v3.4 | v3.5 | shop v1 (2026-07-16) |
|---|------|-------------|------|------|------|
| 1 | Door readability (framed arches, no phantom doors) | design gate + paint eyeball | 8 | 7 (right portal weak) | 8 (auto door frames) |
| 2 | Architectural logic (rhythm, focal point, negative space) | design gate | 8 | 9 | 8 (centred counter focal) |
| 3 | Silhouette vocabulary (molded, no box salad) | design gate | 8 | 8 | 7 (shelving boxy by identity) |
| 4 | Spacing/breathing room (≥1 cell around freestanding masses) | geometry audit (scripted) | 5 | 9 | 9 (walkable by construction) |
| 5 | Alignment (paint-vs-volumes overlay; masses in-volume) | overlay + coherence | 8 | 8 (2 props 0.05c; SW brazier 1.6c) | 8 (recall 0.9555) |
| 6 | Corner/seam integrity (walls meet cleanly) | paint eyeball | 5 | 9 | 8 |
| 7 | Containment (nothing painted outside playable; no invented objects) | overlay eyeball | 6 | 6 (bones on apron — flux prior, open defect) | 6 (invented staircase OUTSIDE envelope — throne-gallery class) |
| 8 | PoE2-family beauty (vs real-art control) | blind panel | pending | 6.2 (incumbent 8.2, control 8.8 in-band) | 6 vs control 8, Δ−2.0 IN-BAND (cycle-1) |
| 9 | **WALKABILITY (qa/walk_test.py: camera pose + engine truth + BFS orphans + doors + visual registration)** | **automated gate — #1596 sandbox** | n/a (predates gate) | GREEN (crypt live 2026-07-16) | see walk report |

**Axis 9 is a HARD FLOOR, not a score** (VISION.md TIER-0): the room does not ship without a green
`walk_report.json`, however the other eight axes read. The static half (`qa/walk_static.py` —
manifest lint, ortho triple-check, orphan/landing checks) runs in CI on every PR; the live half
drives the player in the #1596 sandbox lane (never the owner's campaign).

## The refinement loop (per room, ~30 min + 29-47 CU/cycle)
author geometry → scripted spacing audit → box greybox render → DESIGN GATE (axes 1-4; REJECT is
free) → flux base numImages=3 + PICK BY EDGE-RECALL vs greybox — MANDATORY FOR EVERY ROOM CLASS
(tavern failed 2/2 single-shots; crypt v3.6 then failed 1/1 on the depth-RICH class, killing the
"depth-rich rooms are safe single-shot" exemption same-day) → Gemini structure-lock pass → overlay (axis 5) +
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

### ★ Panel-ruler calibration (2026-07-16 — the two-anchor rule)
A Δ-vs-control band is only comparable under the SAME scorer wording: re-scoring the SHIPPED shop +
snug plates under a rewritten panel prompt read Δ−3.0/Δ−2.5 (they shipped at Δ−2.0/Δ−1.0) — ~1pt of
pure ruler drift. RULE: every panel batch includes at least one SHIPPED plate as a disguised
CALIBRATION REFERENCE; the ship verdict is "candidate Δ within ~0.5 of the calibration reference
under the same run", not the raw band. The PoE2 control anchors the scale's top; the shipped plate
anchors the SHIP BAR.

## Cycle log
| date | room | cycle | verdict | CU |
|------|------|-------|---------|----|
| 2026-07-15 | crypt | v3.5 | ITERATE: axes 1-7 ~8 but panel 6.2 (vignette framing, carving softness, flat light drama); WINS readability vs incumbent 7.2>6.5 | 29 |
| 2026-07-15 | crypt | v3.6 | geometry SHIPPED (focal light cluster, niches removed — panel levers); paint honest negative: style collapse at single-shot cs0.85 on the depth-rich class → best-of-N now universal | 9 |
| 2026-07-15 | tavern | 1 | process-refined honest negative: molded table/bar kinds + cue-mass rule (1.33h top = 0 depth delta, raised to 2.0) landed; base registration fails flat-interior class at cs0.85 single-shot -> best-of-3 selection gate next | 18 |

| 2026-07-15 | crypt | v3.6-restored | ★ BAR MET: panel 8.3 (Δ control −1.1; incumbent 7.2) via paint_room one-command chain; draw recall 0.9595 | 29 |
| 2026-07-15 | tavern | 2-restored | ★ BAR MET: panel 8.4 (Δ −1.0); the previously-unpaintable room | 29 |
| 2026-07-15 | throne | registered-1 | panel 7.0; levers: material wash + Gemini-additions (gallery) | 29 |
| 2026-07-16 | shop | 1 | ★ SHIPPED cycle-1: panel 6 vs control 8 Δ−2.0 IN-BAND; recall 0.9555; WALK-GREEN (#1596 sandbox, visual 5/5 ~0.3c) — the pipeline's first hands-off NEW CLASS | 47 |
| 2026-07-16 | dwing wing (3 GENERATED rooms) | 1 | honest negative: NEW invention class — Gemini MULTIPLIES features (2 pillars→5, 1 door→5 arches); flux bases geometry-PERFECT (0.96/0.83/0.77); recall is precision-blind to additions → paint_room base→styled drop WARNING shipped | ~140 |
| 2026-07-16 | dwing wing | 2 | EXACT-COUNT lock: rooms 0+2 structurally HONEST (eyeball-adjudicated; instrument characterized as edge-contrast-biased); room_1 RESHAPED (12x7→square, side doors→back wall). PANELS rooms 0+2: 6-vs-9 Δ−3.0 OUT OF BAND 2/2 — "no narrative focal point / empty floor" = geometry-richness on crates-only DunGen rooms | ~140 |
| 2026-07-16 | dwing room_1 | 3 | SHAPE+DOOR-WALL locks: structurally honest (wide-shallow held, side doors correct). Lock library proven: EXACT-COUNT · ROOM-SHAPE · DOOR-WALL | 47 |
| 2026-07-16 | dwing wing | 4 | GEOMETRY lever: dress_focal (PR #1611 — altar+braziers by door count) → re-render → focal-named recipes; LAST wing cycle this run (in-band ⇒ ship; miss ⇒ documented ceiling) | ~140 |
| 2026-07-16 | dwing room_0 | 4-verdict | ★ ADOPTED — first GENERATED room to ship-grade: Δ−3.0 == CAL_shipped_shop Δ−3.0 under the SAME panel ruler (see calibration rule below); base recall 0.9694 (focal-dressed conditions better); honest structure (altar+braziers+2 pillars as authored) | — |
| 2026-07-16 | dwing rooms 1+2 | 4-bug | c4 CONTAMINATED for these rooms by a recipe-authoring bug: shared "ALTAR where present" grounding INVITED invented altars (2/2) + room_1 shape section diluted → c5 = bug-fix rerun (per the param-slot discipline: diff recorded inputs before declaring a ceiling) | ~94 |
| 2026-07-16 | dwing room_1 | 5-verdict | ★ ADOPTED — c5 (bug-fix rerun) structurally perfect (shape held, side doors correct, braziers at stands, no invented altar); Δ−3.0 == CAL_shipped_shop Δ−3.0 SAME RUN = the shipped bar; base recall 0.9203 | 47 |
| 2026-07-16 | dwing room_2 | 5-negative | HONEST NEGATIVE (parked): focal-geometry paints failed structure 2/2 — c4 invented altar (recipe bug), c5 invented a SUNKEN CISTERN + stairs-DOWN over the east door landing (dodged the upward-only vertical lock; no recall warning = precision-blind). SINGLE-FLAT-LEVEL clause shipped to the shared lock; 4-door+dense-corner class = the hardest seen | 47 |
| 2026-07-16 | tavern_snug | 1 | honest negative: 6 vs 9 Δ−3.0 OUT OF BAND (flat-interior class; invented post + stairs) | 47 |
| 2026-07-16 | tavern_snug | 2 | honest negative: 6 vs 9 again = PROMPT PLATEAU (blue-violet/atmosphere levers no gain); styled recall 0.60 (drift) | 47 |
| 2026-07-16 | tavern_snug | 3 | ★ SHIPPED via the GEOMETRY lever (2 authored timber posts): 7 vs 8 Δ−1.0 IN-BAND (+1 median); styled recall 0.8415 (best final yet); WALK-GREEN (visual 6/6 0.15-0.4c). Flat-interior class CONFIRMED with a measured intervention | 47 |

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

## ROOM-CLASS COVERAGE (2026-07-15 procedural-world run)
The unified pipeline (geometry → build_room_unified.cs greybox+depth+boxes → paint) now covers:
| class | geometry | greybox design-gate | paint status |
|-------|----------|---------------------|--------------|
| crypt | author_crypt_* + v3.5/v3.6 | PASS (0.04-0.05 cell registration) | v3.5 panel 6.2; gemini-restyle 7.5 (drift) |
| tavern | /tmp/tavern_v2 (molded bar/table) | PASS | flux-blocked (endpoint regression) |
| throne hall | throne_hall_geometry (dais/throne/banner kinds) | PASS (120 walkable, connected) | gemini-restyle painterly preview banked |
| town (N-room) | tools/generate_town.py from a DunGen layout | per-room PASS | greybox-plated walk proven |
Molded kind vocabulary in build_room_unified.cs: wall_run · stone_pillar · sarcophagus · stone_well ·
brazier · altar · barrel · table · bar · **dais · throne · banner** (new). A new class needs geometry
+ (0-2 new molded kinds when its furniture reads wrong as an existing kind).

## THE TOWN COMMAND CHAIN (Phase E, proven end-to-end)
```
tools/generate_town.py <dungen_layout.json> --rooms r0,r1,r2,r3 --town-id <slug> --out-dir <d>
  → <slug>_<room>_geometry.json ×N (unified-painter-ready) + <slug>_world.json (reciprocal door pairs,
    door_cells[i]↔connections[i]) + <slug>_plates_fragment.json (cameraPin orthos + boxes sidecars)
qa/seed_gfx_town.py <state_dir> <out-dir> <slug>   → engine world; cross_door walks all hops
```
Proven: a 4-room DunGen subgraph seeded + walked room_0→1→2→3, every cross_door landing correct.

## ★ THE GREYBOX→GEMINI ROUTE (flux-outage response + a standing beauty lever)
When flux depth-CN is unavailable/regressed: feed the pixel-registered greybox straight to
model_google-gemini-3-1-flash (structure-lock + scene-grounding prompt, 2K, thinkingLevel HIGH,
seed 123, NO referenceImages) → PoE2-caliber painterly in ONE call (crypt + throne panels 7.5).
TRADEOFF: Gemini recomposes ~0.8 cell → NOT registered enough for the walkable/occluder pipeline;
use for BEAUTY PREVIEWS + design-gate visualization. The registered path stays flux-base → Gemini
(0.05 cell) — restore when the flux endpoint recovers. Full detail qa/evidence/gemini-restyle/.
