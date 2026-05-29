# RPG UI Reference Patterns — Pathfinder / Kingmaker / BG3 / DNDBeyond

> Text-only reference for the OpenWorlds audit. **Do NOT commit any competitor
> screenshots** — see `docs/OPENWORLDS_DESIGN_ASSET_POLICY.md`. Cite patterns by
> description, not image.

The audit scores OpenWorlds against the **patterns** these games converged on,
not against any specific frame of art. A player who knows BG3 or Kingmaker
expects each screen to converge on these patterns; the rubric C3 score reflects
how close OpenWorlds is to the genre's literacy floor.

## P1 — The Map / Atlas (Pathfinder: Kingmaker + BG3 World Map)

| Pattern | Why it matters | OpenWorlds analog |
|---|---|---|
| **Spatial map, anchored** — locations sit at fixed coordinates on a region backdrop, edges are real travel routes. | Players read distance + adjacency at a glance; "node list over a city photo" reads as broken. | `screen-map.jsx` lines 268–361 (force-directed layout) + line 480-484 (region backdrop) |
| **Time + weather indicator** — clock-driven; sky tint shifts dawn / day / dusk / night. | The world feels alive without input. Manual toggle reads as a setting. | `screen-map.jsx` line 117-118 (`atlasTimePhase` clock-driven) ✓ |
| **Encounter / quest pins** — pin glyphs (sword for hostile, scroll for quest, anvil for downtime) overlay the node. | Catalogs strategic context. | `screen-map.jsx` line 746-747 (settlement/camp glyph) — extend with quest pins |
| **Fog-of-war / "discovered"** — unvisited nodes show as silhouettes or `?`. | Conveys exploration progression. | Partial via `loc.visited` (line 710) — not fully used |
| **Travel cost panel** — selecting a destination shows minutes / risk / rations. | Forces real travel choices. | `screen-map.jsx` line 598-599 ("minutes away") ✓ |
| **Make Camp / Long Rest** — prominent CTA, only available where safe. | Rest cadence is core to CRPG pacing. | line 192-200 prominent + gated on `canCamp` ✓ |
| **Region label + scale + compass** — tells the player WHERE they are without zooming. | Spatial orientation. | line 514-519 (compass rose) + line 520 (atlas label) ✓ |

## P2 — The Character Sheet (DNDBeyond + BG3 inspect)

| Pattern | OpenWorlds analog |
|---|---|
| **Portrait + name + race/class/level + alignment** header card. | `screen-character.jsx` line 115-130 ✓ |
| **6 ability scores with mod + save** — STR/DEX/CON/INT/WIS/CHA grid. | line 159-161 (scores) + line 195-199 (saves) ✓ |
| **AC, HP/HPmax, Speed, Initiative, Prof Bonus** — top-line stats. | line 126-130 (pills) + line 175-178 ✓ |
| **Spell slots + prepared spells + cantrips** — gridded slot tracker. | `SpellSlotTrack` line 696-726 ✓ — but `RestPrepareModal` rest+prepare is display-only (line 296-304) |
| **Class resources** — Lay on Hands pool, Channel Divinity uses, Rages. | `ResourcesStatus` line 520-562 ✓ |
| **Skills with proficiency markers** — black diamond = proficient, double = expertise. | `SkillsTab` line 628-648 — shows mod, NO proficiency dot/marker (BG3 norm) |
| **Equipped gear strip** — slot icons (head, chest, hands, ring, boots). | line 203-221 — uses `Placeholder label={it.glyph}` NOT IconPlate/Img → item icons missing |
| **Death saves + concentration** badges. | line 535-549 ✓ |
| **Inspect tooltip with full rules text** on hover. | `AbilityCard` line 587-605 has Tooltip ✓ but Feats line 607 + spells only show one-line `detail` |
| **Multiclass tabs** when class layers stack. | Not yet supported. |

## P3 — The Inventory (BG3 + Kingmaker)

| Pattern | OpenWorlds analog |
|---|---|
| **Equipment-doll** — paper-doll silhouette with slot drop-zones around it. | `screen-inventory.jsx` line 145-164 uses a flat 6-slot grid below the portrait, NOT a doll. Equipment slots only cover Head/Neck/Body/Hands/Ring/Boots (line 299-302) — BG3 has Mainhand/Offhand/Ranged/Cloak/Amulet/2 Rings/Underwear too |
| **Item tile grid** with type-coloured borders + qty in corner. | `ItemSlot` line 322-364 ✓ — type-coloured borders ✓ qty corner ✓ |
| **Filter chips** (All / Arms / Armor / Spell / Quest / Rare / Sundries). | line 192-210 ✓ |
| **Item detail pane** with description, weight, value, properties, lore. | `ItemDetail` line 366-441 ✓ |
| **Encumbrance / Weight bar** — intentionally removed (line 168-171) for honesty ✓ |
| **Coin purse with PP / GP / SP / EP / CP**. | line 174-183 ✓ |
| **Per-hero pack** (each character carries their own). | line 130-142 hero switcher ✓ |
| **Context menu** (right-click → Equip / Use / Hand to companion / Drop). | line 272-294 ✓ great |
| **Compare-on-hover** — old item vs hover item delta. | NOT IMPLEMENTED |
| **Drag-to-equip / drag-between-heroes**. | NOT IMPLEMENTED — context menu only |

## P4 — Combat / Initiative (Pathfinder + BG3)

| Pattern | OpenWorlds analog |
|---|---|
| **Initiative tracker** — vertical strip, current actor highlighted, foe portraits red-tinted, HP bar per row. | `screen-combat.jsx` `InitiativeRow` line 549-594 ✓ portrait wired (line 571) ✓ |
| **Tactical map with movement / cone / line targeting** — grid + range overlays. | `CombatMap` line 405-473 has a 16×10 grid with tokens but **NO movement preview, NO range/aoe overlay, NO attack-of-opportunity indicators** |
| **Action economy badges** — Action / Bonus / Reaction / Movement clearly visible per token. | `CombatantSummary` line 273-275 (`ApBadge`) + `CommandCenterPanel` line 303-308 ✓ |
| **Multiattack indicator** — "1/2 attacks left" on a multiattacker. | line 294-297 attack budget ✓ |
| **Conditions on tokens** — small icon ring around the portrait. | NOT IMPLEMENTED — cues live in `CueChip` (line 366-382) but not overlaid on token |
| **Damage type icons in log** — sword/fire/poison glyph in each damage line. | `BattleLogLine` line 636-661 — text only, no icons |
| **Crit / miss visual** — golden flash on crit, grey miss; persistent in log. | NOT IMPLEMENTED visually; log is plain text |
| **Spell preview before cast** — area circle + targeted enemies list. | NOT IMPLEMENTED — Cast action just sends |

## P5 — Dialogue / Parley (BG3 + Pathfinder)

| Pattern | OpenWorlds analog |
|---|---|
| **Portrait + name + alignment** of the speaker. | `screen-dialogue.jsx` `ParleyMenu` line 192-198 ✓ |
| **Numbered branch options** with a DC + skill name + modifier shown inline. | line 209-246 ✓ (e.g. "1. Athletics +3 DC 14") |
| **Free-form / "say what you want"** option. | line 248-259 ✓ |
| **Alignment / persuasion tags** ("[INTIMIDATION]", "[INSIGHT]") prefixing options. | label only — no clear tag |
| **Approach history side panel** — "you tried Athletics earlier, failed". | line 266-288 ✓ |
| **Scene art behind the panel** — anchors the conversation. | line 132 ✓ |
| **Difficulty / Stakes telegraph** — "Hard" / "Hostile crowd" — colored. | line 169-176 — easy/medium/hard buttons with tooltip ✓ but tooltip-only DC explanation could be louder |

## P6 — Quest Journal (Kingmaker + BG3)

| Pattern | OpenWorlds analog |
|---|---|
| **Active / Past / Rumors tabs**. | `screen-journal.jsx` line 81-92 ✓ |
| **Per-quest objective checklist** with strikethrough on done. | line 246-269 ✓ |
| **Quest log entries** — date-stamped, narrative format. | line 213-218 ✓ |
| **Show on map** button. | line 354 ✓ |
| **Named NPCs in this quest** with portrait + role. | line 275-292 — but **uses `<Placeholder>` not Img!** (line 283) — portraits never render |
| **Reward preview** — XP + gp + items. | line 343-350 — gated on `quest.reward` ✓ |
| **Director debt / "campaign owes" advisory** — unique to OpenWorlds, great touch. | line 127-148 ✓ |

## P7 — Bestiary / Codex (Pathfinder + DNDBeyond Monster Manual)

| Pattern | OpenWorlds analog |
|---|---|
| **Searchable index** with creature plate thumbnail + name + CR. | `screen-bestiary.jsx` line 156-176 ✓ |
| **Stat block** — Size · Type · Alignment header, then AC / HP / Speed / 6 abilities / Saves / Senses / CR / Resistances / Immunities. | line 243-267 — **MOST FIELDS EMPTY** (HD/AC/Speed/Senses/Save not projected by surface, line 26-49 — they're set to `""` in `liveBestiaryEntry`) |
| **Known actions** as tags. | line 277-285 ✓ |
| **Tactics / Lore** prose section. | line 269-273 (body) + line 298-304 (tactics) ✓ but body/tactics may not be populated either |
| **Encountered-at** location reference. | StatLine "Encountered" line 265 — empty |
| **Spoils / Loot pool**. | line 306-314 — empty in live surface |

## P8 — Faction / Relations (Pathfinder: Kingmaker Curtailments / BG3 approval)

| Pattern | OpenWorlds analog |
|---|---|
| **Faction list with banner color + reputation bar**. | `screen-relations.jsx` line 79-96 ✓ + `RepBar` line 210-229 ✓ |
| **Standing thresholds named** (Hostile / Cool / Civil / Cordial / Welcome). | line 219-220 ✓ |
| **NPC roster with portrait + role + disposition dot**. | line 119-139 ✓ |
| **Companion approval gauge** — separate from faction rep. | line 408-419 ✓ |
| **Banter / Relationships / Ties** lists. | line 424-437 ✓ |
| **Betrayal warning telegraph** — when bond fractures. | line 421-423 ✓ (great pattern) |
| **Companion arcs** — per-companion personal quest list with status. | line 152-165 + `CompanionArcCard` line 170-208 ✓ |
| **Last spoken quote**. | line 465-475 ✓ |

## P9 — Crafting / Forge (Pathfinder + BG3 alchemy)

| Pattern | OpenWorlds analog |
|---|---|
| **Recipe browser** with category tabs. | `screen-forge.jsx` line 132-147 ✓ |
| **Recipe card** with components + DC + time. | line 184-199 ✓ |
| **Component availability** (have vs need, green/red). | `ComponentSlot` line 325-344 ✓ |
| **Crafter picker** — who at the bench? | line 235-256 ✓ but uses `<Placeholder>` not `Img` for portraits (line 247) |
| **Success probability gauge**. | line 268-285 ✓ |
| **Workshop ledger** — past craft attempts. | line 297-318 — uses **hardcoded log** (line 30-34) not engine-projected |
| **Material breakdown on failure**. | NOT IMPLEMENTED — "materials are not lost" message only |

## P10 — Merchant / Shop (BG3 + Kingmaker)

| Pattern | OpenWorlds analog |
|---|---|
| **Merchant portrait + name + greeting**. | `screen-merchant.jsx` line 65-77 ✓ |
| **Reputation/disposition gauge**. | line 80-89 ✓ |
| **Buy / Sell tabs**. | line 124-138 ✓ |
| **Two-pane table or grid: merchant stock + your stash**. | line 146-218 (single table view, switching tabs) — not split |
| **Per-item icon + name + qty + weight + price**. | line 158-204 ✓ |
| **Cart / "the counter"** for staged transactions. | line 238-264 ✓ |
| **Coin purse on screen**. | line 230-234 ✓ |
| **Haggle / reputation effect on price**. | line 91-114 ✓ + applied at line 54 ✓ |
| **Compare-on-hover** — old item vs hover item delta. | NOT IMPLEMENTED |
| **Restock countdown**. | NOT IMPLEMENTED |
| **Multiple merchants per region** with distinct stock. | NOT IMPLEMENTED — line 338-368 single hardcoded Talli |

## P11 — Camp / Long Rest (BG3 + Pathfinder)

| Pattern | OpenWorlds analog |
|---|---|
| **Timeline ribbon** — dusk → dawn with cursor. | `camp-sidebar.jsx` `TimelineBar` line 301-348 ✓ great pattern |
| **Role slots** — Hunting / Cooking / Camouflage / Watch1 / Watch2. | `RoleSlot` line 372-428 ✓ + `WatchSlot` line 430-478 ✓ |
| **Drag-to-assign** companions. | line 244-245 ✓ |
| **Recipe / meal picker** with stat bonus preview. | line 172-184 ✓ |
| **Rations needed vs in-pack** balance. | line 105-113 ✓ |
| **Talk to companion / "wants to say something"** affordance. | line 261-280 ✓ but `TALK_PROMPTS` is only `_default` line 595-603 — no per-companion content |
| **Begin Resting CTA**. | line 293-295 — **disabled "Display-only"** (preview) — not wired |

## P12 — Character Creation (BG3 + Pathfinder)

| Pattern | OpenWorlds analog |
|---|---|
| **Stepped wizard** — Race → Class → Background → Abilities → Portrait → Name. | `screen-create.jsx` line 19-27 ✓ 7-step |
| **Race card grid** with portrait + bonus + size. | `StepRace` line 221-247 ✓ — but `Placeholder` not `Img` (line 555) → no race portraits |
| **Class card grid** with role + HP/hit-die + tags. | `StepClass` line 249-275 ✓ — but `Placeholder` not `Img` → no class portraits |
| **Subclass / archetype picker** at level 3 or 1 (paladin oath). | NOT IMPLEMENTED — single class only |
| **Background skill bonuses**. | line 304 ✓ |
| **Alignment grid** — 3×3 LG/NG/CG…CE. | line 313-330 ✓ |
| **Point buy with ability cost table** (8=0, 14=7, 15=9). | `abilityCost` line 572-576 ✓ standard 5e |
| **Racial bonuses shown live**. | line 359-388 ✓ |
| **Portrait gallery** with "bring your own". | line 416-428 — 12 placeholders, no actual portraits |
| **Final review + bind**. | `StepReview` line 477-538 ✓ |
| **Starting gear list**. | line 521-528 ✓ |

## P13 — Worlds / Launcher / Save Slots (BG3 main menu, Kingmaker chapter screen)

| Pattern | OpenWorlds analog |
|---|---|
| **Hero masthead art** with title plate. | `screen-launcher.jsx` line 79-101 ✓ |
| **Campaign list / chronicles** with thumb + last-played + chapter. | `CampaignRow` line 349-386 — **uses `<Placeholder label="seal">` not real campaign art** (line 371) |
| **Selected campaign detail** — party portraits + recap + region. | line 187-291 ✓ |
| **Resume / New / Delete** CTAs. | line 277-281 ✓ |
| **Per-save thumbnail** (BG3 saves the last scene as PNG). | NOT IMPLEMENTED — same masthead used everywhere |
| **New chronicle modal** — system + tone + difficulty + GM strictness. | `NewCampaignModal` line 398-461 ✓ but the AI GM SegRadio has no onChange (line 450) — non-functional |

## P14 — Acts / Chapter Progression (Pathfinder: Kingmaker, BG3 Acts I/II/III)

| Pattern | OpenWorlds analog |
|---|---|
| **Vertical timeline / spine** with wax-seal nodes. | `screen-acts.jsx` `ActSpineRow` line 103-161 ✓ great |
| **Current act marked + "You are here" pill**. | line 147-151 ✓ |
| **Per-act key choices recap**. | line 212-239 ✓ |
| **Memorable beats / callbacks**. | line 243-261 ✓ |
| **Who walked this act** — party-at-start. | line 263-275 — uses `<Placeholder>` not Img → no portraits |

## P15 — Settings (BG3 + Pathfinder)

| Pattern | OpenWorlds analog |
|---|---|
| **Sections: Sound / Display / Gameplay / Controls / Accessibility / Saves / About**. | line 33-42 ✓ |
| **Live sliders** for master/music/sfx/ambience/voice. | line 87-91 — **all preview/non-functional** ✓ honest |
| **Reduce motion + high contrast + UI scale** functional. | line 183-184 ✓ + `OpenWorldsA11y` bridge |
| **Keybind list** with conflict detection. | `KEYBINDS` line 580-593 ✓ static list, no rebind |
| **Save slot list** with screenshots. | `SaveSlot` line 618-644 — uses `Placeholder` for thumbs |
| **Native bridge / app status** panel. | `NativeAppSection` line 271-366 ✓ unique to OpenWorlds, well-presented |

## P16 — World Seed / Game Setup (unique to OpenWorlds)

This screen has no direct AAA-CRPG analog. It's closer to **NaNoWriMo project settings**
or **the "Storyteller's Codex"** in indie story games (Citizen Sleeper, Wildermyth).
The current implementation is **entirely display-only** (line 194 disabled, line 4-15
state never persisted) — it should either wire to a `/seed-surface` POST lane or be
honestly retired in favor of a single "world seed" badge on the campaign card.

---

## Cross-cutting conventions OpenWorlds already does well

- **Honest empty states** + "(preview)" tags everywhere a button isn't engine-wired. This
  is BETTER than BG3/Kingmaker (which have a few dead UI elements). Keep this discipline.
- **Parchment + brass + corner filigree** chrome — pure OpenWorlds, no AAA-CRPG analog.
  Distinctive and on-brief for the "scribe's codex" framing.
- **Toast system** (`window.useToast`) — consistent across screens.
- **Tooltip** wrapper with `<InfoTooltip>` content cards — good pattern.
- **`Img` component** with `/image?scope=<id>` fetch + graceful Placeholder fallback —
  the single bridge between the wiki-ingest pipeline and the UI (chrome.jsx line 237-258).
  ★ Every screen that uses `<Placeholder>` directly for a person/thing that COULD have
  art should instead use `<Img>` with the right scope.

## Cross-cutting conventions OpenWorlds is missing

- **Inspect-on-hover for items/spells/feats** is partial; many tiles fall back to no
  tooltip (e.g. `screen-character.jsx` Equipped slot line 203-221 — no tooltip).
- **Sortable / filterable tables** (Inventory grid, Bestiary index, Merchant table) —
  filter chips exist on Inventory ✓ + Bestiary search ✓; Merchant has a dead "Filter…" button.
- **Keyboard discoverability** — shortcuts are listed in Settings (line 580-593) and the
  keyboard handler exists (app.jsx line 186-224), but no on-screen hint chip per page.
- **Loading skeletons** — `setSurfaceStatus("loading")` is rarely used for a visible state;
  the screens just go from empty → populated. A skeleton row would convey "waiting" vs
  "no data."
- **Iconography registry** — `OpenWorldsIcon` exists (line 174 issue tracks this) but
  many screens still use one-char glyphs or `<Placeholder label="…">` instead.
