# WorldOS QA Scoring System — standardized reference

> Source of truth for HOW we measure a playtest. Current as of 2026-05-26.
> The running results ledger is `qa/scores_db.py` (SQLite) → `qa/scores_ledger.md` (`add_run()` / `--render`); `qa/SCORECARD.md` is LEGACY narrative.
> For the current app/native handoff tools and RRI routing, start with `qa/QA_TOOLS.md` and
> `WorldOS-GUI-RUNBOOK.md`; this file describes the story/mechanical scoring model.

The fitness function = **1 hard behavioral gate** (deterministic pass/fail) + **3 LLM
lenses** (each 1–5). The gate is the honest floor; the lenses grade quality above it.

## 1. Behavioral gate — HARD pass/fail (`qa/assert_behavioral.py`)
LLM scorers grade prose and can't be trusted to flip RED on a structurally broken run,
so this deterministic gate does. Exit 0 = GREEN (warnings allowed), 1 = RED. FATAL checks:
- DM turns > 0 **and** player turns > 0; ≥ 1 dice roll.
- **World-progression floor** (runs ≥ 6 beats): the clock advanced **and** the party
  visited ≥ 2 locations. (+ WARN if no new named NPC entered.)
- Player did **not** narrate the world / assert outcomes (the facade over-write heuristic, H4).
- **Structural story-craft floor**: if a companion is present, it speaks (no "log, not a scene").
- **Mechanical resolution**: a player `[cast]`/`[attack]`/`[check]`/`[save]` move MUST be
  resolved by the DM (`cast_spell`/`attack`/`saving_throw`/`roll`) — an unresolved
  declaration is FATAL.
- **State integrity**: combat not left active at end-of-run; no stray monsters at a location;
  no lingering conditions left dangling.
- WARN (soft, not fatal): player asked **0 questions + 0 checks** across the run →
  "passive plot-passenger" — this is where `clarify` / `request_check` usage is tracked.

### RED-cap (anti-loophole)
If the gate is RED, all three LLM scorecards are **capped to ≤ 2.5 / INVALID** and
annotated with the failed checks (`worldos_cap_score_red`). A dead/non-progressing scene
can never display as 4.1 again. On a GREEN run, scores pass through untouched.

## 1b. Feature-engagement coverage — the dead-system tracker (WS0)
`qa/feature_engagement.py`

**The gap this closes.** The behavioral gate + the three lenses grade *prose, dice, and a few
structural floors*, but an entire authored subsystem (companion approval, camp downtime, faction
questlines, the companion agenda, decisions) could be **100% inert** across a whole sweep and the
RRI still scored 10/10 — *nothing was engagement-coverage*. A frozen-relationship run that
narrated the companion but never moved a gauge passed; a run that seeded factions and never joined
one passed.

**The manifest.** `feature_engagement.SYSTEMS` is the reviewed list of the 10 authored story
systems, each a `SystemSpec(id, precondition, detector, severity)`. A run is, per system:
- **ENGAGED** — the detector is true (the engine state / DM tool counts prove the system fired).
- **N/A** — the precondition is false (the run had no occasion: solo party, no factions seeded,
  too short) **or unknown** (a beats-keyed precondition with no transcript beats count).
- **INERT** — the precondition is **true** and the detector is **false**: the system was *owed*
  and never fired. **This is the signal.**

`engagement_coverage(state, tool_counts=None, session_beats=None)` returns
`{coverage, engaged[], na[], inert[{id,why,severity}]}`. It is **PURE-READ over engine-mutated
snapshot state** (`attitude_value`, `last_long_rest_day`, `faction.joined/standing`,
`narrative_arc.act`, `consequence.fired/trigger_day`, the arc/agenda `fired` flags,
`campaign.decisions/quests/factions/*_arcs`) **or DM tool-counts — never fiction/prose** (engine
invariant #3). It REUSES `story_readout.structural_coverage_from_state` /
`felt_shape_from_state` so the shared buckets never drift, and old snapshots round-trip (every
predicate null-guards a missing collection / a `None` `narrative_arc`).

**The forcing meta-test.** `servers/engine/tests/test_feature_engagement_manifest.py` mirrors
`test_tool_schema_budget.py`: it asserts `{s.id for s in SYSTEMS} == REVIEWED_SYSTEM_IDS`, so
adding/removing a tracked system is a **deliberate, visible diff** — the manifest can never
silently drift out of coverage (the exact failure WS0 exists to prevent).

**The deterministic RRI gate.** `qa/release_readiness.py` adds **one gate, `story_engagement`**
(in `DETERMINISTIC_GATES` — no live LLM). It rolls each persona's `engagement_coverage`
(merged into `score.json` by `qa/inject_structural_coverage.py`) up across the sweep: a system is
owed if **any** persona owed it, engaged if **any** persona engaged it; **inert for the sweep**
iff owed-by-≥1 **and** engaged-by-none. The gate **FAILS only on a FATAL inert system**. When
**no** persona block carries `engagement_coverage` (a legacy corpus), it is an **evidence-gap
SKIP** — excluded from `passed`/`total`, so RRI math stays **byte-identical** (mirrors the
latency-gate skip). The `ENGAGEMENT` report section names every inert system + a fix hint.

**Two N/A invariants (load-bearing — they keep the loop from ever false-RED-ing):**
- `session_beats` lives in the **transcript, not the snapshot**, so the signature accepts it
  explicitly and **every beats-keyed precondition defaults to N/A when it is `None`** (the inject
  callsite passes `None` → those systems are N/A there — safe under-detect).
- Under `WORLDOS_GATE_COMBAT_SPRINT`, all **FATAL** systems are **skipped** (mirrors
  `assert_behavioral.py`) — a single pre-seeded fight exercises no story system.

**WARN-first → FATAL graduation discipline.** Every system ships **`severity='warn'` this PR**, so
the axis is **strictly additive**: it adds *zero* fatals to `assert_behavioral.py` and *cannot*
flip a currently-green run RED. **Graduation to FATAL is a FUTURE, post-sweep PR** — after one
real 5-persona sweep proves the inert/owed classification is calibrated (the same discipline as
`flat_arc` / `caster_has_spellbook`). **Two systems are BLOCKED and stay WARN regardless** —
`faction_arc` + `companion_quest_arc`: a snapshot-only precondition can't tell *seeded-but-locked*
from *never-seeded* (a known open spike), so they must never graduate until that is resolved.

**Where it surfaces.** `assert_behavioral.py` emits one `engagement_<id>` WARN per inert system
(additive, after `structural_completeness`); `inject_structural_coverage.py` merges the block into
each persona `score.json`; `scores_db.py` records `engagement_pct` + `engagement_inert`;
`release_readiness.py` gates + reports it.

## 2. The three LLM lenses (1–5 each; run concurrently)
Scored by `qa/score.sh` (claude -p) or `qa/score_openclaw.sh` (gpt-5.4, off the claude quota).

| Lens | Rubric / schema | Scores what | Dimensions |
|---|---|---|---|
| **Mechanical** | `rubric.md` / `score_schema.json` | DM tool-stream (`$RUN.md`) | tool_sourced, rules_correctness, state_integrity, companion_agency, player_agency, exploration, narrative_pacing, robustness |
| **Story-craft** ("The Loremaster's Eye" / Tolkien) | `rubric_tolkien.md` / `score_schema_tolkien.json` | two-sided play (`$RUN.play.md`) | scene_craft, grandeur, character_depth, prose_atmosphere, dramatic_momentum, thematic_resonance, memorability (+ scope / progressed / per-act breakdown) |
| **5e rules-fidelity** ("The Angry DM") | `rubric_angry_dm.md` / `score_schema_angry_dm.json` | DM tool-stream vs SRD 5.2.1 bench card | rules_as_written, mechanical_completeness, tool_fidelity, action_economy, combat_resolution, conditions_and_effects (+ coverage) |

- **Story-craft is STINGY + ACT-RELATIVE**: BG3-calibrated, detects scope (an 8-beat slice
  is ~Act 1, NOT docked for lacking a climax); judges grandeur/stakes relative to act position.
- **Mechanical**: hallucinated mechanics are the worst defect; `tool_sourced` + `rules_correctness` weighted heaviest.
- **Angry DM**: adversarial; walks an exhaustive 5e checklist (d20 tests, ~15 action types, all 14 conditions).

## 3. North-Star targets (the loop's exit bar)
- **Story-craft ≥ 4.3**, **Mechanical ≥ 4.5**, **gate GREEN**, **zero critical/high** adversarial defects.
- **GPT-5.4 grades ~1.5 pts HARSHER than claude.** Claude (`score.sh`) is the PRIMARY baseline;
  gpt-5.4 (`score_openclaw.sh`) is a stricter cross-check, not the headline number.

## 4. Runners
| Script | What it runs |
|---|---|
| `run_duo.sh <run> <world> <persona> [beats] [budget]` | AI player + DM duo (claude -p); 3 lenses + gate |
| `run_duo_openclaw.sh <run> <world> <persona> [beats]` | same, via gpt-5.4 (OpenClaw gateway; off the claude quota) |
| `run_party.sh` | AI player + N companion AGENTS + DM (multi-agent ensemble; the betrayal path) |
| `run_parallel.sh` | 2–3 isolated concurrent runs (the velocity model) |
| `run_qa.sh` | single-agent full-plugin playtest |

Default DM/player model = `sonnet` (`WORLDOS_DM_MODEL` / `WORLDOS_ACTOR_MODEL` env override; Opus for key structural-adherence runs).

## 5. Reading a result line
`[duo] done. story-craft=X mechanical=Y angry-dm=Z behavioral=GREEN|RED`
- **RED** ⇒ X/Y/Z are RED-capped; read `$RUN.gate.txt` for the failed checks.
- **GREEN** ⇒ real scores; compare against the North-Star targets and append via `qa/scores_db.py` `add_run(...)` (→ `scores_ledger.md`; `SCORECARD.md` is legacy).

## 6. Variance & noise floor
The three LLM lenses are **stochastic graders**: re-scoring the *same comparable run* yields
a slightly different `overall` each time. You cannot read a score without knowing this
jitter — a single 4.2 and a single 4.4 may be the same run scored twice. This section
records the measured per-lens noise and the rule for when a single run is trustworthy.

**How it's measured.** A "comparable cluster" = ≥2 **GREEN** runs that share the same
`build_sha` + `surface` + `methodology` + `scorer_model` (+ ruler, when stamped). The
spread (population stdev / range) of `overall` *within* a cluster is the scoring noise,
because everything else is held fixed. We read this from the on-disk ledger
(`qa/scores.db`: `story_overall` / `mech_overall` / `angrydm_overall`) and from any
committed per-lens scorecards in `qa/transcripts/` (`*.tolkien.json` / `*.score.json` /
`*.angrydm.json`). RED-capped scores are excluded (they're rubric-capped, not real).

**Measured per-lens noise floor** (max within-cluster spread, GREEN, comparable; from the
committed `qa/scores.db`, 2026-06-16 snapshot, 75 rows):

| Lens | Measured max stdev | Measured max range | Documented floor (stdev / range) |
|---|---|---|---|
| **Story-craft** (Tolkien) | 0.15 | 0.30 | **0.20 / 0.40** |
| **Mechanical** | 0.25 | 0.50 | **0.30 / 0.60** |
| **Angry-DM** (5e fidelity) | 0.35 | 0.70 | **0.40 / 0.80** |

The **documented floor** rounds the measured spread UP for a little headroom and is the
contract enforced by `qa/test_lens_variance.py` (the test goes RED if a future re-score
blows past it, forcing a conscious re-derivation of *both* this table and the test
constant — they must stay mirrored). **Angry-DM is the noisiest lens** (adversarial,
exhaustive 5e checklist), so it leans on median-of-N hardest. As the corpus grows, re-run
the test's `__main__` diagnostic (`python3 qa/test_lens_variance.py`) and tighten the floor
toward the new measured max.

**The rule — single-duo for velocity, median-of-N for gating:**
- **Velocity / inner loop:** a **single duo** is fine. Treat any score as `X ± floor`; a
  delta smaller than the lens floor (e.g. story moved 4.2 → 4.3, < 0.40 range) is **noise,
  not signal** — don't chase it.
- **Release / auto-merge gating:** use the **median of N ≥ 3** comparable re-scores. The
  median of 3 collapses worst-case single-run jitter to well inside the noise band (a
  single run can sit a full half-floor from truth; the median of a straddling triple does
  not), so a North-Star call (story ≥ 4.3, mech ≥ 4.5) is made against the median, not a
  lucky/unlucky single draw. When a release decision hinges on a margin **smaller than the
  lens floor**, N=3 is the minimum; widen to N=5 for the angry-dm lens specifically.
- A score reported without N is implicitly N=1 — acceptable for velocity, **never** for a
  gate. `qa/test_lens_variance.py` is the deterministic, CI-safe guard that keeps this
  floor honest (it reads only on-disk artifacts; live re-derivation is an explicit,
  opt-in, non-CI step gated behind `WORLDOS_LIVE_SCORER=1`).
