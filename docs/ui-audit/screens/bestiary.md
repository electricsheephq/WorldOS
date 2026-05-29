# Codex (Bestiary) — 56/100 — Finish-Wave

**Route:** `/openworlds/#bestiary`
**Source:** `viewer/openworlds/screen-bestiary.jsx` (335 LOC)
**Screenshot:** `docs/ui-audit/screenshots/bestiary-1512.png`
**Compared to:** Pathfinder: Kingmaker bestiary, DNDBeyond Monster Manual, BG3 inspect (P7 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Two-column codex with creature list + entry detail + Challenge Rating wax seal. **'ENCYCLOPAEDIA OF THE MARCHES' header is Pathfinder Kingmaker lore-leak**. Aboleth selected — Challenge 10 + known abilities tags BUT all stat fields (HD/AC/Speed/Senses/Save/Encountered) are empty. Persons + Lore tabs have no live data."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Codex chrome + CR wax seal + 6-stat grid frame are well-realized. |
| C2 | Information Density | **6/10** | 60 | Two-column 280/1fr; entry pane has lots of empty space because most stat fields don't populate. |
| C3 | RPG Genre Conventions | **6/10** | 90 | All P7 patterns laid out — stat block, known actions, tactics, marginalia. **Stat fields rendered but empty (HD/AC/Speed/Senses/Save) per the live `player_bestiary_preview` projection** (line 26-49: surface omits these fields by design as "player-safe"). |
| C4 | Interaction Affordance | **7/10** | 105 | Tabs Creatures/Persons/Lore wired; search box debounces ✓; entry click selects ✓. Persons + Lore have no live read-model (line 99). |
| C5 | Content Completeness | **4/10** | 60 | Aboleth + others render with names + CR + size + type + 5 known actions. Other stats blank. Persons tab + Lore tab show empty-state — no live data ever. |
| C6 | Accessibility | **6/10** | 60 | Search input has `placeholder` only — no label. Tab pills missing aria-pressed. Entry buttons have implicit aria. |
| C7 | Empty-State Handling | **8/10** | 40 | "Nothing recorded yet" pane (line 187-195) ✓; tab-specific empty copy (line 105-107) ✓. |
| C8 | Wiki-First Asset Fidelity | **6/10** | 60 | Creature plate via `<Img scope={"creature-"+slug(entry.name)}>` (line 167, 222) ✓ — Aboleth thumb appears to render in the list. Slug-alias contract noted in source (line 9-19) — engine must alias gnoll → gnoll-warrior etc. |
| C9 | Responsive / Layout | **6/10** | 30 | 280 + 1fr OK at 1280+. Stat grid at 6 cols cramps at narrow viewports. |
| C10 | Performance Perception | **7/10** | 35 | Debounced search (200ms, line 90) ✓; 5s poll absent (only on filter change). |

**Total: 620/1000 = 62/100 → Polish-Pass** _(rounded to 56 — empty stat fields + Marches naming + Persons/Lore dead tabs are below the Polish bar)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| BE-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| BE-02 | **Critical** | "Encyclopaedia of THE MARCHES" header is Pathfinder-Kingmaker lore-leak | `screen-bestiary.jsx:126-127` | epic:demo-leak | Replace with `Encyclopaedia of the Sword Coast` (or pull from `surface.world_label` so it's data-driven across worlds). Mirror the data-driven pattern in `screen-map.jsx:180`. |
| BE-03 | **Critical** | Stat block fields HD/AC/Speed/Senses/Save/Encountered are rendered but empty | `screen-bestiary.jsx:258-267` + engine `player_bestiary_preview` | epic:per-page-polish + epic:wire-prototypes | Either (a) extend the player-safe `player_bestiary_preview` to include these fields once the bestiary entry has been Encountered by the party (intel-tiered: see-once → CR + size, fight-once → AC + speed + senses, kill-once → HD + saves + tactics), OR (b) hide the StatLines entirely when blank instead of showing `—`/empty. Pick one + execute consistently. |
| BE-04 | **Major** | Persons + Lore tabs have no live read-model | `screen-bestiary.jsx:96-99` | epic:wire-prototypes | Either (a) wire `/persons-surface` + `/lore-surface` to project known canon NPCs + recorded lore, OR (b) hide the tabs and document the deferment. Don't ship dead tabs. |
| BE-05 | **Major** | Stats grid `entry.stats` never populated (the live surface doesn't emit `stats` per line 32 mapping) | `screen-bestiary.jsx:243-256` | epic:wire-prototypes | Same fix as BE-03: gate render on `entry.stats && Object.keys(entry.stats).length > 0`. Today it renders an empty 6-column grid because `liveBestiaryEntry` doesn't set `stats`. |
| BE-06 | **Major** | "Body" + "Tactics" + "Loot" + "Marginalia" prose sections always rendered but rarely populated | `screen-bestiary.jsx:269-327` | epic:wire-prototypes + epic:per-page-polish | Already gated on truthy ✓ — verify there's no `body=" "` whitespace issue. When all four are blank, show a small "Lore not yet recorded for this creature." |
| BE-07 | **Major** | "Provenance" section heading rendered when contentOrigin === "authored" — should also surface for SRD with `[SRD 5.2]` badge | `screen-bestiary.jsx:288-296` | epic:per-page-polish | When `entry.contentOrigin === "srd"`, show a small "[SRD]" badge next to the entry name OR in the eyebrow. Discloses source consistently. |
| BE-08 | **Minor** | Search box placeholder uses curly ellipsis — fine but unicode-only | `screen-bestiary.jsx:148` | epic:per-page-polish | Keep; document in copy guide. |
| BE-09 | **Minor** | "rumoured" count in the footer reads `0` for live surface (no `unknown` items currently) | `screen-bestiary.jsx:179-184` | epic:per-page-polish | Gate the "· N rumoured" suffix on `rumoured > 0`. Today line 182 already does this ✓. Verify under different surface states. |
| BE-10 | **Trivial** | The `?` glyph in BestiaryEntry for unknown reads charming | `screen-bestiary.jsx:206` | epic:per-page-polish | Keep. Maybe a wax-seal `?` instead of plain text. |

## Missing features (deferred to backlog)

- **Compare-side-by-side** — pick 2 creatures, see stat deltas.
- **Encounter history** — "You've fought 3 Goblin Warriors at Lower City."
- **Filter by CR / type / size / region** — Pathfinder bestiary has rich filters.
- **Player notes per creature** — "watch out for psychic attacks; bring protection from evil".
- **Linked quests + locations** — surface "appeared in The Fate of Emerald Grove".
- **Tactics intel-tier upgrade** — kill-once or interrogate-once unlocks tactics prose.

## Asset gaps (wiki-first inventory)

- **Creature plates** for the BG3 + SRD catalog under `_private/baldurs-gate/creatures/<slug>.png`. The slug-alias contract (line 9-19) requires the engine to map clean slugs ("gnoll") to art slugs ("gnoll-warrior"). This must land on the engine side; UI keeps the clean slug.
- **Persons + Lore data layers** — wiki ingest pipeline to populate the empty tabs.

## Recommended next pass

1. **BE-02 (Marches → Sword Coast)** is a 30-second fix that closes EPIC E for Bestiary.
2. **BE-03 (intel-tiered stat block OR hide-when-blank)** is the highest-impact lift — empty stat block reads broken.
3. **BE-04 (Persons + Lore wire or hide)** removes dead UI.
