# Fast QA Gate — iterate in minutes, not 90-minute sweeps

**Problem.** We were using the *milestone* gate (the full 5-persona `.app` sweep) as the *iteration*
loop. That sweep is ~60–90 min and ~$8–12, and most of its cost is wasted re-work per iteration:
a `--effort max` cold-open world-build **five times**, 5 full free-play personas, an 8-beat duo —
when the thing we changed only needs a small, targeted signal. It's also single-run-noisy (±2 on
satisfaction, ±0.3–0.5 on the LLM lenses), so one sweep can't even tell signal from noise.

**Goal (the owner's bar):** ~80% of the sweep's signal at ~90% less time, so we can iterate 5–6×
faster and reserve the sweep for milestone validation.

This design was produced by three parallel agents (a cost/signal map, a design, and an **adversarial
critique**). The critique is why this doc exists — it caught that the *obvious* fast design is a
false-confidence trap.

---

## The trap: naive "seed everything from a mid-arc snapshot" is WORSE than useless

The intuitive cheap design — pre-build mid-arc snapshot fixtures and probe from them — **removes
exactly the surfaces where our real bugs live.** Grounded, code-cited failures it would have
GREEN-lit:

1. **The skill-case crit** (optimizer, 2026-06-03: Arcana +3 not +6). `_normalize_skill_case`
   (`models.py`) runs *before* the snapshot is written, so a mid-arc fixture bakes in the **fixed**
   state. Seed the optimizer from it and it reads +6 and passes — while a real **seat-path**
   regression ships. The "optimizer catches skill-case" justification is **inverted**.
2. **Combat reachability** (the actual G1 fail). `run_combat_sprint.sh` seeds a fight already standing
   in the room and sets `CLAWDND_GATE_COMBAT_SPRINT=1`, which *turns off* the travel/rest progression
   floor (`assert_behavioral.py`). It proves the engine *resolves* a fight; it says nothing about
   whether a free-play persona ever *reaches* one — which was the real 0-combat failure.
3. **Cross-persona variance** (the actual G3 fail). Real sweeps fail on the **veteran (sat=5)** or the
   adversarial (Enter-submit move-sink), not the optimizer — which "takes one turn to confirm the loop
   and doesn't care about prose." One fixed persona samples the wrong order statistic (G3 is the
   *minimum* across personas, not the mean).
4. **A 4-beat duo falls below `MIN_BEATS=6`** → silently disarms 5 FATAL behavioral floors
   (`world_advanced_time`, `party_traveled`, `combat_not_left_active`, `xp_awarded`, `player_probed`).
5. **Cold-open setup integrity** (the #356 native-transition gate; the stochastic no-PC seating bug in
   `play_party.sh`) — a snapshot pre-seats past all of it.

Honest signal of the naive design: **~55–65%**, not 80% — and the missing 35–45% is concentrated in
the bug classes that have actually shipped.

---

## The corrected 3-tier design

| Tier | What | Cost | Owns |
|---|---|---|---|
| **0 — Deterministic (CI/pytest, $0, ~60s)** | seat-path skill correctness · rest-restores · travel-moves · combat-through-engine · model normalizers | **~60s, $0** | the structural + **seat-path** + engine-transition classes — *including the skill-case crit* |
| **1 — Fast LLM loop (~13 min, ~$2.5)** | **rotated** persona `[iter % 5]` from a **short cold-open** (catches seating + free-play reachability + variance over 5 loops) · **≥6-beat** duo (floors stay armed) · a "free-play *reached* combat" check distinct from the sprint | **~13 min** | the satisfaction/quality *iteration* signal (honestly, not a verdict) |
| **2 — Milestone sweep (unchanged)** | full 5-persona `.app` + RRI + native part-A, **+ correlation tracking** | ~90 min | the release verdict |

**The biggest lever isn't a new harness — it's moving deterministic signal into pytest/CI.** The
seat-path skill test alone (Tier 0) would have caught the optimizer's #1 crit in CI for $0, with zero
sweeps.

### Tier 0 — built now (`qa/fast_gate.sh`)
Runs a curated subset of the existing deterministic engine tests + the new end-to-end seat-path skill
test (`test_canon_wizard_seat_yields_proficient_skill_bonus` in `tests/test_canon_abilities.py`).
Covers: G1 combat (`test_combat`), G1 rest (`test_rests`), G1 travel (`test_travel`), G2 seat-path
data (`test_canon_abilities` + `test_character_skill_normalization`). `$0`, ~60s, no LLM, no cold-open.
Run it on every change before deciding whether a heavier probe is even warranted.

### Tier 1 — the fast LLM loop (next, with the critique's mitigations baked in)
Not yet built — it needs a small harness change (skip the cold-open splice when handed a seeded
snapshot, in `run_duo.sh` and `ui_playtest.sh`). The **non-negotiable corrections** from the critique:
- **Rotate the persona** by iteration index `personas[iter % 5]` — same per-loop cost, full variance
  over 5 loops. Record which persona ran so the ledger shows coverage-over-time.
- **Keep the duo ≥6 beats** so `assert_behavioral.py`'s progression floors stay armed.
- **Run *some* persona from a real cold open each loop** (not only a snapshot seed) so seating +
  free-play reachability stay covered; add a `seed_canon_fixture.py` seat-path assertion every loop
  (zero-LLM) to keep skill-case/seating coverage even when a probe snapshot-seeds.
- **Add a "free-play *reached* combat/travel/rest" check** distinct from the seeded sprint — never let
  the sprint's green imply reachability.
- **Story/mech on a short seeded duo is a regression *tripwire*, not the 4.3/4.5 verdict** (those bars
  were calibrated on 8-beat cold-opened duos). Defer the precise judgment to the sweep.

### Tier 2 — keep honest via measured correlation
Every K iterations / before merge, run the full sweep and write the row to `qa/scores.db`. Then
compute the **correlation** between the fast-gate verdict and the full-RRI verdict over history — so
"~80% signal" is *measured*, not asserted. Never write `pass=1` to `scores.db` from a fast run, and
label every fast verdict **"INNER-LOOP — not a release verdict."**

---

## What the fast loop intentionally DEFERS to the milestone sweep (the honest ~20%)
Cross-persona breadth in a single run; the cold-open / max-effort world-build itself; the #356 native
`.app` transition (macOS Part-A); full-arc long-horizon coherence; the authoritative 4.3/4.5 story/mech
bar; and the RRI rollup. The fast gate answers *"did this change break the core loops / seat path /
single-persona satisfaction — worth spending 90 min on the sweep?"* — not *"is this releasable?"*
