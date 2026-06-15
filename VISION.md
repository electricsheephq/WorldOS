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
   ambiguous waits. Real art, not placeholders. The session has rhythm.
5. **No-prior-knowledge accessible.** A first-timer is never lost, overwhelmed, or staring at jargon
   or a dead control. Onboarding teaches itself.

## Invariants (load-bearing — NEVER violate; an option that breaks one is wrong by definition)

- **Engine = sole writer** of state (`snapshot.json` under `campaign_lock`, atomic replace).
- **Additive-by-default** — empty == today; old snapshots round-trip; every new field defaults to
  current behavior; each feature is independently removable.
- **Gates/triggers read ONLY engine-mutated gauges** (flags, reputation, attitude, day, standing) —
  **never fiction.** The DM judges the *cause*; the engine owns the *number*.
- **Engine rolls; the DM is told.** Probability proposes, the DM disposes.
- **Viewer is read-only** on state — it posts move-intents; it never mutates.
- **Never break wire contracts** — additive, keyword-only, defaulted; never reorder/rename/retype an
  existing param.
- **QA is gateway-free and never touches Eva** (the owner's live agent) — no profile-sourcing, no
  gateway reconfig, no Eva infra. One live Mac/GUI harness at a time. CI/full suites on GitHub.

## The bar + release ladder

- **Engine Excellent** *(met)* — behavioral GREEN; no engine defect in combat sprints; zero-critical
  / arc-completes / no-give-ups; audit P0/P1 closed; a dogfood arc with no engine "broken" moment.
- **Player-Ready Beta** — a real built `.app`; a no-prior-knowledge dogfood arc with no "broken"
  moment; an honest, felt session.
- **1.0 GA** — Beta + notarized signing + feature parity: companions felt, visual parity, story at
  the bar.

## How we build

- **Dogfooding is the feedback spine.** Real play surfaces what matters; the dogfood log is the
  prioritized backlog. Don't guess — play it. But *measure before you trust a complaint*: a felt
  report can be a harness artifact or a mis-read (verify against the artifacts before fixing).
- **Ultracode workflows for velocity** — fan-out builders (TDD → PR) → adversarial verifiers
  (revert-check) → admin-merge → fast_gate.
- **Adversarial verification before merge** — every merged PR is revert-checked (revert the fix → the
  test goes RED for the documented reason; green-on-revert = reject).
- **Research before load-bearing code** — a load-bearing surface (tool API / schema / wire) with 2+
  real options gets a first-principles decision doc, not speculative code.
- **Decisions are anchored here via `worldos-decide`** — score against this vision + the invariants,
  fire adversarial sub-agents, reach 95% confidence, then move. Escalate to the owner *only* when the
  call genuinely turns on their private context (product taste, business priority, a vision *change*)
  that no analysis resolves — and then bring a recommendation, not an open question.

## What we are NOT doing

- Not optimizing a score for its own sake (no rubric-gaming).
- Not generating portraits for the 2,000+ canon NPCs — they have wiki art. Image-gen is for **custom
  characters + genuine gaps only**.
- Not seating banned BG3 origin heroes (Astarion, Gale, Karlach, Lae'zel, Shadowheart, Wyll, Halsin)
  or dead figures as PCs — they are companions/NPCs only.
- Not shipping a correct-but-lifeless session and calling it done.
