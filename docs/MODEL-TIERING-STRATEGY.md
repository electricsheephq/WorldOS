# WorldOS Model & Effort Strategy

Status: **MEASURED (2026-06-02)** for the latency/effort mechanics; the **model choice itself remains an
open owner decision** (see below). Supersedes the 2026-05-31 "proposal/to-test" whiteboard. Companion:
the `worldos-latency-forensics` skill (the measurement method + full lever taxonomy).

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

## The OPEN decision (owner's call — do not assume)
**Which model the DM runs is not settled.** The measured trade: **Opus = higher story (4.4–4.5)**;
**Sonnet = faster + cheaper + already the default** and its story (4.2) is near the 4.3 bar. Pick ONE and
hold it for the campaign; expose it as `WORLDOS_DM_MODEL` (option, not a hardcode). Do NOT unilaterally flip
the default to Opus — that would create a config mismatch the codebase doesn't currently have.

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
- The exact Opus model id valid in the current CLI/runtime.
- Whether a lower routine effort holds story ≥ 4.3 (needs the quality A/B).
- Scorer-to-Opus re-baseline as a later one-time calibration.
