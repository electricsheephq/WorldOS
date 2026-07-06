# Engine-run combat loop + monster-AI (Track 2a) — design + PR ladder

Status: DESIGN ONLY. No behavior change. This ADR is the load-bearing contract the
PR ladder below implements. Citations are `file:line` against `servers/engine/`
at `56df939` (verify before editing — line numbers drift).

---

## 1. Context, goals, non-goals

### Where combat is today
Combat *resolution* is already engine-run, SRD-faithful, and **LLM-free** — but the
*sequencing* (whose turn, what they do, advance) is hand-driven by the DM via one
`claude -p` call per fight. The proof of the resolution being reusable, all in
`servers/engine/`:

- `attack()` (`server.py:5273`) — rolls `1d20+bonus` vs the target's AC, sets
  `hit = atk.crit or (not atk.fumble and atk_total >= target_ac)` (`server.py:5479`);
  crits double damage dice (`combat.double_dice`, `server.py:5711`); a miss spends no
  dice and applies no damage; the full economy/advantage/maneuver/rider/parry machinery
  runs inside this one function. **No LLM anywhere in the call.**
- `next_turn()` (`server.py:4620`) — advances to the next living combatant, enforces the
  PC-skip guard (`server.py:4636`), and auto-rolls end-of-turn **repeat saves**
  (`server.py:4656`) — Hold Person etc. end themselves without a DM prompt.
- `start_combat()` (`server.py:3897`) — rolls initiative (`1d20+initiative_bonus`,
  `server.py:3916`), builds the order, surfaces Extra-Attack / Multiattack / omitted-companion
  advisories.
- `use_action()` (`server.py:4947`) — action economy: `action | bonus | reaction | free |
  movement | skip | disengage | dash`.
- `cast_spell()` (`server.py:7184`) — any of ~339 SRD spells; slots, upcast, concentration,
  saves, conditions — all engine-tracked.
- `move_to_zone()` (`server.py:4115`) / `move_to_coords()` (`server.py:4325`) — the two
  positional models (zones S2.7, grid #461).
- `combat_grid.py` (#461) — pure (x,y) helpers: Chebyshev distance (`combat_grid.py:19`),
  movement budget (`combat_grid.py:47`), open-floor reachability flood (`combat_grid.py:56`),
  opportunity-attack-on-leave predicate (`combat_grid.py:124`).
- `_monster_combat_entry()` (`server.py:3685`) — already produces a monster's
  **authoritative attack lines** (to-hit, damage, `damage_rolls`, Multiattack composition,
  legendary actions) straight from the bestiary stat block. This is the exact tactical input
  the monster-AI needs; it does not need to invent anything.
- `spawn_monster()` (`server.py:2521`) + the bestiary (`bestiary.py`) — populate the fight.

### The goal (owner-decided)
Evolve combat from DM-narrated theater-of-mind toward an **engine-run, BG3-style** system:
the engine drives the fight (faster, SRD-faithful) and the DM narrates the *felt* result
instead of every roll. Two modes:

- **LIVE** — the engine auto-sequences **monster/NPC** turns; **PCs stay player/DM-driven**.
  The engine runs each non-PC turn through the same write path, then **stops at a PC turn**
  and hands a compact per-turn digest to the DM to narrate. The felt result is still the DM's.
- **TEST** — full engine-only auto-combat (random PCs vs random monsters, **no LLM at all**),
  for a trustworthy mechanical signal independent of the (currently hanging) LLM scorer.

### Goals
- Reuse the existing resolution surface **verbatim** (sole-writer preserved — the loop calls
  the same `attack()`/`cast_spell()`/`move_to_*`/`next_turn()`, never a parallel path).
- A **pure, deterministic, testable** monster-AI (no LLM, no I/O) with a clear path from a
  greedy v1 to BG3-tactical depth.
- TEST-only toggles (`force_hit`, `fast_resolve`, a deterministic dice-seed) that can **never**
  affect a live game.
- SRD 5.2 fidelity. Additive, default-off, byte-identical when the loop is not engaged.

### Non-goals
- **No new resolution rules.** The loop orchestrates existing verbs; it does not re-implement
  hit/damage/saves.
- **No PC auto-play in LIVE.** PCs are always player/DM-driven live (the loop stops at a PC turn).
- **No tactical-grid expansion** beyond what #461 already ships (movement/OA) — BG3-grade
  positioning depth is PR-D, gated on the #461 ladder.
- **No DM-narration change** in the engine. The DM still narrates the felt result live; the
  loop only *surfaces* a digest, it does not write prose.

---

## 2. The monster-AI contract

### Where it lives
A new pure module `servers/engine/combat_ai.py`, mirroring `combat_grid.py`'s pure-helper
posture: **no Campaign mutation, no lock, no save, no LLM, no I/O** — just a decision over
read-only state. The MCP-facing loop in `server.py` wraps it with the lock + the existing
write verbs. Keeping it pure is what makes it deterministic and unit-testable.

### Signature

```python
def pick_action(actor: Character, combat_state: CombatView) -> Intent: ...
```

- `actor` — the acting `Character` (read-only). The AI reads exactly what the engine already
  exposes: `speed` (`models.py:858`), `conditions`, `current_hp`/`max_hp`, `class_resources`
  (`models.py:922`), `spell_slots`/`spells_known` (`models.py:914-915`), and the
  combatant's grid `x`/`y` / `zone` (`models.py:1319,1328`).
- `combat_state` — a **read-only snapshot** assembled by the loop from the live `Combat`
  (`models.py:1363`) + `_monster_combat_entry()` (`server.py:3685`) for the actor's
  authoritative attack lines, plus the living foes/allies with positions, AC, HP. No new
  persistent model — `CombatView` is an in-memory dataclass the loop builds per turn.
- Returns an **`Intent`** — a tiny typed value object the loop translates into one or more
  existing-verb calls:

```python
@dataclass(frozen=True)
class Intent:
    kind: Literal["attack","cast","move","dash","disengage","dodge","skip"]
    target_id: str = ""           # for attack / single-target cast
    attack_name: str = ""         # scopes a Multiattack budget (server.py:5304)
    spell_name: str = ""          # for cast
    to_cell: tuple[int,int]|None = None   # for move (grid)
    to_zone: str = ""             # for move (zone)
    note: str = ""                # human-readable rationale (for the digest / debugging)
```

`Intent` is **declarative**: it names *what* the actor wants. The loop is the only thing that
writes — it maps `Intent.kind` to `attack()` / `cast_spell()` / `move_to_coords()` /
`move_to_zone()` / `use_action(kind=...)`, and re-asks `pick_action` for the next attack when a
Multiattack grants more than one strike. If the engine refuses an Intent (out of reach,
incapacitated, no slot), the loop degrades to `skip` rather than crashing — exactly the
advisory-not-block posture the rest of combat already uses.

### Greedy v1 (highest-expected-value, deterministic)
The v1 policy is intentionally simple and explainable, scored by a pure EV function over the
authoritative numbers the engine already surfaces:

1. **Retreat-if-low** — if `current_hp <= floor(max_hp * RETREAT_FRACTION)` (e.g. 0.25) and a
   safer cell/zone exists, prefer `disengage` + `move` away from the nearest threat (uses
   `combat_grid.reachable` + `distance_ft`). Bounded so a monster does not flee forever.
2. **Best in-reach attack** — among the actor's stat-block attacks (`_monster_combat_entry`),
   pick the highest-EV strike on a *reachable, living enemy*. EV ≈ `P(hit) * E[damage]`, where
   `P(hit)` is derived from to-hit vs the target's effective AC and `E[damage]` from the
   attack's `damage_rolls`. Ties broken by lowest target HP (focus-fire) then by stable id
   (determinism). Multiattack composition is honored by emitting the Multiattack's sequence.
3. **Best cantrip / spell** — if no melee/ranged weapon is in reach but a damaging cantrip or a
   save-or-suck spell with an available slot can reach, pick the highest-EV one
   (`cast_spell`). Save spells score by `P(target fails save) * value`.
4. **Move-to-reach** — if the best attack/cantrip is out of reach, `move` toward the
   highest-EV target up to the movement budget (`combat_grid.movement_budget_cells`), then the
   loop re-asks `pick_action` so a "move then attack" turn resolves both halves.
5. **Dodge / skip fallback** — if nothing productive is reachable, `dodge` (defensive) or `skip`.

`RETREAT_FRACTION`, focus-fire, and the EV weights are module constants so the policy is one
readable function. **Path to BG3-tactical:** the same `Intent` contract supports richer policies
later — positioning for cover/flanking (#461 PR-3/PR-7), target-priority heuristics (focus the
healer, break concentration), action-economy optimization (bonus-action spells), and a pluggable
`policy=` arg so a fight can run greedy-v1 or a future `tactical-v2` without touching the loop.

### Why pure
No LLM, no I/O, no campaign mutation means: (a) **deterministic** — same state + same seed →
same Intent, so the combat smoke is reproducible; (b) **unit-testable** in isolation (feed a
`CombatView`, assert the `Intent`) without standing up a campaign; (c) **fast** — no model call
on a monster turn, which is the whole point of LIVE mode (the DM's budget is spent on narration,
not on rolling goblin dice).

---

## 3. The auto-sequencing loop

Two new MCP tools in `server.py`, both calling **only** the existing write verbs (sole-writer
preserved — there is exactly one path that mutates the campaign, and it is the same one the DM
uses today):

### `run_combat_round(campaign_id, mode="live") -> dict`
Sequences the combatants from the current turn to the end of the round:

```
for each combatant from current turn onward (next_turn order):
    if mode == "live" and combatant.kind in ("player","companion"):
        STOP — return the digest so far + {"awaiting_pc": <id>}   # PCs stay DM-driven
    intent = combat_ai.pick_action(actor, build_view(c, actor))
    apply(intent)            # → attack() / cast_spell() / move_to_*() / use_action()
        # a Multiattack re-asks pick_action per granted strike (server.py:3787 budget)
    record per-turn digest entry {actor, intent.kind, rolls, damage, target_state}
    next_turn(campaign_id)   # advance + auto-resolve repeat saves (server.py:4656)
```

- The **LIVE** variant runs only **non-PC** turns and **stops at the first PC turn**, returning
  a compact `digest` (one line per monster turn: who, what, the roll, the damage, any condition
  applied / kill) for the DM to narrate, plus `awaiting_pc`. The DM resolves the PC turn with the
  normal verbs, then calls `run_combat_round` again to run the next batch of monster turns.
- The **TEST** variant (`mode="test"`) runs **everyone**, including the randomly-generated PCs.

### `run_combat_autonomous(campaign_id, mode="test", max_rounds=20) -> dict`
Calls `run_combat_round` repeatedly until a victory/defeat/round-cap condition:

```
while combat active and round <= max_rounds:
    result = run_combat_round(campaign_id, mode)
    if mode == "live" and result.awaiting_pc:   # never auto-plays a PC
        return result                            # hand control back to the DM
    if one side has no living combatants:        # victory / defeat
        end_combat(...)  ; break
return rollup {rounds, turns, victor, per-round digests, mechanic-fired flags}
```

- **LIVE** never auto-plays a PC: the moment a PC turn comes up it returns to the DM.
  `run_combat_autonomous` in live mode is therefore "advance the fight up to the next PC
  decision" — useful for clearing a swarm of mooks between PC turns.
- **TEST** runs the whole fight to a terminal state with no LLM and (with a seed) reproducibly.
- The round-cap is a safety rail against a pathological non-terminating fight (two monsters that
  can never hit each other); it ends the fight as a draw and flags `round_cap_hit`.

**Sole-writer invariant.** Neither tool introduces a new write path. Every state change goes
through `attack()`/`cast_spell()`/`move_to_*()`/`use_action()`/`next_turn()`/`end_combat()` under
the existing `campaign_lock` + `save_campaign`. The loop is an *orchestrator*, not a second engine.

---

## 4. Test toggles (TEST-ONLY, can never affect a live game)

Three additive, default-off knobs, **all guarded** so they are inert outside an explicit sandbox/
test context. The guard is the load-bearing safety design — `force_hit` corrupting a real game is
the headline risk, so the toggles must be *structurally* unreachable live, not merely defaulted off.

### 4a. The sandbox guard
There is **no** existing sandbox/test-mode flag in `server.py` (only `WORLDOS_TOOLTIMING_PATH`,
`server.py:169`) — so this is net-new. Design:

- A single module-level predicate `_combat_test_mode_enabled() -> bool` that returns True **only**
  when an explicit opt-in is present: env `WORLDOS_COMBAT_TEST=1` **and** the campaign is flagged
  as a sandbox (a new `Campaign.is_sandbox: bool = False`, set only by the test pre-seed, never by
  any live-play tool). Both must hold — env alone or flag alone is not enough. This is a
  belt-and-suspenders guard: a production deployment never sets the env, and a real campaign never
  carries the flag.
- The `force_hit` / `fast_resolve` reads are wrapped: `if hr.force_hit and _combat_test_mode_enabled()`.
  If the guard is false, the toggle is dead code — byte-identical to today.

### 4b. `HouseRules.force_hit` (TEST-ONLY)
A new field on `HouseRules` (`models.py:1524`), additive default `False`:

```python
force_hit: bool = False  # TEST-ONLY (sandbox-guarded): the attack roll auto-hits
```

Hooks in at exactly **one** place in `attack()` — the hit decision (`server.py:5479`). When the
guard is live and `force_hit` is set, `hit` is forced True **but the natural die is still rolled
and read for crit/fumble** so the rest of the function (crit-doubling, parry, riders, economy) runs
unchanged. Specifically: `force_hit` must **NOT** synthesize a nat-20 — it forces `hit`, not
`is_crit`. A forced hit on a natural non-20 stays a normal hit; a natural 20 still crits as it
would anyway. This keeps crit/damage accounting honest (the smoke can still assert that crits
double dice and that a real nat-1 is *not* turned into a hit unless `force_hit` is explicitly on).

### 4c. `HouseRules.fast_resolve` (TEST-ONLY)
```python
fast_resolve: bool = False  # TEST-ONLY (sandbox-guarded): damage = average instead of rolled
```
Hooks in at the damage roll (the `dice_mod.roll(expr)` calls in `attack()`'s damage branch,
`server.py:5712`/`5672`). When live, damage uses the **expected average** of the expression
(`NdM` → `N*(M+1)/2`) instead of a random roll, so a TEST fight resolves in a predictable number
of rounds. Crit-doubling still applies to the dice term first (double the dice, then average), so
crit accounting is preserved. `fast_resolve` only changes the *magnitude* of damage, never whether
a hit lands or whether a crit happened.

### 4d. Deterministic dice-seed
`dice.roll()` already accepts a `seed` (`dice.py:44`) but the combat path calls it **without**
one (`server.py:5464`, `5712`, …). Per-call seeds would make every roll identical (a constant),
which is wrong. The correct design is a **process-level seeded RNG**:

- A module-level `random.Random` in `dice.py` seeded once from `WORLDOS_COMBAT_SEED` (env) or a
  `seed=` param threaded by the TEST loop. `roll()` draws from this shared RNG when no explicit
  per-call seed is given, so a *sequence* of rolls is reproducible (seed S → the same fight every
  time) without every roll collapsing to one value.
- This is **additive**: when `WORLDOS_COMBAT_SEED` is unset, the RNG seeds from the OS entropy
  exactly as `random.Random(None)` does today — byte-identical non-deterministic behavior. The
  seed is a TEST affordance; it does not need the sandbox guard (a deterministic seed in a live
  game is harmless — it only fixes the dice, it does not bend outcomes), but it is only *set* by
  the TEST lane.

### Why `force_hit` must not corrupt crit/damage accounting
The smoke's whole value is asserting that *real* mechanics fire. If `force_hit` forged a nat-20,
crit-doubling would fire on every hit and the smoke could no longer distinguish "crits double
dice" (a real invariant) from "force_hit faked a crit." So `force_hit` is scoped to the `hit`
boolean only; `is_crit`, `crit_source` (`server.py:5515`), and the damage-doubling stay driven by
the genuine natural die. Likewise `fast_resolve` averages the *rolled* dice (post crit-doubling),
so a crit still visibly doubles the damage magnitude.

---

## 5. The engine-only combat smoke (`qa/combat_smoke.py`)

A new pure-Python harness — **zero LLM** — that gives a trustworthy mechanical signal independent
of the hanging Angry-DM scorer (the scorer hangs on combat-sprint transcripts; the smoke does not
use it at all).

- **Setup:** reuse `qa/pre_seed_combat.py`'s zero-LLM seed (already spans the full surface: a
  Battle Master fighter, a War-Domain cleric with save spells, a Ghoul whose Claw forces a CON
  save-or-Paralyzed, mooks tuned to reach 0 HP in ~3-5 rounds). Optionally randomize PC/monster
  picks from the bestiary for breadth.
- **Run:** `start_combat()` → `run_combat_autonomous(mode="test", seed=S)` to a terminal state.
- **Determinism:** set `WORLDOS_COMBAT_SEED` so the whole fight is reproducible; assertions are
  stable across runs.
- **Assert every mechanic fires** (the smoke FAILS if a class of mechanic never appeared, which is
  how it catches the seam-bugs a code-read misses):
  - a **hit** and a **miss** both occurred (the to-hit gate works both ways);
  - a **crit** doubled its dice (run enough rounds / a seed that produces one; or a `force_hit`+
    nat-20 fixture);
  - a **save** was forced and both a success and a failure occurred (Ghoul Claw CON save, cleric
    Hold Person);
  - a **condition** was applied and later **cleared** by an end-of-turn repeat save
    (`next_turn` auto-resolve, `server.py:4656`);
  - **concentration** was set and broken by damage;
  - a **class resource** (Superiority Die / Channel Divinity / spell slot) was spent and the pool
    decremented;
  - **XP** was awarded on a kill (`_award_kill_xp`, the `end_combat` XP path);
  - a **death save** sequence ran when a PC dropped to 0 HP.
- **Why it is trustworthy:** it exercises the *real* `attack()`/`cast_spell()`/`next_turn()` write
  path (not a mock), deterministically, with no model in the loop — so a green smoke means the
  mechanics genuinely fired, and a red smoke points at a real seam-bug. This is the lesson from the
  felt-vs-scores work: *running the surface* found two HIGH seam-bugs (#1033, #1081) a confident
  code-read had called "≥4.5, engine-enforced." The smoke makes that surface-run cheap, repeatable,
  and CI-able.

---

## 6. Phasing, risks, reuse-vs-net-new

### PR ladder
| PR | Scope |
|----|-------|
| **PR-A** | `combat_ai.py` (pure `pick_action` + greedy-v1 EV policy + `Intent`) and the TEST toggles (`force_hit`/`fast_resolve` + sandbox guard + the process-level dice-seed). No loop yet; `combat_ai` unit-tested in isolation. |
| **PR-B** | `qa/combat_smoke.py` + `run_combat_autonomous(mode="test")` + `run_combat_round(mode="test")`. The engine-only smoke is the deliverable; this is where the trustworthy mech signal lands. CI job runs it on a fixed seed. |
| **PR-C** | The **LIVE** loop: `run_combat_round(mode="live")` (auto-runs non-PC turns, stops at a PC turn, returns the digest) + `run_combat_autonomous(mode="live")`. Wire the digest into the DM surface so the felt result is narrated. |
| **PR-D** | Tactical depth: a `policy="tactical-v2"` that uses #461 positioning (cover/flanking/focus-fire/concentration-breaking) for BG3-grade monster play. Gated on the #461 grid ladder (PR-3 cover, PR-7 flanking). |

### Risks
- **Monster-AI quality.** A greedy v1 can look dumb (no kiting, no focus-fire beyond lowest-HP).
  *Mitigation:* the `Intent`/`policy=` seam means v2 is additive; v1 is explicitly "correct but
  simple," and the smoke asserts *mechanics*, not tactical brilliance.
- **LIVE mode changing felt pacing.** Auto-running monster turns could feel like the DM lost the
  fight's rhythm, or surface a wall-of-digest. *Mitigation:* the digest is compact (one felt line
  per monster turn) and the DM still narrates; LIVE is opt-in per fight, theater/zone fights are
  unchanged when the loop is not engaged.
- **`force_hit` (or `fast_resolve`) leaking to a live game** — the headline risk. *Mitigation:*
  the double guard (env `WORLDOS_COMBAT_TEST=1` **and** `Campaign.is_sandbox`), the toggle being
  dead code when the guard is false, and a unit test asserting that with the guard off a `force_hit`
  HouseRules value changes nothing. The flag is never set by any live-play tool.
- **AI infinite loop / non-termination.** Two monsters that can never resolve. *Mitigation:* the
  `max_rounds` cap ends the fight as a draw with `round_cap_hit`.
- **Determinism regressions.** The process-level RNG must seed from OS entropy when unset.
  *Mitigation:* an explicit test that an unset `WORLDOS_COMBAT_SEED` is non-deterministic (two runs
  differ) and a set one is reproducible (two runs identical).

### Reuse vs net-new
| Concern | Status | Where |
|---|---|---|
| Hit/crit/damage resolution | **REUSE** | `attack()` `server.py:5273`, hit at `5479`, crit-double `5711` |
| Initiative + turn order | **REUSE** | `start_combat()` `server.py:3897` |
| Turn advance + repeat saves | **REUSE** | `next_turn()` `server.py:4620`, repeat saves `4656` |
| Action economy (dash/disengage/dodge/skip) | **REUSE** | `use_action()` `server.py:4947` |
| Spellcasting (slots/saves/concentration) | **REUSE** | `cast_spell()` `server.py:7184` |
| Movement (zone + grid) | **REUSE** | `move_to_zone()` `4115`, `move_to_coords()` `4325`, `combat_grid.py` |
| Authoritative monster attack lines | **REUSE** | `_monster_combat_entry()` `server.py:3685` |
| Multiattack budget | **REUSE** | `_attacker_multiattack_count()` `server.py:3787` |
| Dice + seed param | **REUSE** | `dice.roll()` `dice.py:40`, seed `dice.py:44` |
| Zero-LLM combat seed | **REUSE** | `qa/pre_seed_combat.py` |
| HouseRules toggle home | **REUSE** | `HouseRules` `models.py:1524` |
| **Monster-AI `pick_action`** | **NET-NEW** | `servers/engine/combat_ai.py` |
| **Auto-sequencing loop** | **NET-NEW** | `run_combat_round` / `run_combat_autonomous` in `server.py` |
| **TEST toggles + sandbox guard** | **NET-NEW** | `HouseRules.force_hit`/`fast_resolve`, `_combat_test_mode_enabled`, `Campaign.is_sandbox` |
| **Process-level dice-seed wiring** | **NET-NEW** | module RNG in `dice.py` + `WORLDOS_COMBAT_SEED` |
| **Engine-only combat smoke** | **NET-NEW** | `qa/combat_smoke.py` |

---

## 7. Decisions (resolved 2026-07-02 — recorded in #1100)

1. **LIVE companion turns.** Companions are `kind == "companion"`. The question was whether
   **companions** should be auto-sequenced by the engine in LIVE (they are NPCs the engine could
   run, freeing the DM), or DM-driven like PCs (they have approval/loyalty arcs that may want DM
   agency). **Decision: companions stay DM/agent-driven in LIVE, same as PCs** — the engine
   auto-runs only **hostile monsters/NPCs**. Companions are party-side and keep their
   approval/loyalty agency; only **TEST** mode auto-runs everyone.
2. **Digest granularity in LIVE.** The question was one felt line per monster turn vs. a
   per-round rollup — a swarm of 8 mooks is 8 lines/round either way. **Decision: per-round LIVE
   digest.**
3. **`fast_resolve` semantics.** The question was whether average damage is enough, or whether
   the owner wants a `force_damage=N` fixed-value variant too. **Decision: average-only** for v1;
   `fast_resolve` always uses the expected average of the damage expression, no fixed-value knob.
4. **Sandbox flag plumbing.** The question was whether `Campaign.is_sandbox` (a new top-level
   Campaign field touching `store.py`'s tolerant-load) is worth it, or whether an env-only guard
   is simpler. **Decision: the double guard** — `WORLDOS_COMBAT_TEST=1` **AND**
   `Campaign.is_sandbox` (the field). Defense-in-depth on the `force_hit` risk is worth the one
   field; both must hold for the TEST-ONLY toggles to be reachable at all.
5. **Retreat policy.** The question was whether monsters should ever flee (morale) or always
   fight to the death. **Decision: monster v1 = greedy retreat-if-low** — a `RETREAT_FRACTION`
   threshold triggers a retreat-if-low policy (not fight-to-the-death), with the hook present for
   a later, richer morale policy.
