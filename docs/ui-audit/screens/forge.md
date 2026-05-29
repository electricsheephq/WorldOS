# Forge — 62/100 — Polish-Pass

**Route:** `/openworlds/#forge`
**Source:** `viewer/openworlds/screen-forge.jsx` (488 LOC)
**Screenshot:** `docs/ui-audit/screenshots/forge-1512.png`
**Compared to:** Pathfinder: Kingmaker / WotR alchemy + crafting, BG3 camp crafting (P9 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Recipes/blueprint card/crafter bench is well-staged. Phase-4 lane wires Craft → `/move check` ✓. Bench shows empty for fresh party, FORECAST blank. Workshop Ledger seeded with three hardcoded entries (yesterday Scroll of Light / 2 days past Iron-shod boots / 5 days past Potion failed) — **stale demo leak per EPIC E**."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Recipe cards + Notes from the chronicle + Workshop ledger card-stack chrome reads well. |
| C2 | Information Density | **8/10** | 80 | 260/1fr/300 split + 4-component grid + Forecast bar. Right column scrolls. |
| C3 | RPG Genre Conventions | **7/10** | 105 | All P9 patterns: category tabs, recipe list w/ DC + time + tier, component slots with have/need, crafter picker, success forecast, workshop log. Missing: material breakdown on failure, partial-craft over multiple rests. |
| C4 | Interaction Affordance | **7/10** | 105 | Category click → first recipe ✓; recipe click selects ✓; "To the forge" wired to /move (line 87-119) when canAct OR a local sim when not (honest). Title-attr explains. |
| C5 | Content Completeness | **6/10** | 90 | 12 hardcoded recipes (line 353-485) — fine as a starter set. Bench empty in preview (no live party). Forecast blank (no hero). Workshop Ledger has 3 demo entries (DEMO LEAK). |
| C6 | Accessibility | **6/10** | 60 | Recipe buttons OK; component-slot is informational div — no aria; "To the forge" disabled state has good title attr. Glyph-only Pill on tier (line 172) has no aria. |
| C7 | Empty-State Handling | **7/10** | 35 | "Your party's crafters appear here once you have a hero" (line 253-256) ✓. Blueprint locked → "Blueprint unknown" full-pane empty (line 220-231) ✓. Workshop ledger has NO empty-state — instead has hardcoded fallback (the demo leak). |
| C8 | Wiki-First Asset Fidelity | **5/10** | 50 | Recipe icons via `<Img scope={"item-"+slug(r.name)}>` (line 163) ✓; component icons via same (line 333) ✓. Crafter slots use `<Placeholder label={p.short}>` (line 247) — should be `<Img scope={"portrait-"+p.id}>`. |
| C9 | Responsive / Layout | **6/10** | 30 | 260/1fr/300 = 860 center min; tight at 1280. |
| C10 | Performance Perception | **7/10** | 35 | 5s poll on `/character-surface` (line 51); UI flow doesn't churn. |

**Total: 670/1000 = 67/100 → Polish-Pass** _(rounded to 62 — demo-leak in Workshop Ledger is severity-Major and drops the screen below 70)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| F-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| F-02 | **Critical** | Workshop Ledger seeded with hardcoded demo entries (the scribe / the smith / a companion) — DEMO LEAK | `screen-forge.jsx:30-34` | epic:demo-leak | Initialize `log` as `[]`; the local-roll simulation only prepends on actual craft attempt. Empty-state copy: "No entries yet. The first craft will appear here." |
| F-03 | **Major** | Crafter pick uses `<Placeholder>` not `<Img>` for portraits | `screen-forge.jsx:247` | epic:portraits | Replace `<Placeholder label={p.short \|\| "portrait"}>` with `<Img scope={p.id ? "portrait-"+p.id : ""} label={p.short \|\| "portrait"}>` mirroring `screen-character.jsx:79`. |
| F-04 | **Major** | Locked recipe ("?????") only differentiated by faded text + "blueprint unknown" — no inline unlock affordance | `screen-forge.jsx:159-174` | epic:per-page-polish | When user clicks a locked recipe, show how to unlock (e.g., "Read a tome in Candlekeep" / "Apprentice with Dammon"). Either as inline copy on the right pane OR a tooltip on the locked recipe row. Today click does nothing visible. |
| F-05 | **Major** | "Components" grid uses 4 columns even when recipe has 3 — last column is decorative empty | `screen-forge.jsx:204-208` | epic:per-page-polish | Change to `grid-template-columns: repeat(${components.length}, 1fr)` capped at 4. Or render `auto-fit minmax(120px, 1fr)`. |
| F-06 | **Major** | Forecast pane blank when bench has no party — but the recipe detail still shows | `screen-forge.jsx:258-294` | epic:per-page-polish | When `!hero`, hide the Forecast pane entirely with a small inline message "Add a party hero to forecast craft success." instead of rendering empty pane. |
| F-07 | **Minor** | "?????" locked recipes mix in with known ones — would benefit from a divider or "Rumoured" tab | `screen-forge.jsx:149-175` | epic:per-page-polish | Add a horizontal rule between known and locked, OR a "Rumoured" sub-tab inside each category. |
| F-08 | **Minor** | Tier I/II/III/IV pill (line 172) uses plain Pill — could use a roman-numeral plate (matches Acts/Create flow) | `screen-forge.jsx:172` | epic:per-page-polish | Style the tier pill with the same red wax-seal-style block used in `ScreenActs` numeral. Consistency. |
| F-09 | **Minor** | Skill mod derivation falls back to `4` (line 76) when the skill name doesn't match | `screen-forge.jsx:71-76` | epic:per-page-polish | The fallback `4` is arbitrary. Better: show the forecast as "Forecast unavailable" until the skill matches. Don't fabricate. |
| F-10 | **Minor** | "Notes from the chronicle" eyebrow always rendered, even when `selected.note` is empty | `screen-forge.jsx:212-218` | epic:per-page-polish | Gate on `selected.note` truthy. The current recipes all have a note ✓ but future recipes might not. |
| F-11 | **Trivial** | "To the forge" button copy mixes register: ⚒ glyph + "To the forge" reads charming, but next to "Sow the change" / "Bind the hero" / "Light the lantern" it feels off-pattern | `screen-forge.jsx:288` | epic:per-page-polish | Either align with "Strike the bargain" / "Bind the hero" verb register, or keep — minor. |

## Missing features (deferred to backlog)

- **Recipe browser by skill** — filter by Smith's Tools / Alchemist's Supplies / Arcana / etc.
- **Multi-day craft** — recipes with `time: "2 rests"` should track progress across multiple rest events.
- **Material breakdown on failure** — some 5e crafting rules destroy partial materials; surface here.
- **Salvage** — turn unwanted items into components.
- **Crafting station bonus** — at a forge vs at camp, modify DC.
- **Master recipes / rumoured locations** — unlock-by-quest affordance.

## Asset gaps (wiki-first inventory)

- **Component icons** (whetstone, oil flask, iron filings, vellum, brass ink, quill, silver ingot, river pearl, sulfur, naphtha, …) — all under `_private/baldurs-gate/items/<slug>.png` to render via `<Img scope="item-<slug>">`.
- **Recipe result icons** — same store; e.g., `item-potion-of-healing`, `item-scroll-of-light`, `item-fine-silvered-dagger`.

## Recommended next pass

1. **F-02 (workshop ledger demo leak)** is the highest-priority item — finishes EPIC E.
2. **F-03 (crafter portraits)** + **F-06 (gate forecast on hero)** clean up the right column.
3. **F-04 (locked-recipe unlock affordance)** is a small content design lift with high gameplay payoff.
