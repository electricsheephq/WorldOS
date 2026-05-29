# Image Coverage — baldurs-gate world (W2b wiki-image manifest)

How the viewer requests art and what `manifest_images.json` covers. The render bridge
(`viewer/server.py` → `GET /image?scope=…`) normalizes both the UI's engine-id scope and
the manifest slug through `_scope_key` (lowercase; `:`/`_`/space→`-`; strip leading
kind/entity tokens in `{portrait, scene, item, map, npc, char, pc, loc, location, region,
scope}`). A manifest entry resolves a UI request **only when both sides collapse to the same
key**. Sources of truth: `content/worlds/baldurs-gate/world.json` (regions + cast) and
`content/worlds/baldurs-gate/characters/*.json`. WebFetch-verified entries dated 2026-05-27.

## What the app requests

- **Scenes** — `screen-map.jsx` sets `scope={loc.id}` (the raw engine location id, e.g.
  `loc-lower-city`). Dialogue/table screens also pass `imageScope`/`locationScope` for
  named-landmark backdrops.
- **Portraits** — every screen builds `portrait-<id>` (`camp-sidebar`, `screen-character`,
  `screen-relations`, `screen-dialogue`, `screen-table`, `screen-inventory`). For roster
  NPCs `<id>` is the `npc-*` id from world.json; `portrait-npc-shadowheart` → key
  `shadowheart`.
- **Items** — `screen-inventory.jsx` builds `item-<id|slug(name)>`.

## Scene gap matrix — top-level map locations (loc-* in world.json)

| location id | required key | source | status |
|---|---|---|---|
| loc-lower-city | lower-city | bg3.wiki *Lower City* (cityscape still) | wiki-sourced (already) |
| loc-upper-city | upper-city | bg3.wiki *Upper City* (`Upper_City_Invasion.webp`, cityscape still) | **ADDED** — wiki-sourced |
| loc-outer-city | outer-city | bg3.wiki *Rivington* (`Rivington.jpg`, top-view still; no "Outer City" page exists — 404) | **ADDED** — aligned |
| loc-wyrms-crossing | wyrms-crossing | bg3.wiki *Wyrm's Crossing* | wiki-sourced (already; alignment note added) |
| loc-elturel | elturel | bg3.wiki *Elturel* (`The_Descent_of_Elturel.png`, environment still) | **ADDED** — wiki-sourced |
| loc-reithwin | reithwin | bg3.wiki *Reithwin Town* (`Reithwin_Town.webp`, town-square still) | **ADDED** — aligned |
| loc-candlekeep | candlekeep | bg3.wiki *Candlekeep* lead = tiny portrait icon (not a still) | resolves but **GENERATION GAP** |

Before this pass only lower-city, wyrms-crossing, candlekeep resolved; **4 of 7 locations
showed placeholder art.** All 7 now resolve. The non-location scenes (elfsong-tavern,
moonrise-towers, last-light-inn, sorcerous-sundries, high-hall, rivington, emerald-grove,
wyrms-rock) are intentional landmark backdrops, not map locations — kept as-is.

## Portrait coverage

- **Cast NPCs (world.json):** jaheira, minsc, astarion, shadowheart, wyll, karlach,
  the-emperor, withers, raphael — all covered. **claudan = GENERATION GAP** (original
  WorldOS character, no wiki page).
- **Character-file heroes (`characters/*.json`, player-pickable / lore):** astarion, gale,
  halsin, isobel, jaheira, karlach, minsc, shadowheart, withers, wyll covered.
  **lae-zel FIXED** (was `portrait:lazel` → key `lazel`, never matched engine id
  `npc-lae-zel`; now `portrait:lae-zel`). **rolan ADDED** (`Rolan.png`). **lia ADDED**
  (`Portrait_Lia.png`). **jergal** left uncovered — its wiki lead is a deity *symbol*, not a
  portrait, and the wiki confirms Withers is Jergal's avatar (already covered); a god rarely
  surfaces as a party `portrait-<id>`. Low priority.
- Extra bench portraits kept (cazador-szarr, dame-aylin, ketheric-thorm, minthara,
  the-dark-urge, viconia-devir, volo) — valid named figures the DM may stage.

## Items

16 curated legendary/notable weapon & armour icons (bg3.wiki item leads ARE the icon).
Unchanged this pass; scaling to the full catalog tracked in `ITEM_ICONS_PLAN.md`.

## Prioritized GENERATION GAPS (no wiki still exists — gateway / ImageGen2 fallback)

1. **scene:candlekeep** — Candlekeep is absent from BG3, so bg3.wiki has only a tiny icon.
   A Forgotten Realms Wiki (Fandom) page has a real fortress-library image, but Fandom
   returns HTTP 403 to our fetcher, so it can't be ingested here. Generate a Sword-Coast
   cliffside fortress-library still, OR add a Fandom source if a fetch path is opened.
2. **portrait:claudan** — Claudan the Chronicler is an original, non-canon WorldOS easter-egg
   character. No wiki anywhere; generation is the only path.
3. *(low)* **portrait:jergal** — only if a god-portrait is ever wanted distinct from Withers.

## Verify

`python3 -c "import json;json.load(open('tools/ingest/manifest_images.json'))"` → parses.
Do NOT run `wiki_images.py` from here (per task scope).
