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

**The destination (owner-ratified 2026-07-03): a FULL RENDERED GAME at Pillars-of-Eternity-2 /
Pathfinder-Kingmaker quality.** Combat rendering is only the first step of the game system. The end
state: the party WALKS around beautiful pre-rendered environments, TALKS to NPCs on-stage, explores
with quests, and combat is one mode entered in place — the DM composes the game live by placing
characters and pulling scored templates from a growing library. Two mechanisms carry the evolution:
- **The TEMPLATE LIBRARY** (the Dragon-Age model): a curated library of pre-rendered environments
  (~100 room-units: cities, towns, forests, crypts, dungeons) plus reusable scored content (quests,
  NPCs, villains, encounters) — made by us, usable in ANY game the DM assembles.
- **The HARVEST LOOP**: generated games get scored; high-scoring artifacts are eval-gated and
  PROMOTED into the library; future games assemble from the library. The game improves as it is
  played, and play shifts from pure AI generation toward library-assembled sessions needing less AI.

**Feature tiers (one engine + one DM under all of them — permanent):**
`T0` text-only adventure (CLI / any agent chat) · `T1` 2D OpenWorlds (today's app) · `T2` rendered
combat (the demo) · `T3` walkable rendered world (the North Star). **The text tier is a full,
forever-supported way to play** — treasure, towns, crypts, combat all work with the DM in text mode;
graphics are a presentation upgrade, never a dependency. (Invariant below.)

It is a real game **and** an experiment — in whether an autonomous agent can build, harden, and
steer a game system to a shippable bar, making its own decisions against this documented vision.
The experiment's engine is the **measurement culture** (control-anchored art panels, story lenses,
the behavioral gate, the FELT track): the instruments are what let autonomous agents build to a
bar, and they are a product in their own right — WorldOS is the game AND the proof of the method.

## North star

**A no-prior-knowledge player launches the app, plays a complete 8-beat Baldur's-Gate-caliber arc,
and never once feels "this is broken."** The felt player session is the product. RRI gates, test
scores, and rubric numbers are *measurement*, never the target — no score-gaming.

**The far north star** (what "done" ultimately looks like): that same player, in the T3 tier, walks
their party through a rendered town at PoE2 quality, talks to an NPC standing by the hearth, picks
up a quest the library already scored as excellent, and fights the battle that follows on the same
screen — while a T0 player gets the identical adventure in pure text. Every intermediate release is
a rung toward that, and every rung must be a real, playable product on its own.

## Operating principle: DECISION-BY-EVAL

- **Every run is a schema'd loop** (owner-ratified 2026-07-08): each run type — play QA, render, panel, generation, extraction, promotion — closes HEALTH → EVIDENCE → SCORE → VERDICT → POINTER (the Universal Run Contract, docs/OPERATIONS.md) and lives as a row in docs/RUNBOOK-INDEX.md. Processes evolve by editing their row, never by ad-hoc drift.

When a load-bearing decision lacks an instrument, **building the instrument IS the first step** —
never decide by vibes what can be decided by measurement. This is how the experiment self-drives:
every sprint names its gate as a runnable eval; every library promotion is eval-gated
(control-anchored panels for art, disguised hand-authored canon as controls for content); every
"is it better?" is a same-instrument delta. `worldos-decide` anchors here; the noise laws
(±1.2 panel variance, positive-control anchoring) are part of the ruler, not footnotes.

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
- **The text tier always plays.** Every rendered-tier feature (scene-at-rest, walking, on-stage NPC
  talk, staged combat) is a PRESENTATION of engine surfaces the T0/T1 tiers already consume — never a
  new gameplay dependency. Each W-series sprint ships a text-tier byte-identity test proving the
  non-rendered path is unchanged. The DM can run the entire game in text mode, forever.
- **Renderers are pure consumers on every tier** — the Unity game surface (T2/T3) talks to the engine
  exactly like OpenWorlds: reads surfaces, posts move-intents through the same `/move` kinds. No
  renderer-side game state, no client-side path prediction (the renderer animates only
  engine-confirmed paths).
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
knowledge dogfood arc with no "broken" moment; honest felt session — **scoped to the shipped 2D
OpenWorlds surface**: the Unity demo renderer is deliberately NOT a Beta gate; render-in-app is
the next rung's gate) → **1.0 Playable Combat Demo**
(a PLAYABLE, MODULAR combat scene rendered in-app on the PoE2 painterly stack — 2D camera-pinned
backdrop + real 3D actors on the frozen dimetric camera — running on PLACEHOLDERS, i.e. a demo cast +
~10 monsters with a default-on-miss registry, default VFX/sounds; the proof is that the *workflows*
are repeatable and the **backdrop scorecard PASSES** for the demo room while actors/effects ride the
placeholder-OK tier; see "Graphics North Star (PoE2)") → **1.0 GA** (the Demo's proven workflow
applied to real, polished art + Beta's story/world bar + notarized + feature parity: companions felt,
visual parity at the PoE2 bar, story at the bar — **plus the platform thesis at minimum viable
scope: the bring-your-own-agent surface documented and ONE provider lane (Claude Code) verified
end-to-end**; further agent lanes are post-GA platform work, epic #911).

**The ladder executes in three ACTS — sequencing source of truth: `docs/roadmap/PRODUCT-ROADMAP.md`**
(charters, binding gates, lanes, the Owner Gate Register). **Act I — The Demo**: sprints S1–S10 to
GA as pinned (Beta ≈ v1.0.9 · Demo-1.0 ≈ v1.0.10 · GA = v1.1.0). **Act II — The Walkable World +
The Harvest Loop**: the W-series (scene-at-rest → walk → talk → living stage → the Unity player
tier) and HV-series (artifact evals → extract → promote → reuse → flywheel ops), interleaving with
Act I where parallel-safe. **Act III — The Universe Platform**: template packs, remaining agent
lanes, hosted runtime, creator, KOTOR-class universes, engine-as-platform.

**★ RENDER DELIVERY — DECIDED (2026-07-03, owner-delegated; full rationale
`docs/roadmap/RENDER-DELIVERY-DECISION.md`): Unity IS the interactive game surface for the rendered
tiers.** A Unity player build (macOS first) consumes engine surfaces and posts move-intents exactly
like OpenWorlds — frame-streaming was rejected (it cannot grow into walkable realtime play, the T3
destination). Staged: demo era = Unity standalone launched beside the app; embed/unify later only
if warranted. OpenWorlds remains the meta-UI, the T1 surface, and the QA harness surface.

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

**★ GEOMETRY IS GROUND TRUTH (load-bearing principle, 2026-07-16 — the reframe that makes scale
tractable).** Collision + occlusion are driven ENTIRELY by the geometry — the `scene_grid` + the boxes
sidecar — which is **true by construction**. The painted plate is **COSMETIC**: it only has to look
*coherent* with that geometry, never be pixel-exact to it (the 3D-first kit chain's paint + edit
passes WILL nudge a door, invent a gallery, move a prop — and that's fine). So "walkability" is never
"make the paint match the geometry";
it is (1) project the geometry through the **same camera** the plate was painted at (so actors +
occluders land on the painted masses), and (2) reconcile only the **cosmetic** seams a player reads as
wrong (a door glow/label + cross-door landing that sits on the painted arch; a painted walkable-looking
space that has no backing grid cell). **Geometry wins every collision dispute; paint is set-dressing.**
This is why a room's walkability is *generated-true* and only needs the automated walk-test to catch
cosmetic/seed regressions — not per-room hand-tuning of collision against paint. It is what makes the
20→50→200-room library tractable.

### The graphics scorecard (BACKDROP-BINDING)

The graphics gate is **split into tiers**, and TWO of them are binding: **WALKABILITY** (can you
actually play the room) and the **BACKDROP** craft tier (does it look PoE2). **These are DIFFERENT
gates and BOTH are hard floors — the blind beauty panel is BLIND to whether the room is walkable.**
The actor/effect tier rides "default-if-missing" and is **placeholder-OK** until the polish phase.
Lens names (`L1`–`L7`) are the `visual-critic` panel lenses, logged to `qa/scores_db.py`
(`surface="visual"`); the deterministic checks are `qa/visual_pregate.py`. For rooms built through the
3D-first kit chain the **per-object registration gate** (`qa/object_align_check.py`, a calibrated floor
per room, #1734) and the **seg-registration bar** (0.99 on the kit render) are binding alongside
walkability and the control-anchored backdrop.

- **★ TIER-0 — WALKABILITY (binding; AUTOMATED; the hardest floor — a room does NOT ship unwalkable):**
  A room is not done until the automated walk-test (`qa/walk_test.py`) is **GREEN**. It drives the live
  player cell-by-cell over the QA channel and asserts, with NO human: (a) the character lands on the
  clicked cell's plate-correct screen position (actor projection matches the painted plate), (b)
  impassable cells (walls/props) REJECT the move, (c) each door_cell sits on the painted arch and
  cross_door lands on the reciprocal door, (d) tall occluders mask the character behind them, (e) no
  painted walkable-looking space lacks a backing grid cell. **Beauty ≠ walkable** — we shipped 3
  panel-8+ rooms on 2026-07-15 that were entirely unwalkable because this gate did not exist as
  automation. It exists now; walk-green is the ship gate (`qa/room_pipeline.py` exits non-zero without
  it). See the Room Readiness Pipeline below + epic #1581.
- **TIER-1 — BACKDROP (binding; the foundation; ALL must hold):**
  > **★ GATE RECALIBRATED 2026-07-02 (the positive-control finding).** Absolute panel scores were
  > proven un-citable: blind on our own instrument, REAL shipped PoE plates scored 3.0–4.6 and real
  > BG2EE 4.6–5.6 while our plates scored 5.0–6.7 — the old "≥ 8 absolute" was unattainable BY
  > CONSTRUCTION. The craft gates below are therefore **CONTROL-ANCHORED**: every verdict panel
  > embeds a disguised REAL-ART CONTROL (a shipped plate not among the refs, fair presentation)
  > and a gate passes when the candidate's same-panel score **meets or beats the control's**
  > (protocol: `.claude/skills/visual-critic` "CALIBRATION-CONTROL PROTOCOL"). Deterministic gates
  > (washout, pathing) are unchanged. Status: the crypt plate MEETS the recalibrated craft bar
  > (6.72/median-7 vs real controls 3.0–5.6, clean instrument); remaining named craft work =
  > brushstroke looseness, wall-repetition, relief crispness.
  - **L6 painterly-plate craft ≥ the real-art control** (brush economy, atmospheric depth,
    PoE2-caliber art direction — judged as same-panel delta vs the embedded control) **AND**
  - **L1 registration/cohesion ≥ the real-art control** (the painted floor registers with the
    gameplay grid; actors will plant on the same plane the engine reasons about) **AND**
  - **detail ≥ the real-art control** (no muddy / under-detailed plate vs what real shipped plates
    score on the same panel) **AND**
  - **0 / 3 washout** (the three deterministic illusion-breakers in `visual_pregate.py` — none may
    trip; a washed-out plate is an automatic fail) **AND**
  - **pathing-map-correct** (the walkmask / pathing map the renderer derives matches the painted
    geometry — walkable floor is walkable, painted obstacles block, destinations resolve to engine
    zones). A backdrop is not "done" until pathing reads correctly off it. **AND**
  - **FELT track** (new): the composed game frame (plate + actors + rings at viewport scale)
    passes the "would a player screenshot and share this?" lens — the story side's felt-vs-scores
    lesson applies to graphics identically; forensic deltas alone don't ship a game.
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
  > **MEASURED 2026-09-02 (#1738):** the shipped build renders actors **under-lit** against the plates,
  > so the L2/L3/L4 floors above are **aspirational** until an actor-luminance primitive exists.
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

### The Room Readiness Pipeline (how rooms are made — self-verifying, compaction-proof)

A room is authored + verified by ONE resumable command, `qa/room_pipeline.py --room <id>`, that chains
every gate — the **3D-FIRST KIT CHAIN**: generate-geometry → design-gate → `build_room_kit` (kit
assembly) → seg registration **≥ 99 % on the kit render** → the kit scene's own depth → flux depth-CN
base → structure-holding edit → global + per-object alignment gates (`qa/styled_align_check.py`,
`qa/object_align_check.py`) → composite → kit-derived boxes sidecar → **BEAUTY gate** (the blind
control-anchored panel) → **WALKABILITY gate** (`qa/walk_test.py`) → adopt (`promote.py` + registry) →
report. `qa/paint_room.py` (the pinned flux-depth-CN → Gemini painter) is the RETIRED paint-first
painter, kept for legacy rooms only — new and regenerated rooms go through the kit chain.
Each stage writes durable evidence + a stage marker; the command exits **non-zero unless BOTH the beauty
panel AND the walk-test are green** — that exit code, not a human "ship it", IS the gate. The pipeline
is hand-off-able to a sub-agent or run by the engine itself, which is what makes hands-off iteration
toward the 20→50→200-room library real. Full runbook: `docs/ROOM-PIPELINE-RUNBOOK.md`; epic #1581.

**★ RESUME PROTOCOL (any cold start / after compaction — read in this order so work survives context
loss):** (1) the `active-sprint` charter issue (currently **#1702**, DEMO COMPLETION then the town) +
`docs/roadmap/NOW.md` (the queue) → (2) this section (the doctrine) → (3)
`docs/ROOM-PIPELINE-RUNBOOK.md` (the executable process) → (4) the active plan's STATE block → then run
`qa/room_pipeline.py --resume`. Epic #1581 is the room-pipeline epic (history), not the queue head.
The GOAL, QUEUE, PROCESS, and STATE live in the repo + GitHub — never only in an agent's head. This is the fix for our #1 diagnosed failure: a process that lived in context
died at compaction and we regressed a whole feature.

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
