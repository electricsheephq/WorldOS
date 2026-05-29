# OpenWorlds App — Page-by-Page UI/UX Audit (2026-05-27)

**Method:** live walkthrough of every screen via the preview server (`127.0.0.1:8799/openworlds/`).
**This is the same UI the desktop app ("WorldOS") shows** — the app launches `viewer/server.py` and loads `/openworlds/`. (`/dashboard` is the *old* `dashboard.html`, the `play.sh` surface — not this UI.) So fixes here land in the app on rebuild/restart.
**Scope of this doc:** audit + scored backlog ONLY. No product code changed. We prioritize/sequence together before fixing.

> Honest framing: the engine, live read-model binding, and a few screens (Relations, Parley) are genuinely good. But the owner's read is correct — the app is **unfinished, unmapped, and disconnected**. The three roots: (1) **you can't actually play in it** (read-only viewer, dead action buttons); (2) **no real map** (one city photo repeated everywhere); (3) **portraits/art missing almost everywhere**. Overall app readiness ≈ **4.5/10**.

---

## Scored backlog (worst → best)

| Page | Score /10 | Headline gaps |
|---|---|---|
| Map / Atlas | 3 | Not a real map — same city photo, node list; manual day/night; "Make Camp" buried; unwired read-only icons |
| World Seed | 3 | Display-only (not saved); **demo leak: "Linzi (chronicler)"**; tone/difficulty/system selectors inert |
| Creation Plane | 3 | Display-only — **can't actually create/save a character**; no race portraits; no PC portrait |
| Forge | 3 | Display-only prototype; **demo leak: Linzi/Vell/Cassian/Mira** in notes + ledger; placeholder art |
| Session / Table | 4 | **Recap text overlaid on image = unreadable**; **no Caelar portrait**; action buttons dead ("no live move sink") |
| Merchant / Market | 4 | No merchant portrait, no item images; display-only (purchases not wired) |
| Camp (sidebar) | 4 | Wired to live party now, but "Make Camp" is preview-disabled + hard to reach |
| Heroes / Character | 5 | No PC portrait; **spellbook doesn't function** ("No spells prepared", no browse); else solid 5e sheet |
| Stash / Inventory | 5 | No item images on most gear; no PC portrait; coins/values now correct |
| Battle / Combat | 5 | **No portraits** (PC token + initiative tracker are placeholders); **HP display wonky**; read-only; good bones (zones/initiative/log) |
| Codex / Bestiary | 5 | Creature **images are placeholders**; **stat fields (HD/AC/Speed/Senses/Save) empty**; "The Marches" naming; Persons/Lore demo |
| Launcher / Chronicles | 6 | Masthead scene art now renders (fixed); but PC portraits placeholder; "Worlds" repeats the one BG image |
| Journal / Quests | 6 | Clean + live + honest empty-states; quests carry no art/region |
| Acts | 6 | Honest "not tracked yet" empty-state; mostly empty until the Campaign Director compiles |
| Settings | 6 | Native-bridge control panel (correct; live only in the real app); secondary tabs (Sound/Display/…) need an audit pass |
| Parley | 7 | Owner loved it. Minus: no portrait; the Easy/Med/Hard toggle reads like a setting (doesn't belong here); "Read-only" is unexplained |
| Relations | 7 | Best-realized screen — live factions + NPCs with **real portraits rendering** + dossier |

---

## Cross-cutting EPICS (the roots — fix these and many per-page items fall out)

### EPIC A — Portraits & character art everywhere (highest visible impact)
PC/companion portraits are missing on Session, Party, Battle, the initiative tracker, and Parley (Caelar is a blank placeholder everywhere). Race portraits absent on Creation. Merchant/NPC portraits, Bestiary creature images, and item images all placeholder. **Canon NPCs already render** (Relations proves the pipeline works) — the gap is **generated/original characters** (PCs, homebrew). Decision needed: gateway-generated portraits vs. default class/race art vs. both. Then wire the `portrait-<id>` scope through every party/initiative/token slot.

### EPIC B — A real, anchored map (Atlas)
Today the map is a node list with the same city photo behind it — not a spatial map. Build an actual region map the locations anchor to (the location graph + connections already exist in the engine). Sub-items: day/dawn/dusk/night should be **system/clock-driven**, not a manual user toggle; make **"Make Camp"** prominent; remove or wire the read-only travel/action icons.

### EPIC C — Make OpenWorlds playable (the "disconnected" root)
The Session/Battle/Parley action bars are dead — "Read-only: no live move sink", DECLARE/SAY/DO/CHECK disabled. OpenWorlds is a *read-only viewer*; play still happens in the old dashboard via `POST /move`. For a finished app, wire OpenWorlds' action surface to the `/move` lane so you can **play in the app itself**. This is the deepest item — it's why the app feels disconnected.

### EPIC D — Per-scene art (stop reusing one image)
The same Baldur's Gate cityscape repeats on Launcher, Session, Atlas, Parley, and Worlds. Several per-location scenes already exist in `_private` (lower-city, upper-city, outer-city, elturel, reithwin, candlekeep, …) — wire each screen to the **current location's** scene, and fall back gracefully, so screens stop looking identical. Plus item icons (Stash/Market) and creature images (Bestiary).

### EPIC E — Finish the demo-leak removal (round 2)
Wave-3 emptied `data.js`, but the **display-only prototype screens carry their own local Pathfinder demo content**: World Seed ("Linzi (chronicler)") and Forge (Linzi/Vell/Cassian/Mira in the notes + workshop ledger). Re-theme or gate these to BG/empty.

### EPIC F — Wire (or honestly retire) the prototype screens
Creation Plane (can't save a character), Forge (crafting), Merchant (transactions), World Seed (params) are all "Display-only … not saved." Decide per screen: wire to the engine for 1.0, or keep an honest prototype and lower its prominence. Creation Plane is the most important (no way to make a character in the app).

### EPIC G — Per-page UI/UX polish
Session: move recap off the image / add a scrim so it's readable. Battle: fix the wonky HP display + add portraits to combatant cards and the initiative tracker. Parley: drop the Easy/Med/Hard toggle (or move to settings), explain or remove "Read-only", add the actor portrait. Bestiary: populate the empty stat fields. Character: a real, browsable spellbook. Buttons throughout: wire to a real action or hide (honesty) — the travel icon is the canonical example.

---

## Per-page detail

### Map / Atlas — 3/10
Live location graph (real BG nodes: Baldur's Gate, Candlekeep, Elfsong, Steel Watch Foundry, Undercity…) + current-location scene + prose render. **But:** the "map" is a node list over a single repeated city photo — not a spatial/anchored map (EPIC B). Day/Dawn/Dusk/Night is a manual toggle (should be clock-driven). "Make Camp" is easy to miss. "Travel Here" is greyed; several icons are read-only/unwired. "Read-only" badge.

### World Seed — 3/10
Display-only ("seed parameters are not wired … Changes will not be saved"). System (D&D 5e) / Tone (Heroic/Grim/Picaresque/Mythic) / Difficulty selectors are inert. **Demo leak:** "Seeded … by **Linzi (chronicler)**", plus hardcoded date/pattern/engine values (EPIC E).

### Creation Plane — 3/10
A beautiful 7-step wizard (Lineage→Calling→Past→Aptitudes→Face→Name→Bind). **But display-only — "No character will be saved"** (EPIC F): you cannot actually create a playable character in the app. Race cards (Human/Halfling) show "sketch" placeholders (no race portraits, EPIC A); the result panel shows "portrait · 0".

### Forge — 3/10
Display-only crafting prototype (Smithing/Alchemy/Scribing/Enchanting; recipes, components, DCs). Placeholder art. **Demo leak:** chronicle note "…— Linzi, scribe" and Workshop Ledger entries "Cassian · made / Mira · failed" (EPIC E). The party panel was gated to live in wave-3, but these local constants weren't.

### Session / Table — 4/10
The primary play surface. Live: party card (Caelar 37/44), conditions, the Tabletop Chronicle combat log, active quests, quick stash, encounter panel. **Critical UX bug: the recap prose is overlaid on the scene image in dark text → unreadable** (EPIC G). **No Caelar portrait** (EPIC A). Action bar dead — "Read-only: no live move sink", DECLARE/CONTINUE/SAY/DO/CHECK disabled (EPIC C). Scene reuses the one BG image (EPIC D).

### Merchant / Market — 4/10
Re-themed to BG (Quartermaster Dell, Heapside) — no PF leak now. Honest "Display-only" badge. **But:** merchant portrait is a placeholder; no item images; purchases not wired (EPIC F). Wares are system-neutral.

### Camp (sidebar) — 4/10
Wired to the live party in wave-3 (no longer the demo party). **But** "Make Camp" is preview-disabled and hard to reach (EPIC B sub-item); roles/watch are drag-placeholder.

### Heroes / Character — 5/10
Solid live 5e sheet — abilities, 5e saving throws, class resources (Lay on Hands, Channel Divinity), feats, AC/HP/speed. **But:** no PC portrait (EPIC A); **the Spellbook doesn't function** — it shows "No spells prepared" with no way to browse/inspect spells (EPIC G). Skills are fine (owner agreed).

### Stash / Inventory — 5/10
Live items, correct coins/values, honest preview labels on Give/Drop. **But:** most gear shows generic tiles (item images only for a subset); no PC portrait (EPIC A/D).

### Battle / Combat — 5/10
Genuinely good bones: live combat (Caelar vs 2 Goblin Warriors), zone tactical field, initiative order, action economy ("1/2 attacks left", "bloodied/steady"), battle log. **But:** **no portraits** — PC token and the initiative tracker are placeholders (EPIC A); **HP display is wonky** UX-wise (EPIC G); read-only (EPIC C).

### Codex / Bestiary — 5/10
Creatures are **live** (Aboleth CR 10 etc. with abilities + CR; "20 known · 2 rumoured"). **But:** creature images are placeholders (EPIC D); the stat block fields (HD/AC/Speed/Senses/Save/Encountered) are **empty** (EPIC G); "Encyclopaedia of **The Marches**" naming is off-world; Persons & Lore tabs are demo.

### Launcher / Chronicles — 6/10
Now renders the BG masthead scene art + the last-scene thumbnail (fixed this session); live campaign list with real BG3 recaps. **But:** party portraits are placeholders (EPIC A); the same BG image risks repeating across cards/Worlds (owner's "Worlds page same picture repeated", EPIC D).

### Journal / Quests — 6/10
Clean, live (real quest hooks like "The Fate of the Emerald Grove"), honest "No active quests yet" empty-state, deterministic fig numbers. Quests carry no art/region to render (minor).

### Acts — 6/10
Honest "Acts not tracked yet — the campaign director has not compiled act progress" empty-state. Mostly empty until the Director runs; correct behavior, not a defect.

### Settings — 6/10
The native-app "Supervisor Bridge" control panel (App Status, Start/Stop Viewer/Provider, Providers, Dependencies). Shows "Native Bridge Unavailable" in the preview (expected — it connects only in the real desktop app). Secondary codex tabs (Sound/Display/Gameplay/Controls/Accessibility/Saves/About) weren't deep-audited this pass.

### Parley — 7/10
Owner-favorite. Live skill slots with correct modifiers (Athletics/Insight +3 proficient, DC 14) + free-form + the scene backdrop. **Flags:** no actor portrait (EPIC A); the Easy/Med/Hard toggle reads like a global setting, not a per-parley control; "Read-only" is unexplained (EPIC G).

### Relations — 7/10
The most finished screen. Live factions (Flaming Fist, Guild, Zhentarim, Harpers, Remnants) with standings + live NPCs (Jaheira, Astarion, Shadowheart, Wyll, Karlach, Minsc) with **real portraits rendering** + dossier prose. This is the template for what "done" looks like.

---

## Recommended first wave (for alignment)
1. **EPIC A (portraits)** + **EPIC D (per-scene art)** — biggest visible lift, partly mechanical once we pick the portrait approach.
2. **EPIC G quick wins** — Session readable recap, Parley toggle/label cleanup, Battle HP + portrait cards, Bestiary stats. Low-risk, high polish-per-hour.
3. **EPIC E (demo-leak round 2)** — fast, finishes the honesty work.
4. **EPIC C (playable in-app)** — the deepest, highest-value; needs design (wire OpenWorlds → `/move`).
5. **EPIC B (real map)** + **EPIC F (wire prototypes)** — larger builds, design-led.
