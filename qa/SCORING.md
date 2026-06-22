# WorldOS QA Scoring System — standardized reference

> Source of truth for HOW we measure a playtest. Current as of 2026-06-19 (post-24h reorient).
> The running results ledger is `qa/scores_db.py` (SQLite) → `qa/scores_ledger.md` (`add_run()` / `--render`); `qa/SCORECARD.md` is LEGACY narrative. Emit the standardized closeout block for a scored run with `python3 qa/closeout.py <run-id>` (ruler-fenced Δ-vs-last-comparable, flags non-opus DM).
> For the current app/native handoff tools and RRI routing, start with `qa/QA_TOOLS.md` and
> `WorldOS-GUI-RUNBOOK.md`; this file describes the story/mechanical scoring model.

> **Everything here is MEASUREMENT, never the target.** The north star (`VISION.md`) is the
> *felt player session* — a no-prior-knowledge player plays a complete 8-beat Baldur's-Gate-caliber
> arc and never once feels "this is broken." Scores, RRI, and rubric numbers exist only to *measure*
> that; **no score-gaming.** The gate-severity work in §1a is the sharp edge of this: making the
> measurement HONEST (it was punishing legitimate good story-craft) is the opposite of gaming a number.

The fitness function = **1 hard behavioral gate** (deterministic pass/fail) + **3 LLM
lenses** (each 1–5). The gate is the honest floor; the lenses grade quality above it.

## 0. The scoring ruler is VERSIONED — scores are NOT comparable across rulers

Every scored run is stamped with the **content hash of the ruler that graded it**:
`scoring_config_version` (`sc_…`, the FULL ruler — rubrics + schemas + all gates incl. RRI) and
`lens_config_version` (`lc_…`, the 8 files that produce the lens numbers). Computed by
`qa/scoring_config_version.py`, written on every `add_run(...)`. **This is load-bearing: a lens number
means nothing without its ruler.** A 4.1 under a stricter ruler can be *better play* than a 4.8 under a
looser one. NEVER compare two numbers across different `sc_`/`lc_` hashes — compare within a ruler, or
re-score the old transcript under the current ruler.

**The ruler is a deliberately-tightening FEEDBACK LOOP, not a fixed yardstick.** As we add
engine-enforced systems (companions, acts, quests, betrayal, travel, combat coverage), the ruler is
tightened to DEMAND those are actually *engaged* (gauge-backed), not merely narrated — so the same felt
session scores *lower* under a newer ruler than an older one. **That drop is the ruler working, not a
quality regression.** Expect current numbers to read below historic numbers; that is by design — the
scorer exists to drive autonomous build-and-improve.

### Ruler history
- **`sc_f283fdce1d24` / `lc_e52028b6acd3` — current (2026-06 scoring-hardening).** Re-versioned ONLY
  by the **one-decimal lens precision** change: the per-dimension score type in all three lens schemas
  (and the Tolkien per-act `score`) went `integer 1–5` → `number, minimum 1, maximum 5`, and the four
  rubric files now instruct "score each dimension 1.0–5.0 to one decimal." **No range, threshold, cap,
  or weighting changed** (story ≥ 4.3, mech ≥ 4.5 unchanged) — the bands are identical, the grader can
  just register *where within a band* a dimension lands instead of rounding to a whole number. This is a
  **precision** re-version, not a stringency one: a given session should read at the *same* overall ±
  the rounding it no longer has to do.
  - **Why not `multipleOf: 0.1`?** It looks like the natural "one decimal" constraint but is an
    IEEE-754 footgun — under a real validator `4.3` is *not* a multiple of `0.1` (`4.3/0.1 = 42.9999…`
    in binary float), so `multipleOf: 0.1` would REJECT legitimate one-decimal scores. The pipeline feeds
    the schema to the LLM as advisory *text* (no runtime `jsonschema.validate()` on scorecards), so the
    one-decimal expectation is carried by the **rubric instruction**, and the schema stays a plain
    `number` in `[1,5]` — accepts `4.3`, still rejects `5.5` / `0.5` / a string. Do not "tighten" it back
    to `multipleOf: 0.1`.
  - Was `sc_cf47d34e219e` / `lc_e06a888f7c08` on origin/main pre-change; the older `sc_d4b93982763a` line
    below was already stale — PRs #1040/#1081/#1083/#1086 had re-versioned the ruler since it was written,
    the exact silent-drift this stamping discipline exists to catch.
- **`sc_d4b93982763a` / `lc_d7fcfddd5bf7` — the 2026-06 cycle → `v1.0.5-rc1` ruler.** Materially
  STRICTER than the v1.0.4 rulers. Adds: the **feature-engagement coverage scorer + forcing gate**
  (#1018 — every *owed* authored system narrated-but-not-gauged is now a coverage miss); the
  **acts-engine felt-shape scorer + flat-arc gate** (#1001/#1002 — a flat, act-less arc is penalized);
  **betrayal un-inversion** (#999) and the **romance gate** (#997); the **`dm_advanced_time` unmask**
  (#1024 — a frozen-clock DM no longer hides); and the **gate-severity accuracy repair** (#1030 —
  removes false FATAL-caps so the floor is TRUE, not loose). A social/short slice that exercises few of
  these reads markedly lower here than under v1.0.4.
- **Older `sc_…` rulers (≤ `v1.0.4-rc5`).** Looser: no feature-engagement coverage demand, no acts
  felt-shape, pre-betrayal-fix. Historic numbers — e.g. the `gs-ledger-deep` story **4.8** full-depth
  proof — were graded by an OLDER ruler; **do not read them as directly comparable to current numbers.**

To compare a historic run to today honestly, **re-score its transcript under the current ruler**
(`qa/score.sh <transcript.md> <state.json> <rubric> <schema> <out> [budget]`), then compare `sc_`-equal
rows only.

### Re-versioning discipline — the MECHANICAL rule (run it after ANY rubric/schema/gate edit)
The hashes above silently re-version themselves the instant any ruler file changes (a rubric anchor, a
schema, a behavioral/RRI gate). That is the point — but a stale "current" line in this doc (which is
exactly what `sc_d4b93982763a` had become) means a reader trusts the wrong hash. So after **any** edit to
a file in `SCORING_CONFIG_FILES` (`python3 qa/scoring_config_version.py --files` lists them), the
mechanical, do-not-skip steps are:

1. **Recompute** both hashes — `python3 qa/scoring_config_version.py --label` (the FULL `sc_…` ruler) and
   `python3 qa/scoring_config_version.py --lens` (the `lc_…` lens ruler).
2. **Confirm the hash changed** vs the previous "current" entry. If it did NOT change but you edited a
   ruler file, the file is not in `SCORING_CONFIG_FILES` (a real gap — add it) or your edit was a no-op.
3. **Re-stamp** the new `sc_…` / `lc_…` as a fresh top entry in the *Ruler history* above (1 line on WHAT
   changed and whether it was a *precision* / *stringency* / *RRI-only* re-version), and add a 1-line
   note to `CHANGELOG.md` under `[Unreleased]` with both hashes.

This keeps the ledger's `scoring_config_version` / `lens_config_version` columns trustworthy and the doc's
"current" marker honest. (`--label` exists today; there is no auto-restamp — re-stamping is the deliberate
human step that records the *why*.)

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

## 1a. Gate severity — a FATAL must mean a true integrity failure (honest-measurement repair)
Because a RED caps **all three lenses ≤ 2.5**, the line between **FATAL** (caps the run) and
**WARN** (advisory, doesn't cap) IS the measurement. The contract (the post-24h reorient,
PR #1030 — paired with the #1027 coercion fix below):

> A FATAL behavioral gate fires **only on a true integrity / correctness failure** — no PC
> seated, a rejected/validation-walled tool call, dice never used, a save corrupted by a real
> engine bug, a fight genuinely abandoned. It must **NOT** fire on a quality/completeness signal
> that a *legitimate short emergent duo* trips. Those are WARNs.

A Phase-2 GLM-vs-Claude 1-v-1 found **Claude opus runs RED-capping too** (2/2), so two FATAL gates
were demoting good story-craft (VISION pillar 1) on **both** models — a **model-agnostic false-cap**,
not a model quirk. The two beat-scoped fixes (`qa/assert_behavioral.py`):

- **`party_traveled`** (`assert_behavioral.py:676–696`). The bare `visited >= 2` rule read a deep
  6–7-beat **single-scene social duo** as "never left the opening scene" → FATAL. **Now beat-scoped:**
  `SINGLE_SCENE_MIN_BEATS = 8` — below 8 beats this is a **WARN** (a single-scene vignette is not a
  stuck DM); **at/above 8** it stays **FATAL**. The strict anti-gaming in-place-progression exception
  (the run must have **advanced the clock AND resolved an actual completed quest** — `clock_advanced
  AND arc_resolved`, deliberately *not* clock-only or beats-only, adversarially verified against a
  cheap-`set_quest_status("active")` game) is **unchanged**; a substantial run that never moves *and*
  never progresses is still a FATAL stuck DM.
- **`combat_not_left_active`** (`assert_behavioral.py:326–397`). A 6-beat duo that **enters combat
  near its beat budget and truncates mid-fight** legitimately never reaches `end_combat` → the old bare
  FATAL capped a run that did nothing wrong (proven: `qa/transcripts/claude-1v1-2`, an opus duo whose
  final DM line is cut off mid-sentence). **Now severity rides a `started_late` discriminator** (where
  the last `start_combat` lands in the ordered tool stream): a fight that started in the **final ~20%**
  of calls — or a **resume-into-combat** session with **no `start_combat` this run** — is a
  **truncation → WARN**. Only a **genuine abandon** (a substantial run `≥ COMBAT_ABANDON_MIN_BEATS = 10`
  where combat started **early** with room to resolve, `end_combat` never fired, and the fight is **still
  active** at the snapshot) stays **FATAL** — that corrupts the next load (and the engine's `start_combat`
  next-load guard is the deeper backstop).

**This is honest-measurement repair, NOT score-gaming.** A gate-severity audit classified *every*
FATAL gate KEEP-FATAL vs over-aggressive and changed **only** the two quality/completeness ones; an
adversarial verifier confirmed **no true integrity gate was weakened** — `player_in_party`,
`no_rejected_tool_calls`, `dice_used`, `dm_produced_output`, SRD-correctness, and the XP gates are all
**untouched** — and the behavioral-gate **corpus still REDs genuine failures** (`party_traveled` padded
to 8 beats, `combat_not_left_active` reshaped to a real-abandon profile — both still trip the preserved
FATAL path). The opus runs that wrongly RED-capped (`claude-1v1-1`/`claude-1v1-2`) are now GREEN; the
corpus + taxonomy suite (39) and `fast_gate` (226) stay green. This makes the floor measure *broken*,
so the lenses can grade good short story-craft instead of being capped to ≤ 2.5.

### 1a.1 List-arg coercion — the tool-arg contract (#1027)
The **#1 source** of the model-agnostic RED-cap was *upstream* of the gate: FastMCP validates a tool
call's args against the Pydantic type hints **before** the function body runs, so a model passing a
bare string (`approval_tags="honest_dealing"`) or a comma-string (`actor_ids="id1,id2"`) where a
**list** is expected was rejected ("Input should be a valid list") → the FATAL `no_rejected_tool_calls`
gate → all three lenses capped. This deflated **~30%** of runs and hit the **Claude** baseline
transcripts (`baseline-rc1`, `cue-thaw`) exactly as hard as GLM — *not* a GLM-only problem.

**The contract** (`servers/engine/models.py:21–63`): list-typed tool args coerce at the validation
layer via a reusable `_coerce_list` **`BeforeValidator`** (the `ListArg` / `StrListArg` / `OptStrListArg`
aliases), applied to the high-traffic DM-called args — `record_decision` (options / actor_ids /
approval_tags), `author_companion_gauges`, `start_combat` (combatant_ids / surpriser_ids), `cast_spell`
(target_ids), and the nested `persist_beat` decision path (`server.py:12582–12594`). Behavior:
`None → None`; a real `list → unchanged`; `"" → []`; `"foo" → ["foo"]`; `"a,b , c" → ["a","b","c"]`;
**anything genuinely wrong (int / dict) is returned as-is so Pydantic STILL rejects it loudly** — the
coercion is purely additive and never swallows a real type bug. Critically, a `BeforeValidator` is
**invisible to `json_schema()`**, so the emitted wire schema stays a plain `array` and the pinned
schema byte-budget (`test_tool_schema_budget`) does not regress. The model gets coerced, not walled —
so a stringified list no longer manufactures a false `no_rejected_tool_calls` RED.

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
SKIP** — excluded from `passed`/`total`, so the **RRI math** (`rri` / `gates_total` / `release_ready`)
stays **byte-identical** (mirrors the latency-gate skip). The serialized `rri.json` still gains the
additive `gate_detail.story_engagement` / `signals.engagement_*` keys (no value/verdict change). The
`ENGAGEMENT` report section names every inert system + a fix hint.

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
`[duo] done. story-craft=X mechanical=Y angry-dm=Z behavioral=GREEN|RED [status=unscorable]`
- **RED** ⇒ X/Y/Z are RED-capped; read `$RUN.gate.txt` for the failed checks.
- **GREEN** ⇒ real scores; compare against the North-Star targets and append via `qa/scores_db.py` `add_run(...)` (→ `scores_ledger.md`; `SCORECARD.md` is legacy).

### A BLANK lens value = scorer FAILURE, not a valid no-score (WS0a)
A lens value of `FAILED:<status>` (or, on an old build, a **BLANK** value — `story-craft= mechanical=`
with nothing after the `=`) means **the scorer itself FAILED to produce a score**, NOT that the run
legitimately has no score. This used to masquerade as a silent valid no-score: when `qa/score.sh`
exhausted its retries on a generic failure it exited rc=1 **without writing the lens file**, so the
result line's `jq -r '.overall//"?"'` printed BLANK while `behavioral=GREEN` still printed — a failed
scoring read as a passing run (observed live: `story-craft= mechanical= angry-dm= behavioral=GREEN`).
- `qa/score.sh` now **always** leaves the lens file as valid JSON: an `{"error":"scorer_failed",…}`
  sentinel on generic retry-exhaustion, or `{"quota_exhausted":true,…}` on a 429.
- `qa/run_duo.sh` validates all three lens files (`worldos_validate_lens_file`): a lens that is
  missing / empty / non-JSON / a sentinel / non-numeric-`.overall` is **not** a score. Any such
  lens marks the whole run **`status=unscorable`** — a DISTINCT status that is **neither GREEN
  (passing) nor a blank no-score**. The lens prints as `FAILED:<missing|invalid|sentinel|nonnumeric>`.
- **An `unscorable` run is an INFRA failure of the measurement, not a product measurement** — do
  NOT record it in `scores_db`, do NOT read its (failed) lenses as quality signal. Re-run the
  scoring (or the whole duo); inspect `$RUN.<lens>.json` for the `error`/`last_api_error` field.

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

## 7. Timing columns — where a beat's seconds go (Wave-1)
Additive observability, not a gate. A finished run now reports **where time goes**, flowing
**per-tool-call sidecar → `qa/latency_rollup.py` → `qa/scores.db` columns → the `story_readout` TIMING
stamp**. The engine wraps each `@mcp.tool()` once and, **only when `WORLDOS_TOOLTIMING_PATH` is set**
(default-OFF — production pays nothing), appends `{ts, tool, wall_ms, ok, campaign_id}` per call to a
JSONL sidecar (PR #1006). `latency_rollup.py` (PR #1007) then derives two dimensions: **per-kind
generation** means — `combat_s_per_beat` / `social_s_per_beat` / `camp_s_per_beat`, each the mean beat
`duration_api_ms` over beats classified by their tool calls (cold-open / combat / camp / social; combat
outranks camp) **straight from the transcripts, no sidecar needed**; and a **tool-exec split** from the
optional sidecar — `mean_tool_call_ms`, `slowest_tool` (largest *total* summed `wall_ms`), and
`tool_exec_pct` (= Σ tool wall-s ÷ Σ whole-beat `duration_ms`, with a `tool_exec_pct_basis` stamp).
These land as additive `scores.db` columns (`combat_s_per_beat`, `social_s_per_beat`, `mean_tool_call_ms`,
`slowest_tool`, `tool_exec_pct`, `duration_wall_s`; old rows read NULL via `ALTER TABLE`) and as a
one-line `TIMING |` readout next to `COVERAGE`, e.g.
`TIMING | beat~86s gen~96s cold~240s | combat~140s social~70s camp~95s | tool=3% slowest=scene_context`
(the tool clause is omitted when no sidecar). **The headline finding: engine tool-exec is only ~1–4% of
a beat** — routine beats are ~90–100% **generation/decode-bound** (Opus more so, extended thinking), so
when a combat turn feels slow it's the *model thinking*, not the tools. Everything degrades to `None`
without a sidecar, leaving the rest of the rollup byte-identical.

## 8. Differential fact-fidelity — the content-loss measure the 1–5 lens is BLIND to
`qa/fact_fidelity.py` · inventories under `qa/fact_inventories/`

**The gap this closes (the 2026-06-21 lens-blindness finding).** The three 1–5 LLM lenses grade
*prose / plot-gist*, not *arc-completeness*. Proven on the combat transcript `ow-combat-031717.md`,
uncapped scorer: deleting the **entire** climax+resolution (antagonist reveal, the "43 names"
MacGuffin, all end-session XP/reputation mechanics) moved story `4.0 → 4.0` (identical) and mech
`3.6 → 4.1` — *higher*, **non-monotonic** (fewer beats ⇒ fewer SRD errors to find). Cutting to the
first 25% only dropped story to `3.7` (inside the 0.40 noise floor). **The lens scores a whole story,
a gutted one, and a mangled digest ~alike.** This is partly by design — the lens grades quality *above*
the deterministic floor (§1); structural brokenness is the floor's job — but nothing read the
*transcript itself* for content fidelity, so a compressed / truncated / narration-incomplete candidate
was uncertifiable.

**The instrument.** A **fact inventory** is a committed list of discrete, grep-able facts extracted
from a reference transcript — each `{id, desc, patterns[], severity}` (`patterns` are case-insensitive
regexes, ANY-of; `severity` ∈ `critical|high|normal`). `score_fidelity(facts, candidate_text)` reports
the fraction preserved (flat + severity-weighted), the dropped-fact ids, and `critical_loss` (any
dropped `critical` fact — an antagonist reveal, the central MacGuffin, an end-session mechanic). The
CHECK is **fully deterministic — no LLM** (so it never inherits the lens blindness it measures), which
is exactly why it is the **sensitive** measure for regression detection (compression A/B,
candidate-vs-baseline), NOT a prose-quality grader.

**Authoring gotcha (load-bearing).** A transcript's tool-call **tally** lists tool *names*
(`end_session`, `award_xp`, `adjust_reputation`…), so a fact keyed on a tool name survives truncation
via the tally. End-session-mechanic facts therefore match the **call RESULT** (`"xp": 350`,
`"reputation": 6`, `"ended": "session-…"`), never the tool name.

**Sensitivity (reproduced, same `ow-combat-031717.md`, 27-fact inventory):** baseline **100%** →
climax-deleted **59%** (`critical_loss=True`) → first-25% **33%** (`critical_loss=True`) — monotonic,
tracking the owner's grep-verified 45-fact differential (100% / 53% / 27%). The measure separates
gutted from whole where the lens could not.

**Usage.** `python qa/fact_fidelity.py <inventory.json> <candidate.md> [--min-fidelity F] [--json]`
→ exit 0 if the candidate clears the floor with **no critical loss**, else 1. Raw playtest transcripts
live under the gitignored `/qa/transcripts/`, so the committed regression test
(`qa/test_fact_fidelity.py`) runs on a small synthetic fixture
(`qa/fact_inventories/sample_session.*`); an opt-in test reproduces the finding on the real
`ow-combat-031717.md` when it is present locally. The committed `ow-combat-031717.facts.json` inventory
re-derives the differential whenever that transcript is regenerated.

**Wired consumer — the recap content-fidelity guard** (`qa/test_recap_fidelity.py`). The instrument is
not "available but uncalled" (the VISION *written-but-never-read* pathology) — it guards a REAL lossy,
player-facing surface: `recap.format_recap` (the DM's `previously_on`, read every resume) keeps only the
most-recent `max_entries` story beats, soft-truncates each to `max_entry_chars`, and drops oldest-first
under a `max_chars` budget — tunable knobs under active latency pressure (it's the latency-collapse path,
`server.py`), with no other content-loss guard. The test asserts the recap of a reference session
preserves its *critical* facts at the shipped defaults, and has TEETH: a leaned budget drops a critical
fact and `critical_loss` catches it (adversarially verified — regressing the recap default to 40 chars
turns the guard RED). **Use this pattern when leaning any lossy context knob** (`recap.py` budgets, the
`scene_context` throttle constants in `server.py`): pin a reference + critical-fact inventory and assert
the leaner derivation still clears `critical_loss=False` — the regression the 1–5 lens cannot see.
**NOT** a per-run scored dimension: each fresh emergent run has no reference to diff against, so
fact-fidelity is an opt-in regression guard (compression/lean A/Bs, recap continuity), never a lens.
