# CAMP-PASS2 — one more refinement pass on camp_clearing_night (issue #1481)

**Goal:** push the OUTDOOR-LORA (`model_RsWEcQL2NWXwoyEodWVE2vWG`) camp result past the borderline
6.5 (`qa/evidence/plate-sprint/outdoor-lora/`) toward the >=7.0 converged bar, specifically attacking
the recurring firepit-ring ground artifact. Max 2 iterations, best-of-3 each, per the mandate.
**Result: NOT CONVERGED — ceiling reached at median 6.0 both iterations.**

| file | what |
|------|------|
| `iter1/` | iteration 1: best-of-3 anchor mints (lorasScale 0.70/0.65/0.60) with a firepit-pin prompt clause, Gemini re-registration, gates, panel |
| `iter2/` | iteration 2: reused iter1's 3 anchors, re-ran Gemini with a stronger structure-lock-EXCEPTION clause authorizing repaint of the ring specifically, gates, panel |
| `gallery.html` / `gallery.html.rows.json` | owner-visible contact sheet, both iterations |

## Per-iteration results

| Iter | Candidate median | Incumbent | Control (in-band 6.8-9.2) | Firepit lever tested | Result |
|---|---|---|---|---|---|
| 1 | **6.0** | 7.0 | 9.0 (valid) | (a) soft prompt-pin ("keep the fire-ring exactly as composed") at BOTH the base mint and Gemini stage + (b) lorasScale 0.70/0.65/0.60 sweep + (c) best-of-3 rejection at the anchor stage | NOT CONVERGED |
| 2 | **6.0** | 7.0 | 9.0 (valid) | (a) v2: explicit STRUCTURE-LOCK EXCEPTION authorizing Gemini to repaint the ring region specifically (reused iter1's 3 anchors, no new anchor spend) + (c) best-of-3 rejection at the Gemini stage | NOT CONVERGED |

## Which lever fixed the firepit artifact

**None of them.** This pass tested all three prescribed levers, including a stronger variant of (a) in
iteration 2, and the concentric-ring ground artifact around the campfire was visually present, to a
similar degree, in every generated candidate that reached the Gemini re-registration stage: **3 raw
anchors inspected pre-Gemini** (best-of-3 rejection, iter1: lorasScale 0.70/0.65/0.60, all similarly
affected) **and 4 total Gemini-styled candidates** (1 in iter1 on the selected anchor + 3 in iter2 on
all 3 iter1 anchors, since iter1 only spent the Gemini pass once on the winner of its anchor-stage
best-of-3, while iter2 ran Gemini on all 3 to test the stronger override across the full anchor set):

- **(a) prompt pinning** — a soft "keep it as composed" clause (iter1) and an explicit authorized
  override naming the ring a "rendering artifact, not authored geometry" (iter2) both failed to
  suppress it. Gemini's general structure-lock behavior appears to treat the ring as intrinsic to
  "campfire-lit ground" regardless of instruction wording.
- **(b) lorasScale 0.6-0.7** — swept 0.70/0.65/0.60 at the anchor-mint stage (seed 42 fixed). All
  three anchors showed the ring to a visually similar degree; lorasScale in this band does not gate
  its severity. iter2 confirmed this a second way: the SAME 3 anchors, run through a *different*
  Gemini prompt, produced the SAME outcome.
- **(c) best-of-N rejection** — never found a clean winner in either iteration, because the defect
  was present across the board rather than concentrated in a subset of candidates. Selection instead
  had to fall back to secondary quality signals (surrounding ground cleanliness, absence of stray
  artifacts) since the firepit ring itself did not discriminate between candidates.

**Root-cause read (unchanged from the original smoke test's hypothesis, now more strongly evidenced):**
the ring is most likely a learned prior in how the model renders campfire ground-light falloff —
reinforced once it appears in the minted anchor and then treated as structure to preserve — not a
prompt-controllable defect within this recipe's Gemini re-registration stage. A real fix would need a
lever this pass was not scoped to test (e.g. isolated inpainting of the ring region as a post-process,
retraining/adjusting the base LoRA away from radial falloff, or swapping the base mint's control image
away from a bare point-light depth cue).

## Best candidate path

`qa/evidence/plate-sprint/camp-pass2/iter1/gemini/candidate_iter1_scale060.jpg` and
`qa/evidence/plate-sprint/camp-pass2/iter2/gemini/candidate_iter2_scale060.jpg` are tied at median 6.0;
neither is a clean improvement over the already-evidenced outdoor-lora smoke-test candidate (6.5) or
the adopted `camp_clearing_night_v2` incumbent (7.0 this panel). **Do not adopt either** — this PR is
evidence-only, per the mandate.

## Converged

**No.** Candidate median 6.0 < the >=7.0 target both iterations, and slightly below the prior
borderline 6.5. The firepit-ring artifact is a confirmed, reproducible ceiling on the
`model_RsWEcQL2NWXwoyEodWVE2vWG` + flux-ControlNet + Gemini-restyle recipe for `camp_clearing_night`,
not a tuning gap the mandate's three levers could close in 2 iterations.

## Advisory registration note (#1491)

`camp_clearing_night` is an organic/outdoor room class — edge-recall vs the greybox is ADVISORY only
per #1491 (content-blind, anti-correlated with faithful organic reinterpretation on this class). Both
iterations' overlays (`iter1/overlay_iter1_scale060.jpg`, `iter2/overlay_iter2_scale060.jpg`) show good
structural alignment despite sub-0.95 recall numbers (0.8253, 0.5689, computed at the correct 1344x768
plate-contract resolution) — consistent with #1491's own findings, not treated as a gate failure.
`check_plate_drift` against `qa/room_manifests/camp_clearing_night_v2.cells.json` also shows most props
DRIFT for both candidates; that manifest is authored against the ADOPTED v2 plate's own composition
(walls/cabin/torches), not the plain contract greybox our candidates are generated from — the same
manifest/greybox mismatch camp-armB's candidate hit against this identical pairing. Advisory only,
noted in each iteration's `panel_verdict.json`.

**Correction (adversarial PR review, 2026-07-10):** the two selected candidate files were originally
committed at Gemini's native 2744x1568 output resolution rather than the room's 1344x768 plate
contract, which caused `check_plate_drift`'s manifest check to hard-reject on a size mismatch
(`checked=0`) instead of running a real comparison. Both files have been downsampled to 1344x768 and
the registration/drift numbers above are recomputed at the correct size; the panel scores are
unaffected since scoring was done by direct visual inspection, not off image metadata. The corrected
dimensions are auditable from text alone via
`iter{1,2}/gemini/candidate_iter{1,2}_scale060.dimensions.json` (width/height + sha256 of the
committed JPEG), added per a follow-up review request rather than requiring a reviewer to execute
image code to confirm the size claim.

## Cost actuals (record on #1481)

- Iteration 1: 3 anchor mints @ 9 CU = 27 CU + 1 Gemini re-registration @ 20 CU = 20 CU -> 47 CU
- Iteration 2: 0 new anchor spend (reused iter1's 3 anchors) + 3 Gemini re-registrations @ 20 CU = 60 CU
- **Total: 107 CU (~$2.04 at the ~$0.0191/CU rate implied by the outdoor-lora dry-run estimate)**
