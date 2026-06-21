---
name: sprint-handoff-doc
description: Write a rich handoff / status doc that genuinely transfers knowledge when a multi-PR sprint on a new subsystem will be picked up by a different agent or after a compaction. Use when finishing a sprint, handing off a subsystem, or asked "are we handoff-ready / is this documented for a fresh agent." Produces a HANDOFF.md (diagrams + knowledge base + gotchas + backlog) AND cross-links it from the entry-point docs so it is actually discoverable.
---

# Sprint handoff / knowledge-transfer doc

A status list is NOT a handoff. The goal is that a cold agent (or post-compaction you) becomes
productive **without re-deriving the hard-won knowledge**. Put it at `<subsystem>/HANDOFF.md`.

## Structure (8 parts)
1. **TL;DR** — what's done vs NOT, in one honest paragraph. Don't let a diagram imply "shipped" when
   it's "planned."
2. **Status table** — done-vs-open with issue numbers + PR links.
3. **Architecture diagrams** — Mermaid (renders on GitHub): the build pipeline, the runtime data
   flow, the component/scene/layer stack. Plus a **diagram-vs-reality** caveat list — every place the
   diagram over-promises vs what's actually built (this is where cold agents get misled).
4. **Run / validate** — the EXACT commands to run + validate it, with expected output.
5. **Knowledge base** — the research facts that took effort to learn and are NOT reproducible from the
   code (the "why", the lineage, external-tool findings). Cite sources.
6. **Gotchas** — things that cost real time and aren't obvious from source.
7. **Prioritized backlog** — the next 3 issues with rationale, then the rest.
8. **References** — research runs, primary sources.

## The mandatory close step (the discoverability gate — do not skip)
A handoff doc that isn't reachable from the main entry points **doesn't count**. Before declaring done:
```
grep -ciE '<subsystem>|HANDOFF' WorldOS-RUNBOOK.md README.md docs/GETTING_STARTED.md .claude/skills/worldos-dev/SKILL.md
```
If any count is **0**, add a one-line pointer there. Also create or point a dev skill (`godot-dev`-style)
so the skill matcher auto-surfaces the subsystem for a fresh agent.

> Validated 2026-06-21: this structure took fresh-agent onboarding to ~95% — a lean status-only first
> draft scored ~72% (the work was invisible from the entry points). The rich version + the cross-link
> close step is what closed the gap.
