# Action-Replay envelope contract (M0)

> **Status:** M0 freeze artifact — epic **#645** (Render Frontier), child task **R645.1**.
> This is the **third** M0 contract surface, alongside the two already landed in this dir:
> the [render-profile contract](./render-profile.md) (R0.1, *frame state*) and the
> [graphical move-intent vocabulary](./move-intents.md) (R0.2, *the write lane*). It formalizes
> the third M0 freeze the canonical roadmap names — **`/events` ordering/replay guarantees**
> (R0.3 / GAP 2 in `spikes/m0-phaser-thin-client/README.md`) — into a concrete envelope a
> thin-client renderer plays as discrete animated beats. Companion to the canonical roadmap
> (`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`, §4 decision 5). This doc **defines the contract**;
> the implementing PR (envelope projection over `/events`, the ordered-replay test) lands later
> against `viewer/server.py` + `viewer/tests/`.

## What it is

A turn-based *video game* (GT1 SNES pixel; GT2 Pillars/BG isometric) does not just draw the
current frame — it **animates the transitions** between committed engine states: an attack
swings, damage numbers fly, a token falls on death, a heal pulses. The render-profile says
*how to draw a location and its actors*; the move-intents say *what a click sends back*. Neither
says **what sequence of animated beats to play after the engine resolves a turn.** That is the
gap this contract closes.

The **Action-Replay envelope** is the ordered, replayable projection of the engine's combat/turn
events that a renderer **tweens deterministically with zero client rules.** Each beat is one
envelope record:

```
{ seq, actor_fk, verb, target_fk, result, anim_hint }
```

The renderer pulls the envelope from `/events`, sorts by `seq`, and plays each record as one
animation — never deciding *what happened*, only *how to show it*. It is the animation timeline
the spike README flags as under-specified (GAP 2, "B2 — animation timeline" in the #645 spec).

## The one invariant (why this contract exists at all)

**The Python engine is the sole writer of game state. The renderer is a *projection*, never a
second source of truth.** This is the same invariant the [render-profile contract](./render-profile.md#the-one-invariant)
and the roadmap (`WORLDOS-GRAPHICS-ROADMAP.md` §0, §4.1) state for *frame state* — restated here
for the *time axis*:

- The engine resolves a turn (rolls, damage, conditions, death, clock advance) and writes the
  outcome to its **sole-writer narration record**, the per-session JSONL log that `/events` tails.
- The envelope is a **read-only derivation** of that log. It carries *no* state the engine did not
  already decide. A renderer that drops every poll and re-fetches loses nothing — the engine
  re-emits the same ordered beats.
- A renderer **never** computes a result, predicts a beat, or persists envelope output as truth.
  It owns pixels, tweens, and input gestures — nothing else.

### Why generative world-models are rejected as the renderer (the load-bearing corollary)

This invariant is precisely **why a generative world-model (Genie 3, Runway GWM-1) cannot be the
renderer.** A world-model *generates* the next frames from its own learned dynamics — it is a
**second source of truth that drifts** (the #645 viability matrix: ~1-min visual memory, then
clipping/gravity inconsistency; research preview, no multi-tenant license). The moment the
rendered surface invents what happened next, it has violated sole-writer. The Action-Replay
envelope is the structural reason WorldOS renders **engine-state-driven sprites/tiles/backdrops**
(PixelLab / Retro-Diffusion, production-viable today) and reserves world-models for
*non-authoritative* establishing-shots/cutscenes only (R645.5, parked under #585/#586). The
renderer **replays**; it never **imagines**.

## The schema

The envelope is an array of records, each describing one animated beat. Every field except
`anim_hint` is a **projection of engine truth** (FK or engine-decided value); `anim_hint` is the
only renderer-facing presentation field, and it is **advisory** (a renderer with no matching
animation falls back to a generic beat — it must never block on a hint it doesn't recognize).

| Field | Type | Source | Meaning |
|-------|------|--------|---------|
| `seq` | integer | **engine** (`/events` line index) | Monotonic per-beat order key. **Authoritative ordering** — see below. |
| `actor_fk` | string \| null | engine actor id | FK to the engine actor that acted (`char_<uuid12>` / `mon_<…>`); joins to `render-profile core.actors[].engine_actor_id`. `null` for environment/DM beats. |
| `verb` | string | engine event kind | The resolved action class (closed vocabulary, below) — *what the engine did*, not what the player asked for. |
| `target_fk` | string \| null | engine actor/location/zone id | FK to the thing acted upon (actor id, `engine_location_id`, or zone name). `null` for untargeted beats. |
| `result` | object | engine outcome | The engine-decided outcome of the beat (roll, damage, condition delta, death, miss). Renderer reads it to choose numbers/states to show; it **never recomputes** it. |
| `anim_hint` | string \| null | derived (advisory) | Presentation cue for the renderer (e.g. `melee_swing`, `cast_projectile`, `damage_flinch`, `death_fall`, `heal_pulse`, `zone_move`, `none`). **Advisory only**; unknown/absent → generic beat. |

```jsonc
// GET /events?since=<cursor>  →  the renderer's ordered animation queue
[
  { "seq": 412, "actor_fk": "char_a1b2c3d4e5f6", "verb": "attack",
    "target_fk": "mon_cultist_03",
    "result": { "outcome": "hit", "roll": { "d20": 17, "total": 21 },
                "damage": { "total": 8, "type": "slashing" } },
    "anim_hint": "melee_swing" },

  { "seq": 413, "actor_fk": "mon_cultist_03", "verb": "condition",
    "target_fk": "mon_cultist_03",
    "result": { "outcome": "bloodied", "hp_after": 3 },
    "anim_hint": "damage_flinch" },

  { "seq": 414, "actor_fk": "char_77ee99aa11bb", "verb": "cast",
    "target_fk": "char_a1b2c3d4e5f6",
    "result": { "outcome": "heal", "amount": 7, "hp_after": 24 },
    "anim_hint": "heal_pulse" },

  { "seq": 415, "actor_fk": "char_a1b2c3d4e5f6", "verb": "move_to_zone",
    "target_fk": "the dais",
    "result": { "outcome": "moved", "zone": "the dais" },
    "anim_hint": "zone_move" }
]
```

### `verb` — the closed, engine-decided vocabulary

`verb` mirrors the engine's **resolved** event classes (the `worldos.combat_event.v1` payload
already on combat events: `event`, `actor`, `target`, `roll`, `damage` — see
`viewer/server.py:_combat_battle_log`). It is the **outcome class**, not the player's intent —
the resolved counterpart to the move-intent `kind`:

| `verb` | Engine event it projects | Typical `anim_hint` |
|--------|--------------------------|---------------------|
| `attack` | weapon attack resolution (hit/miss/crit) | `melee_swing` / `ranged_shot` / `miss` |
| `cast` | spell resolution | `cast_projectile` / `cast_aoe` / `heal_pulse` |
| `damage` | HP loss applied (any source) | `damage_flinch` |
| `condition` | condition gained/lost, bloodied, downed | `status_apply` / `status_clear` |
| `death` | a combatant drops / dies | `death_fall` |
| `save` / `check` | saving throw / ability check resolved | `roll_reveal` |
| `move_to_zone` | a combatant repositions across named zones | `zone_move` |
| `travel` | the party moves to a new location | `scene_transition` |
| `narrate` | a non-mechanical DM beat (sets up the next animated beat) | `none` |

**One verb may not map 1:1 to one move-intent** — a single `{kind:"attack"}` intent
(`move-intents.md`) can resolve into several beats (`attack` → `damage` → `condition` → `death`),
each its own envelope record with its own `seq`. The renderer plays them in `seq` order to get the
full animated exchange. This asymmetry is deliberate: **intents are requests; envelope records are
the engine's resolved, ordered narration of what those requests produced.**

## The `/events` ordering / replay-determinism requirement

The envelope is only useful if it is **totally ordered and replayable** — otherwise tokens pop,
double-fire, or animate out of sequence. Two guarantees, both grounded in mechanics that
**already exist** in `viewer/server.py`:

1. **Total order via `seq`.** `/events` already stamps every row with `seq` = its **absolute line
   index in the session log** (`_read_events`, `viewer/server.py:5880` — *"a line index is a true
   monotonic per-beat identity"*). The session log is the engine's sole-writer narration record,
   so `seq` is a true monotonic per-beat clock. The renderer **must** sort by `seq` and **must
   not** rely on array/arrival order. Across a session rotation, the globally-unique order key is
   `${session_id}:${seq}` (the viewer already composes exactly this for narration dedup —
   `viewer/server.py:5842`, `_session_recent_events`); the envelope inherits that key space so a
   beat is never replayed twice or dropped across a rotation.

2. **Replay determinism (idempotent re-fetch).** Polling `/events?since=<cursor>` from any cursor
   `≤ current` **must** return the same beats with the same `seq`, the same `result`, and the same
   relative order. This is what makes the renderer a pure projection: a dropped frame, a reconnect,
   or the M3 WebSocket/SSE upgrade (C8) changes *transport*, never *content*. A renderer that
   replays from `since=0` reconstructs the entire animated history identically. The half-written
   trailing-line guard already in `_read_events` (don't advance the cursor past a partial line)
   is part of this guarantee — a beat is only ever surfaced once it is fully committed.

### The acceptance test this contract owes M0

R645.1's implementing PR must add an **ordered-replay assertion** to the surface-stream test lane
(extends `viewer/tests/test_surface_stream.py`, named in the #645 spec as B2's retire-path):

- **Strict monotonic order:** for any `/events` response, `seq` values are strictly increasing
  within a session; `${sid}:${seq}` is globally unique.
- **Idempotent replay:** fetching `since=N` then `since=0` yields a superset whose tail matches the
  `since=N` result beat-for-beat (same `seq`, same `result`) — no reordering, no duplication, no
  drift.
- **Projection-only:** every `actor_fk` / `target_fk` resolves to an id present in the
  render-profile `core` (or is `null`); no envelope field carries state absent from the engine log.

These mirror the spike's BLOCKING **stable-actor-id** test (#431) — token tweening pops if ids
churn — and together they are the "surface-read guarantees" half of M0 (R0.3) made testable for the
*time axis* the render-profile (frame axis) and move-intents (write axis) don't cover.

## How a renderer consumes the envelope (the contract in one paragraph)

After sending an intent via `POST /move` (see [move-intents.md](./move-intents.md)), the renderer
polls `GET /events?since=<cursor>`, receives zero or more Action-Replay records, **sorts by `seq`**,
and enqueues each as one animated beat. For each record it looks up `actor_fk` / `target_fk` in the
[render-profile](./render-profile.md) `core.actors[]` / `core.locations[]` to find the sprite/token
to animate, plays the `anim_hint` tween (falling back to a generic beat if the hint is unknown),
and renders the `result` (damage number, HP bar, condition icon, death) **exactly as the engine
decided it** — never recomputing. The animation queue drains in `seq` order; the engine snapshot
(`/combat-surface`, `/character-surface`, `/atlas-surface`) always overrides optimistic UI once the
beats finish. Because every beat is a replay of committed engine truth, frame latency is irrelevant
(a beat may take 200ms or 2s) and **no input prediction, rollback, or netcode is ever needed** —
the turn-based model is what makes this thin-client contract closeable today (RTwP is rejected
permanently; roadmap §4.6).

## Versioning

The envelope rides the **same `schema_version` discipline** as the render-profile: adding a new
`verb`, a new `anim_hint`, or an optional `result` field is **additive and backward-compatible**
(a renderer ignores hints/verbs it doesn't know and plays a generic beat). Removing or repurposing a
field, or changing the meaning of `seq`/ordering, is a **breaking change** that bumps the contract
major and requires migrating authored renderers. The reversal signal (when this contract is wrong):
the ordered-replay test can't reconstruct an animated turn from `/events` alone, OR a renderer needs
a beat the envelope can't express without inventing state — at which point the gap is re-frozen
*before* any tier ships it, exactly as move-intents froze the write vocabulary in M0.

## How it slots under M0 / M1 / #441

- **M0 (contract freeze):** This is the **third leg** of the M0 freeze the roadmap names in §4
  decision 5 — *"render-profile · graphical move-intent vocabulary · surface-read guarantees
  (… `/events` ordering)."* render-profile (R0.1) and move-intents (R0.2) are landed; this doc
  freezes the `/events` **ordering/replay** half of R0.3 as a concrete envelope, so all three M0
  contracts are specified before any renderer build. It is **read-only over the already-shipped
  `/events` + `seq` machinery** — no new engine state, no sole-writer change.

- **#441 (M1 — GT1 SNES pixel turn-based MVP):** the GT1 combat view (R1.2, "zone-mode combat +
  character UI, pure replay of engine `/events`") is the **first consumer** of this envelope — it is
  exactly the "pure replay of engine `/events`, zero client rules" the capability matrix assigns to
  **C3 combat presentation, Branch A** (`WORLDOS-GRAPHICS-ROADMAP.md` §2). The R1.3 QA gate ("no VTT
  grid chrome in zone mode") pairs with this contract's projection-only guarantee: the renderer
  shows *engine-decided zone beats*, never invented coordinate tactics.

- **GT2 (#456) + the M0 spike:** the same envelope drives GT2 isometric replay unchanged (it is
  renderer-agnostic, like `core`). The throwaway `spikes/m0-phaser-thin-client/` proves the read
  side; this contract makes its "replay engine-decided combat (zero client rules)" line testable.

- **Out of scope for M0:** measured-grid tactics (C1-B / #461) would add coordinate beats to the
  envelope — **deliberately excluded**, evidence-gated, and would be its own first-principles pass
  (it requires the engine to gain coordinate authority, a sole-writer *state* change). The envelope
  v1 carries **named-zone beats only**, mirroring the render-profile's `theater|zone`-only
  positioning.

## Cross-references (do not duplicate — these are the companion contracts)

- **Frame state:** [`render-profile.md`](./render-profile.md) + [`render-profile.schema.json`](./render-profile.schema.json)
  — how to draw a location and its actors (the FKs this envelope's `actor_fk`/`target_fk` join to).
- **Write lane:** [`move-intents.md`](./move-intents.md) — the intents a renderer sends; this
  envelope is the *resolved, ordered* read-side counterpart (intents in → ordered beats out).
- **Canonical roadmap:** [`../WORLDOS-GRAPHICS-ROADMAP.md`](../WORLDOS-GRAPHICS-ROADMAP.md) §0 (the
  invariant), §2 (C3 combat presentation), §4 decision 5 (the three M0 contracts), §5 (M0 R0.3 /
  M1 #441).
- **Spike (read-side proof):** `spikes/m0-phaser-thin-client/README.md` GAP 2 ("`/events`
  ordering/replay semantics").
- **Engine substrate (informative, not normative):** `viewer/server.py:_read_events` (`seq`
  stamping, L5880), `_session_recent_events` (`${sid}:${seq}` key, L5842), `_combat_battle_log`
  (`worldos.combat_event.v1` payload the envelope projects, L2012).
