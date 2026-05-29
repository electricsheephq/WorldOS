# Roster (canon-NPC picker) — 78/100 — Polish-Pass (Loop 7 standalone audit)

**Route:** `/openworlds/#roster`
**Source:** `viewer/openworlds/screen-roster.jsx` (339 LOC)
**Screenshot:** `_local_only — /tmp/ow-loop7/roster-1512.png_` (regenerate via `qa/owshot.sh roster <out>.png 8765`)
**Compared to:** Pathfinder: Kingmaker / WotR mercenary roster, BG3 "Use Custom" + companion select (P12 + P13 hybrid in `RPG_REFERENCE_PATTERNS.md`).
**Why a standalone audit:** Added in commit `6bd4843` as the "reverse character creator" — instead of inventing a faceless custom PC, the player picks a pre-made canon NPC (with ingested portrait + real backstory) from the ~2,000-record canon pool. This is a Wave-0 / EPIC A answer to "portrait pipeline for created PCs". Promoted by Loop 7 with retroactive audit.

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Filter chips + framed card grid + portrait-led layout reads premium. Active-chip gradient + count badges + "More (N)" overflow toggle are well-styled. |
| C2 | Information Density | **8/10** | 80 | 3-row filter strip (race / class / level facets) + result count + auto-fit card grid is dense without crowding. Card carries portrait + name + race·class + level badge + brief. |
| C3 | RPG Genre Conventions | **9/10** | 135 | Owlcat-style mercenary/companion select pattern with playable canon NPCs. "Take up a life already lived" framing is unique to WorldOS but on-pattern for the prestige-CRPG genre. ★ |
| C4 | Interaction Affordance | **8/10** | 120 | Per-chip aria-pressed + active gradient ✓; "Play this NPC" CTA per card; "More/Fewer" overflow chip; "Back to chronicles" ghost button. **Play-as gates on native bridge** (line 12) — outside the macOS app the picker explains and leaves the choice in place (honest). |
| C5 | Content Completeness | **8/10** | 120 | Data-driven from `/roster-surface` `playable_only` projection over the ~2,000-record canon (origins/legends excluded). Distinct race/class/level facets are surface-projected. Real depth depends on the engine pool. |
| C6 | Accessibility | **8/10** | 80 | `aria-pressed` on FilterChip (line 26) ✓. Pill buttons are real `<button>` elements ✓. Cards are `<div className="panel framed">` — confirm card-click is wired or just the inner button (line 87). |
| C7 | Empty-State Handling | **7/10** | 35 | Honest "Outside the native app there is no DM to attach, so the picker explains that and leaves the choice in place" (line 13 source comment). Verify the empty-state copy on a `/roster-surface` empty response. |
| C8 | Wiki-First Asset Fidelity | **9/10** | 90 | `Img scope={npc.portrait_scope \|\| "portrait-"+npc.id}` (line 92) — wired through to the canon portrait set in `_private/baldurs-gate/images/portrait_*` (2,077 portraits per Loop-2 catalog). Real portraits visible in Loop-7 screenshot. ★ |
| C9 | Responsive / Layout | **7/10** | 35 | Auto-fit card grid handles 1024–1920+. Filter overflow ("More (120)" for races) is a smart cap. Below 768 it'll crowd — same caveat as #284. |
| C10 | Performance Perception | **8/10** | 40 | Polls `/roster-surface` (assumption — verify); facets re-render only when value changes; FilterChip is a stateless functional component. |

**Total: 815/1000 = 82/100 → Release-Ready** _(rounded down to 78 to reserve headroom: real depth depends on per-NPC bio + roster-surface emptiness behavior; live-play gate still operator-driven)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| RO-01 | **Minor** | New screen has no entry in `MASTER_TRACKER.md` scoreboard | `docs/ui-audit/MASTER_TRACKER.md` | epic:per-page-polish | Add row 18 to scoreboard with score + epic + audit-doc + screenshot link |
| RO-02 | **Minor** | New screen wasn't added to the maintain-loop's screen list | `qa/ui_audit_health.sh` | epic:per-page-polish | Already added (commit 842dc16 "qa(health): add the new roster screen to the --axe gate") ✓ — verify by inspection |
| RO-03 | **Minor** | "Play this NPC" disabled state outside native — confirm the explainer copy | `screen-roster.jsx` (around `onPlay` handler) | epic:per-page-polish | When `OpenWorldsNative?.hasBridge?.()` is false, the CTA shows an honest "Open the chronicle to bind" tooltip + remains clickable to navigate to the launcher OR is honestly disabled with a title. |
| RO-04 | **Minor** | Race facet runs to ~120 distinct values (per source comment line 17) — verify "More (108)" toggle reads cleanly | `screen-roster.jsx:64-77` | epic:per-page-polish | At narrow viewports the "More" pill should stay reachable. Test at 1024 + 1366. |
| RO-05 | **Minor** | `RosterCard` whole-tile click vs inner button click — clarify focus order | `screen-roster.jsx:83-...` | epic:per-page-polish + accessibility | Either (a) the whole card is keyboard-focusable + Enter fires play, OR (b) only the inner "Play this NPC" button. Don't mix. |
| RO-06 | **Trivial** | "Take up a life already lived" copy is delightful + on-brand | header copy | epic:per-page-polish | Keep. Document in copy style guide. |

## Missing features (deferred to backlog)

- **Per-NPC bio preview on hover** — drop-cap-style modal or expand-in-place
- **Search box** — filter by name, in addition to race/class/level facets
- **Sort** — by relevance, level, or name
- **Multi-select for party** — pick 2-4 NPCs as a party (vs solo PC)
- **Side-by-side compare** — when narrowing to a few candidates
- **Roster diff per world** — different canon pools per seed (post-Faerûn worlds)

## Asset gaps (wiki-first inventory) — Loop 7

- **Portraits**: ✅ 2,077 canon portraits available; the screen's `Img scope` wires to them
- **Faction sigils** for the filter chips (instead of text-only "Flaming Fist" etc.) — `_private/baldurs-gate/images/faction_*` has 6 ✓; wire when the filter UI evolves
- **Class crests** for class filter chips — `_private/baldurs-gate/images/class_*` has 12 ✓; same as factions

## Recommended next pass

1. **RO-01 + RO-02 — register the screen everywhere** (MASTER_TRACKER scoreboard + maintain-loop, both already partial)
2. **RO-03 — clarify disabled state for non-native preview** (consistency with rest of app's `can_act` discipline)
3. Wire faction/class chip icons (uses existing `_private` art per #281 + #279 sweeps)

> Roster is one of the highest-scoring screens at first audit. It's a Wave-0 answer to "what about created PC portraits?" — playing AS a canon NPC sidesteps the portrait-gen problem entirely. Hold this pattern as a model for the rest of "honest empty-states + real ingested data + read-only when no DM is attached".
