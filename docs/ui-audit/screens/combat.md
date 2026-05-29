# Battle (Combat) — 66/100 — Polish-Pass

**Route:** `/openworlds/#combat` (alias `#battle`)
**Source:** `viewer/openworlds/screen-combat.jsx` (674 LOC)
**Screenshot:** `docs/ui-audit/screenshots/combat-1512.png`
**Compared to:** BG3 tactical map + initiative ribbon, Pathfinder: Kingmaker action bar (P4 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Tactical grid + token portraits (PC has art, foes are red blobs) + initiative + action economy + command center + battle log. 7-action tile bar. All read-only here ('No active actor' / 'Read-only'). The audit doc's '5/10' is stale; portraits + action economy + targetability are wired."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **7/10** | 70 | Tactical map dark-gradient + grid + token glow + zone chips read like a tactical UI. Tokens have portrait art via `<Img>` (line 517-524) ✓. Foe tokens are red radials w/o portrait (no creature art piped). |
| C2 | Information Density | **8/10** | 80 | 1fr/280px split + 7-button action tile bar + Initiative + Command + Battle Log is dense and CRPG-typical. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P4 patterns: initiative tracker w/ HP bar (line 549-594), action economy badges (line 273-275), multiattack budget ("X/Y attacks left" line 294-297), targetability list. Missing: condition icons on tokens, AOE/range preview, attack-of-opportunity. |
| C4 | Interaction Affordance | **8/10** | 120 | Action tiles gate on `action.available && action.move && canAct && !busyAction` (line 153) — proper. `disabled_reason` shown in hint (line 152). Refresh + Back-to-Table buttons in header. |
| C5 | Content Completeness | **5/10** | 75 | Foe creature names rendered ✓ but no creature portraits/sprites (Goblin Warriors are red blobs). Battle log shows good events but no damage-type icons. CombatEmptyState (line 384-403) good. |
| C6 | Accessibility | **6/10** | 60 | Token buttons have no aria-label (line 491-501). Initiative rows have no role="button" semantic. SlotPip strikethrough on used (line 361) is text-only signal. Glow animations not gated on reduced-motion. |
| C7 | Empty-State Handling | **8/10** | 40 | `CombatEmptyState` is clean — "No active initiative" + scrim + back-to-table CTA (line 384-403). |
| C8 | Wiki-First Asset Fidelity | **5/10** | 50 | PC token wired ✓; **foe tokens use a red radial gradient (line 504-509) instead of `<Img scope="portrait-<id>"/>` — should mirror PC token (line 517-524)**. Battle backdrop is `<Placeholder label={terrain}>` (line 421) — no battle scene art for the location. |
| C9 | Responsive / Layout | **7/10** | 35 | At 1512 the 1fr/280px split is fine. Below 1280 the right column would crowd. 16×10 grid (line 406) is hard-coded. |
| C10 | Performance Perception | **7/10** | 35 | 5s poll (line 56); SVG grid pattern is cheap. Force-directed sim not used here (static tokens). No 404 storms. |

**Total: 685/1000 = 68/100 → Polish-Pass** _(rounded to 66)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| B-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| B-02 | **Critical** | Foe tokens use red radial only — `<Img scope="portrait-<id>">` not wired for foes | `screen-combat.jsx:485-547` | epic:portraits | The PC token (line 517) already renders an Img on top of the radial; foes should too. When `t.team === "foe"`, attempt `<Img scope={"portrait-"+t.id} fit="cover">` over the red radial. Mirrors PC pattern. Creature art via wiki ingest pipeline already keyed `creature-<slug>` — verify a foe token's `t.id` matches what the engine emits OR add a `creatureSlug` alias map. |
| B-03 | **Critical** | Tactical battlefield uses `<Placeholder>` for terrain — no battle scene art | `screen-combat.jsx:421` | epic:per-scene-art | Replace `<Placeholder label={terrain}>` with `<Img scope={"location:"+encounter.location_id} fit="cover" style={{opacity:0.55}}>` so the player fights "on" the current location's scene (Lower City street → market plaza scene art tinted darker). |
| B-04 | **Major** | No condition icons on tokens | `screen-combat.jsx:526-545`, command cues line 254-281 | epic:per-page-polish | Each token should render a 2-3-icon strip below the HP bar for the top conditions on that combatant (poisoned/frightened/prone/blinded etc. — 14 SRD conditions). Surface from `t.conditions` on the combat-surface. Mirror the `CueChip` styling. |
| B-05 | **Major** | No range/AOE preview when hovering an attack/spell action | `screen-combat.jsx:144-158` | epic:per-page-polish | Hovering "Attack" or "Cast" should show the action's range (e.g., 5ft melee, 30ft cone) as a translucent overlay on the grid. Needs `action.range_ft` / `action.aoe_kind` from the engine. |
| B-06 | **Major** | Battle log entries are text-only — no damage-type icons | `screen-combat.jsx:636-661` | epic:per-page-polish | Each `meta` row already has chips; add a small fire/cold/poison/radiant icon when the engine emits `meta.damage_type`. Mirror BG3 log conventions. |
| B-07 | **Major** | Initiative row HP bar uses `healthRatio` which fakes 0.38 for "bloodied" when `hpKnown=false` | `screen-combat.jsx:475-483, 549-594` | epic:per-page-polish | When `t.hpKnown=false`, the bar should NOT be filled at a precise ratio (the engine doesn't know either). Render a striped pattern OR an empty bar with a "?" — never a precise-looking value the player will misread. |
| B-08 | **Minor** | "End turn" tile missing keyboard shortcut hint | `screen-combat.jsx:200, app.jsx:198` | epic:per-page-polish + keyboard | The global key map (app.jsx) doesn't bind end-turn. Add a key (e.g., `Enter` outside an input) and surface it on the tile (`<kbd>↵</kbd>` in the hint). |
| B-09 | **Minor** | CommandCenter "No active actor" reads ambiguously when between rounds | `screen-combat.jsx:299-309` | epic:per-page-polish | When `actor.name` is empty AND `initiative.length > 0`, say "Between rounds — top of order is X" rather than "No active actor". |
| B-10 | **Minor** | `CombatToken` button has no focus-visible style | `screen-combat.jsx:491-501` | epic:per-page-polish + accessibility | Add `:focus-visible` outline so keyboard players can tab through tokens. Today only the inset shadow on `selected` shows. |
| B-11 | **Trivial** | Grid is 16×10 hard-coded; engine might emit smaller arena | `screen-combat.jsx:406` | epic:per-page-polish | Read `surface.grid.cols` / `surface.grid.rows` if present; fall back to 16×10. |

## Missing features (deferred to backlog)

- **Movement preview** — drag a token to see the remaining movement bar before commit.
- **AOE template painter** — sphere/cone/line painted on grid for spell range.
- **Attack-of-opportunity indicator** — pulsing ring when a foe is in AOO range.
- **Multi-target select** — for Cleave / Fireball.
- **Quick-spell hotbar** — usually 1-9 keys; rich CRPG UX.
- **End-of-combat summary** — XP/loot/conditions-cleared overlay.

## Asset gaps (wiki-first inventory)

- **Creature portraits** for SRD bestiary + BG3 canon foes — `creature-<slug>` scope keyed off the bestiary slug map. Goblin Warrior, Bugbear Chieftain, Mind Flayer, Aboleth — see `screen-bestiary.jsx:9-19` for the slug-alias contract.
- **Per-location battle scene art** — same scope set as `screen-table.jsx` uses for non-combat scenes.
- **14 SRD condition icons** for B-04 — already partly in the icon registry (#174).

## Recommended next pass

1. **B-02 (foe portraits)** is the most-felt visual gap — foes as red blobs reads as 90s prototype.
2. **B-03 (battle scene backdrop)** lifts the tactical map from "abstract grid" to "BG3-feeling combat".
3. **B-04 (condition icons on tokens)** + **B-06 (damage-type icons in log)** are paired polish wins.
