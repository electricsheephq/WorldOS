# CAMERA-PIN IMG2IMG EXPERIMENT (Phase D datum)

**Lane:** bounded ART EXPERIMENT, DATUM-ONLY — produces candidates + measurements, does NOT adopt/wire
anything (`plates_manifest.json`, `room_recipes.json`, room-manifest seeds are all untouched).
**Repo base:** `origin/main` @ `c6037d26`. **Worktree:** `~/WorldOS-worktrees/wt-camerapin-img2img`
(branch `camerapin-img2img`). **CU spend: 27 / 60 cap** (3× flux.1-dev img2img @ 9 CU each; the panel
is LLM-scored, no Scenario spend).

## Hypothesis (the "surviving beauty lever" from the LAYERED-refuted chain, PRs #1557/#1559/#1561)

A LOW-STRENGTH img2img pass at the SAME camera (camera-pin) over the already-registered, already-
adopted `crypt_fresh_v1.png` (panel 7.0, docs/MODEL-REGISTRY.md / qa/evidence/crypt-fresh/RECIPE.md)
can add carved-stone / paint fidelity (+0.5 to +1.0 panel points) WITHOUT breaking registration —
unlike a second heavy Gemini structural-edit pass (refuted: breaks camera, 0.63 recall, #1557/#1559).

## 1. Chain — registry-canonical only

- **Base image (the img2img input):** `qa/evidence/crypt-fresh/crypt_fresh_v1.png` (1344x768), the
  CURRENT canonical crypt plate, panel baseline **7.0** (qa/evidence/crypt-fresh/RECIPE.md).
- **Model:** `model_bfl-flux-1-dev` (registry-CANONICAL, docs/MODEL-REGISTRY.md), img2img mode —
  `image=<uploaded crypt_fresh_v1.png>`, `strength` swept, **NO loras**, **NO controlImage /
  controlModality** (img2img alone needs neither LoRA nor ControlNet on this model — confirmed via
  `model_schema_get`: `image`+`strength` are independent of `controlImage`/`controlModality`).
  `width=1344 height=768 numInferenceSteps=28 guidance=3.5 seed=123 numOutputs=1` (best-of-1 per
  strength — budget discipline; 9 CU/image confirmed via `dry_run`).
- **Strength sweep:** {0.20, 0.30, 0.40}. **3 jobs, 27 CU total** (job ids: `job_ieZ54S78Ettjf5hWAM9fFJ9q`
  / `job_CuTJPzZFFeDakeM6vgC6hEdk` / `job_nAiPu8BzTPi13VEMke1VZ7Uq`; output assets
  `asset_YSrpyqzjJVHQjYDniLYCv6u1` (0.20) / `asset_Bu3J6qzCsw6Cejzv4VzLe1SY` (0.30) /
  `asset_wvkwVJ8XMnXBiS7pdSkmByMA` (0.40)).
- **Prompt:** `detail_pass_prompt.txt` — a DETAIL-ENRICHMENT framing (push carved-stone relief / oil-
  brush fidelity / chiaroscuro depth on what already exists), carrying the CRITICAL STRUCTURE-LOCK and
  ADDITIONS-LOCK clauses **verbatim** from `qa/evidence/crypt-fresh/style_pass_prompt.txt` (the
  "change NO structural element" / "add NO new furniture" lock language), so the img2img pass is held
  to the exact same non-negotiable contract the original style pass was.

## 2. Gates (run, not cited) — `gate.py`

`gate.py` reuses the shipping primitives with zero re-implementation, same pattern as
`qa/evidence/1556/gate.py`:
- **Gate 1 — edge-recall vs the INPUT** (`qa/plate_overlays.registration_recall`, EDGE_THR 24, TOL 3,
  1344x768 contract). Bar **>=0.95**.
- **Gate 2 — inverse-coherence NET-NEW** (`qa/journey_visual_sweep.inverse_coherence_flags` against
  `qa/room_manifests/crypt_fresh.cells.json`, camera_fit ortho 10.5224, occlusion cells taken directly
  from the manifest's own per-prop `occlusion` field — no live engine surface needed since the camera +
  geometry are IDENTICAL to the input by construction, camera-pin). NET-NEW = flagged on the candidate
  but NOT already flagged on the input; further split into inside-authored-occlusion (not invented) vs
  on-genuinely-clear-floor (an ADDITIONS-LOCK violation).
- **Gate 4 — `qa/visual_pregate.run_pregates` hard floor** (G1 frame-lit + G6 luma-staging-law; no
  scenegrid/actors supplied since this is a background-only detail pass with no actors in frame, so
  G2-G4 occupancy/floor-contact/screen-scale SKIP by design).

| strength | recall vs input | Gate 1 (>=0.95) | net-new flagged (clear-floor) | Gate 2 | G1 frame-lit | G6 luma-staging | Gate 4 verdict |
|---|---|---|---|---|---|---|---|
| 0.20 | **0.9527** | **PASS** | 0 | PASS | PASS (mean_luma 0.2201) | HIGH (FLAG) | FLAG* |
| 0.30 | 0.9332 | **FAIL** | 0 | PASS | PASS (mean_luma 0.2207) | HIGH (FLAG) | FLAG* |
| 0.40 | 0.9150 | **FAIL** | 0 | PASS | PASS (mean_luma 0.2212) | HIGH (FLAG) | FLAG* |
| *(input itself, sanity check)* | 1.0000 (vs itself) | n/a | n/a | n/a | PASS (mean_luma 0.2151) | HIGH (FLAG) | FLAG* |

\* **G6 luma-staging-law FLAGs identically on the INPUT plate itself** (the already-adopted, panel-7.0
`crypt_fresh_v1.png`: near_black 15.7%, lit 34.2%, median_L 47.9 — all outside its PASS band). This
pregate's staging-law bands were calibrated against COMBAT frames with actors; on this still
background-only plate it is a pre-existing, non-discriminating condition, not a candidate-introduced
regression (same honest-negative treatment as `check_grid_paint_coherence.py` in the original
crypt-fresh RECIPE.md, which the 8.0 incumbent also fails identically). Advisory only for this
experiment; not load-bearing for the verdict below.

**Only strength 0.20 clears Gate 1.** Registration overlays (`build_overlays.py` ->
`overlays/overlay_strength_0{20,30,40}.png`, `overlays/progression_strip.png`) make the drift visible:
at 0.30 the low wall/step geometry near the torch has already started to reshape; at 0.40 a new
candle/urn-like object appears at the base of the torch wall that isn't in the input, and the
near-floor stair/step area has visibly changed shape — camera-pin registration is NOT free even at
"low" strength on this model; the usable ceiling for this experiment is ~0.20-0.25.

## 3. Blind 5-scorer NEUTRAL-ANCHOR panel (the sole Gate-1 survivor, strength 0.20)

Full data: `panel/panel_verdict.json` + `panel/blind_mapping.json`. Protocol: blind slots A (input,
this round's pre-experiment baseline) / B (disguised real-art control — the SAME
`control:visual:pillars-of-eternity-2:poe2_ruins_brazier_integration_01` used in the original
crypt-fresh adoption panel, band [6.8, 9.2]) / C (candidate = strength_020.png), never disclosing the
input as "the house best" (#1560 neutral-anchor rule). 5 independent scorer subagents
(fast-worker/sonnet), each blind to the slot identities, calibration-control instructions applied (no
AI-prior primer — score the craft you see, per the visual-critic SKILL.md §③ CALIBRATION-CONTROL
PROTOCOL).

| slot | scores (5) | median | mean |
|---|---|---|---|
| A (input, this round) | 7.5, 7.5, 7.5, 7.5, 6.5 | **7.5** | 7.30 |
| B (PoE2 control) | 9.5, 9.0, 9.5, 9.0, 9.0 | **9.0** | 9.20 |
| C (candidate, strength 0.20) | 6.5, 5.5, 6.8, 5.5, 7.0 | **6.5** | 6.26 |

Control median 9.0 lands inside its registered band [6.8, 9.2] (mean 9.2 sits at the band's top edge)
→ **instrument valid this round.** Input (A) reproduces its previously-published 7.0 within normal
panel variance (±1.2, per the calibration protocol) → cross-round consistency check holds.

- **delta candidate − input (same panel round): -1.0**
- **delta candidate − control: -2.5**
- **delta candidate − prior published crypt_fresh baseline (7.0): -0.5**

**Cross-scorer consistency (the load-bearing finding):** 4 of 5 scorers independently flagged the SAME
localized defect on the candidate — the skull/bone-pile prop degrading into an unreadable
"chain-of-orbs" / "beaded chain tangle" / "incoherent tangle of chain-and-ball shapes." The
deterministic inverse-coherence gate did NOT catch this (0 net-new flagged cells at strength 0.20):
the defect resamples FINE texture within an already-exempted occlusion cell rather than raising
silhouette DENSITY on clear floor, so it is invisible to a cell-level edge-density detector even
though it is visible to every scorer. 3 of 5 also independently noted the sarcophagus/effigy carving
reading softer, or the relief flattening toward generic motifs. Only 1 of 5 scorers rated the
candidate above the input (7.0 vs 6.5), citing more confident effigy drapery brushwork — the lone
dissent, not enough to move the median.

## Verdict: REFUTED

The camera-pin low-strength img2img lever does **not** clear the hypothesis on this evidence. Only
strength 0.20 survives the registration gate at all (0.9527, barely above the 0.95 bar — 0.30 and 0.40
both fail outright at 0.9332/0.9150), and that sole survivor scores **median 6.5 on the neutral-anchor
panel, 1.0 point BELOW the input in the same panel round** (and 0.5 below the previously-published
7.0 baseline) — the opposite of the hypothesized +0.5 to +1.0 gain. The panel's flaw lists converge on
a plausible mechanism, not just a number: flux.1-dev img2img at strength 0.20 preserves enough
low-frequency composition to hold edge-recall registration, but still resamples small, high-frequency
detail (the skull pile, fine relief carving) toward a blurrier, less legible result. **"Low-strength" is
not "detail-safe" by construction on this model** — the registration gate and the paint-fidelity goal
pull in the same direction only up to a point, and this experiment's single seed/strength-grid finds
that point below where any fidelity gain shows up. Honest negative: this specific lever (plain
flux.1-dev img2img, no LoRA, no ControlNet, strength <= 0.40) does not deliver the surviving-beauty
win the LAYERED-refuted chain (#1557/#1559/#1561) was hoping to find. Open avenues NOT explored here
(out of this experiment's scope/budget): a targeted/masked img2img restricted to specific carved
surfaces (avoiding the skull-pile class of small prop), a different img2img-capable model from the
#1556 bake-off (e.g. `model_microsoft-mai-image-2-5-edit`, the strongest style-pass challenger at
0.9999 recall / 0 invented), or a multi-seed re-run at strength 0.20 to check whether the skull-pile
defect is seed-specific.

## Budget

27 / 60 CU cap (3× flux.1-dev img2img generations @ 9 CU each, best-of-1 per strength; the panel used
no Scenario spend — 5 LLM subagent scorer calls only). No training. No adoption action taken.
