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
   charter from the roadmap section using the existing charters as the template.

## The loop (per issue)

worktree off main → implement ADDITIVELY (honor every VISION invariant) → red-first single-process
tests (`uv run --directory servers/engine python -m pytest <files> -q -p no:xdist`) +
`qa/fast_gate.sh` → push + PR (HEREDOC body) → review-gated merge (CI green + review threads
resolved; validate bot findings against source before acting — they are hypotheses) → prune.
Full detail: the `worldos-dev` skill / `WorldOS-RUNBOOK.md`. Heavy QA runs on the support VM
(`WorldOS-GUI-RUNBOOK.md`); box/Unity work follows `extensions/renderers/unity/CANONICAL.md`
(read it FIRST — canonical state lives there) + the GUI runbook's box discipline.

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
