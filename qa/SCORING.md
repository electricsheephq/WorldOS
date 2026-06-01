# WorldOS QA Scoring System — standardized reference

> Source of truth for HOW we measure a playtest. Current as of 2026-05-26.
> The running results ledger is `qa/SCORECARD.md`.
> For the current app/native handoff tools and RRI routing, start with `qa/QA_TOOLS.md` and
> `WorldOS-RUNBOOK.md`; this file describes the story/mechanical scoring model.

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
- **GREEN** ⇒ real scores; compare against the North-Star targets and log to `SCORECARD.md`.
