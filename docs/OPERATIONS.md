# OPERATIONS — how ANY agent continues WorldOS from a cold start

> You were told "continue" and given this repo. This page is the whole bootstrap. Read it, then
> the two anchors, then execute. You do not need any prior conversation's context.

## The 60-second orientation

1. **Read `VISION.md`** — what the product is, the North Star (walkable rendered world; text tier
   forever), the pillars, the LOAD-BEARING INVARIANTS (violating one is wrong by definition), the
   quality bars, and the operating principle **decision-by-eval** (no instrument → build the
   instrument first).
2. **Read `docs/roadmap/PRODUCT-ROADMAP.md`** — the three Acts, every sprint (S/W/HV series) with
   its binding gate, the version map, the Owner Gate Register (human-gated items — never block on
   these silently; park `blocked/needs-human` and take other work).
3. **Find the active work**: `gh issue list --label sprint-charter --state open` — charters are
   the live sprints. Each charter lists ORDERED issues with `lane:*` labels, the runnable gate,
   and the invariant checklist. Claim an unclaimed issue in your lane (comment on it), or if no
   charter is active, the roadmap's sequencing section says which sprint is next — author its
   charter from the roadmap section using the existing charters as the template. Never claim an
   issue outside an open sprint-charter's listed lanes, even if it matches your lane label.

## The loop (per issue)

worktree off main → implement ADDITIVELY (honor every VISION invariant) → red-first single-process
tests (`uv run --directory servers/engine python -m pytest <files> -q -p no:xdist`) +
`qa/fast_gate.sh` → push + PR (HEREDOC body) → review-gated merge (CI green + review threads
resolved; validate bot findings against source before acting — they are hypotheses) → prune.
Full detail: the `worldos-dev` skill / `WorldOS-RUNBOOK.md`. Heavy QA runs on the support VM
(`WorldOS-GUI-RUNBOOK.md`); box/Unity work follows `extensions/renderers/unity/CANONICAL.md`
(read it FIRST — canonical state lives there) + the GUI runbook's box discipline.

## Run economics — match the instrument to the question

**★ QA-economics v2 (owner-ratified 2026-07-06) — playtests are BATCH evidence, never PR gates:**
- **An hour-scale playtest duo is NEVER a per-PR validation step.** The 24-beat Opus ruler duo runs
  ONCE per merged BATCH (e.g. at the end of a sprint/push), as release evidence — not per PR, not per
  iteration. If you are about to launch a long duo to check one PR's effect, stop: that question has a
  cheaper instrument.
- **Default PR validation ladder** (stop at the first rung that answers): Tier-0 `qa/fast_gate.sh`
  (free, seconds, EVERY change) → Tier-1.5 `qa/mechanism_probe.sh` (~$1, cue/mechanism questions) →
  `qa/run_combat_sprint.sh` (~2 min) when the change is combat-adjacent. LLM story lenses are NOT run
  per-PR.
- **Story-quality iteration runs in the BACKGROUND on GLM** (off-budget z.ai; batch-read the results at
  the next natural checkpoint) and never blocks the build critical path. Story polish is a later-pass
  concern once the system is feature-complete; the engine/renderer/pipeline lanes keep moving.

Spend the cheapest instrument that answers the question (tier table + honest signal accounting:
`docs/qa/FAST_GATE.md`):
- **MECHANISM iterations** ("does obligation cue X fire? does the DM act on it?") → the **Tier-1.5
  mechanism probe** FIRST (`qa/mechanism_probe.sh <name> <fixture>`; ~$1 / ~10 min, deterministic
  verdict) — that probe verdict is the answer for most iteration loops. Reach for a **GLM 12-beat
  duo** as a live corroborator ONLY when the probe result is surprising or you need a richer live
  transcript than the probe's deterministic tally gives you — off-budget z.ai via the glm profile
  (`WORLDOS_DM_MODEL=glm-5.2 qa/run_duo.sh … 12`); it is CONDITIONAL, not a mandatory second step
  after every probe (that would erase the cost/time savings the tier exists for). Never burn a
  scored Opus duo to answer a wiring question.
- **RULER measurements** (story ≥ 4.3, mech ≥ 4.5, release evidence) → a **24-beat Opus duo ONLY**.
  The mechanism tiers are tripwires, never the quality verdict.
- **Sonnet is NEVER the DM** (measured: story 2.9 vs Opus 4.1, AND slower) — it is the scorer /
  worker / AI-playtester model. Opus drives scored DM runs; GLM is the off-budget batch/corroborator DM.
- **Stamp provider + methodology on every `scores_db` row** (`dm_model`, `methodology`, and a
  provider note when GLM/z.ai drove it) so a GLM/probe row is never mistaken for a clean Opus ruler
  run. A Tier-1.5 probe row specifically stamps `surface="engine-duo"` + `scorer_model="derived"`
  (deterministic verdict, no LLM lens) alongside `methodology="mechanism-probe"` — the full
  identity `qa/mechanism_probe.sh` writes — so it can never be confused with an ordinary scored duo.

## The traps that cost real time (measured; do not relearn)

- New behavioral-gate `chk()` → MUST update `qa/BEHAVIORAL_GATE_TAXONOMY.json` + (FATAL) the
  `qa/gate_corpus/` manifest — these run in the `qa-release-gate-tests` CI lane, NOT fast_gate
  (passes locally, fails CI).
- `required_conversation_resolution` blocks all-green PRs on unresolved bot threads — resolve with
  evidence; don't churn commits (each commit spawns a new bot round).
- Never cite a scored run without an infra-health check (failed-beats, quota sentinels,
  `.infra_invalid.json` — the run-invalidation guard stamps these).
- A release-gate measurement pins a frozen SHA; DM = Opus pinned explicitly; mech = combat-sprint
  median (n≥3), never one duo; panels are control-anchored (absolute numbers are never citable).
- Log every scored run to `qa/scores_db.py`; scores are measurement, never the target.

## Standing cadences

- **Harvest flywheel (once HV3+ lands):** every scored run auto-nominates artifacts; nightly
  artifact-scoring batch; weekly curation; backdrop cadence = 2 environments a night, panel-gated
  (roadmap §4c/HV5).
- **Release trains:** cut per the roadmap's version map when a sprint's gate passes; CHANGELOG per
  merge batch; GitNexus re-index once per merge batch.
- **When you need a decision** and the answer isn't in VISION/roadmap: run the `worldos-decide`
  skill (anchor → scorecard → adversarial check → 95% gate). If the decision lacks an eval,
  building the eval IS the next task. Escalate to the owner ONLY genuine taste/priority/business
  calls — and bring a recommendation.

## Definition of "you are done for now"

There is no "done" — there is the next gate. A session ends cleanly when: the claimed issues are
merged with their gates green, the charter/roadmap state is updated (issues closed with evidence,
next items noted), every scored run is in the ledger, and anything blocked is labeled with what
unblocks it. Then take the next issue.
