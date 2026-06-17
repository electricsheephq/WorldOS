# WorldOS — Sprint Map

_The single "what sprint are we in + what ships next" navigation layer over the GitHub milestones.
Re-organized 2026-06-03 (15 → 5 open milestones; every open issue homed). Renderer detail lives in
`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`; current build/gate state in `WorldOS-OPERATING-GOAL.md`._

## Releases
- **Shipped:** v1.0.0 (engine) · v1.0.1 (action lanes + companions) · v1.0.2 (a11y / UI burndown) ·
  v1.0.3 (WorldOS → WorldOS rename). See `CHANGELOG.md`.
- **Next ship — v1.0.4:** gated on the **non-partial 5-persona RRI verdict (#466)** on a fresh
  `dist/WorldOS.app`. The RRI is a hard-gate floor — native gate + arc complete + cross-persona
  satisfaction ≥7 (no give-up) + 0 critical bugs + story ≥4.3 + mechanical ≥4.5 + behavioral GREEN +
  UI-audit + image-render ≥95% + palette-live. Definition: `qa/release_readiness.py`.
- **Version note:** `v1.0.x` = shipped product (semver). `v0.3` in milestone names = the
  *feature-parity roadmap phase* (compete-with-fables.gg / BG3 depth), **not** a shipped version.

## The board — 5 milestones in 4 lanes

### ★ ACTIVE · Engine / Story / UX — `v0.3 Sprint 6 — Depth & Legibility` (~49)
The living-world gameplay depth: the BG3/PFK **parity lane** (`#591` meta + camp/memory/tools pillars
`#592–594` + the combat/merchant/map/dialogue/character children `#595–624`), gate-blocker reliability
(`#640`/`#623`), and persona-depth gaps (`#345`/`#350`/`#353`). **This is the main implementation
agent's active code lane** — touch it for metadata only.

### ★ ACTIVE · UI / UX polish — `Graphics Release 1.0 — Polish Wave` (~52)
The per-screen UI/UX **audit** polish (the 2026-05-29 audit): per-screen scorecards (`#244–259`) +
cross-cutting findings (`#272–320`) + creation/atlas/portrait audit (`#375–384`). Despite the
"Graphics" name this is the **GUI/dashboard** track — distinct from the pixel renderer below.

### Renderer (north-star R&D) — `Graphics — Renderer Roadmap (GT1–GT3)` (~8)
The pixel-renderer roadmap: GT1 SNES MVP (`#441`) → GT2 isometric / Godot (`#456`/`#457`) →
GT3 hex (`#585`/`#586`) → RPG-Maker BYOL (`#458`/`#460`) → measured-grid tactics (`#461`). Mostly
post-1.0 / the video-game north-star. **Detailed M0–M6 breakdown: `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`.**

### Release gate / infra — `Agent-Grade App Testability` (~2)
The RRI release verdict (`#466`) + the UX-first release-readiness sprint (`#467`). Feeds the v1.0.4
ship decision.

### Parked · Post-1.0 — `Post-1.0 / vNext` (~19)
Someday / roadmap: universes (`#330` KOTOR / `#331` VtM / `#332` Shadowrun), multiplayer (`#31`),
distribution (`#32`), the strategy epics (`#643` eval-scoring / `#644` open-IP packs / `#645` render
pipeline), the north-star QA epic (`#33`), and the superseded Owlcat-era epics (`#57–61`/`#71`/`#73` —
revisit-or-close, no delivering PR). Not in an active sprint.

## How to read priority
GitHub has **no P0–P3 labels** — priority = **`severity:*` label + milestone membership**:
| label | meaning |
|---|---|
| `severity:critical` | blocks play / shatters CRPG framing |
| `severity:major` | a BG3/PFK player calls it out in 60s |
| `severity:minor` | polish gap; wouldn't block a ship |
| `severity:trivial` | preference |
Within an active sprint, work **critical → major → minor**. Epics carry no severity (they're roadmap
containers, not work items).

## Label taxonomy (so it stops being tribal knowledge)
- **Priority:** `severity:critical/major/minor/trivial`.
- **Stage:** `tier-1` (provider-backed app/engine — current product) · `tier-2` (OpenClaw integration) · `foundation`.
- **Family:** `epic` + `epic:*` (per-page-polish, wire-prototypes, portraits, renderer, contract, ai-loop, ugc, atlas, …).
- **Surface:** `screen:*` (launcher/table/combat/dialogue/map/character/forge/inventory/relations/bestiary/journal/acts/create/merchant/seed/settings) · `area:engine` · `area:viewer`.
- **Renderer matrix:** `graphics` + `gt0/gt1/gt2` (game type) + `branch:a/b` (maturity) + `cap:c1–c10` (capability).
- **Quality:** `ui-audit` · `accessibility` · `asset-gap`.

## Cross-references
- Renderer canon — `docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`
- Current build/gate state (READ FIRST on resume) — `WorldOS-OPERATING-GOAL.md`
- Release gate — `qa/release_readiness.py` (RRI) + `qa/app_handoff_gate.py` (handoff)
- Dev loop + architecture — `WorldOS-RUNBOOK.md`

---
_Re-org note (2026-06-03): 15 open milestones → 5 — drained + closed the trailing `v0.3 Sprint 1–5`,
folded the renderer M-series into one bucket, renamed `v0.3 Later` → `Post-1.0 / vNext`; 34 unmilestoned
issues homed; 21 non-epic issues given a severity. Clarification vs the original plan: `Polish Wave` is
the **UI-audit/GUI** track (not the renderer), and the pixel renderer is the separate `Renderer Roadmap`
bucket — so the board landed at 5 lanes, cleaner than the planned ~7. The standalone "post-1.0 tracker"
issue was made redundant by the `Post-1.0 / vNext` milestone and skipped._
