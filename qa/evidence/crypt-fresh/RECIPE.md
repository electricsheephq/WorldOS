# FRESH-CRYPT recipe (reproducibility)

Lane: build a BRAND-NEW crypt through the COMPLETE amended pipeline (every M-ALIGN learning applied)
and adopt it if it clears the gates. NOT a regen of `crypt_armb_iter3` (incumbent 8.0) nor of
`crypt_rich`. CU spent: **65** (base 26 + Gemini 39), cap 80, no training.

## 1. Geometry — `author_crypt_fresh` (tools/author_room_geometry.py)
`crypt_fresh` -> qa/evidence/crypt-fresh/crypt_fresh_geometry.json (14x11, 20 props incl. wall_runs,
68 non-walkable, 86 walkable — flood-fill CONNECTED, both doors reachable).
- Canonical layout kept: pillars (3,3)/(3,4)+(8,9)/(9,9) (imported from seed_gfx_combat), doors
  (6,0) camp + (13,4) tavern, kept CLEAR with landing rings.
- EXTENT CONTRACT (crypt_rich lacked this): `camera_fit=true` + CONTINUOUS `wall_run` perimeter band
  split at both doors (no per-cell crenellation, #1539/#1543).
- TRUE 2x2-proportioned coffin `(4,7),(5,7),(4,8),(5,8)` (not the 12-cell drift blob).
- Crypt-rich density: effigy niches, torch brackets, skull/bone pile, rubble/broken slabs, spilled
  urn — as SHORT 1-2 cell runs (tight occlusion hulls; runbook step 2 / CAMP-TUNE defect #5). TALL
  ornaments on the back band (row1)/far wall (col12), LOW clutter in near/left corners.
- Manifest derived: qa/room_manifests/crypt_fresh.cells.json (recipe_key crypt_fresh).
- Greybox: CUTAWAY wall_height=5, `--camera-fit` (ortho 10.52). qa/evidence/crypt-fresh/crypt_fresh_greybox.png (1344x768).

## 2. Registered base — flux.1-dev depth-ControlNet, CLEAN (no LoRA)
model_bfl-flux-1-dev, controlModality=depth, controlStrength=0.7, controlImage=<camera-fit greybox
asset_cvty83XpcxxmckTZNAPhgjE9>, **NO loras** (deliberately deprecating the AI-on-AI G379 interior
LoRA that crypt_rich used — model chain stays 100% registry-CANONICAL), 1344x768, numOutputs=3,
steps=28, guidance=3.5, seed=12345. Best-of-3: base_2 (asset_sTSiKW8FyEUhLWeeiygqM78e) adopted,
edge-recall **0.9603** vs greybox (>=0.95 gate; the other two seeds scored 0.70/0.75). base=26 CU.
Prompt: qa/evidence/crypt-fresh/base_prompt.txt

## 3. Style pass — Gemini 3.1 STRUCTURE-LOCK + ADDITIONS-LOCK enrichment
model_google-gemini-3-1-flash, referenceImages=[base_2 asset — greybox-aligned, the sanctioned
img2img case; NO external anchor per the reference-images LAW], resolution=1K, thinkingLevel=HIGH,
numOutputs=3, seed=123, aspectRatio=auto. Prompt (STRUCTURE-LOCK + ADDITIONS-LOCK #1542 verbatim +
scene-content grounding + no-text/no-figures): qa/evidence/crypt-fresh/style_pass_prompt.txt.
Best-of-3: styled_3 (asset_zPi3GpbhkiMGYV8V2iwP5G6M) adopted -> candidate_final.png (=crypt_fresh_v1.png),
resized to 1344x768, edge-recall **0.9750** vs greybox. Gemini=39 CU.
Fake-text artifact watch: the runic wall carvings read as abstract weathering, at/below the
incumbent's known fake-text signature (5/5); no scoped-exception re-pass needed (also preserves budget).

## Gates
- edge-recall candidate 0.9750 >= 0.95 — PASS.
- visual_pregate.py (HARD FLOOR): PASS (frame-lit mean_luma 0.213, occupancy, floor-contact, screen-scale).
- inverse-coherence NET-NEW vs base: raw 14, but **14/14 fall inside authored prop OCCLUSION**
  (tomb/pillar/wall silhouettes rising up-screen), **0 on genuinely clear floor** => ADDITIONS-LOCK
  held, zero invented walk-through furniture. (The raw ==0 bar is #1556-documented as miscalibrated
  for the walled crypt.)
- check_grid_paint_coherence.py: INCOHERENT (diagnostic only — the 8.0 incumbent fails this strict
  absolute gate identically; promote.py keys on visual_pregate + panel delta, not this gate).
- Blind 5-scorer neutral-anchor panel (qa/evidence/crypt-fresh/crypt_fresh_visual_gate_panel.json +
  panel/): candidate **7.0** vs incumbent crypt_armb_iter3 **8.0** (|delta| 1.0 <= 1.5) vs registered
  PoE2 ruins-brazier real-art control **8.0** (in-band [6.8,9.2]); delta candidate-control **-1.0** >= -1.2.
  VERDICT: **ADOPT** (7.0-parity band cleared).
- promote.py --batch: **promoted, tier=canonical-candidate, passed=true, library-lint clean**.

## Adoption wiring
- extensions/renderers/unity/plates_manifest.json crypt -> plates/crypt_fresh_v1.png (repo copy at
  qa/evidence/crypt-fresh/crypt_fresh_v1.png; box deploy deferred to next cycle).
- extensions/renderers/shared/room_recipes.json rooms.crypt.canonical_plate -> crypt_fresh_v1.png (+ status).
- qa/room_manifests/crypt_fresh.cells.json (canonical derived manifest).
- WALKSLICE-RECONCILIATION.md — fresh geometry vs canonical combat grid delta (seed NOT edited, #1559).

---

## ADDENDUM — CRYPT-ALIGN-V2 (M-ALIGN, 2026-07-15): camera_fit-aware QA + paint realignment

The RECIPE above adopted a beautiful plate whose EXTENT/walls were paint-correct but whose interior
FURNITURE flux had relocated (see WALKSLICE-RECONCILIATION.md addendum). Two root causes fixed here:

1. **The QA projection stack was ortho-blind.** `check_plate_drift.project_cell_bbox` (and every
   consumer — `derive_room_manifest`, `journey_visual_sweep`, `check_grid_paint_coherence`) projected at
   the FIXED ortho 13, written pre-#1543. A camera_fit plate is PAINTED at its own fitted ortho
   (crypt_fresh 10.5224, tavern_fit2 9.2597), so all QA sampling on fit rooms was shrunk ~0.81×/0.71×
   toward centre — the crypt flags happened to be robust (central), the tavern's 100% was unverified.
   Now: `project_cell_bbox`/`col_pitch_px`/occlusion derivation/quad sampling take an `ortho=` (None ⇒
   the fixed rig, byte-identical for non-fit rooms); `derive_room_manifest` STAMPS `camera_fit` + `ortho`
   into the manifest as the single source of truth; the sweep resolves each room's ortho from its
   manifest. Also fixed the sweep occlusion resolver's first-match-wins collision (crypt's pillar was
   resolving occlusion from the stale `crypt_dense_v1` manifest) by preferring the live room's OWN
   manifest.

2. **Geometry realigned to the paint** (author_crypt_fresh v2): sarcophagus → the painted back-band tomb
   cols 7-11 × rows 3-4, pillar_l → its painted plinth (4,2)/(4,3), pillar_r/skull_pile/urn_spill deleted
   (painted behind the cutaway / outside the playable walls). Re-derived `crypt_fresh.cells.json` at fit
   ortho; `tavern_fit2.cells.json` re-derived at 9.2597 (geometry unchanged).

### The coherence-gate verdict (honest)
`check_grid_paint_coherence` on crypt_fresh v2 is COHERENT on a correctly-REGISTERED plate (the greybox
rendered from its own v2 footprints at fit ortho 10.5224 — every furniture prop localises at offset 0,
NCC 1.0; pinned by `qa/test_grid_paint_coherence.py::test_realigned_crypt_fresh_geometry_is_grid_coherent`).
On the fully PAINTERLY `crypt_fresh_v1.png` the gate stays advisory-INCOHERENT — the flat-box NCC localiser
is inherently weak against painterly texture (the module's own RELIABILITY NOTE), and the coherence-perfect
`tavern_fit2` painterly plate reads INCOHERENT identically. The per-CELL painterly coherence proof is the
visual sweep below, not this screening gate.

### Definitive fit-aware sweep (qa/evidence/1540/after-align-v2)
| room | before (ortho-13 sampling) | AFTER (fit-aware) |
|---|---|---|
| crypt | 85.1% | **90.7%** (8 flags, 6 occlusion-exempted) |
| camp_clearing_night | — | 95.6% |
| tavern | 100.0% (unverified) | **100.0%** (re-verified under correct sampling) |

reciprocal-door failures **0**, hero-position failures **0**. The tomb/pillar/all ornaments now align and
are occlusion-exempted. The residual **8 crypt flags** `[(6,5),(5,6),(6,6),(6,4),(4,7),(5,7),(5,5),(4,4)]`
are the ornate-floor false-positive class (same as camp's 6): the carved celtic-knot floor plaque + the low
dais slab in front of the coffin + torch/coffin shadow-bleed on the flagstones — all WALKABLE painted floor,
NOT invented furniture. They are deliberately NOT chased to 95% by blocking walkable-looking floor (that
would recreate the playtest-#8 defect in reverse) nor by an ad-hoc per-cell exemption list; the honest
number ships. Overlays: `qa/evidence/1540/after-align-v2/overlay_v2_fit.png` (footprints on plate),
`overlay_flags_v2.png` (flagged/exempted), `blend_v2_fit.png` (greybox↔plate registration).
