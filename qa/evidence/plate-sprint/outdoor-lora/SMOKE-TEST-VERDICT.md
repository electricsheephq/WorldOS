# OUTDOOR-LORA smoke test verdict (2026-07-10)

Model: `model_RsWEcQL2NWXwoyEodWVE2vWG` ("WorldOS Painterly Exterior (FLUX)"), trained on the
18-image quality-passed set in `training_manifest.md` (job `job_U3HDsSj7T4aPETy7MRCuX6oK`,
1080 CU, ~172 min).

Pipeline per room: mint anchor via schema-correct `model_run` (`model_bfl-flux-1-dev` + depth
ControlNet str 0.65/end 0.6 + the new LoRA, `lorasScale` 0.7) -> Gemini instruction-edit
re-registration (`model_google-gemini-3-1-flash`, structure-lock + dimetric-lock prompt,
`referenceImages=[minted anchor]`) over the room's own greybox -> overlay + advisory edge-recall
(`qa/plate_overlays.py` / `advisory_eval.py`, content-blind per #1491) -> 5-scorer blind panel
(control + reference/incumbent + candidate, shuffled, `qa/scores_db.py` surface=visual).

## Results

| Room | Candidate median | Reference | Baseline cap | Success bar | Result |
|---|---|---|---|---|---|
| forest_road | **2.0 / 10** | cross-lane anchor (camp_clearing_night_v2) 7.0, control 9.0 | 6.0 | >6.5 | **SEVERE REGRESSION** |
| camp_clearing_night | **6.5 / 10** | incumbent (camp_clearing_night_v2, actual adopted plate) 6.0, control 9.0 | 6.0 | >6.5 | **BORDERLINE** (right at the line, not clearly above) |

Full scores, mapping, and defect notes: `forest_road/panel_verdict.json`, `camp/panel_verdict.json`.
scores_db rows: `outdoor-lora-smoke-forest_road-2026-07-10`, `outdoor-lora-smoke-camp-2026-07-10`.

## Headline finding

The new exterior-only, quality-passed LoRA **does not uniformly fix the outdoor generalization
gap** the effort was built to close (issue #1481). It generalizes cleanly to **camp**
(no invented architecture, no characters, modest +0.5 median lift over the incumbent — though
short of a clean pass) but **regresses severely on forest_road**: all 5 blind scorers
independently flagged broken/wireframe/"melted" architecture — the greybox's dense tree-line box
volumes got repainted as disconnected wooden shrine/hut structures with carved doors and
curtains, not trees. This is the SAME invented-architecture failure mode ARM C (the interior
LoRA) produced on this room (CB-FOREST precedent, median 6.0 there) — just worse (median 2.0)
and with a different visual signature (broken wireframe vs solid stone).

**Root-cause hypothesis (new, beyond the original training-data-domain framing):**
forest_road's greybox represents its tree line as adjacent axis-aligned rectangular box volumes
(`forest_road_greybox.png` / `forest_road_greybox_depth.png`) — a coarser, more literally
"architectural-looking" placeholder shape than camp's greybox. The model appears to read blocky
rectangular volumes as buildings regardless of whether its LoRA was trained on interior or
exterior images — a **geometry-shape bias**, not purely a training-domain bias. Edge-recall
(the automated advisory metric) is misleading here: forest_road scored a deceptively HIGH 0.9678
specifically because the candidate preserved the box edges too literally (as walls, not trees),
while camp's genuinely better result scored a LOWER 0.6645 because it correctly reinterpreted
the boxes as rounded organic forms. The overlay images are the binding evidence, not the number
(confirmed both visually and by the 5-scorer panel, independent of the metric).

## What this means for issue #1481

- The OUTDOOR-LORA is a genuine, validated improvement for **camp-class** rooms (open clearings
  with sparse, discrete tree/prop placeholders) but is **not ready to replace** ARM C or be
  declared the fix for forest_road-class rooms (dense linear tree corridors with adjacent box
  placeholders) without either (a) a differently-authored greybox for that structure class
  (more organic/clustered tree footprints instead of axis-aligned boxes), or (b) further LoRA/
  prompt iteration specifically targeting box-shaped placeholder reinterpretation.
- Do not adopt either candidate as a new canonical_plate off this single smoke test. camp's
  result is a borderline-promising lead worth one more iteration (fix the firepit ring artifact);
  forest_road's result should be treated as a confirmed non-fix.

## Cost actuals (record on #1481)

- Training: 1080 CU (job_U3HDsSj7T4aPETy7MRCuX6oK, ~172 min, model_RsWEcQL2NWXwoyEodWVE2vWG)
- Smoke test: 4 anchor mints @ 9 CU = 36 CU + 2 Gemini re-registration passes @ 20 CU = 40 CU
  -> 76 CU
- **Total: 1156 CU (~$22.13 at the ~$0.0191/CU rate implied by the 1080 CU dry-run estimate)**
- Account model-count limit could not be read via API (403, role scope) — noted for any future
  training on this account; if creation/training ever fails on a count-limit error, stop and
  report, do not delete existing models.
