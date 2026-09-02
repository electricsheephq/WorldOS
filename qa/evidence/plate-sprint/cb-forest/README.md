# CB-FOREST — PLATE SPRINT synthesis test evidence

**Question:** does the ARM C LoRA (`model_G379oza2qhm6MkqDrtTvvmmw`, "WorldOS Painterly
Architectural (FLUX)", issue #1481) mint better outdoor anchors than the z-image+house-LoRA
layered bootstrap (iter3), lifting the ARM-B `forest_road` pipeline past the outdoor **6.0 cap**?

**Result: NO — NOT converged, and registration regresses.** Panel median **6.0**, flat vs iter3's
6.0 and vs the outdoor incumbent's own established 6.0 bar; below the >=7.0 sprint bar. Full
findings in `findings.json`.

| file | what |
|------|------|
| `anchor_candidates_contact_sheet.jpg` | the 3 ARM-C-minted anchor candidates (seeds 42/7/99) |
| `anchor_winner_seed99.jpg` | the picked anchor (seed 99 — least architecture-contaminated of the 3) |
| `armb_r1_vs_r2_bestof2.jpg` | the ARM-B re-registration best-of-2 (Gemini edit, referenceImages=anchor) |
| `candidate_cb_forest_r1.jpg` | the final candidate (r1, best-of-2 winner) |
| `overlay_candidate_vs_greybox.jpg` | greybox structural edges (magenta) composited over the candidate |
| `greybox_control.jpg` | the forest_road ControlNet-depth control image |
| `config.json` | the `qa/plate_loop.py` config used to gate + stage the panel |
| `panel_verdict.json` | the blind 5-scorer panel verdict (raw scores + medians) |
| `findings.json` | full findings, anchor/best-of-2 eyeball reads, registration numbers, convergence verdict |

## Headline findings

1. **ARM C's architectural-interior training bias overpowers the depth ControlNet + an explicit
   anti-architecture prompt clause on all 3 anchor mints.** Seeds 42 and 7 render as near-complete
   crypt/dungeon corridors (seed 7 even has a human figure); seed 99 was the least-bad but still
   invents a dressed-stone tunnel mouth. The LoRA's 10/10 architectural-interior training set
   (crypt/church/tavern/camp-interior/undercroft) dominates at the style-first control settings
   (str 0.65/end 0.6) the recipe specifies.
2. **The invented architecture propagates through the ARM-B re-registration pass** —
   `referenceImages` is a content anchor, not a pure style anchor (consistent with PR #1490 and
   camp-armB's own finding 1). Best-of-2 run r2 additionally invented a *new* stone wall beyond
   what the anchor itself carried; r1 (fewer added elements) was picked.
3. **Registration is measurably worse than iter3 on the identical base+prompt+recipe:**
   edge-recall 0.4519 vs iter3's 0.9902 — the only variable changed is the anchor image. Advisory
   per #1491, but the overlay corroborates a real regression (an invented tunnel with no greybox
   counterpart), not just a metric artifact.
4. **Panel: candidate ties the 6.0 outdoor cap exactly; all 5 scorers unanimously flagged the
   invented architecture / artifacts.** Median 6.0 (raw `[5,5,6,6,6]`), same as iter3 and the
   outdoor incumbent's own cross-lane bar. The disguised real-art control scored outside its
   6.8–9.2 validity band this run (scorers penalized it for genre mismatch, not slot confusion —
   a panel-validity caveat, not a candidate signal). Note: this config reuses
   `camp_clearing_night_v2.jpg` as both the disclosed house-best reference AND the blind incumbent
   slot, so the incumbent's 9.0 read here is inflated by self-identity, not an independent score.

## Convergence verdict

Panel median **6.0** (< 7.0 gate) · registration edge-recall **0.4519** (advisory, < 0.95 gate, and
worse than iter3's 0.9902 on the identical recipe) → **NOT converged.** ARM C is validated for its
intended char-free architectural/interior use case (issue #1481) but does **not** generalize to,
and actively **regresses** vs, the already-adopted z-image+house-LoRA layered-anchor bootstrap
(iter3) for the outdoor `forest_road` class. Adoption is out of scope for this lane (evidence-only,
consistent with every prior plate-sprint arm).
