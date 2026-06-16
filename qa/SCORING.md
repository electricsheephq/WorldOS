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
annotated with the failed checks (`clawdnd_cap_score_red`). A dead/non-progressing scene
can never display as 4.1 again. On a GREEN run, scores pass through untouched.

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

Default DM/player model = `sonnet` (`CLAWDND_DM_MODEL` / `CLAWDND_ACTOR_MODEL` env override; Opus for key structural-adherence runs).

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
  opt-in, non-CI step gated behind `CLAWDND_LIVE_SCORER=1`).
