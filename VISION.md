# WorldOS — Vision

> The anchor every non-trivial decision is measured against. When you are about to ask the
> owner "should I do X or Y?", run the `worldos-decide` skill against this document instead.

## What it is

WorldOS is a post-Baldur's-Gate-3 **living-world D&D 5e (SRD 5.2)** game: a deterministic Python
engine that is the sole writer of world state, the **OpenWorlds** in-browser viewer, and a native
macOS app. The player plays through **their own AI agent** — Claude Code, Codex, OpenClaw, Hermes,
or the WorldOS app — which acts as the Dungeon Master against the engine. The engine guarantees the
rules; the AI DM brings an **epic, Baldur's-Gate-caliber story** to life. The long arc: a
universe-system that generates worlds you play with any agent.

It is a real game **and** an experiment — in whether an autonomous agent can build, harden, and
steer a game system to a shippable bar, making its own decisions against this documented vision.

## North star

**A no-prior-knowledge player launches the app, plays a complete 8-beat Baldur's-Gate-caliber arc,
and never once feels "this is broken."** The felt player session is the product. RRI gates, test
scores, and rubric numbers are *measurement*, never the target — no score-gaming.

## The pillars (a good decision advances ≥1 without dulling another)

1. **Story-craft first.** Epic, mature, BG-caliber storytelling; the DM's prose is the star; judge
   it with a Tolkien lens. A correct-but-flat session is a failure.
2. **A living, reactive world.** The world pushes back. Choices have *gauge-backed* consequences.
   Companions have felt approval, telegraphed-then-fired betrayal, real agendas. NPCs speak; factions
   move; the player's mark is left on the world.
3. **Deterministic correctness.** SRD 5.2, faithfully. The engine rolls and resolves; the DM is
   *told* the result and narrates it. No rules-cheating; fiction never overrides the dice.
4. **It feels alive.** The screen always shows motion; the world responds promptly; no frozen,
   ambiguous waits. The session has rhythm. **Real art via a proven workflow — placeholders are the
   PATH, not a violation.** The destination is real, PoE2-caliber art; the way there is to prove the
   *generation workflow* once on throwaway assets (a demo cast, ~10 monsters, default VFX/sounds),
   then swap them for the real ones through that same proven pipeline. A placeholder asset in the
   playable combat demo is the workflow doing its job — it is NOT a vision violation, as long as the
   pipeline that produced it is the one that will produce the polished asset later, and the
   foundation underneath it (the painterly backdrop + pathing) already clears its quality bar. The
   failure mode this pillar forbids is *shipping placeholders as the destination* and calling it done
   (see "Graphics North Star (PoE2)" for the binding backdrop scorecard that separates the
   foundation — held to the bar now — from the placeholder-OK actor/effect layer).
5. **No-prior-knowledge accessible.** A first-timer is never lost, overwhelmed, or staring at jargon
   or a dead control. Onboarding teaches itself.

## Invariants (load-bearing — NEVER violate; an option that breaks one is wrong by definition)

- **Engine = sole writer** of state (`snapshot.json` under `campaign_lock`, atomic temp + `os.replace`).
- **Additive-by-default** — empty == today; old snapshots round-trip; every new field defaults to
  current behavior; each feature is independently removable (`_StrictModel`, tolerant load drops only
  unknown top-level keys).
- **Gates/triggers read ONLY engine-mutated gauges** (flags, reputation, attitude, day, standing) —
  **never fiction.** The DM judges the *cause*; the engine owns the *number*.
- **Engine rolls; the DM is told.** Wander/betrayal/variant rolls happen in-engine, surface in the
  tool return; the DM narrates. Probability proposes, the DM disposes.
- **Viewer is read-only** on state — it posts move-intents; it never mutates.
- **The renderer reads a registry by SLOT and resolves to a default on miss.** Every visual asset
  (actor model, monster, VFX, sound) is addressed by a stable slot key; the renderer looks the slot
  up in an asset registry and, on a miss, falls back to a default/template asset — it never hard-codes
  an asset path and never fails because an asset is absent. **Swapping or regenerating ANY asset =
  ZERO renderer edits.** This is the asset analogue of *engine = sole writer*: the registry is the
  single source of truth for asset bindings, the renderer is a pure consumer, and the entire
  placeholder→real-art transition is a registry change, not a code change.
- **Never break wire contracts** — additive, keyword-only, defaulted; never reorder/rename/retype an
  existing param.
- **QA is gateway-free and never touches Eva** (the owner's live agent) — no profile-sourcing, no
  gateway reconfig, no Eva infra. One live Mac/GUI harness at a time. CI/full suites on GitHub.

## Architecture anchors (the surfaces a decision must respect — full detail in `WorldOS-RUNBOOK.md`)

- **Three engine servers** (`servers/engine`, `/rules`, `/voice`). The engine writes `snapshot.json`
  under `campaign_lock` (`store.py`); the **player facade** (`player_server.py`, the `worldos-player`
  MCP) is read-only on state and only appends move-intents the DM resolves.
- **The OpenWorlds viewer** (`viewer/server.py` → `/openworlds/`) renders state + a `/move` sink +
  two-sided `/chat`; it never mutates state. The native macOS app hosts it.
- **The DM is a `claude -p` agent**, one invocation per beat, driving the engine via MCP tools. Beats
  are **generation-bound** (~100–150s routine, measured `duration_api_ms`); the levers are the
  heartbeat / streaming / `--effort`, never tool round-trips (engine exec is ~1–4% of a beat). Model
  decision: DM = Opus, medium routine effort (`docs/MODEL-TIERING-STRATEGY.md`). GUI/VM-sweep lane:
  `WorldOS-GUI-RUNBOOK.md`.

## The bar, the scorecards & the release ladder

**Engine Excellent** *(met)* — behavioral GREEN; no engine *defect* in combat sprints (mech
deductions are DM-craft, not engine bugs); zero-critical + arc-completes + no-give-ups across the 5
personas; audit P0/P1 closed; a dogfood arc with no engine "broken" moment.

**The release scorecard — the 11 RRI gates** (`qa/release_readiness.py`; a failed gate is a HARD
FLOOR that caps the score, not a soft average):
`native_gate PASS · arc completed · cross-persona satisfaction ≥7 & no give-up · 0 critical bugs ·
story ≥4.3 · mechanical ≥4.5 · behavioral GREEN · ui-audit PASS · image-render ≥95% · palette-live
true · no rejected tool calls` (the last is fatal). RRI is *measurement*, never the target.

**The quality lenses & targets** (`qa/SCORING.md`) — 1 deterministic gate + 3 LLM lenses; **the
behavioral gate RED caps every lens ≤2.5**:
- **Behavioral GREEN** (`qa/assert_behavioral.py`) — both sides act, dice/clock/party honest, no
  rejected tool calls.
- **Story-craft ≥ 4.3** (Tolkien lens, `qa/rubric_tolkien.md`).
- **Mechanical ≥ 4.5** (`qa/rubric.md`) + 5e-fidelity (Angry-DM, `qa/rubric_angry_dm.md`).
- **0 critical/high** adversarial defects.
- Measure mech as a **combat-sprint median (n≥3)**, never a single duo (proven variance). Don't chase
  sub-0.2 lens deltas — bank real fixes.

**The QA verification tiers** (`docs/qa/FAST_GATE.md`; match the test to the change — NEVER report a
Tier-0/1 result as a release verdict):
- **Tier 0 — `qa/fast_gate.sh`** (~2s, deterministic): EVERY engine/content/viewer change + CI.
  Catches the structural / seat-path / rest / travel / combat-resolution regression classes for free.
- **Tier 1 — `qa/fast_probe.sh`** (~$1–3, ~20min): DM-craft / UX / satisfaction iteration — one
  rotated persona + a ≥6-beat duo (below 6 disarms the FATAL behavioral floors). Iteration signal only.
- **Tier 2 — the 5-persona VM sweep + Mac native part-A → RRI** (~$40, ~90min): milestone verdict
  only, after 0+1 pass; ≤3 sweeps per milestone. Heavy sweeps run on the support VM (the 16GB Mac
  OOMs). Log every scored run to the ledger (`qa/scores_db.py`).

**Release ladder:** Engine Excellent → **Player-Ready Beta** (a real built `.app`; a no-prior-
knowledge dogfood arc with no "broken" moment; honest felt session) → **1.0 Playable Combat Demo**
(a PLAYABLE, MODULAR combat scene rendered in-app on the PoE2 painterly stack — 2D camera-pinned
backdrop + real 3D actors on the frozen dimetric camera — running on PLACEHOLDERS, i.e. a demo cast +
~10 monsters with a default-on-miss registry, default VFX/sounds; the proof is that the *workflows*
are repeatable and the **backdrop scorecard PASSES** for the demo room while actors/effects ride the
placeholder-OK tier; see "Graphics North Star (PoE2)") → **1.0 GA** (the Demo's proven workflow
applied to real, polished art + Beta's story/world bar + notarized + feature parity: companions felt,
visual parity at the PoE2 bar, story at the bar).

## Graphics North Star (PoE2)

> The story/engine sections above are the *what-it-plays-like* bar. This is the *what-it-looks-like*
> bar, with its own binding scorecard. The full self-driving plan lives in
> `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP-POE2.md`; this section is the load-bearing distillation that
> `worldos-decide` and the `visual-critic` skill anchor against.

**PoE2 = the single reference bar.** **Pillars of Eternity II: Deadfire** is the one named reference
WorldOS's graphics are scored *against* — not "iso-CRPG in general," not BG2 (BG2 is a
tactical-readability cross-check only), not Disco Elysium (a dark-pocket/mood cross-check only).
Every render is scored as a **gap to specific PoE2 frames**, not on vibes.

**The visual form:** **2D painterly plates + real 3D actors on a fixed dimetric camera.** The
backdrop is a camera-pinned painterly *plate* (2D, hand-painted feel); the actors and monsters are
**real 3D animated models** grounded and lit into that plate; the camera is **frozen dimetric**
(orthographic, the one camera contract in `extensions/renderers/unity/CANONICAL.md` — elevation 30°,
yaw 45° corner-iso, isotropic cells — *do not fork it*). The palette is **warm/cool firelit**:
warm hearth/lantern key against cool fill, **blue-violet shadows (NEVER pure black)**,
**atmospheric and moody — NOT flat-gray**. A washed-out, flat, gray, or pure-black-shadow frame
fails the bar by definition.

**Rooms must work WITH actors (occlusion = art-time CUTAWAY).** Because the camera is permanently
fixed, occlusion is solved at ART TIME, not by a runtime fade: the camera-near walls are **CUT** and
there is **NO ceiling**, so the interior + actors + pathing are always visible; tall interior props
stay in the **back half** of the grid (a per-prop see-through fade on approach is a deferred Phase-2
layer). A room is authored as one or more **camera-sized room-units** — each its own plate + one-source
pathing — and bigger spaces are **composed** by linking units at authored **door cells**: the party
crosses a doorway (`cross_door`) and the room-agnostic renderer swaps to the linked unit's plate (a live
room transition). One authored `scene_grid` per unit is the SINGLE source of both the painted room and
its pathing (props are obstacles by construction). See `CANONICAL.md` "Occlusion model" +
`docs/roadmap/ROOM-OCCLUSION-PATHING-SPRINTS.md`.

### The graphics scorecard (BACKDROP-BINDING)

The graphics gate is **split into two tiers**, and the **backdrop tier is the binding one** — it is
the reusable engine piece (a room's plate + its pathing is what every scene is built on), so it is
held to the bar *now*. The actor/effect tier rides "default-if-missing" and is **placeholder-OK**
until the polish phase. Lens names (`L1`–`L7`) are the `visual-critic` panel lenses, logged to
`qa/scores_db.py` (`surface="visual"`); the deterministic checks are `qa/visual_pregate.py`.

- **TIER-1 — BACKDROP (binding; the foundation; ALL must hold):**
  - **L6 painterly-plate craft ≥ 8** (brush economy, atmospheric depth, PoE2-caliber art direction)
    **AND**
  - **L1 registration/cohesion ≥ 8** (the painted floor registers with the gameplay grid; actors
    will plant on the same plane the engine reasons about) **AND**
  - **detail ≥ 7** (no muddy / under-detailed plate) **AND**
  - **0 / 3 washout** (the three deterministic illusion-breakers in `visual_pregate.py` — none may
    trip; a washed-out plate is an automatic fail) **AND**
  - **pathing-map-correct** (the walkmask / pathing map the renderer derives matches the painted
    geometry — walkable floor is walkable, painted obstacles block, destinations resolve to engine
    zones). A backdrop is not "done" until pathing reads correctly off it.
- **TIER-2 — ACTOR / EFFECT (placeholder-OK now; polished much later):** the deterministic
  **pre-gates PASS** (frame-lit · floor-contact · screen-scale · occupancy · motion-liveness —
  numbers, not vibes) **AND** the integration lenses clear a **soft ≥ 5.0**:
  - **L2 occlusion / grounding ≥ 5.0** (actor is PLANTED — a visible soft contact shadow, feet meet
    the floor, not floating),
  - **L3 scene-light coherence ≥ 5.0** (actor lit BY the scene's key light, not a foreign flat key),
  - **L4 character integration ≥ 5.0** (a grounded, scene-lit **real-3D** actor reads as belonging in
    the plate — the billboard/"pasted sticker" look is the LOW end; the L4 ceiling is a polish-phase
    target, not a demo gate).
  A placeholder demo-cast model that is grounded, lit, and screen-correct PASSES Tier-2 even though
  it is not the final art. That is the point.
- **Binary combat-FUN checklist (the demo must be *playable*, not just pretty):**
  - [ ] A real engine attack drives the render (engine = sole writer; renderer replays `/events`).
  - [ ] The hit shows a VFX (default-on-miss slash/impact is fine) AT the correct engine cell.
  - [ ] A damage number + HP-bar drop is visible and matches the engine result.
  - [ ] Actors face their heading; movement reads as turn-based motion (not a static tableau).
  - [ ] Death/defeat resolves visibly (topple/fade) — no actor frozen after 0 HP.
  - [ ] The whole exchange is legible at the dimetric camera (no occlusion guesswork; L5 readability OK).

**Why the split:** the backdrop + pathing are **THE foundation** — the reusable, regenerate-and-reuse
engine piece — so they clear the bar *before* a scene is built on them. Actors and effects are swapped
through the proven pipeline (the registry-by-slot invariant), so they are legitimately placeholder
until the polish phase. This is exactly the Pillar-4 reconciliation made measurable.

## How we build — the dev + decision loop

- **The dev loop:** worktree off `main` → implement additively → single-process TDD
  (`uv run … pytest -p no:xdist`) → push + PR (HEREDOC body; never pipe `gh pr create` through
  `tail`) → **merge ONLY after adversarial verification** (revert the fix → the test goes RED for the
  documented reason; green-on-revert = reject) → sync + `fast_gate`. Fan this out with ultracode
  workflows.
- **Dogfooding is the feedback spine** — real play surfaces what matters; the dogfood log is the
  prioritized backlog. But *measure before you trust a complaint*: a felt report can be a harness
  artifact or a mis-read — verify against the artifacts before fixing.
- **Decisions anchor here via `worldos-decide`** — score against this vision + the scorecards, fire a
  Scorer + a Red-team skeptic, gate on 95%; escalate to the owner ONLY when the call genuinely turns
  on owner-private context (taste, business priority, a vision *change*) — and then bring a
  recommendation, not an open question.
- **Research before load-bearing code** — a load-bearing surface (tool API / schema / wire) with 2+
  real options gets a first-principles decision doc, not speculative code.
- **The expensive lessons** (don't relearn them): validate-before-fixing (the LLM scorer mis-attributes
  root cause — reproduce against the engine first); surfacing info ≠ the DM *using* it (fold the value
  into a trigger the DM already hits, or enforce in-engine); reuse-before-rebuild (the engine is
  usually 80–90% there — find the existing primitive); wire-as-you-go (the live-DB harness is commit
  ~5, not ~47); QA must exercise the *production* path (a divergent harness over-states problems).

## Pathologies we don't re-create

- **Written-but-never-read state** — machinery the engine writes that nothing consumes at the
  read-seam (the companion approval gauge was the purest case: built, wired, frozen at 0). If you add
  a gauge, wire its *read* AND its *write* in the same change.
- **Path divergence** — N seat paths / wrapper lanes each missing a different guard. Reuse the
  canonical predicate; don't fork.
- **Beat unreliability** — dead beats, a 401 masquerading as DM prose, timeouts killing healthy beats.
  Fail loud, recover, never mask.

## What we are NOT doing

- Not optimizing a score for its own sake (no rubric-gaming).
- Not generating portraits for the 2,000+ canon NPCs — they have wiki art. Image-gen is for **custom
  characters + genuine gaps only**.
- Not seating banned BG3 origin heroes (Astarion, Gale, Karlach, Lae'zel, Shadowheart, Wyll, Halsin)
  or dead figures as PCs — they are companions/NPCs only.
- Not shipping a correct-but-lifeless session and calling it done.
