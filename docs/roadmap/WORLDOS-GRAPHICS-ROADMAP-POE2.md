# WorldOS Graphics Roadmap — PoE2 painterly CRPG (self-driving)

> **The repo-homed self-driving graphics roadmap.** This is the in-repo home for the graphics/PoE2
> goal: a *repeatable AI system* that generates a painterly Pillars-of-Eternity-II-caliber world and
> drives a playable combat scene to the bar. It is the execution plan behind the **"Graphics North
> Star (PoE2)"** section of [`VISION.md`](../../VISION.md) and is the in-repo distillation of the
> operator plan (`~/.claude/plans/worldos-graphics-autonomous-goal.md`, owner-local — not committed).
>
> **Relationship to the other graphics roadmap.** [`WORLDOS-GRAPHICS-ROADMAP.md`](./WORLDOS-GRAPHICS-ROADMAP.md)
> is the *taxonomy* roadmap — game-types (GT0/GT1/GT2) × capabilities (C1–C10), the Branch-A/Branch-B
> matrix. THIS file is the *execution* roadmap for the **GT2 PoE2 painterly renderer specifically**:
> the milestone ladder, the score-gates, and the asset-pipeline workflow that fill in GT2's column.
> The taxonomy file says *what kinds of games*; this file says *how the PoE2 look gets built, scored,
> and scaled*. Neither overwrites the other; this one is additive.
>
> **Single-writer discipline (mirrors the engine).** The Python engine is the **sole writer** of game
> state. Every renderer, asset pipeline, and AI loop in this roadmap is a **read-only consumer** of the
> engine read-models (`/atlas-surface`, `/character-surface`, `/combat-surface`, `/chat`, `/events`) +
> the single `POST /move` intent lane. No renderer or gen-loop ever becomes a second source of truth.
> The asset analogue: the renderer reads an **asset registry by SLOT** and resolves to a default on a
> miss — swapping/regenerating any asset is **zero renderer edits** (the new VISION.md invariant).

---

## 0. The GOAL

A **repeatable AI system that generates a painterly PoE2 world** and renders a playable combat scene
to the bar — built so the same workflow scales from one room to a world:

> **room → pathing → actors → playable → scale.**

1. **room** — generate a camera-pinned painterly **backdrop plate** for a room.
2. **pathing** — derive a correct **walkmask / pathing map** off that plate (walkable floor walkable,
   painted obstacles block, destinations resolve to engine zones).
3. **actors** — drop **real 3D animated actors** into the plate, grounded + scene-lit + readable.
4. **playable** — wire a real engine attack → the renderer replays it (VFX + damage number + HP drop)
   so the scene is *played*, not just looked at.
5. **scale** — make the workflow **repeatable**: 1 room → 1 scene → larger scenes → a world, with a
   part library + room-to-room transitions for cross-room consistency.

The binding quality gate is the **BACKDROP scorecard** (see §3); actors/effects ride a
**default-if-missing** registry and are **placeholder-OK** for the demo (polished much later). The
demo cast is a handful of throwaway actors + ~10 monsters; the proof is that the **workflows are
proven + repeatable**, not that the demo art is final.

---

## 1. The PoE2 layer stack (build it the way PoE2 layered it)

PoE2 built its painterly world by layering rendered passes on a **2D side**. WorldOS mirrors that
order — each layer is shippable on its own and is *added*, never a rewrite:

| Layer | What it is | WorldOS form | Owned by | Bar |
|------|-----------|--------------|----------|-----|
| **L-stack 1** | **2D backdrop + 3D models** | Camera-pinned painterly plate (2D) + real 3D animated actors on the frozen dimetric camera | renderer (reads engine surfaces) | **TIER-1 backdrop scorecard PASS**; actors placeholder-OK |
| **L-stack 2** | **A few 3D effects** | Default-on-miss VFX at the engine cell (slash/impact flash, one spell/projectile), hit-react, death | renderer (replays `/events`) | combat-FUN checklist; effects placeholder-OK |
| **L-stack 3** | **Day/night + lighting** | Scene key-light direction/color; warm/cool firelit palette; blue-violet (never black) shadows; later normal-map dynamic lights (the PoE glow) | renderer | L3 scene-light coherence; atmospheric-moody not flat-gray |
| **L-stack 4** | **Water** | Animated water / reflective surfaces, as PoE2 added on the 2D side | renderer | art-direction polish (post-demo) |

The stack order is also the **risk order**: L-stack 1 (backdrop + grounded actors) is the foundation
and gets the binding gate; L-stack 3/4 (lighting depth, water) are atmosphere layers added once the
foundation holds. This matches PoE2's own production order — paint the plate, then light it, then add
the moving water.

---

## 2. Scope progression — one workflow, growing scope

The *same* generate→pathing→actors→playable workflow runs at every scope. Scaling is **repeating the
proven workflow with a part library + transitions**, not building a new pipeline per scope.

| Scope | What it is | What it proves |
|------|-----------|----------------|
| **1 room** | One detailed painterly room, pathing-mapped, with a playable combat exchange | the workflow exists end-to-end |
| **1 scene** | The room as a real *playable* combat scene driven by the engine (multi-actor, VFX, death) | the workflow is *played*, not just rendered |
| **larger scenes** | 2nd + 3rd UNIQUE detailed rooms; a camera-pinned **part library** (floor/wall/prop kit) for cross-room consistency; room-to-room transition (engine scene-swap on an edge cell) | the workflow is **repeatable + consistent** across rooms |
| **a world** | Many rooms/scenes stitched into a traversable area at the PoE2 bar | the workflow **scales** to a world |

---

## 3. The graphics scorecard (the gate the milestones are measured against)

The binding gate, lifted verbatim from VISION.md's **"Graphics North Star (PoE2)"** so the roadmap and
the vision can never drift. Lenses `L1`–`L7` are the `visual-critic` panel lenses, logged to
`qa/scores_db.py` (`surface="visual"`); the deterministic checks are `qa/visual_pregate.py`.

- **TIER-1 — BACKDROP (binding; the foundation; ALL must hold):**
  **L6 painterly-plate craft ≥ 8** AND **L1 registration/cohesion ≥ 8** AND **detail ≥ 7** AND
  **0/3 washout** (no `visual_pregate.py` illusion-breaker trips) AND **pathing-map-correct** (the
  derived walkmask matches the painted geometry).
- **TIER-2 — ACTOR / EFFECT (placeholder-OK now; polished later):** deterministic **pre-gates PASS**
  (frame-lit · floor-contact · screen-scale · occupancy · motion-liveness) AND **L2/L3/L4 ≥ 5.0**
  (planted · scene-lit · grounded-real-3D-actor-reads-as-belonging). A grounded, lit, screen-correct
  placeholder PASSES — that is intended.
- **Binary combat-FUN checklist:** engine attack drives the render · VFX at the correct cell · damage
  number + HP-bar drop matches the engine · actors face heading + read as turn-based motion · death
  resolves visibly · the exchange is legible at the dimetric camera.

> **North-star convergence target (whole combined scene):** visual-critic **overall ≥ 8.0 across 2
> runs, every lens ≥ 6.5, 0 CRITICAL/HIGH, detail ≥ 7 (0 washout), all pre-gates PASS** — the gate the
> self-driving loop drives toward. Brake a scene at its gate OR at a diminishing-returns ceiling
> (no material gain across a pass ⇒ done-enough; log it and move on).

---

## 4. Milestones with score-gates

Two tracks. **F-track** = the *foundation/plumbing* milestones that make the pipeline measurable and
single-writer-clean (docs, camera, scorecard, GitHub). **M-track** = the *content/quality* milestones
that drive renders to the bar. F-track lands first (it is what makes M-track scoreable); the
visual-critic loop is the work-selector inside every M-milestone (render → pregate → 5–7-lens panel
≥2 runs → fix the lowest lens / open CRITICAL → re-render → log scores_db).

### Foundation track (F)

| ID | Milestone | What "done" means | Gate |
|----|-----------|-------------------|------|
| **F1** | **camera-unfork** | ONE dimetric camera contract, declared canonical; the deprecated forked contract (e.g. the 06-23 `TavernTier1` cell-5 / 14×10) is retired and not reused | `extensions/renderers/unity/CANONICAL.md` "Camera contract (ONE — do not fork)" is the sole contract; no script forks it |
| **F2** | **scorecard-split** | the graphics gate is split into binding TIER-1 backdrop vs placeholder-OK TIER-2 actor/effect, deterministic + lens-based | the split in §3 is implemented in `qa/visual_pregate.py` + the `visual-critic` panel + logged to `qa/scores_db.py` (`surface="visual"`) |
| **F3** | **vision/docs** (this PR) | the graphics/PoE2 goal has a real home in the canonical docs; placeholder strategy reconciled with the vision | VISION.md "Graphics North Star (PoE2)" + backdrop scorecard + "1.0 Playable Combat Demo" rung + Pillar-4 reconciliation + registry-by-slot invariant; THIS roadmap filed |
| **F4** | **github-map** | the M-track milestones are mapped to GitHub Milestones/Issues with `graphics` + `gt2` + `cap:*` labels so the plan is queryable + executable | each M-milestone below → a GitHub milestone; each unit → an issue, cross-linked from this file |

### Content / quality track (M) — each is a visual-critic loop to its gate

| ID | Milestone | Scope | Gate |
|----|-----------|-------|------|
| **M-A** | **repeatable-backdrop-workflow** | Generate painterly room plates via a *repeatable* pipeline (greybox → img2img → camera-pin) | **3 rooms each ≥ 8** on the TIER-1 backdrop gate (L6≥8, L1≥8, detail≥7, 0/3 washout) — proves the *workflow* is repeatable, not a one-off lucky plate |
| **M-B** | **pathing-mapped-rooms** | Derive a correct walkmask / pathing map off each plate; destinations resolve to engine zones | **pathing-map-correct** on each M-A room (walkable floor walkable, painted obstacles block, edge cells transition) |
| **M-C** | **3D-actors-grounded/lit/readable** | Drop real 3D animated actors (demo cast + monsters) into the plates | TIER-2 pre-gates PASS + **L2/L3/L4 ≥ 5.0**; actors grounded (contact shadow, feet on floor), scene-lit (key light), readable at the dimetric camera |
| **M-D** | **playable-combat-in-app** | A real engine attack drives the render in-app; VFX + damage + HP + death | the **binary combat-FUN checklist** all-checked; engine = sole writer, renderer replays `/events`; this is the **1.0 Playable Combat Demo** rung |
| **M-E** | **scale-to-a-world** | Part library (camera-pinned kit) + room-to-room transitions; many rooms stitched | ≥ N rooms at the TIER-1 bar with cross-room consistency; a traversable multi-room area at the PoE2 bar (north-star convergence target, §3) |

**Dependency order:** F1→F2→F3→F4 (foundation), then M-A→M-B→M-C→M-D→M-E (each M depends on the
prior; M-A/M-B can overlap once a plate exists). M-track gating presupposes F2 (the scorecard split)
and F1 (the unforked camera) are in place — without them the scores aren't trustworthy.

---

## 5. The self-driving loop (how the M-track is executed)

The graphics work is **work-selected by the visual-critic loop**, mirroring the engine's story/mech QA
loop on the visual side:

```
render  →  qa/visual_pregate.py  →  (if FLAG: fix + re-render)
        →  visual-critic 5–7-lens panel (≥2 runs)
        →  fix the LOWEST lens / open CRITICAL
        →  re-render
        →  log every scored frame to qa/scores_db.py (surface="visual")
        →  brake at the scene's gate OR a diminishing-returns ceiling
```

- **Engine = sole writer; renderer read-only.** The loop never mutates engine state; it consumes the
  read-model surfaces + replays `/events`. A real engine attack is what drives the M-D render.
- **Registry-by-slot.** Asset swaps/regenerations are registry changes, never renderer edits — this is
  what makes placeholders cheap to replace and the scale step (M-E) a content task, not a code task.
- **Commit each working unit.** Box git per render increment; PRs for any engine/viewer change. The
  CANONICAL.md iteration discipline is binding (persist the build script, capture + score + log,
  register the new current-best, mark the superseded one DEPRECATED).

---

## 6. Cross-references

- **Vision anchor:** [`VISION.md`](../../VISION.md) → "Graphics North Star (PoE2)" (the binding
  scorecard), Pillar 4 (the placeholder reconciliation), the release ladder's "1.0 Playable Combat
  Demo" rung, and the registry-by-slot invariant.
- **Taxonomy roadmap:** [`WORLDOS-GRAPHICS-ROADMAP.md`](./WORLDOS-GRAPHICS-ROADMAP.md) — GT2 is the
  game-type this file executes; C2 (scene), C3 (combat), C4 (movement/pathing), C5 (assets), C7
  (lighting) are the capabilities its milestones advance.
- **Operator plan:** `~/.claude/plans/worldos-graphics-autonomous-goal.md` (owner-local; the live
  self-driving run this roadmap is the committed distillation of).
- **Renderer canonical state:** [`extensions/renderers/unity/CANONICAL.md`](../../extensions/renderers/unity/CANONICAL.md)
  — the ONE camera contract (F1), the current-best-per-surface registry, and the iteration discipline.
- **Visual scorer:** the `visual-critic` skill (the "Angry-DM for graphics") + `qa/visual_pregate.py`
  (deterministic pre-gates) + `qa/scores_db.py` (`surface="visual"` regression ledger).
