# WorldOS Model & Effort Strategy

Status: **DECIDED (2026-06-06): the DM runs Opus** (owner's call, below) — flipped the default sonnet→opus
across production + gate + QA; latency mitigation in progress. The latency/effort mechanics were **MEASURED
(2026-06-02)**. Companion: the `worldos-latency-forensics` skill (the measurement method + full lever taxonomy).

## What was measured (not recollection)
- **The DM is generation-bound.** Per-beat `claude -p` time is ~90–100% `duration_api_ms` (model thinking +
  emitting); true engine tool-exec is ~1–4%. So latency is driven by **input-token mass + per-turn thinking
  (effort)**, NOT by tool round-trips, and NOT by the GUI/harness (`duration_api_ms` is surface-independent).
- **Effort is the big wall-clock lever.** Routine beats ~100–126s at `--effort medium` ≈ the original
  Opus-high feel; cold-open (max-effort world-build) ~280–400s. Dropping below medium is faster but trades
  story quality.
- **Story A/B (engine-duo, same week, same Sonnet scorer):** Opus story 4.4–4.5 vs Sonnet 4.2; Sonnet is
  faster/cheaper. Both surfaces (engine harnesses AND the `.app`) currently **default Sonnet** — there is no
  tracked artifact where Opus was "decided." Models are **options we offer, never bolted on.**

## Invariants (do these regardless of model)
1. **ONE model end-to-end per campaign.** **NEVER switch model mid-campaign** — a model switch invalidates the
   Anthropic prompt cache; **effort changes are cache-safe.** (This retires the old "Opus cold-open / Sonnet
   routine" model-tiering proposal — tier the EFFORT, not the model.)
2. **Effort tier:** `--effort max` cold-open (one-time world-build), `--effort medium` routine. Drop to a lower
   routine effort ONLY behind a default-off flag AND a quality A/B that holds story ≥ 4.3.
3. **Build-the-world-once-expensive → live-in-it-cheap:** re-ground each beat from the compact `scene_context`
   digest (durable threads + recent-narration tail) off the snapshot+session-log, NOT the ~690K transcript.
4. **`alwaysLoad` the engine MCP tools** (un-defer) — neutral latency win on the cold-open, cache-stable.

## The DECISION (made 2026-06-06 by the owner — Opus)
**The DM runs Opus.** A fresh same-build A/B (lean-OFF craft duo, veteran, current scorer) confirmed the lift:
**story-craft 3.6 → 4.0** with EVERY weak dimension lifted 3→4 (scene_craft / character_depth /
dramatic_momentum / thematic_resonance), and the duo mech (angry-dm) 3.4 → 3.9 — same direction as the prior
4.2→4.4 read. The dungeon-master prompts were already MAXIMAL (the NPC-speak-back non-negotiable + the per-beat
gate), so **Sonnet was the ceiling, not the prompt.** Owner decision: **"Full Opus + attack latency"** — flip
the default to Opus everywhere (production `play.sh` / `play_party.sh`, the gate `ui_playtest_app.sh`, the QA
duos/sprints), keep the **player facade on Sonnet** (near-free no-tool agent), and **mitigate latency rather
than trade the model**. Cheap iteration can still opt out via `WORLDOS_DM_MODEL=sonnet`.

**Latency mitigation** (Opus = slower beats; the narrative already failed the latency gate at Sonnet):
(1) `alwaysLoad` the engine tools — ON. (2) **streaming** — the live-progress rule + `log_event` narration-first
make the scene appear *as it composes* (a slow Opus beat is *felt-OK* if the prose streams). (3) the **#679
recovery** — a stuck beat no longer bricks the bar. (4) **THE KEY ENABLER: fix the #640 lean re-ground root**
so lean-ON is safe again (it currently CONTAMINATES — see latency-forensics "lean is broken"), restoring
invariant #3's compact `scene_context` re-ground = fast Opus beats instead of the full-transcript replay
lean-OFF forces. Until #640 lands, Opus runs lean-OFF (correct but slow); the re-measure gate is a same-SHA
Opus sweep confirming story ≥4.3 AND no latency give-up.

## REFUTED — do not re-chase
- **A Haiku (or any small-model) "research-packet" helper to prefetch for the DM** — the beat is
  generation-bound, so a prefetch helper touches ≤5% of the wall-clock. Refuted by the latency forensics.
- **A headless `--fast` mode** — doesn't exist for `claude -p`; use `--effort`.

## GLM as a cheap batch-QA engine (QA-only; Claude stays the quality bar)
GLM (z.ai's **GLM 5.2**, served over an Anthropic-compatible endpoint) is a **cost lever for QA sweeps**,
NOT a model the player ever touches. The point is to run cheap batch QA — many duos to find bugs / smoke a
build — without spending Anthropic tokens, while **Claude remains the quality bar** for the release gate.

**The clean model-profile system** (`qa/glm_profile.sh`; PRs #1026 + #1028). A **single model choice flows
coherently** through the harness via `WORLDOS_DM_MODEL` (+ `WORLDOS_ACTOR_MODEL`). The profile is keyed off
that one choice:
- **No-op for Claude.** If neither role names a GLM model, `worldos_apply_glm_profile` does NOT apply the
  profile and never alters a Claude default — a clean Claude run is byte-for-byte unchanged.
- **Switch-back is always clean (no leak).** On the Claude path the profile *defensively scrubs* any stray
  GLM-injected env left in the shell after a QA run (`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_API_KEY` / `API_TIMEOUT_MS` / a `/tmp/glm-claude-config` `CLAUDE_CONFIG_DIR`). Each unset is
  **GLM-conditional** (matched by the z.ai host or a byte-match to `glm.env`), so a legitimate `sk-ant-` key,
  `api.anthropic.com`, a corporate proxy, or a user's own config dir is **never touched**. "Switch back to
  Opus is always clean" even if a GLM export leaked.
- **Mixed-model guard.** If exactly one role is GLM (a half-GLM/half-Claude config — almost always a
  mistake, since `ANTHROPIC_BASE_URL` is process-global and the "Claude" half would silently inherit z.ai),
  it warns and **normalizes both roles to GLM** so a run can never silently route the two roles to different
  providers.
- **Product is forced clean-Claude.** `scripts/play.sh` + `scripts/play_party.sh` conditionally neutralize
  ambient GLM env before any `claude -p`, so the `.app` always runs Claude (Opus) quality and never opts into
  GLM. **QA uses GLM via `qa/glm_profile.sh`; the product play path never does.**
- **The scorer is ALWAYS isolated-Claude.** `qa/score.sh` runs the pinned-`sonnet` scorer under
  `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY …` so it uses clean `~/.claude` (Anthropic OAuth)
  **regardless of which model PLAYED the game** — the measurement is on a constant scorer, never on GLM.

**When to use GLM:** cheap batch QA sweeps to save Anthropic tokens (bug-finding, build smoke, parallel
duos). **NOT the final release gate** — Claude stays the quality bar; GLM is a viable cheap batch-sweep
engine, not a replacement for Claude on the release scorecard. Run it via
`WORLDOS_DM_MODEL=glm-5.2 WORLDOS_ACTOR_MODEL=glm-5.2`; the profile auto-wires the z.ai endpoint + raised
timeouts/retry ceilings (GLM is ~2–3× slower than Opus). See the **GLM QA lane** notes in `WorldOS-RUNBOOK.md`
and `WorldOS-GUI-RUNBOOK.md`.

### The cap-rate finding (honest-measurement repair, NOT a GLM weakness)
The ~30% GLM "cap rate" that early overnight sweeps showed was **NOT a GLM quality weakness** — it was
**self-inflicted, model-agnostic over-aggressive FATAL gates** capping **both** models. The Phase-2 reorient
ran a GLM-vs-Claude 1-v-1 and immediately found that **Claude opus runs RED-capped too** (2/2). A RED
behavioral gate caps all three lenses to ≤2.5, and several FATAL gates were nuking *legitimate* short
emergent sessions on both models. Two root causes, both fixed:
- **`no_rejected_tool_calls`** — a model passing a string/comma-string where a list arg was expected was
  rejected by Pydantic → FATAL. Fixed at the validation layer (#1027: a `BeforeValidator` coerces
  `str → [s]` / comma-`str → split`, schema unchanged, genuinely-wrong types still rejected).
- **`party_traveled` / `combat_not_left_active`** — a deep single-scene social duo read as "never left the
  opening scene," and a 6-beat duo that truncated mid-fight read as "combat abandoned" → FATAL. Fixed by
  making severity **beat-scoped / discriminator-aware** (#1030: WARN below the single-scene/late-start
  threshold, FATAL only for a genuine stuck-DM or real abandon). **Adversarially verified: no true
  integrity gate was weakened** — the corpus fixtures still RED genuine failures (player-seated,
  rejected-tools, dice, dm-output, SRD-correctness, xp all untouched).

This is the spirit of the north star: **scores are measurement, never the target.** The gate had been
FALSE-CAPPING good story-craft (short single-scene / truncated-combat sessions are legitimate, and pillar 1
is story-craft first) — fixing it makes the measurement *honest*. This is the OPPOSITE of score-gaming.

**Honest GLM-vs-Claude quality (measured on the fixed engine, 2026-06-19).** Same-SHA 1-v-1
(`43a5ecc`: #1027 coercion + #1028 clean-profile + #1030 gate-severity), same world/persona/6-beats,
both scored by the isolated Claude sonnet scorer. 5 runs (3 Claude opus/sonnet + 2 GLM-5.2), **all
behavioral GREEN — 0 RED-caps** (vs the pre-fix ~30%, which was the self-inflicted gate false-cap, NOT
a GLM weakness):

| model | story | mech | angry | cold-open |
|---|---|---|---|---|
| Claude (opus DM / sonnet actor) | **4.13** | 3.67 | 3.33 | ~205–249s |
| GLM-5.2 (both roles) | 3.9 | **3.8** | **3.4** | **604–872s** |

**Verdict:** GLM is **comparable quality** — within ~0.2 on every lens; *higher* on mechanical (3.8 vs
3.67) and angry-DM (3.4 vs 3.33), ~0.2 lower on story-craft (3.9 vs 4.13). A real QA runner, not
degraded. **Its true cost is LATENCY** — GLM cold-opens run 604–872s (3–4× Claude's ~205–249s) and
routine beats ~120–166s (vs ~80–96s), so a 3-run GLM batch is ~2.5–3.5h. ⇒ **Use GLM for cheap
overnight / VM batch sweeps where latency is hidden; never interactive, and never the final release
gate (Claude stays the quality bar).** Both models sit BELOW the RRI release bar (story ≥4.3, mech
≥4.5) — story ~4.0–4.1 is close; the mech ~3.7–3.8 gap is largely the emergent-social-duo coverage
artifact (little combat to score), not an engine defect (Engine-Excellent is met).

## Validation ladder (cheap → expensive; before any model/effort spend)
digest-correctness (1 engine call, no LLM) → cache-stability (1 two-beat run) → effort/flag-wiring probe
(confirm the runner consumes the flag — see worldos-dev "QA must exercise the flag") → short duo A/B on the
32 GB VM (same SHA + seed, one credit window) → ONLY THEN the 5-persona `.app` gate (Mac native Part-A + the
VM part-B sweep; see the Support VM lane). Hold the scorer constant during any A/B.

## Still open
- **The #640 lean re-ground root** — the key Opus-latency enabler (re-enable lean safely; repro-first).
- The exact Opus model id valid in the current CLI/runtime (confirm `claude --model opus` resolves on the gate path).
- The same-SHA Opus re-measure sweep: does Opus + streaming hold story ≥4.3 AND keep cross-persona sat ≥7 (no latency give-up)?
- Scorer-to-Opus re-baseline as a later one-time calibration.
