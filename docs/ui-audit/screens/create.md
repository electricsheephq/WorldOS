# Creation Plane (Create) — 60/100 — Polish-Pass

**Route:** `/openworlds/#create`
**Source:** `viewer/openworlds/screen-create.jsx` (741 LOC)
**Screenshot:** `docs/ui-audit/screenshots/create-1512.png`
**Compared to:** BG3 character creator, Pathfinder: WotR creator (P12 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Lovely 7-step wizard (Lineage → Calling → Past → Aptitudes → Face → Name → Bind). 6 race cards on Lineage step — all use **placeholder thumbs, no race portraits**. Right pane previews 'What you have made' with Unnamed Human + Fighter + 27 points. Bind hero wires to native startProviderSession. Out of 6 races + 6 classes + 9 backgrounds — covers the bones, not the full PHB."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Step list with roman numerals + drop-cap on race cards + ability score plates + right-side live preview reads premium. |
| C2 | Information Density | **8/10** | 80 | 240/1fr/280 split + 6-card grid on Lineage + ability point-buy grid + live preview — packed without crowding. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P12 patterns: stepped wizard, race + class card grid, background w/ skills, 3×3 alignment, 27-point buy w/ standard cost table, portrait gallery, name + biography, final review. Missing: subclass picker (paladin Oath / wizard School / ranger Conclave). |
| C4 | Interaction Affordance | **8/10** | 120 | Step click jumps to any step ✓; Back/Continue buttons ✓; Bind hero wires to native bridge ✓; ability ±1 buttons gate on score range + remaining points ✓. |
| C5 | Content Completeness | **5/10** | 75 | **6 races (Human/Halfling/Dwarf/Elf/Half-Elf/Tiefling) — missing Gnome / Dragonborn / Half-Orc / 5e+ races (Aasimar/Genasi/Goliath/Tabaxi). 6 classes — missing Barbarian / Druid / Monk / Ranger / Sorcerer / Warlock. 9 backgrounds — none use SRD canon names (Acolyte/Soldier/Sage). Family/House field has no state binding (line 458). Biography is local-state only (not passed to bindHero spec line 45-54).** |
| C6 | Accessibility | **6/10** | 60 | Step buttons OK. Alignment buttons need aria-pressed. Slider/Toggle wrap input range — verify. Drop-cap render on long card body. |
| C7 | Empty-State Handling | **7/10** | 35 | Final Step ("Bind") shows even with Unnamed hero — graceful. Point-buy reset button (line 399-403) ✓. |
| C8 | Wiki-First Asset Fidelity | **2/10** | 20 | **`StepRace` uses `SelectCard` with `<Placeholder portrait={r.glyph}>` (line 555) — NO race art renders. `StepClass` same. `StepPortrait` shows 12 `<Placeholder label={"portrait "+i}>` (line 426). Final review uses `<Placeholder label={"portrait · "+hero.portrait}>` (line 489).** This is the biggest asset gap in the app. |
| C9 | Responsive / Layout | **7/10** | 35 | 240/1fr/280 = 760px center; tight at 1280. 2-column race card grid scales OK. |
| C10 | Performance Perception | **8/10** | 40 | No surface poll (creation is local-state). bindHero is one-shot. |

**Total: 665/1000 = 67/100 → Polish-Pass** _(rounded to 60 — asset gap on race/class/portrait is sev-Critical for the genre, drags total down)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| CR-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| CR-02 | **Critical** | NO race portraits — `SelectCard` uses `<Placeholder>` for ALL races | `screen-create.jsx:540-570` | epic:portraits + epic:per-scene-art | Replace `<Placeholder label={portrait}>` with `<Img scope={"race-"+raceId} fit="cover">` where `raceId` is the race key. Ingest 6+ race portraits per the wiki-first direction (FR human, halfling, dwarf, elf, half-elf, tiefling iconic art under `_private/baldurs-gate/races/`). Falls back to Placeholder ✓. |
| CR-03 | **Critical** | NO class portraits/sigils — same Placeholder issue on `StepClass` | `screen-create.jsx:249-275` (uses `SelectCard`) | epic:portraits + iconography | Pass a `classSigil` scope to SelectCard → `<Img scope={"class-sigil-"+classId}>`. 6 sigils ingestable from BG3/5e wiki. |
| CR-04 | **Critical** | NO portrait gallery — `StepPortrait` is 12 Placeholders with `label="portrait N"` | `screen-create.jsx:409-437` | epic:portraits | Replace with `<Img scope={"portrait-default-"+i}>` referencing a curated gallery in `_private/baldurs-gate/portraits/default/` (gateway-gen or stock heroic portrait set). Mention of "Bring your own — drop a PNG" (line 432) is a delightful affordance — wire a drag handler later. |
| CR-05 | **Major** | Race set is incomplete (missing Gnome, Dragonborn, Half-Orc, post-PHB races) | `screen-create.jsx:591-640` | epic:per-page-polish | Add `gnome` (Forest + Rock subraces), `dragonborn` (with breath-weapon pick), `half-orc`. Match 5e PHB minimum set. |
| CR-06 | **Major** | Class set is incomplete (missing Barbarian, Druid, Monk, Ranger, Sorcerer, Warlock) | `screen-create.jsx:642-726` | epic:per-page-polish | Add the 6 missing PHB classes with HP / role / glyph / starting kit + first-level features. Match 5e PHB minimum set. |
| CR-07 | **Major** | Background names are non-canonical ("Wanderer", "Disinherited Noble", "Hedge-witch", "Spy") | `screen-create.jsx:728-738` | epic:per-page-polish | Either (a) replace with SRD canonical names (Acolyte, Charlatan, Criminal, Entertainer, Folk Hero, Guild Artisan, Hermit, Noble, Outlander, Sage, Sailor, Soldier, Urchin) OR (b) keep current names as a stylistic OW choice and add a "fits-as-Acolyte" subline for cross-reference. Owner call. |
| CR-08 | **Major** | Family/House field has no state binding | `screen-create.jsx:456-460` | epic:wire-prototypes | Wire to `useState`; pass into `bindHero` spec as `house` or `family`. Today user input is discarded. |
| CR-09 | **Major** | Biography is local state only, never passed to bindHero | `screen-create.jsx:440, 64` | epic:wire-prototypes | Pass `biography` into the `spec` object handed to `startProviderSession`. The engine can store it as flavor text on the PC record. |
| CR-10 | **Major** | No subclass / archetype picker — Paladin oath / Wizard school / Cleric domain | `screen-create.jsx:249-275` | epic:per-page-polish | Add a Step 1.5 (between Class and Background) when the chosen class has subclass choices at L1 (Cleric domain, Sorcerer origin, Warlock patron). Other classes pick subclass at L3 — defer. |
| CR-11 | **Minor** | Ability bonus stacking shows total but not subrace bonus origin | `screen-create.jsx:359-388` | epic:per-page-polish | When hovering an ability, surface "+1 from Human" — explicit pedagogy for new players. |
| CR-12 | **Minor** | "What you have made" right pane points-remaining label color crimson when > 0 reads as warning | `screen-create.jsx:209-211` | epic:per-page-polish | When `hero.points > 0`, use a muted-amber color, not crimson — points remaining isn't an error state. Save crimson for true blockers. |
| CR-13 | **Minor** | Alignment grid uses 9 buttons; clicking jumps the alignment without explanation | `screen-create.jsx:313-330` | epic:per-page-polish | Add a tooltip on each alignment ("Lawful Good: a paladin's path; obeys law in service of the good") so new players understand the choice. |
| CR-14 | **Trivial** | Step labels "Lineage / Calling / Past / Aptitudes / Face / Name / Bind" are charming but non-standard | `screen-create.jsx:19-27` | epic:per-page-polish | Keep — the framing is a strength. Document in copy style guide so future flows match. |

## Missing features (deferred to backlog)

- **Drop-PNG-to-replace portrait** — affordance promised in copy (line 432) but no drag handler.
- **Random / "roll me a hero"** — Pathfinder + 5e tools have it.
- **Bring-your-own background** — custom skill+feature combo.
- **Variant Human** — replace racial bonuses with 1 feat at L1 (5e common pick).
- **Multi-class hint** — "you may dip into Rogue at L2 by visiting a trainer."
- **Class capstone preview** — "at L20 you become …" — sets aspiration.

## Asset gaps (wiki-first inventory)

- **Race portraits / sketches** — the biggest single ask. 6-12 portraits per race (sex/skin tone diversity) under `_private/baldurs-gate/races/<race>/<i>.png`.
- **Class sigils** — heraldic crest per class.
- **Default portrait gallery** — 12-24 heroic portraits the player can pick from.
- **Background sigils** — small icon per background.

## Recommended next pass

1. **CR-02 + CR-03 + CR-04 (race / class / portrait art)** is THE biggest visible-quality lift. Without this the Creation Plane looks like a sketchbook.
2. **CR-08 + CR-09 (wire Family + Biography)** finishes the no-discarded-input pass.
3. **CR-05 + CR-06 (expand race + class set)** is content-led — does require new SelectCard data, no new structure.
