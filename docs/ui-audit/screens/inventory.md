# Stash (Inventory) — 66/100 — Polish-Pass

**Route:** `/openworlds/#inventory` (alias `#stash`)
**Source:** `viewer/openworlds/screen-inventory.jsx` (455 LOC)
**Screenshot:** `docs/ui-audit/screenshots/inventory-1512.png`
**Compared to:** BG3 inventory (paper-doll + grid + compare), Pathfinder: Kingmaker stash (P3 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Three-column hero-pack-detail layout. Caelar silhouette + 6 empty equipment slots + coin purse on left; auto-fill item grid in center with filter chips; item detail on right showing Chain Mail. Honest preview labels. Stash mostly empty — preview state. Equipment slots are flat row, not a paper-doll."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Tile grid, filter chips, coin slots all look like a CRPG inventory. Item detail panel reads cleanly. |
| C2 | Information Density | **8/10** | 80 | 320/1fr/320 split + auto-fill grid (72px min) packs items efficiently. Coin purse + equipment doll + filters + detail fit. |
| C3 | RPG Genre Conventions | **7/10** | 105 | All P3 patterns except paper-doll (line 145-164 is a 6-slot flat grid, not a doll silhouette w/ slot zones). Compare-on-hover absent. Drag-to-equip absent (context menu only). |
| C4 | Interaction Affordance | **8/10** | 120 | Filter chips ✓; item click selects ✓; right-click context menu (line 244-247) ✓; Equip/Use/Drop wired to `/move` with canAct gate (line 279-291) ✓; `(preview)` labels when not canAct (line 280, 283, 287, 291) ✓. |
| C5 | Content Completeness | **6/10** | 90 | Stash visible has 2 items + 58 empty slots — fine. Coin Purse populated from `hero.currency` ✓. Equipped grid empty — needs starting gear in fresh saves. Weight column blank `—`. |
| C6 | Accessibility | **7/10** | 70 | Right-click for context menu is mouse-only; need keyboard-equivalent (Enter on focused item → open menu). Filter chip buttons have no `aria-pressed`. |
| C7 | Empty-State Handling | **9/10** | 45 | "No party in this world" (line 107-118) ✓; "Empty pack" / "{name} is carrying nothing yet" (line 219-225) ✓; "The counter is empty. Take something off the shelf." (sister screen). |
| C8 | Wiki-First Asset Fidelity | **6/10** | 60 | Item icons via `<Img scope={"item-"+slug(item.name)}>` (line 346) ✓ — chain mail icon renders. Equipment slots use Img too (line 155) ✓. Hero portrait silhouette is correct fallback. Many item icons depend on `_private/baldurs-gate/items/` being populated. |
| C9 | Responsive / Layout | **6/10** | 30 | 320/1fr/320 = 960px center min; at 1280 it's tight. No collapse for narrow viewports. |
| C10 | Performance Perception | **8/10** | 40 | 5s poll on `/inventory-surface` (line 74); empty-state until first fetch ✓; no 404 storms. |

**Total: 720/1000 = 72/100 → Polish-Pass** _(rounded to 66 — paper-doll absence + drag-to-equip absence are non-trivial gaps for the genre)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| I-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| I-02 | **Major** | No paper-doll silhouette — equipment is a 6-slot flat grid | `screen-inventory.jsx:145-164` | epic:per-page-polish | Replace the post-portrait 6-slot row with a paper-doll layout: portrait centered, slot bubbles positioned around (Head top, Neck below head, Body center-overlay, Ring on either hand, Boots bottom). Mirrors BG3/Kingmaker. Slots remain Img+Placeholder hybrid. |
| I-03 | **Major** | Equipment slots are limited to 6 (Head/Neck/Body/Hands/Ring/Boots) — missing Mainhand/Offhand/Ranged/Cloak/Amulet/2nd Ring | `screen-inventory.jsx:299-302` | epic:per-page-polish | Expand `EQUIP_SLOTS` to the BG3/5e canonical set: Helm, Cloak, Amulet, Mainhand, Offhand, Body Armor, Gloves, Ring 1, Ring 2, Belt, Boots, Ranged. Engine `hero.equipped` should emit slot strings matching these. |
| I-04 | **Major** | No compare-on-hover (hover an item, see delta vs equipped) | `screen-inventory.jsx:322-364` | epic:per-page-polish | When hovering an item that has a matching `slot`, show a small overlay with `Δ AC: +1`, `Δ DMG: +1d4` vs the currently equipped item in that slot. Requires engine to emit per-item `stat_deltas`. |
| I-05 | **Major** | No drag-to-equip; context menu is mouse-only (right-click) | `screen-inventory.jsx:244-294` | epic:per-page-polish + accessibility | (a) Wire HTML5 drag from item grid to equipment slots, posting `{kind:"equip", slot, item}`; (b) make context-menu keyboard-accessible (Enter on focused → open menu). |
| I-06 | **Minor** | Sort + Mark Trash + Loot Pile bottom buttons all disabled "(preview)" | `screen-inventory.jsx:260-263` | epic:wire-prototypes | Either wire to a real engine route (sort = client-side OK; Mark Trash = local state; Loot Pile = engine-owned aggregate) OR remove the disabled buttons. Don't ship dead UI. |
| I-07 | **Minor** | Empty grid slots (line 251-253) keep rendering a Placeholder even when stash has < 60 items | `screen-inventory.jsx:251-253` | epic:per-page-polish | The 60-slot empty padding feels like 90s D&D character sheet "you have N empty pack slots" which doesn't match the rest of OpenWorlds (organic / no max-pack-size). Remove the padding placeholders; let the grid grow to actual content + a "drop more here" affordance. |
| I-08 | **Minor** | Weight column blank `—` everywhere because the engine doesn't sum it | `screen-inventory.jsx:389` | epic:per-page-polish | Removed encumbrance bar per a prior wave (line 168-171 comment) — fine. Then weight column should also be hidden, not show `—` placeholders. Be consistent. |
| I-09 | **Minor** | Coin Purse uses 5 metals (PP/GP/SP/EP/CP) — Electrum + Copper rarely seen in 5e | `screen-inventory.jsx:174-183` | epic:per-page-polish | If the engine never mints Electrum, hide the EP slot. Same for Copper. Keep currency types data-driven. |
| I-10 | **Minor** | Hero switcher pills (line 132-142) at small party (1-2) leave a lot of dead space | `screen-inventory.jsx:131` | epic:per-page-polish | Wrap pills tighter or stack vertically when there's room beneath the hero portrait. |
| I-11 | **Trivial** | "Loot Pile" is a charming name but no UI affordance ever shows up for it | `screen-inventory.jsx:262` | epic:per-page-polish | Define what "Loot Pile" should do (split-with-party + drop) before unhiding. |

## Missing features (deferred to backlog)

- **Item search** — filter chips cover types; text search would help at > 50 items.
- **Per-slot weight summary** — small "X / Y lb" under the body slot.
- **Item rarity tags + color** — rare/uncommon/legendary; partly there via `type` (line 323-327) but not surfaced in detail.
- **Wishlist / mark-for-purchase** — flag items in merchant view from inventory.
- **Sets** — equipped items that form a set with bonuses.
- **Attune slots** — 5e magic-item attunement (max 3) display.

## Asset gaps (wiki-first inventory)

- **Item icons** for the BG3 / SRD weapon + armor catalog under `_private/baldurs-gate/items/<slug>.png`. Verify the slug-derivation matches `itemScope` (line 19-22).
- **Heroic gear set** for the starting kit per class (Fighter chain mail + greataxe, Wizard quarterstaff + spellbook, …) — see `screen-create.jsx:CLASSES[<id>].kit` for the canonical kit names.
- **Coin icons** are radial gradients (line 312-315) — could ingest real BG3 coin art per metal.

## Recommended next pass

1. **I-02 (paper-doll)** is the visible "this looks like a real CRPG inventory" lift.
2. **I-03 (full slot set)** + **I-04 (compare-on-hover)** are paired wins toward BG3 parity.
3. **I-06 + I-07 (clean dead UI)** finish the honesty pass on the polish bottom.
