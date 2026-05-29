# Atlas (Map) — 72/100 — Polish-Pass

**Route:** `/openworlds/#map`
**Source:** `viewer/openworlds/screen-map.jsx` (856 LOC), `camp-sidebar.jsx` (605 LOC)
**Screenshot:** `docs/ui-audit/screenshots/map-1512.png`
**Compared to:** Pathfinder: Kingmaker world map, BG3 fast-travel map, Crusader Kings 3 region map (P1 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "The audit-doc score of 3/10 is stale — this is now a real, force-directed, spatial atlas with a region backdrop, day/night clock, zoom/pan, prominent Make Camp, and travel intent wired. Big lift. Remaining gaps: only one known location for BG, watermark text behind nodes, and the strategic sidebar reads thin."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Force-directed pins, hover cards, region backdrop with parchment overlay, candle-glow on selected node — strong. Compass rose (line 514-519) is a delightful detail. |
| C2 | Information Density | **7/10** | 70 | Header bar with location count + urgent count + last tick is well-paced; right sidebar with selected location detail + strategic context + discovered list is dense but scannable. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P1 patterns present: spatial map, clock-driven time, encounter pins (current/visited/known), travel cost ("X minutes away"), Make Camp CTA, compass rose, region label. ✓ |
| C4 | Interaction Affordance | **8/10** | 120 | Wheel zoom / drag pan / Fit button ✓; pin click selects + side panel updates + Travel CTA gated on `canAct` ✓; Make Camp gated on `canCamp` with glow when available (line 196) ✓. |
| C5 | Content Completeness | **5/10** | 75 | **Only ONE known location ("Baldur's Gate — Lower City")** in this preview state — should be Lower City, Upper City, Outer City, Wyrm's Crossing, Rivington, Reithwin, Elturel, Candlekeep, the Underdark, … per the BG3 canon. The atlas-surface `known_locations` only carries discovered nodes; the engine isn't seeding the BG nav graph for fresh saves. |
| C6 | Accessibility | **6/10** | 60 | `ClockDial` has `aria-label` ✓ (line 826); zoom buttons have `title` only — no `aria-label`. Pin button has no role/aria-label. Reduced-motion: the `flicker` animation on `isCurrent` pin pulse (line 762) should respect `[data-reduced-motion=on]`. |
| C7 | Empty-State Handling | **8/10** | 40 | "No atlas data" empty-state (line 574-582) is honest + on-brand. Strategic context "No active threads … pick up a thread to set one in motion" (line 638-648) with two affordances — best-in-class empty state. |
| C8 | Wiki-First Asset Fidelity | **7/10** | 70 | Region backdrop wired via `REGION_BACKDROP = { "baldurs-gate": "map-sword-coast" }` (line 399) → `<Img scope="map-sword-coast">` (line 481-484) ✓. Need `_private/baldurs-gate/maps/sword-coast.jpg` present. Hover card uses `<Img scope={"location:"+loc.id}>` (line 778) ✓. |
| C9 | Responsive / Layout | **7/10** | 35 | At 1512 the 1fr/340px split works; below 1280 the sidebar would crowd the map. Force-directed simulation re-runs on layout-key change, not on resize — no rebalance on viewport change (acceptable). |
| C10 | Performance Perception | **8/10** | 40 | 7s poll (longer than other surfaces, line 80) — good. Force-directed sim is `O(n^2 × 320)` per layout-key change; locked in `useMemo` — won't run on poll tick ✓. |

**Total: 710/1000 = 71/100 → Polish-Pass** _(rounded to 72)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| M-01 | **Critical** | Only one known location in BG atlas for a fresh save | atlas-surface (engine), `screen-map.jsx:99-99` | epic:atlas | The atlas-surface for `baldurs-gate` seeds the BG nav graph with all canon districts (Lower City, Upper City, Outer City, Wyrm's Crossing, Rivington, Reithwin, Elturel, Candlekeep, Steel Watch Foundry, Undercity, Bhaal Temple, …) as `known` or `rumoured` on day 1. A new player should see a populated map, not an empty one with one node. |
| M-02 | **Critical** | Title-bar text overlaps nav-rail (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | (See L-01.) |
| M-03 | **Major** | Watermark "OPEN WORLDS ATLAS" text behind nodes is too prominent | `screen-map.jsx:520` | epic:per-page-polish | Reduce `fontSize` from `5` → `3.5`, `fill` opacity from `0.24` → `0.12`. OR move the watermark to a small bottom-right ornament rather than across the map's mid-band. |
| M-04 | **Major** | No fog-of-war / "rumoured" visual differentiation | `screen-map.jsx:708-797` | epic:atlas | A node with `loc.visited=false && loc.current=false` already renders a distinct pin (line 758) — but the pin label still shows the full name. For unknown/rumoured nodes, render "?????" instead of the name and gate the hover card. Mirror the bestiary's `entry.unknown` pattern (line 169-171). |
| M-05 | **Major** | Travel button gives no time/risk preview when hover-checking destination | `screen-map.jsx:622-630` | epic:atlas | When hovering a destination pin AND a travel option exists, the hover card should show `minutes away` + `route_kind` + `danger`. The hover card already exists (line 765-794); add a `travel` panel beneath the tags. |
| M-06 | **Major** | Strategic Context sidebar is mostly empty for fresh saves | `screen-map.jsx:635-679` | epic:atlas | Even with no quests/clocks/projects, surface the Campaign Director debts (`directorAdvisory.debts`) here so the sidebar always reads "the campaign owes something". Today it shows "No active threads in this region yet" with 2 nav buttons — fine but thin. |
| M-07 | **Minor** | No quest pin overlay (sword for hostile, scroll for quest) on map nodes | `screen-map.jsx:744-749` | epic:atlas | The atlas-surface emits `quest_markers` (line 102) but `LocationPin` doesn't overlay them. Add a small chip below the pin glyph for nodes with quests/clocks/projects (3 chips max). |
| M-08 | **Minor** | Make Camp button shows "(preview)" tooltip but no inline preview banner | `screen-map.jsx:156-165`, `camp-sidebar.jsx:293-295` | epic:wire-prototypes | When `!canAct` and Make Camp is clicked, surface a single toast ("Resting is engine-owned — start a live session") instead of opening a 6-panel camp sidebar with a disabled CTA. OR keep the camp sidebar but add a `<PreviewBanner>` at the top of `CampSidebar` (mirror `screen-settings.jsx:370-378`). |
| M-09 | **Minor** | Discovered list at bottom of sidebar duplicates the pin list | `screen-map.jsx:665-678` | epic:per-page-polish | Useful as a keyboard-nav fallback. Add `aria-label` and a search/filter input for > 20 locations. |
| M-10 | **Minor** | ClockDial reads the engine clock but is decorative-only — no tooltip on segments | `screen-map.jsx:815-853` | epic:per-page-polish + accessibility | Each segment already has a `title` attr (line 838 nested span) — verify pointer events reach it. Bump `title` to outer span. Audit reduced-motion path. |
| M-11 | **Trivial** | Force-directed layout sometimes nudges close-cluster nodes off-canvas | `screen-map.jsx:294-361` | epic:per-page-polish | When a node ends up at x<10 or x>90 after the sim, clamp it. Today it relies on the normalize pass (line 346-352) — fine for ≥ 5 nodes, sketchy for 1-2. Edge case. |

## Missing features (deferred to backlog)

- **Region overview / district zoom** — BG3's map has district zoom-in (Lower City → market plaza → tavern). Defer to post-1.0.
- **Travel-with-events** — moving across hostile terrain triggers wandering encounters. Engine-owned; surface here when wired.
- **Player-placed markers** ("I want to come back here") — Pathfinder + Skyrim both allow this. Defer.

## Asset gaps (wiki-first inventory)

- **`map-sword-coast` regional backdrop** (`_private/baldurs-gate/maps/sword-coast.jpg`) — owner's wiki direction lists `mapgenie.io` + `wand.com` as sources for the regional map.
- **Per-location scene art** — `location:<location_id>` is consumed by both Atlas hover and Atlas detail panel; need a complete set for the BG nav graph.
- **Pin glyph overrides per location type** — currently uses 3 globals (atlas.travel / camp.rest / settlement.tavern). A tower / temple / dungeon / port distinction would help legibility.

## Recommended next pass

1. **M-01 (seed the BG nav graph)** is the highest-impact item — a one-node atlas reads as broken even though the layout/zoom/clock are great.
2. **M-03 (watermark dim)** is a 30-second fix that lifts visual polish noticeably.
3. **M-08 (Make Camp preview banner)** — cleans up the cross-screen "(preview)" honesty pattern.
