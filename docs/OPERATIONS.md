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
3. **Find the active work**: `gh issue list --label active-sprint --state open` — exactly ONE issue carries `active-sprint` (the queue head; move the label at every charter transition). `gh issue list --label sprint-charter --state open` lists ALL charters — charters are
   the live sprints. Each charter lists ORDERED issues with `lane:*` labels, the runnable gate,
   and the invariant checklist. Claim an unclaimed issue in your lane (comment on it) — **`[EPIC]`
   issues named in the ACTIVE charter's ordered list ARE claimable directly** (the live lane must
   stay claimable; do not treat "epic" as a reason to skip it). If no charter is active, the
   roadmap's sequencing section says which sprint is next — **writing that next charter from
   `docs/roadmap/PRODUCT-ROADMAP.md` §S(N+1) is the next task, never a stop**, using the existing
   charters as the template. Never claim an issue outside an open sprint-charter's listed lanes,
   even if it matches your lane label. `docs/ACTIVE-GOAL.md` is the standing any-agent driver and
   `docs/roadmap/NOW.md` is the you-are-here surface — read both alongside this page (both land in
   PR #1385; reference by path regardless of merge status).

## Merging

Once checks are green and review threads are resolved: `gh pr merge <n> --squash --auto`. Auto-merge
fires normally — the earlier repo-wide "hang" (#1389, closed) was a **red required Woodpecker
`qa-release-gate-tests` context**, not a GitHub bug: a stale `EXPECTED_SC` ruler pin made the required
check red on every PR while GitHub Actions stayed green, so auto-merge correctly refused to merge.
Resolved by #1431 (restamp) + #1434 (GHA↔Woodpecker list parity + static guard). `--admin` is
**emergency-only** (declare why in a PR comment + file a follow-up) — it bypasses branch protection.
Never push-and-abandon a PR: shepherd every PR you open to merged (or explicitly parked/blocked) before
ending your turn.

## The loop (per issue)

worktree off main → implement ADDITIVELY (honor every VISION invariant) → red-first single-process
tests (`uv run --directory servers/engine python -m pytest <files> -q -p no:xdist`) +
`qa/fast_gate.sh` → push + PR (HEREDOC body) → review-gated merge (CI green + review threads
resolved; validate bot findings against source before acting — they are hypotheses) → prune.
Full detail: the `worldos-dev` skill / `WorldOS-RUNBOOK.md`. Heavy QA runs on the support VM
(`WorldOS-GUI-RUNBOOK.md`); box/Unity work follows `extensions/renderers/unity/CANONICAL.md`
(read it FIRST — canonical state lives there) + the GUI runbook's box discipline.

**Delegation notes:** agents working in the canonical checkout (not a worktree) MUST `git checkout
main` before finishing — two measured pull-aborts came from a session ending stranded on a feature
branch, which then failed the next agent's `git pull` on that checkout.

## Run economics — match the instrument to the question

**★ Self-processing watchers (the no-babysitting contract, 2026-07-08):** any long QA lane (duo / sweep /
probe batch) is launched WITH a watcher script that runs the ENTIRE verdict pipeline itself — infra-health
(rate-limit threshold, beat-count completeness, quota sentinels, `.run_infra_invalid`) → deterministic
behavioral gate → engagement tally → a decision-ready digest — and wakes the orchestrator ONCE.
- **Infra-fail ⇒ NO citable row.** A watcher that detects contamination writes a `*CONTAMINATED/needs-rerun`
  marker (never lens numbers) and says so in its digest. The 'contaminated run cited as clean' failure class
  dies here, mechanically.
- **The watcher IS the wake.** When a self-processing watcher owns the wait, the orchestrator arms NO
  keepalive ticks and deletes any keepalive sentinel — one cold start on the wake is cheaper than a night of
  tick-wakes. Orchestrator wakes are for DECISIONS, not beat-counting.
- **Ruler runs are SOLO-TENANT:** no concurrent sweeps/agent fan-outs on the same Anthropic pool while a
  ruler duo runs (measured: co-tenancy contaminated 2 of 3 gate runs, 2026-07-06/07).

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
- **HV3 promotion (`tools/library/promote.py --batch`):** reads `qa/nominations.jsonl` → threshold
  gate (overall ≥4.0, no dim <3.0, control-valid → `stable`; `canonical` = human-only) → writes the
  pack-shaped `library/`. It is the SOLE writer of `library/` and never edits room_recipes.json or
  the asset registry. **Bootstrap (until HV5's auto-nominator exists):** hand-author the queue — one
  JSON line per `artifact_id` (optional `source_path`/`license`/`curation_note`), sourced from HV2's
  `qa/artifacts_out/<campaign>/**/*.json`. promote.py invents no nomination heuristic. Idempotent
  (`library/.promoted.jsonl` marker); exits 0 with zero promotions. Offline (scorer down / unscored
  noms): `--dry-run` (gate preview, writes nothing) or `--skip-unscored` (promote only already-scored
  rows). Validate with `python3 tools/library/library_lint.py`.
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

## The evidence rule (visual work — owner-ratified 2026-07-08)
Visual claims need pixels reviewers can SEE. Any issue or PR about graphics/animation/rendering
(actors, plates, poses, lighting, viewer UI) MUST attach still-frame evidence that is visible to
reviewers: drag-dropped into the issue, or committed to `qa/evidence/<number>/` on the PR branch
(≤400KB/frame, ≤6 frames; JPEG fine). Local machine paths do not count — agents and bots cannot
read them. Motion defects: a numbered still series is the primary artifact (agents read stills
reliably; GIFs are optional, for humans). Pair frames with qa/visual_pregate.py output when the
change touches placement/pose/scale. Use the `graphics-defect` issue template; the PR template's
Visual Evidence section applies to every visual-affecting PR and requires frames to be **embedded
inline** (markdown image syntax), not just linked — the owner browses PRs and should never have to
click through to a file listing to see a pixel. See qa/evidence/README.md.

## The Universal Run Contract (owner-ratified 2026-07-08 — every run type, no exceptions)
Every run — engine test, probe, sprint, duo, sweep, playtest, render, panel, generation, extraction,
promotion, rollup — closes with the SAME five steps. A run that skips a step didn't happen.
1. **HEALTH** — deterministic infra check BEFORE reading any result (throttle/crash/auth sentinels,
   beat completeness). Contaminated ⇒ a `*CONTAMINATED` marker, never a citable row.
2. **EVIDENCE** — the run's primary artifact captured where reviewers can SEE it: frames →
   `qa/evidence/<n>/` (committed) or the issue; transcripts/manifests → the run dir. Local-only
   paths don't count (the evidence rule above).
3. **SCORE** — one scores-ledger row (`qa/scores_db.py add_run` or the artifacts table) with
   surface + provenance (model, SHA, panel id). No row = no run.
4. **VERDICT** — the run-type's bar applied and stated in its format (PASS/FAIL or score-vs-bar +
   one line), posted where the work item lives (PR/issue comment).
5. **POINTER** — the state surface updated: the lane's charter/issue for lane runs, the routing
   ledger for dispatches, `docs/roadmap/NOW.md` at session close.

**The registry:** every run type has a row in `docs/RUNBOOK-INDEX.md` (runner, tier, required
evidence, scores surface, owning skill/runbook, last-verified). Adding a NEW run type = adding a
row + wiring the five steps. Changing a runner = updating its row. Dispatch packets for any run
MUST name the run type's row so the executing agent inherits the contract.

## Worktree discipline (owner-ratified after #1516 — three lanes' live worktrees deleted mid-session)

**A lane may remove ONLY the worktree it created, by its exact path.** #1516 measured three
concurrent lanes (probe-placement, an owner-play session, true-greybox) losing their
`~/WorldOS-worktrees/*` directories mid-run to a sibling process's cleanup step — suspected cause: a
broad `git worktree remove --force` with a glob/wrong var, or an `rm -rf` sweep, instead of a
single named-path removal. Recoveries succeeded only because of commit-early discipline; each still
cost a rebuild/regeneration round, and one recovery briefly killed the owner's live session.

- **Broad prunes are forbidden mid-session.** Never `git worktree prune`, never glob-remove
  `~/WorldOS-worktrees/*` or `~/worldos-worktrees/*`, never a bare `rm -rf` over the worktrees
  parent dir. Remove exactly the one path you created, when you created it, and nothing else.
  This applies even when you're "just cleaning up" — a stray worktree from a lane you don't
  recognize is not yours to delete.
- **`.worldos-keep` marker = never touch, full stop.** Long-lived shared worktrees (e.g.
  `wt-owner-play`) carry a `.worldos-keep` file at their root. Any cleanup script or dev-loop
  finish step MUST skip any tree containing that marker — check for it before any remove, even a
  single-path one you believe is safe.
- **Commit-and-push-early stays the recovery guarantee.** A worktree that gets swept mid-session is
  only a lost *regeneration round*, never lost work, if you've been committing and pushing as you
  go (per the `worldos-dev` dev-loop) rather than accumulating uncommitted state. **Confirmed again
  independently after #1516 landed** (NEW-ROOM-TAVERN, PR #1531, 2026-07-11): "a parallel
  worktree-prune destroyed the first uncommitted run; rebuilt byte-faithfully — deterministic
  numbers reproduced exactly." The sweeping behavior is still live somewhere in this environment —
  a `.worldos-keep` marker is not yet a proven full mitigation (an unrelated worktree carrying one
  was still removed mid-session during this very docs-consolidate lane, 2026-07-11) — so treat the
  commit/push discipline above as load-bearing, not optional, until the sweeper itself is found and
  fixed.

## The orchestrator-eyeball rule (owner-bound visuals get a human look before shipping)

Any visual artifact the owner is directly bound to — a rest-scene demo, a shipped room plate, a
player-build screenshot posted as release evidence, anything framed as "look at this" rather than
"here's a gate result" — gets a **personal orchestrator review of the actual frames** before it
ships or is reported as done. A green pre-gate and an in-band panel median are necessary but not
sufficient: they measure against an instrument, not against the owner's actual reaction. Eyeball
the frames yourself (0-for-5, per the visual-critic panel discipline) before trusting a panel
verdict enough to post it as "ready." This is the same discipline the felt-rest panel protocol
already states for panel composition (`qa/felt_rest_panel.md`) — restated here as a general
operating rule for anything visual and owner-bound, not just that one panel type.

## Box claim-queue etiquette (single-tenant GEX44)

The GEX44 Unity box is **single-tenant** — one lane's box op at a time. The live pattern (see the
active sprint charter, e.g. #1386 "Rules of engagement"): **claim by commenting on the owning
tracker issue before any box op; release (comment) when done.** Concretely:
- **Poll boundedly, repo-side-first.** Do repo-side work (author geometry, derive manifests, write
  the plate-loop config, stage panels) BEFORE requesting the box, so the box session is
  near-mechanical when it starts (see `qa/evidence/dungen-spike/BOX-DRIVE-RECIPE.md` for the
  pattern: "repo-side is DONE + green; this is the ready-to-run box phase"). Don't idle-poll for
  the box to free up — do the next repo-side unit of work instead, and check back when you need it.
- **Claim, do the bounded op, restore, release.** On the box: `chown -R unity:unity` any files you
  touched, `ctrl+r` (refresh Unity), restore the scene you found, THEN comment release on the
  claiming issue. Restoring state is part of the op, not optional cleanup.
- **The Built-in-RP note:** the box's Unity project is **Built-in Render Pipeline, not URP.**
  Shader/material work that assumes URP (Shader Graph particle materials, certain post-effects)
  needs a repoint step for this box (e.g. `RepointHovlMaterials`, PR #1515/#1525) — check the
  pipeline before importing or wiring any new asset pack; the `unity-asset-stack` skill states this
  per-pack.

## Journey-eval + the coherence gate — standing instruments (not one-off checks)

Two instruments run standing (not just once) against any shipped room, and neither substitutes for
the other:

- **`qa/check_grid_paint_coherence.py`** — the ABSOLUTE grid↔paint coherence gate (#1462/#1491).
  Run it against every registered plate candidate BEFORE the panel (step 5 of
  `docs/ROOM-PIPELINE-RUNBOOK.md`) — it is what would have caught the sarcophagus incident
  (engine-legal cell, paint ~3/4 cell off the authored footprint).
- **`qa/journey_eval.py`** — walks a scripted playable path (prop approaches + parley + door-cross +
  combat-entry), captures frames, and asks factual VQA questions per frame (on_prop, t_pose,
  floating, missing/cloned actor, backdrop-transition-changed). This is the eval-blindness
  instrument: aesthetic panels measure beauty-vs-bar and can score highly around a T-posing actor or
  a character standing inside a painted prop.
  - **★ v1 has a known coverage gap (#1523, "the legal-path blind spot"):** the scripted route only
    ever visits cells picked for OTHER reasons (adjacent to a prop the manifest already marks
    impassable, or a narrative waypoint) — it never deliberately routes onto a cell just because it
    LOOKS solid. A missing-footprint prop (standing on a painted object the engine never flagged
    impassable) is exactly the class of cell that method structurally never targets on purpose — it
    would only get caught by accident. This is a recall gap, not an impossibility: the per-frame
    "on_prop" VQA question runs on every captured frame regardless of why the character is there, so
    an incidental hit is possible, just not engineered. The first live run (PR #1520) scored 0 of 3
    recall against the owner's playtest-#7 missing-footprint punch list (woodpile, crate stack,
    hut/shelter) for precisely this reason — see `qa/evidence/journey-eval-first-run/RECALL.md` for
    the full comparison (a 4th playtest-#7 item, top-right trees/exit, is a separate occlusion-hull
    bug outside this defect class entirely).
  - **v2 (tracked, #1523)** adds an adversarial phase: click every painted-prop candidate region
    (from the manifest's footprint+occlusion sets, plus a coarse grid sweep of high-texture regions)
    and flag whenever the engine ACCEPTS the move — closing the exact gap v1 has by making the probe
    deliberate instead of incidental.
  - Until v2 lands, **do not treat a clean journey-eval v1 run as proof a room has no
    missing-footprint defects** — it answers a different question (does the legal path look right)
    than the coherence gate (does the paint sit on its authored cells) or a human playtest (does
    something painted-but-uncollided exist at all).
