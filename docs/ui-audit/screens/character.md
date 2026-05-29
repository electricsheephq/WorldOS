# Heroes (Character) — 68/100 — Polish-Pass

**Route:** `/openworlds/#character`
**Source:** `viewer/openworlds/screen-character.jsx` (798 LOC)
**Screenshot:** `docs/ui-audit/screenshots/character-1512.png`
**Compared to:** BG3 inspect / character sheet, DNDBeyond sheet, Pathfinder: Kingmaker portrait panel (P2 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Beautiful prestige-CRPG sheet layout — header card + ability column + lower 3-pane split (combat / abilities-skills-spells-feats / lineage-traits). Caelar silhouette + empty equipped slots + ability scores all '10' read sparse in this preview state. Spellbook + Rest modal are honestly labeled preview."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Best-realized sheet layout in the app. AbilityScore plate + StatLine + Divider rhythm + drop-cap traits — feels like a sourcebook. |
| C2 | Information Density | **8/10** | 80 | 200px / 1fr top + 1.05/1.7/1fr lower split is tight. Lots of fields fit without crowding. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P2 patterns: portrait+pills, 6-stat grid, AC/HP/Speed/Init, saving throws, equipped strip, resources, conditions, lineage. Skills tab uses `s.mod >= 0` color (line 641) but doesn't surface proficiency markers (BG3/DNDBeyond use a filled dot). |
| C4 | Interaction Affordance | **6/10** | 90 | Tabs Abilities/Skills/Spells/Feats wired ✓; **Rest & Prepare CTA opens a beautiful 2-step modal but it's preview-only — "Make camp" + "Seal the choices" buttons are disabled with a title attr (line 377, 451).** No browse-spells-not-yet-learned path. No respec / level-up path. |
| C5 | Content Completeness | **5/10** | 75 | This preview shows Caelar with ability scores all `10` (defaults — implies no live `/character-surface` data), empty Equipped grid, empty spellbook. Real values populate when a live save loads. Spellbook prepared-spells set is empty — honest message (line 766-771) but no "browse the spellbook" affordance. |
| C6 | Accessibility | **7/10** | 70 | Tab buttons styled but missing `role="tab"` + `aria-selected`. Roster buttons have implicit aria via `onClick`. Tooltip-on-AbilityCard requires hover/focus to discover (line 587-605) — `cursor: "help"` ✓. UI scale honored via `OpenWorldsA11y`. |
| C7 | Empty-State Handling | **7/10** | 35 | Lineage panel renders "No lineage recorded" when both race + note are empty (line 668-670) ✓. SpellsTab differentiates caster-with-empty-slots vs non-caster (line 766-771) ✓. Equipped grid does NOT have an empty-state — just renders nothing if `hero.equipped` is empty. |
| C8 | Wiki-First Asset Fidelity | **5/10** | 50 | Hero portrait via `Img scope={"portrait-"+hero.id}` ✓ (line 117). **Equipped uses `<Placeholder label={it.glyph}>` (line 212)** — should be `<Img scope={"item-"+slug(it.name)}>` mirroring inventory's pattern (`screen-inventory.jsx:155`). Same for AbilityCard glyph (line 595) + FeatRow glyph (line 616) + Spell entries (line 753) + RestPrepareModal spell tiles (line 427) — all `<Placeholder>`. |
| C9 | Responsive / Layout | **6/10** | 30 | 200px rail + 1fr at the top; 1.05/1.7/1fr at the bottom. Below 1280 the lower 3-pane crowds. No collapse strategy. |
| C10 | Performance Perception | **7/10** | 35 | 5s poll on `/character-surface` (line 39); empty-state until first fetch (line 56-62) — clean. ResourcesStatus + Lineage gate on truthy values ✓. |

**Total: 675/1000 = 68/100 → Polish-Pass**

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| C-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| C-02 | **Critical** | Spellbook has no browse-not-prepared path — caster sees "No spells prepared yet" with no way to inspect their own spellbook | `screen-character.jsx:728-774` | epic:per-page-polish | When `slots.length > 0` AND `groups.length === 0`, surface a "Browse spellbook" CTA that opens a list of known spells (read-only inspect — no preparation, since RestPrepareModal owns that). Wire to a new `hero.knownSpells` field on `/character-surface` (engine projects names; UI renders descriptions via SRD lookup or wiki ingest). |
| C-03 | **Major** | Equipped items use `<Placeholder>` not `<Img>` — even when an item icon exists in `_private` | `screen-character.jsx:212` | epic:portraits + epic:per-scene-art | Replace `<Placeholder label={it.glyph}>` with `<Img scope={"item-"+slug(it.name)} label={it.name}>` exactly as `screen-inventory.jsx:155` does. Falls back to Placeholder via Img's onError ✓. |
| C-04 | **Major** | AbilityCard / FeatRow / SpellsTab tiles use `<Placeholder>` not `<Img>` for the glyph | `screen-character.jsx:595, 616, 753` | epic:portraits + iconography | Replace with `<window.OpenWorldsIcon id={a.icon}>` via `IconPlate` for known SRD ability/feat/spell glyphs, falling back to Placeholder. Many SRD spell glyphs are already in the icon registry (#174). |
| C-05 | **Major** | Rest & Prepare modal has no engine write lane — both CTAs disabled with title attr | `screen-character.jsx:377, 451` | epic:wire-prototypes | When `can_act` true (live session attached), wire "Make camp" to a `POST /move` with `{kind:"rest", type:"short"\|"long", watch:[...]}`. Wire "Seal the choices" to `{kind:"prepare_spells", spells:[...]}`. Engine resolves; modal closes; toast confirms. |
| C-06 | **Major** | Skills tab missing proficiency markers | `screen-character.jsx:628-648` | epic:per-page-polish | Each skill row shows `s.name` + `s.mod` — add a small filled dot ⬤ for `proficient` + ⬤⬤ for `expertise`. Surface `s.proficient` / `s.expertise` from the read model. Mirror DNDBeyond and BG3 inspect. |
| C-07 | **Minor** | XP bar has no current/next value | `screen-character.jsx:134-142` | epic:per-page-polish | The pills above already show "XP {hero.xp.toLocaleString()} / {hero.xpMax.toLocaleString()}" (line 129) — the bar is decorative. Add the next-level milestone as a tooltip on the bar, or remove the bar if the pill is sufficient. |
| C-08 | **Minor** | Equipped grid has no empty-state when `hero.equipped` is empty | `screen-character.jsx:203-221` | epic:per-page-polish | Show "No gear equipped — visit a merchant or open the stash" with a quick-nav button. Today it renders nothing. |
| C-09 | **Minor** | Roster rail buttons in left column lose contrast on Caelar's silhouette portrait | `screen-character.jsx:79` | epic:per-page-polish + accessibility | Audit the contrast of the inset gradient background + silhouette over the active tab; consider a darker active-tab background. |
| C-10 | **Minor** | Damage Reduction section shows two StatLines with no explanation | `screen-character.jsx:258-261` | epic:per-page-polish | Add an `eyebrow` "from armor/feats" subline; the StatLine just says "Value" + "Energy" with numbers — non-obvious to a 5e player (5e doesn't have DR per se). Likely an artifact of an earlier system; consider replacing with "Resistances/Immunities" — 5e canon. |
| C-11 | **Trivial** | "Lay on Hands" + "Channel Divinity" come from `hero.classResources` — verify the live shape | `screen-character.jsx:523-562` | epic:wire-prototypes | Cross-check that engine's `/character-surface` emits `classResources[{id,name,max,remaining\|used}]` per `ResourcesStatus` shape. |

## Missing features (deferred to backlog)

- **Subclass / Archetype picker on level-up** — Caelar shows "Oath of the Crown" in the header subtitle; no level-up flow surfaces this in the UI.
- **Multiclass tab strip** — only single class supported in display.
- **Inspect tooltip for skills** — hover on Arcana → "1d20 + INT mod + prof; used to recall lore about runes / planes / etc."
- **Item compare-on-hover** in equipped strip (current vs hover from inventory).
- **Death-save markers** are pills (line 545) — no animated state on near-death.

## Asset gaps (wiki-first inventory)

- **Item icons** for canonical 5e gear in `_private/baldurs-gate/items/<slug>.png`: longsword, mace, dagger, shortbow, longbow, hand crossbow, light/medium/heavy armor (leather/studded/scale/chain/plate), shield, helmet, cloak, ring, amulet, potion-of-healing, … reuse via inventory's `itemScope`.
- **Ability/Feat glyphs** in the icon registry (#174 already tracks): Lay on Hands, Channel Divinity, Sneak Attack, Bardic Inspiration, Sorcerer Metamagic, Pact Boon, etc.
- **Class portraits** are NOT actually needed here — header uses portrait-<id>, not class crest (per `clawdnd-canonical-setup` invariant).

## Recommended next pass

1. **C-02 (browse spellbook)** is the most-felt gap — a Paladin player tabs to Spellbook and sees a wall of empty.
2. **C-03 + C-04 (Img not Placeholder for items + abilities)** is a sweeping mechanical change with high visible payoff once item art is ingested.
3. **C-05 (wire Rest & Prepare to /move)** — design-led; depends on engine route.
