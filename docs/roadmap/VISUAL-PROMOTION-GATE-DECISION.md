> Ratified decision record for the visual-artifact promotion gate. Implemented in PR feat/visual-promotion-gate (promote.py GATE_STRATEGIES + qa/visual_controls_identity.json + qa/build_visual_controls.py). See that PR for the code + the market_square first-run re-score.

# Decision: visual artifact classes through promote.py's gate

Date: 2026-07-08 · Author: Fable (architect) · Confidence: ≥95% (worldos-decide bar met)
Process: first-principles skill — research (/tmp/research-promote-gate-data.md, incl. dry-run) →
diagram → adversarial debate (a-advocate vs b-advocate) → this record.

## Context
PR #1416 promoted the first library/rooms entries by bypassing promote_batch()'s automated gate
(manual GateResult, disclosed): "room" is rejected at three layers (rubric schema, ARTIFACT_CLASSES,
gate constants), text gate assumes 1-5 absolute thresholds + text-canon control bands, and visual
panels score 0-10 with ad-hoc disguised reference frames. Volume forecast: ~40 rooms/month (HV5
cadence) + actor renders + motion reels queued.

## Options considered
- (a) add "room" to ARTIFACT_CLASSES + scale_max column + per-class thresholds in the text store/gate
- (b) per-class GATE STRATEGY in promote_batch + a serialized IMAGE-control registry; visual scores
  stay in their existing landing zone (runs/surface=visual + panel JSONs)
- (c) formalized permanent manual path

## Decision: **(b), sharpened**
1. **GATE_STRATEGIES[class] dispatch in promote_batch.** Text classes → existing evaluate_gate,
   byte-untouched. Visual classes ("room" now) → a NEW visual gate whose PASS rule encodes the
   visual-critic doctrine: (i) deterministic pre-gates PASS (the hard floor — frame-lit, occupancy,
   pin-check; the stylistic G6 staging-law band and reel-only G5 are NOT floors), (ii) control-anchored
   panel present with a REGISTERED disguised real-art control whose same-panel median lands in-band,
   (iii) candidate-vs-control delta on a **two-tier adoption ladder** (amended 2026-07-08):
   - `delta ≥ VIS_DELTA_PARITY (−1.2)` → tier **canonical-candidate** (at statistical PARITY with real
     shipped art — a human may promote to canonical). PASSES.
   - `VIS_DELTA_ADOPT (−2.5) ≤ delta < VIS_DELTA_PARITY` → tier **stable** (adopted-quality). PASSES.
   - `delta < VIS_DELTA_ADOPT (−2.5)` → REJECT.
   NO absolute-threshold pass — absolutes are not citable for images (measured: blind panels score real
   PoE2/BG2 art 3.0-5.6). A SOLE `delta ≥ −1.2` bar was the DESTINATION (parity) bar, not the
   era-appropriate ADOPTION bar; the second tier is the adopted-quality lane between the two measured
   taste anchors (see Calibration).
2. **qa/visual_controls_identity.json** — the image-control registry mirroring the text registry's
   field shape (class, anchor, band, file, provenance, band_ruler, band_prompt_hash) with 0-10
   anchors + reference-frame paths (the already-used poe2_*/bg2ee_* frames, minus the defective
   UI-chrome one). Built by a sibling of build_artifact_controls.py that SHARES an extracted,
   scale-parametrized band helper (answers the two-registries-drift attack with shared CODE,
   separate DATA — the data genuinely differs: text canon vs image frames).
3. **No artifacts-table shoehorn.** Visual scores stay where they land (runs/surface=visual + panel
   JSONs); the visual gate reads the panel JSON via the nomination's source_path (short-circuits
   _artifacts_by_id — contained blast radius). Promotion audit = the serialized GateResult in the
   library entry + the processed-log, same as text.
4. **Contract docs**: promote.py docstring/CLI contract + scores_db ARTIFACT_CLASSES comment updated
   to name the split explicitly so the next room promotion doesn't rediscover the gap.
5. **Rider**: market_square clean-control re-score through the NEW registry (its #1416 control was
   defective) — the new instrument proves itself on day one (decision-by-eval).

## Calibration (the two-tier ladder, 2026-07-08)
The adoption bar `VIS_DELTA_ADOPT = −2.5` is calibrated on the two measured taste-gated panels — the
only two data points that pair a control-anchored delta with a human taste verdict:
- **camp_clearing_night: delta −2.0 → taste-PASS** (adopted, passed both human taste-gates). The
  adoption bar must therefore sit *below* −2.0.
- **market_square: delta −5.0 → taste-REJECT** (its #1416 adoption rested on a defective control; the
  clean-control re-score scored candidate 4.0 vs a real PoE2 control at 9.0).
−2.5 sits between them. `VIS_DELTA_PARITY = −1.2` is the noise law (parity == within-noise of real art)
— the destination bar. Both are single named constants in `promote.py`; a later taste/noise re-measure
moves ONE of them. First real runs through the gate path: **camp → PASS tier=stable** (delta −2.0),
**market → REJECT** (delta −5.0, retained as an `experimental` incumbent, flagged for re-generation).

## Counter-arguments considered (from the a-advocate)
- "control_valid_for_panel is already scale-agnostic; (a) is just two constants + a cap fix."
  → True mechanically, but the GATE SEMANTICS differ: text passes on absolute thresholds AND
  control validity; visual doctrine forbids absolute passes entirely. The gate branches by class
  under (a) anyway — the unified store buys a shared table whose every consumer (band render,
  min(5.0) cap, threshold constants, ±1.2-on-1-5 ledger verdicts) assumes 1-5.
- "Two registries drift by discipline." → Mitigated structurally: one shared band function, one
  shared field schema; only the data files differ. Drift in CODE is what matters; that's unified.
- "(b) forks the chokepoint." → The strategy dispatch is ~10 lines at the top of promote_batch;
  the visual path reads a panel JSON instead of the DB. Smaller blast radius than re-plumbing
  every artifacts-table consumer for dual scales.

## Risks accepted
- Visual promotions won't appear in scores_ledger's "Artifact panels" section (they're visible via
  runs/surface=visual rows + library entries). Future unification possible; not load-bearing now.
- The ±1.2 noise constant is shared across scales by measured convention, not derivation; if a
  visual-specific noise law is ever measured, it changes ONE constant in the visual strategy.

## Reversibility
High: the strategy dispatch is additive (text path byte-identical); the registry is a new file;
deleting both restores today exactly. Signal it was wrong: visual gate passes/fails disagree
systematically with owner taste-gates on frames → re-measure the noise law or thresholds.

## Deferred
- actor-render / motion-reel strategies (add when those classes cadence; the dispatch + registry
  make each a small entry, not a rebuild).
- ledger unification for visual promotions.
