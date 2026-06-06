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
