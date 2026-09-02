# CAMP-ARMB — PLATE SPRINT Phase 2 evidence

**Goal:** converge `camp_clearing_night` to a styled+registered plate using the ARM B recipe
(flux-1-dev + ControlNet-depth base -> Gemini instruction-edit style pass), proven on `crypt`
(issue #1481). **Result: NOT converged** vs the bar (registration >=0.95 AND panel median >=7.0),
but the recipe DOES generalize with one required change (drop `referenceImages`) — full findings
in `findings.json`.

| file | what |
|------|------|
| `greybox_control.jpg` | the camp ControlNet-depth control image (W6.3b / #1470), camera-pinned 1344x768 |
| `candidate_iter3_g3_BEST.jpg` | the best candidate across 5 tested arms / ~20 generations (registration 0.9439) |
| `overlay_candidate_vs_greybox.jpg` | greybox structural edges (magenta) composited over the best candidate — the registration check, visually confirms good alignment despite the sub-0.95 metric |
| `arm_iter1_cryptref_LEAKED_crypt.jpg` | referenceImages=[crypt_dense_v1] reproduced a full crypt scene (sarcophagus, skulls, pillars) on the camp base — finding 1 |
| `arm_iter1_campref_LEAKED_v2composition.jpg` | referenceImages=[camp_clearing_night_v2] (same-room anchor) reproduced v2's own cabin+wall composition instead of this greybox's layout — finding 1 |
| `style_pass_prompt_winning.txt` | the winning no-referenceImages style-pass prompt (text-only style + scene-content grounding + ARM B's verbatim structure-lock clauses) |
| `config.json` | the `qa/plate_loop.py` config used to gate + stage the panel for the best candidate |
| `panel_verdict.json` | the counted (round 2) blind 5-scorer panel verdict |
| `findings.json` | full iteration log, all 5 tested arms, root causes, and the convergence verdict |

## Headline findings

1. **`referenceImages` is a CONTENT anchor, not a STYLE anchor.** Both `crypt_dense_v1` and the
   same-room `camp_clearing_night_v2` leaked their own scene content instead of just their
   painterly style. ARM B's crypt run never surfaced this because crypt's reference and target
   were the same room. Fix: drop `referenceImages`, carry style as text only.
2. Needed an explicit scene-content-grounding clause (name the blocky greybox placeholders as
   trees/boulders) once `referenceImages` was dropped, or the base template's crypt-authored
   boilerplate ("pillar carvings", "stone relief") got taken literally on an outdoor scene.
3. Base ControlNet `control_strength` 0.85 beat both 0.7 (ARM B's crypt default) and 0.92 —
   non-monotonic, room/base-specific.
4. A mid-run course-correction (citing a sibling `forest-road` lane) suggested camp_clearing_night_v2
   as reference is "benign" and recommended an explicit dimetric-camera-lock clause. Both were
   tested directly on real generations: the same-room reference still hijacked composition (0.64-0.84
   registration across 4 attempts), and the camera-lock clause **regressed** registration on this
   base (0.62-0.86 vs the plain prompt's 0.9439) because this base never had the camera-drift
   defect the sibling lane found on its own base. Documented rather than silently adopted.
5. Edge-recall vs greybox may undercount faithful organic reinterpretation of a boxy authored
   placeholder (a tree trunk naturally sheds edge pixels vs. a box) — the visual overlay shows
   good real alignment despite the metric sitting at 0.9439. Reported as the literal gate result
   (FAIL) with this caveat, matching the sibling lane's own advisory framing.

## Convergence verdict

Registration **0.9439** (< 0.95 gate) · panel median **6.0** (< 7.0 gate, vs incumbent
`camp_clearing_night_v2` at 7.0 and a valid real-art control at 9.0) → **NOT converged.** Adoption
is out of scope for this lane (separate gated process); this PR is evidence-only.
