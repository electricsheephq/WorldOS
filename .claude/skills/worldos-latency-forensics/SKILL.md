---
name: worldos-latency-forensics
description: Root-cause and fix per-beat claude -p DM latency in WorldOS (the living-world D&D 5e engine). Use when a slow DM beat is the binding release blocker (beats trip the gate / a persona gives up on the wait), when choosing a latency lever (effort / lean / alwaysLoad / streaming / scene_context), or when someone proposes a "speed" change (a helper model, a --fast flag, a model switch) you need to validate or refute. Encodes the SDK-instrumented measurement method, the quality-cost lever taxonomy, and the hypotheses already REFUTED so they are not re-chased. Companion to worldos-dev.
---

# WorldOS DM Latency Forensics

The DM is a `claude -p` agent, one invocation per beat (`scripts/play.sh`, `scripts/play_party.sh`,
`qa/run_duo.sh`). When a beat is slow, **MEASURE before you guess** — most "make it faster" ideas
attack the wrong thing, and an expensive 5-persona gate is the worst place to discover that.

## MEASURE FIRST — the authoritative method
Every `claude -p --output-format stream-json --verbose` emits a final `result` event. Parse it:
- **GENERATION time = `duration_api_ms`** — the model thinking + emitting, summed across ALL turns
  of the beat. This is the real cost.
- **TOOL + ORCHESTRATION = `duration_ms` − `duration_api_ms`** — engine round-trips, scheduling, retries.
- Cross-check `tool_result` event timestamps: a real engine round-trip is ≤~3s; any larger gap is
  GENERATION between tool calls, not tool-wait.

**PITFALL (load-bearing):** a naive transcript-walk that bills every "gap between a `tool_result` and
the next `tool_use`" as *tool wait* OVER-counts — it attributes generation-between-tools to tool
latency and fabricates a "tool-heavy" figure. The SDK `duration_api_ms` is authoritative; **true
engine tool-exec is ~1–4% of the beat.** (This is the precise version of the earlier "≈100% thinking
between tool calls" read — same conclusion, correct metric.)

## THE VERDICT (measured 2026-06-02, Sonnet + Opus, baldurs-gate)
- Routine beats are **~90–100% generation-bound** (Opus *more* so — more extended-thinking). Cold-open
  (max-effort world-build) ~280–400s; routine ~100–126s at `--effort medium` ≈ the original Opus-high latency.
- ⇒ **Attack the INPUT token mass + per-turn thinking, NOT tool round-trips.**
- It is **NOT the GUI/harness**: `duration_api_ms` is surface-independent (same headless, in the .app,
  or on the VM). The "5-minute feel" in a playtest is the QA *player-agent's own* between-turn
  deliberation — a real human doesn't incur it.

## LEVER TAXONOMY — choose by quality-cost
**NEUTRAL (no quality cost — just take them):**
- **`alwaysLoad` / un-defer MCP tools** — pins the engine tools so the DM stops burning ~2 `ToolSearch`
  round-trips/beat re-discovering them. The win is the **cold-open** (248→176s, ToolSearch 4→0);
  routine ~unchanged (it's thinking-bound). Cache-stable. `CLAWDND_ENGINE_ALWAYSLOAD` (default on);
  `"alwaysLoad": true` per-server in the generated `dm.mcp.json` + `.mcp.json`.
- **Stream mid-turn narration** (#571) — perceived-latency win + nav-during-compose; doesn't cut
  wall-clock but stops give-ups on the wait.
- **Lean re-ground = the compact `scene_context` digest, SHIPPED (`CLAWDND_LEAN_BEATS=1`) — now the
  PRODUCTION and QA default** — continuing beats start a FRESH session re-grounded from the engine's
  persisted truth (durable threads + recent-narration tail) off the snapshot+session-log, NOT the ~690K
  transcript (~10–27× context drop, story quality held at 4.4; lossless because the engine's FTS5
  retrieves on demand). `qa/run_duo.sh` defaults `CLAWDND_LEAN_BEATS:-1`; the VM `qa/vm/sweep_v2.sh`
  exports lean-ON (production-matching). Lean WAS broken — fixed by #683/#685 (2026-06-06); history in
  SUPERSEDED below. **Do NOT turn lean OFF as a latency or "safety" move** — lean-OFF replays the
  growing Opus transcript (3–5+ min/beat, the latency-give-up vector).
- **Prose-trim** — cap narration length.

**TRADING (quality cost — the OWNER's call, A/B first):**
- **`--effort` (max cold-open / medium routine / low only behind a gate)** — the single biggest
  wall-clock lever, but lower effort can lower story quality. Medium ≈ original good latency; do NOT
  drop below medium without a quality A/B. **NEVER switch *model* mid-campaign** — a model switch
  invalidates the prompt cache (effort changes are cache-safe). Model choice (Opus story vs Sonnet
  speed) is an open owner decision — see `docs/MODEL-TIERING-STRATEGY.md`; do not assume it.

## REFUTED — do NOT re-chase
- **A small/Haiku "research-packet" helper to prefetch for the DM** — touches ≤5% of the beat
  (generation-bound). Refuted.
- **A headless `--fast` flag** — doesn't exist; use `--effort`.
- **`--tools` / `--allowedTools` to stop MCP deferral** — WRONG mechanism (those are built-in /
  permission lists, not deferral controls). The real levers are `alwaysLoad` / `ENABLE_TOOL_SEARCH`
  (verified by binary-safe `grep -a` of the `claude` binary for `alwaysLoad`/`ENABLE_TOOL_SEARCH`/
  `shouldDefer` — real in 2.1.160). Lesson: a subagent's confident config claim is a HYPOTHESIS —
  verify against the binary/source before building on it.

## SUPERSEDED — history kept so it isn't re-litigated (do NOT re-apply the old guidance)
- **"lean-ON is BROKEN — keep lean OFF" (2026-06-05) → SUPERSEDED 2026-06-06 by #683/#685; lean-ON is
  now the production AND QA default** (see LEVER TAXONOMY above). The 2026-06-05 findings were REAL —
  an A/B on the **same build** with the ONLY variable = `CLAWDND_LEAN_BEATS` showed lean-ON introducing
  TWO criticals the clean lean-OFF run did NOT have:
  - **Cross-chronicle contamination** (100% reproducible): every DM beat appended a *different* save's
    opening scene (e.g. Basilisk Gate with the wrong HP/day) — the lean re-ground selected its campaign
    by largest-snapshot and pulled **wrong / parallel-campaign** content. Same root as **#640**
    (multi-campaign divergence): `scripts/play_party.sh` mints a campaign per launch with only a
    ~30-min reuse guard (L201), so the re-ground could latch onto a parallel campaign.
  - **Dropped beats**: the spinner cleared and time advanced but **ZERO narration text** came back.

  **Both fixed (merged 2026-06-06): #683** pins the lean re-ground to the engine-authoritative LIVE
  campaign (kills the #640 contamination root); **#685** adds lean output-discipline (clean prose, no
  empty beats). Since then lean-ON is STANDARD: `qa/run_duo.sh` defaults `CLAWDND_LEAN_BEATS:-1`
  ("validated: ~10–27× context drop, story quality held at 4.4") and the VM `qa/vm/sweep_v2.sh`
  exports `CLAWDND_LEAN_BEATS=1` ("production-matching; #683/#685-fixed" — its header records this
  supersession). A future "lean is broken" report must first reproduce on a post-#683/#685 build
  before anyone disables the flag.

## SEQUENCE — cheap → expensive (never skip ahead)
1. **2-beat engine probe** → measure `duration_api_ms`, num_turns, ToolSearch count.
2. **Cache-stability** (one 2-beat run) → confirm the prefix cache holds across beats.
3. **Effort/flag-wiring probe** → confirm the runner ACTUALLY consumes the flag (see worldos-dev's
   "QA must exercise the flag" — a harness that ignores `CLAWDND_LEAN_BEATS`/effort fakes the A/B).
4. **Short duo A/B on the VM** (engine-duo, gateway-free) → quality + latency deltas, same SHA + seed,
   one credit window (throttling confounds wall-clock).
5. **ONLY THEN** the 5-persona `.app` gate (heavy — use the Support VM lane; see worldos-dev).
Record latency columns (s/beat, cold-open-s, turns/beat) to `qa/scores_db.py` on every run.

Cross-refs: `worldos-dev` (dev/QA loops + VM lane), `first-principles-architectural-decision`
(the lever decision), `docs/MODEL-TIERING-STRATEGY.md` (model/effort decision record).
