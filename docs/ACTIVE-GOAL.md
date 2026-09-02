# ACTIVE GOAL — the standing driver (any agent, any model)

> **Status: ACTIVE.** If you are an agent working this repo autonomously, THIS file defines what
> "done" means. Read it before deciding to stop. It is enforced by a Stop hook on the owner's
> machine, but the rules bind regardless of enforcement.
> Bootstrap order: `docs/OPERATIONS.md` → `docs/roadmap/NOW.md` (you-are-here) → this file →
> `docs/roadmap/PRODUCT-ROADMAP.md` (the plan) → the active sprint charter (label `sprint-charter`).

## THE GOAL

**Execute PRODUCT-ROADMAP.md's sprints in charter order.** The only finish lines are:

1. **Owner check-in** — a new message from the owner is always a stop-and-respond point.
2. **DEMO COMPLETION (PRODUCT-ROADMAP §9) — all four gates green as §9 defines them: G1 certified gates on the OWNER-INSTALLED build with build identity, G2 the N≥3 blind-adjudicated text arc, G3 the walked full route in the sandbox player, G4 the owner playthrough** (the governing milestone since 2026-07-22; charter #1702 as of 2026-09-02), then the first TOWN world (Track C, epic #1640). GA v1.1.0 (milestone #36) remains the Act I terminus AFTER that — not a finish line for the current run.
3. **A VERIFIED blocker on ALL parallel lanes simultaneously** — see Blocker Law below.

**Nothing else is "done."** Completing a task list, finishing a PR batch, closing a sprint,
reaching a "clean checkpoint", or running a long session are all **commit-and-continue** points.
A completed task list means: pull the next item from the queue. This rule exists because on
2026-07-08 an agent cleared its overnight list and stopped with 9 of 10 roadmap sprints
unexecuted, citing a "box blocker" that was never verified (the box was up the whole time).

## THE QUEUE

The work queue is the open **`sprint-charter`-labeled issues, in sprint order** — the current head carries the **`active-sprint`** label (exactly one issue; move it at every transition). **Head as of 2026-09-02: #1702 (the refresh charter toward §9 DEMO COMPLETION; #1386 superseded).** S2 (#1309) / S3 (#1310) queue behind it.

- Closing charter N (with evidence on the issue) **pulls charter N+1**.
- **If sprint N+1 has no charter yet, WRITING that charter from roadmap §S(N+1) is the next
  task** — never a stop. (Charter shape: entry gate, ordered issues, exit gate, QA tier per
  QA-economics v2 in OPERATIONS.md.)
- `[EPIC]` issues named in the ACTIVE charter's ordered list are claimable directly (OPERATIONS
  claim-rule amendment, 2026-07-08).
- Parallel lanes (renderer/graphics — LOCAL Unity on this Mac since 2026-08, the GEX44 box is gone —, harvest-nightly, docs)
  run alongside the charter spine; an idle lane picks up the charter's next unclaimed issue.

## BLOCKER LAW

A lane may park **only** with a fresh-probe row in the table below: the exact command you ran,
its output, and a timestamp. "I believe X is down" is not a blocker; a failed probe from today is.

- **Blockers EXPIRE after 12 hours.** Re-probe and re-enter, or unpark and continue.
- Before writing a row, check the canonical trail first (`docs/roadmap/NOW.md`, `docs/RUNBOOK-INDEX.md`,
  `docs/OPERATIONS.md`, the `~/.claude/CLAUDE.md` WorldOS pointers + `~/.claude/runbooks/worldos-evaos-ops.md`) —
  the 2026-07-08 "box down" blocker was an agent probing wrong IPs for days while the box ran fine at the
  documented address, and the 2026-08-06 "credentials lost" report was two remembered paths, not a probe.
- A blocker on ONE lane never stops the run — move to the next non-blocked queue item.

| Lane | Probe command | Output (trimmed) | Timestamp (UTC) | Expires |
|------|---------------|------------------|-----------------|---------|
| _(none — table must be empty or fresh; stale rows are invalid)_ | | | | |

## OWNER-GATES (the short list reserved for the owner)

Everything NOT on this list is decided autonomously at ≥95% confidence (worldos-decide skill),
with the decision recorded on the relevant issue.

1. **Taste forks** — art direction, tone, "does this FEEL like a game" calls (post frames, ask).
2. **Spend** above ~$25 in a single run, or any new paid service/account.
3. **Teeth-class product dials** — engine soft-rejects/railroading levers (#1313 class).
4. **Publishing outward** — public release notes/pages beyond the repo, store submissions.
5. **Schema/data migrations** that break snapshot round-trip (should never be needed — additive law).
6. Anything a charter explicitly marks **FABLE-GATE** or **OWNER-GATE**.

## CADENCE (standing contracts)

- **Watcher contract** (OPERATIONS.md): long runs are watched by self-processing watchers that
  run the full verdict pipeline and wake the orchestrator once, decision-ready. Infra-fail ⇒ no
  citable scores row (write a `*CONTAMINATED` marker).
- **QA-economics v2** (OPERATIONS.md): fast_gate every PR → mechanism probe (~$1) when
  cue-adjacent → combat sprint when combat-adjacent. Hour-scale duos = batch/release evidence
  ONLY, solo-tenant.
- **Pixels rule**: every graphics/UX merge produces frames (qa/demo_reel.py or box capture)
  posted for the owner. No pixel-less graphics merges.
- **Morning report**: at owner-wake, one artifact (pixels first, verdicts, one ask max).
- **NOW.md**: update `docs/roadmap/NOW.md` (active sprint, live lanes, blockers) at every
  session close and every charter transition.

## STOP DISCIPLINE (what the hook enforces)

A turn/session may end only when one of these is true:
- a **pending auto-wake** exists (background task, watcher, CI watch) — and you did parallel
  queue work while it ran; or
- **every** lane has a fresh (<12h) row in the BLOCKERS table; or
- an **owner-gate** question is pending (asked sharply, bundled with completed progress); or
- finish line 1/2 above is reached.

"The immediate plan is complete" while `sprint-charter` issues remain open is a **bug**, not a
finish line.
