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

The gate checks each prop's **FOOTPRINT** (the impassable floor cells collision keys to), NOT its
up-screen **SILHOUETTE** (occlusion) — those diverge under the iso projection. #1505's owner-playtest-#4
correction is the canonical case: the sarcophagus silhouette rises to cols3-9×rows3-7 (open floor
*behind* the coffin) while its real floor footprint is cols2-7×rows7-9 (where feet land on the painted
box). The manifests now encode **both** per prop.

**Verdict on the CURRENT (deployed) crypt plate `crypt_armb_iter3_v1` (honest red-first-against-reality):**
`INCOHERENT` — see [`A_crypt_current_INCOHERENT.json`](A_crypt_current_INCOHERENT.json).

| prop | status | offset (cells) |
|------|--------|----------------|
| pillar_l | **DRIFT** | 0.79 |
| pillar_r | **UNLOCATED** | 0.86 |
| sarcophagus | **UNLOCATED** | 1.17 |

#1491 proved this plate carries real grid↔paint drift that no shared transform can realign; #1505 could
only recalibrate the sarcophagus footprint to the paint, not remove the residual drift. The gate reads
it INCOHERENT — the defect the owner walked onto, machine-caught. **Synthetic aligned control:**
`COHERENT`, worst offset 0.05 cell — [`A_crypt_aligned_COHERENT.json`](A_crypt_aligned_COHERENT.json).

**Manifests — greybox-derived where geometry exists (owner playtest #5):**
- `tools/derive_room_manifest.py` DERIVES a manifest (footprint + occlusion + walkable) from an
  `export_scene_grid` geometry JSON, via point-in-polygon of each cell's grounded projection against the
  prop's box silhouette (#1505 generalised). `qa/room_manifests/forest_road.cells.json` is generated this
  way (31 props, 420 occlusion cells, 68 walkable) and is **COHERENT against the very greybox its geometry
  describes** — the loop closed at the source.
- `crypt_dense_v1` (deployed grid, `seed_gfx_combat.py` + #1505 footprint) and `camp_clearing_night_v2`
  (W6.2 authored grid, incl. campfire/bedrolls/logs/crates footprints — the owner walked through the fire)
  are flagged `derivation: "measured"` (reconstructed from measured calibrations); geometry-JSON
  derivation for them is a follow-up.

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
- **Live VQA pipeline exercised** (real `claude -p` per frame, not the stub) over two committed crypt
  frames — see [`B_vqa_proof_verdict.json`](B_vqa_proof_verdict.json). This proves the PIPELINE MECHANICS
  end-to-end (image read → per-flag YES/NO → aggregation → verdict) + the missing-character path; it does
  NOT yet exercise discrimination on an in-loop defect (T-pose / wrong-plate / on-prop) — that needs the
  box run on real gameplay frames (the claim in-repo is scoped to the mechanics, not full discrimination).
  - `1_crypt_rest_idle.png` (the party at rest) → **all five flags false** (a legitimate multi-PC party
    is correctly NOT flagged — the negative case).
  - `plate_conditioned_crypt.png` (a bare BASE PLATE, no cast by construction) → **`missing_or_cloned:
    true`** — a sanity check of the positive path (catches "no character present"), not a gameplay-defect
    catch.
  - This live run surfaced + fixed a real question-design bug: the original "singular" phrasing
    false-flagged a 4-PC party; `missing_or_cloned` now flags only *nobody there* or *the same character
    cloned*, never a normal party (and is not asked of establishing 'start' shots).
- The BOX capture (`journey_capture.js` driving the live player) runs on the box when the #1386 claim
  frees (attach a fresh `journey_verdict.json` + 3 sample frames here) — that is where discrimination on
  real in-loop defects is validated.
- Invocation documented in `qa/UI_PLAYTEST.md`; sample plan `qa/journey_plans/camp.json`.

## C. Panel factual-defect checklist — `qa/plate_loop.py`

Additive: every blind panel scorer now answers a 5-item factual defect checklist
(on-prop/T-pose/floating/duplicate/missing) BEFORE scoring, emitted as machine-readable `defects` flags
in the panel prompt. Scoring scales unchanged. Regression-tested
(`test_panel_prompt_carries_factual_defect_checklist`).
