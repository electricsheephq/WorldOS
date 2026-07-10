# EVAL-UPGRADE — instrument sprint (#1386)

Catch player-visible defects automatically so the owner stops being the test harness. Three
instruments for the three real failures this week.

## A. Absolute grid↔paint coherence gate — `qa/check_grid_paint_coherence.py`

The failure: the owner walked onto a painted sarcophagus whose ENGINE cells were legal but whose PAINT
sat ~3/4 cell off the grid footprint. The existing `check_plate_drift.py` is RELATIVE (catches a prop
that MOVED between two plates) and passes an always-off-grid prop forever. This gate is ABSOLUTE: it
regenerates the grid's greybox structural prior from the manifest geometry and localises each prop's
silhouette in the plate via edge NCC, failing if any hard-silhouette prop is painted >0.5 cell off its
authored footprint.

**Verdict on the CURRENT crypt (the honest red-first-against-reality):** `INCOHERENT` — see
[`A_crypt_current_INCOHERENT.json`](A_crypt_current_INCOHERENT.json).

| prop | status | offset (cells) | NCC |
|------|--------|----------------|-----|
| sarcophagus | PASS | 0.35 | 0.29 |
| pillar_l | **DRIFT** | **1.20** | 0.27 |
| pillar_r | **UNLOCATED** | 1.11 | 0.12 |

This matches the player-alignment lane's finding on the same plate (`qa/evidence/1469/iter3`): the final
staging pass "shifted the left half off the greybox" (registration recall 0.708). The left-side pillars
are painted >1 cell off the cells the engine keys collision to — the sarcophagus class of defect, now
machine-caught.

**Synthetic aligned control:** `COHERENT`, worst offset 0.03 cell — see
[`A_crypt_aligned_COHERENT.json`](A_crypt_aligned_COHERENT.json).

**Manifests:** reuses the already-landed `qa/room_manifests/crypt_dense_v1.cells.json` +
`camp_clearing_night_v2.cells.json` (the #1462 seed manifests, regeneratable via
`qa/build_room_manifest.py`) — no new manifest files were needed.

**CI:** the deterministic test suite (`qa/test_grid_paint_coherence.py`: aligned PASS + synthetic shift
CAUGHT + current-crypt INCOHERENT anchor + size guard) is wired into `ci.yml`'s `paint-drift-gate` job.

**Scope / honesty:** the edge localiser is reliable for hard-silhouette architectural props (the crypt
class). Tall organic props (tree foliage) match a box silhouette poorly → low-confidence offset, so a
blocking `gate-recipes` sweep over organic-heavy live plates is NOT wired (it would false-red the camp
trees at NCC ~0.13–0.28); the CLI reports them as a diagnostic only. This is the one open follow-up.

## B. Journey capture + factual VQA — `qa/journey_eval.py`

The failure: a T-posing actor and a wrong-plate bundle reached an owner build; the aesthetic panels
scored beauty AROUND them. This harness walks the playable loop via the #1466 QA cell-click channel (the
same box player `player_smoke.sh` uses), captures a frame per step + both sides of each transition, then
asks factual YES/NO questions of every frame (`qa/journey_vqa_questions.md`, YES=defect) via one sonnet
`claude -p` per frame. ANY yes = journey FAIL naming the offending frame → `journey_verdict.json`.

- Path-gen + VQA aggregation + verdict are pure and unit-tested with a stub scorer
  (`qa/test_journey_eval.py`, 7 tests green).
- **Live VQA pipeline proven** (real `claude -p` per frame, not the stub) over two committed crypt
  frames — see [`B_vqa_proof_verdict.json`](B_vqa_proof_verdict.json):
  - `1_crypt_rest_idle.png` (the party at rest) → **all five flags false** (a legitimate multi-PC party
    is correctly NOT flagged).
  - `plate_conditioned_crypt.png` (bare backdrop, no cast) → **`missing_or_cloned: true`** — the harness
    correctly catches "no character present".
  - This live run surfaced + fixed a real question-design bug: the original "singular" phrasing
    false-flagged a 4-PC party; `missing_or_cloned` now flags only *nobody there* or *the same character
    cloned*, never a normal party.
- The BOX capture (`journey_capture.js` driving the live player) runs on the box when the #1386 claim
  frees (attach a fresh `journey_verdict.json` + 3 sample frames here). The VQA half is already verified
  against reality above.
- Invocation documented in `qa/UI_PLAYTEST.md`; sample plan `qa/journey_plans/camp.json`.

## C. Panel factual-defect checklist — `qa/plate_loop.py`

Additive: every blind panel scorer now answers a 5-item factual defect checklist
(on-prop/T-pose/floating/duplicate/missing) BEFORE scoring, emitted as machine-readable `defects` flags
in the panel prompt. Scoring scales unchanged. Regression-tested
(`test_panel_prompt_carries_factual_defect_checklist`).
