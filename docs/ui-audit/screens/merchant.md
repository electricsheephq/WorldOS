# Market (Merchant) — 70/100 — Polish-Pass

**Route:** `/openworlds/#merchant` (alias `#market`)
**Source:** `viewer/openworlds/screen-merchant.jsx` (371 LOC)
**Screenshot:** `docs/ui-audit/screenshots/merchant-1512.png`
**Compared to:** BG3 merchant UI (split buy/sell + cart + haggle), Pathfinder: Kingmaker shop (P10 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Talli portrait renders + greeting prose + reputation gauge + haggle ±5% buttons + 16-row wares table with item icons + cart panel + coin purse. Phase-4 BUY wired to /move. The Last Light Inn theming is on-canon. Strong overall."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Merchant portrait + drop-cap greeting + table chrome + counter cart all read like a BG3 shop. |
| C2 | Information Density | **8/10** | 80 | 260/1fr/280 split — merchant info + table + cart fit at 1512. 16 rows visible without scrolling. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P10 patterns: portrait + name + greeting + reputation + buy/sell tabs + cart + coin purse + haggle. Missing: split buy/sell pane (single tab), compare-on-hover, restock countdown. |
| C4 | Interaction Affordance | **8/10** | 120 | Tab switch ✓; haggle ±5% ✓ with reaction-line tone shift (line 109-113); Take/Sell row buttons ✓; Strike the bargain wires to /move when canAct (line 286-307) with `(preview)` honest fallback. |
| C5 | Content Completeness | **7/10** | 105 | 16 wares for Talli (line 349-366). Only ONE merchant (line 14 `merchantId = "gate-sundries"` doesn't match `"talli"` in MERCHANTS — a bug, falls back to MERCHANTS[0] = Talli). |
| C6 | Accessibility | **6/10** | 60 | Table headers have eyebrow style but no `<th scope="col">`. Row buttons OK. Coin icons are radial gradient — no aria. Haggle buttons need aria-pressed semantics. |
| C7 | Empty-State Handling | **8/10** | 40 | "No wares on offer / Nothing to sell" row (line 205-216) ✓; "The counter is empty. Take something off the shelf." (line 240-243) ✓. |
| C8 | Wiki-First Asset Fidelity | **7/10** | 70 | Merchant portrait via `<Img scope={"portrait-"+merchant.id}>` (line 70) ✓ — Talli portrait renders. Item icons via `mItemScope` (line 7-10) ✓. **`merchant.id` mismatch (line 14 `"gate-sundries"` vs MERCHANTS line 340 `"talli"`)** breaks the portrait scope on the active id state, but falls back to MERCHANTS[0] silently. |
| C9 | Responsive / Layout | **7/10** | 35 | 260/1fr/280 = 800px center; tight at 1280. Table fine. |
| C10 | Performance Perception | **8/10** | 40 | Single `/character-surface` fetch (line 36) to gate canAct — minimal. |

**Total: 750/1000 = 75/100 → Polish-Pass** _(rounded to 70 — single hardcoded merchant + id mismatch + display-only coins are below Release-Ready bar)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| MK-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| MK-02 | **Critical** | Merchant id mismatch — `useState("gate-sundries")` vs MERCHANTS only carries `id:"talli"` | `screen-merchant.jsx:14, 340` | epic:per-page-polish | Either set initial state to `"talli"` (match the array) OR rename MERCHANTS[0].id to `"gate-sundries"`. The current `find().[0]` fallback silently masks the bug. |
| MK-03 | **Major** | Only ONE merchant — single hardcoded MERCHANTS array | `screen-merchant.jsx:338-368` | epic:wire-prototypes | Wire a `/merchant-surface` read model so merchants vary per location (Talli at Last Light, the smith at Wyrm's Crossing, etc.). Today the same Talli appears in every BG district. |
| MK-04 | **Major** | Coin purse uses local state `useState({gp:232,sp:68,cp:14})` — not engine-projected | `screen-merchant.jsx:16` | epic:per-page-polish | Read coin balance from the active hero's `currency` (mirroring `screen-inventory.jsx:174-183`). Today the merchant's wallet is decoupled from inventory's wallet — confusing. |
| MK-05 | **Major** | Sell tab inventory is `state?.merchantStash` which is never populated by real state | `screen-merchant.jsx:50` | epic:wire-prototypes | Pull sellable items from the active hero's inventory (live `hero.items`) and exclude `quest` items. Mirror `screen-inventory.jsx`'s `stash`. |
| MK-06 | **Major** | "Filter…" button at bottom is non-functional | `screen-merchant.jsx:223` | epic:wire-prototypes | Either wire a filter dropdown (by type/price/weight) OR remove. No dead UI. |
| MK-07 | **Minor** | Reputation bar value `merchant.rep \|\| 32` (line 86-87) — fallback to 32 fakes a value | `screen-merchant.jsx:86` | epic:per-page-polish | When `merchant.rep` is undefined, hide the reputation row entirely. Don't fabricate a value. |
| MK-08 | **Minor** | Buy + Sell are mutually-exclusive tabs — BG3 uses a split pane (your stash above, merchant below) | `screen-merchant.jsx:121-225` | epic:per-page-polish | Refactor to a top-bottom split (or left-right) so player can drag-buy/sell. Today the tab dance is friction. |
| MK-09 | **Minor** | Haggle effect on price applied only at checkout, not on per-row price column | `screen-merchant.jsx:54, 198` | epic:per-page-polish | Show the haggled price per row in the table (strike-through original) so player sees the discount in-line. Today only the total reflects it. |
| MK-10 | **Minor** | Disposition copy "open until dusk · shuttered when the Watch patrols" is flavor-only, no actual time gate | `screen-merchant.jsx:347` | epic:per-page-polish | Either wire a time-of-day check (close after dusk per the clock) OR remove the flavor. Today it's narrative misdirection. |
| MK-11 | **Trivial** | "Strike the bargain" / "Accept silver" copy is great | `screen-merchant.jsx:313` | epic:per-page-polish | Keep. |

## Missing features (deferred to backlog)

- **Restock countdown / next-day stock** — BG3 + Kingmaker do this.
- **Compare-on-hover** — current equipped vs hovered item delta.
- **Multiple merchants per location** — Sundries Talli + a weapon smith + a scribe.
- **Reputation-locked wares** — show "Cordial+ only" pill on rare items.
- **Quest-item recognition** — when you have a quest item the merchant wants, surface a "this would interest Talli" hint.
- **Identification service** — pay X gp to identify magic items.

## Asset gaps (wiki-first inventory)

- **Merchant portraits** for each of the BG canon shopkeepers (Dammon, A'jak'nir Jeera, Tutor Sett, Quartermaster Talli, the smith at Wyrm's Crossing, …) under `_private/baldurs-gate/portraits/`.
- **Shop scene art** for each merchant's stall (Last Light Inn interior, Gather Sundries shop floor) under `_private/baldurs-gate/scenes/`.
- **Item icons** as in inventory.

## Recommended next pass

1. **MK-02 (id mismatch fix)** — 1-line trivial but masks the larger MK-03 issue.
2. **MK-04 (live coin purse)** + **MK-05 (live sell-tab stash)** wire the screen to real state.
3. **MK-03 (multi-merchant)** is the design-led item — pivots Market from "single shop" to "shop network".
