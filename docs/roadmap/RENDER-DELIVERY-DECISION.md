# Decision: Render Delivery — how the Unity render reaches the player's screen

**Status: DECIDED (2026-07-03). Owner-delegated to the architect; ratified in Master Plan v2.**
Resolves issue #1302 (the S2 entry gate). Supersedes the "open load-bearing decision" flag that
stood in VISION.md from the first roadmap pass.

## Context

The Unity painterly renderer (the proven closed-loop pipeline on the GEX44 box) today produces
QA frames only — a one-way box→frames path (`viewer/server.py` box driver scp's render-hints out).
No mechanism existed for a player to SEE the render. The 1.0 Playable Combat Demo requires exactly
that, and the owner's expanded North Star (2026-07-03) raised the stakes: the destination is not
combat stills but a **walkable rendered world** (T3) — party movement, on-stage NPC interaction,
realtime input on the rendered scene.

## Options considered

- **(a) Embedded Unity build inside the macOS `.app`** — Unity as an embedded view; OpenWorlds
  stays the meta-UI. Highest integration cost (embedding, window management, notarization
  interplay with #151), full interactivity.
- **(b) Render service** — headless Unity streams frames/clips into the OpenWorlds combat screen.
  Cheapest UI unification; keeps ONE app surface.
- **(c) Standalone Unity player** launched beside the app — a separate window/process speaking to
  the same engine endpoints. Cheapest integration; weakest "one product" feel initially.

## Decision

**Unity IS the interactive game surface for the rendered tiers (T2/T3), staged:**
1. **Demo era (Act I / S8): option (c)** — a Unity **standalone player build (macOS first)**,
   launched beside the app with a campaign handoff (mirror the `native-bridge.js` pattern). Input
   contract = the EXISTING `POST /move` kinds only.
2. **Later (Act II / W5+): revisit (a)** — embed/unify only if the standalone seam proves to be a
   real product-feel problem, measured by playtest feedback, not assumed.

**Frame-streaming (b) is REJECTED as the strategic path.**

## Rationale

1. **The North Star decides it.** T3 requires realtime interaction ON the rendered scene — click-
   to-walk, click-an-NPC, camera responsiveness. Frame-streaming caps at slideshow interactivity;
   choosing (b) for the demo would build integration plumbing the very next act must throw away.
   Options (a)/(c) are the same architecture (a Unity process consuming engine surfaces) with
   different packaging; (b) is a different architecture with a dead end.
2. **The invariants make (c) safe.** The renderer is a PURE CONSUMER: it reads engine surfaces
   (`/combat-surface`, later the additive `stage` block, `/events` Action-Replay) and posts
   move-intents through the same `/move` kinds as OpenWorlds. No renderer-side game state, no new
   writer, no client path-prediction (animates only engine-confirmed paths). The text/2D tiers are
   provably unaffected (text-tier byte-identity tests ship with every W sprint).
3. **Everything built so far carries forward.** The box pipeline, paint_combat_v1 lineage, actor
   registry, Animator wiring (#1303), day/night selection — all of it runs inside the player build
   unchanged; the delivery decision changes packaging, not render code.
4. **Staging defers the expensive part.** Embedding (a) drags in notarization interplay (#151),
   window/lifecycle management, and app-store questions — none of which the demo needs. A separate
   window is an acceptable demo-era seam; unification is a measured, later call.

## Counter-arguments considered

- *"Two windows feels unpolished for 1.0."* — Accepted as a real cost; mitigated by the campaign
  handoff (one click from the app) and by measuring player reaction at the Beta/Demo playtests
  before paying the embed cost. The Demo's bar is "playable + workflows proven," not final polish.
- *"Streaming would reuse the existing OpenWorlds combat screen."* — True and tempting, but it
  purchases demo-week convenience with an Act-II rewrite. The roadmap explicitly optimizes for the
  T3 destination.
- *"A Unity player build is new packaging work."* — Yes (~the W5 sprint), but it is the SAME work
  T3 requires regardless; doing it at demo scope de-risks it early on the smallest surface.

## Reversibility

High. Because the surface contract is engine-side and tier-agnostic, switching packaging later
(standalone → embedded, or even adding a streaming fallback for low-end hardware) is a packaging
change, not an architecture change. The signal to revisit: Beta/Demo playtest feedback naming the
two-window seam as a top-3 complaint, or macOS lifecycle friction observed in the T3 gate run.

## Consequences (wired into the roadmap)

- S2's entry gate (this doc) is SATISFIED; Animator/VFX work (#1303) proceeds with standalone
  packaging assumptions.
- S8 "Demo Assembly" delivery = the Unity standalone + app handoff.
- W5 "The Unity Player tier" formalizes the build/packaging + the T3 gate (a blind AI playtester
  completes a quest loop IN the rendered surface).
- The local Mac (`/Users/m1/worldos-unity`, Unity 6000.5.6f1) is the render/QA iteration host; the
  player build is the distribution form.
