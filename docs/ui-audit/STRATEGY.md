# WorldOS GUI Strategy — how to actually make the UI work

> **Owner question (2026-05-30):** "The graphic user interface probably looks
> nice, but in terms of usability, it's like a 2 out of 10. I'm not sure if we
> need a system for it or if we need to have the AI play it. What's the best
> way to get the graphic UI to work because the engine works and all the
> running it does on its own, but getting the actual graphic UI to work seems
> to be the hardest piece. I need some ideas."

This memo proposes a path. It's a strategic pivot, not a polish list.

---

## The honest diagnosis

After 8 audit loops + an implementation agent landing real fixes:

- **Engine: ~9/10.** Deterministic, sole writer, 1,400+ tests. Works.
- **Viewer server: ~8/10.** Surface routes return the right shapes. Works.
- **UI chrome: ~7/10.** Parchment-style, well-designed visually.
- **UI play-loop: ~2-3/10 per owner.** Tab clicks miss, dead characters playable, no
  real spellbook, no real battle map, no real shop transactions, Forge in demo
  mode, Acts conceptually muddled, Parley free-form is a stub.

**The gap isn't visual; it's wiring.** 18 screens were built simultaneously by
the implementation agent. ~5 of them are "play-loop-essential". The other ~13
are honest-empty prototypes that compete for the player's attention and reduce
the felt quality of the 5 that matter.

**The math: every prototype screen averages with the play loop in the player's
perception.** 5 great screens + 13 honest empty-states = a 2/10 felt
experience. 5 great screens alone = a 7/10 felt experience.

---

## Three strategic options (pick one)

### Option A — Surface-area collapse (FASTEST to "feels good")

**Hide 11 of the 17 screens.** Ship 6 only:

| Keep (the play loop) | Hide / defer |
|---|---|
| Launcher (Chronicles) | World Seed (decision pending) |
| Session (Table) — the play surface | Forge (decision pending — likely defer) |
| Combat (Battle) | Acts (decision pending — see #314) |
| Map (Atlas) | Bestiary advanced tabs (Persons/Lore not wired) |
| Character (Heroes) | Merchant (mostly mock) |
| Relations | Roster (Wave-0; works but exposes dead-character #305) |
|  | Settings advanced sections (audio/display preview) |
|  | Create (use Roster as the PC path; defer custom PC) |

Each hidden screen gets a 1-line "Coming soon" overlay or is removed from the
nav rail entirely. The honest empty states stop diluting the felt quality.

**Effort:** 1 PR, 1 day. Land in v1.0.4.
**Felt quality jump:** 2/10 → 5-6/10 immediately.
**Cost:** the audit's per-screen depth still applies when those screens come back.

### Option B — AI-driven UI validation (HIGHEST RIGOR)

The engine already runs `qa/run_duo.sh` (a 2-AI playtest: DM + constrained
player). **Reuse it as a UI validator.** The constrained-player agent operates
the UI through the same `/move` endpoints a human would.

Build `qa/ui_play.sh`:
- Spin up a viewer + engine
- Launch a headless Chrome pointed at the viewer
- The "player" agent reads screen content via DOM, decides a move, dispatches via JS click/fill
- The DM agent runs the engine response
- Capture each turn as a screenshot + a "did the UI render this correctly" judgment
- Score against `qa/SCORING.md` rubrics

**This validates: every screen the AI uses is functional.** Screens the AI
can't drive don't matter (or get re-prioritized).

Plus: an unsupervised loop. Run 100 sessions overnight. UI bugs surface as
"agent got stuck on turn N at screen X with hint Y." That's the most rigorous
QA signal possible for a GUI.

**Effort:** 1-2 weeks (extending the existing duo harness).
**Felt quality lift:** indirect — drives the ongoing work order.
**Cost:** the AI player can't tell you "the title bar looks ugly" — only "the
DOM said X but I expected Y."

### Option C — Adopt a real frontend framework (HIGHEST POLISH ceiling)

Current stack: React 18 + ReactDOM + Babel-Standalone (in-browser JSX compile,
no build step). ~25,700 LOC of JSX with no tests, no type safety, no shared
component library.

Migration path:
1. **Vite + TypeScript** — proper build, dev-server with HMR, type checking
2. **TanStack Query** for the `/<screen>-surface` polls (handles polling,
   cache, retry, deduplication automatically)
3. **shadcn/ui + Radix primitives** for buttons / tabs / dialogs / dropdowns —
   accessible by default (closes the tab-click-only-on-text bug at the
   primitive level)
4. **Playwright** for end-to-end UI tests (replace ad-hoc owshot.sh captures)
5. Keep the parchment styling as a **theme on top of shadcn**

**Effort:** 2-3 weeks, can phase per screen.
**Felt quality lift:** the play loop screens become "feels like a real app"
quality (BG3-tier).
**Cost:** big refactor; risks introducing bugs while migrating; the audit doc
re-anchors mid-migration.

---

## Recommended path: A + B + C in that order

1. **Now (1 day): Option A.** Hide 11 screens. v1.0.4 ships a 6-screen WorldOS
   that's honest and good in its 6 screens. **The play loop instantly feels
   release-quality.** This is the highest-leverage 1-day action available.

2. **Next 2 weeks: Option B.** Build the AI-player UI validator. Use it to
   drive a 100-session unsupervised playtest. Whatever bugs surface become the
   v1.0.5 + v1.0.6 issue queue.

3. **After v1.0.5: Option C.** Phased migration to Vite + TypeScript +
   shadcn/Radix. One screen per week. The maintain-loop (`qa/ui_audit_health.sh
   --axe`) catches regressions.

Each phase has a real ship target. Each phase makes the next phase easier
(fewer screens to migrate, AI catches regressions).

---

## What you should NOT do

- **Build more screens.** The current 17 are too many. Adding a Bestiary
  Persons tab, a Merchant Network screen, a Quest Editor — all defer until
  the 6-screen play loop is BG3-tier.
- **Hand-tune CSS per screen.** Every minor finding in the audit is a
  symptom; the cause is "no shared component library." shadcn/Radix solves
  the cause.
- **Try to ship all 17 screens to release-ready quality.** That's the trap
  the implementation agent has been running. Math says it's impossible in
  the timeframe owners care about.
- **Wait for a perfect spec before shipping Option A.** Hiding a screen is
  reversible. Shipping a screen that confuses players costs reputation that
  isn't.

---

## Acceptance criteria — when "the GUI works"

The owner can play one full BG session start-to-finish in WorldOS without
saying "this feels broken" once. Measured by:

1. Launch app → pick a chronicle → click "Resume" → DM narrates → player
   declares a move via the action bar → DM resolves → repeat for 8 beats.
2. One combat encounter resolves on the Battle screen with tokens moving,
   HP decrementing, log scrolling.
3. One Parley resolves with a real skill check landing.
4. One Camp long-rest fires, party HP refreshes, clock advances to morning.
5. No dead UI clicks, no overlapping text, no orphan controls.

If those 5 happen smoothly, the GUI works. **None of the other 11 screens
need to exist for those 5 to happen.** Hence Option A.

---

## Owner decision queue (do these before any agent picks this up)

1. **Approve Option A surface-area collapse?** Y/N. If Y, agent picks the
   6 keepers, hides the rest.
2. **Forge: defer or ship by 1.0?** (#312)
3. **Acts: emergent / seed-defined / drop?** (#314)
4. **Combat tactical grid: hex or square?** (#318)
5. **PC creation primary path: Create wizard or Roster picker?** (Roster
   per #302 is on-brand; Create per #257 is custom. Pick one for 1.0.)

These five decisions unblock 80% of the still-open audit issues.

---

## Closing

The engine is the hard part of a TTRPG simulator and you've solved it. The
GUI is the easy part being made hard by trying to ship 17 screens at once.

Surface-area collapse + AI-driven validation + a real framework — in that
order — turns "2/10 usability" into "release-ready in 4-6 weeks." That's a
real plan, not a polish list.

Refs: parent epic #242. Loop 8 verdict. All audit-cycle issues #244–#320.
