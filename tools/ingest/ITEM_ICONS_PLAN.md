# Item-icon ingest at scale — plan

The current `manifest_images.json` lists **one bg3.wiki page title per item**. That is fine for the
~16 hand-picked legendaries committed today, but BG3 has **hundreds** of items (the `Category:Weapons`
member count alone is **429 pages**, plus armour, accessories, consumables). A title-per-item manifest does
**not** scale to "thousands of items" — we'd be hand-transcribing every page title.

## How bg3.wiki exposes item images
- Every notable item has its own page (e.g. `Markoheshkir`, `Helldusk Armour`). The page's **lead/infobox
  image is the item icon** (`*_Icon.png` / `.webp`) — verified across weapons and armour. This is exactly what
  `wiki_images.py`'s `pageimages → imageinfo` resolver already returns.
- Items are organised by **MediaWiki categories**, not a single list page (`List_of_all_weapons` 404s):
  - `Category:Weapons` → **37 subcategories** by weapon type (Daggers, Greatswords, Maces, …), plus
    umbrella cats (`Martial weapons`, `Melee weapons (376 P)`). Members are listed alphabetically,
    **paginated 200 per request**.
  - Parallel trees exist for `Category:Armour`, shields, amulets/rings, etc.
  - Rarity is a **page property**, not a category — so there's no `Category:Legendary` to cherry-pick from;
    filtering "iconic only" must read each page or a curated allowlist.

## Does the page-title-per-item approach scale? No.
Two problems: (1) someone must enumerate every title by hand; (2) the manifest would balloon to thousands of
near-identical lines. We need a **category-crawl mode**, not more manifest entries.

## Recommended approach — add a category-crawl mode to the image pipeline
1. **Reuse the proven crawler.** `wiki_fetch.py` (lines ~73-79) already resolves `Category:<X>` via
   `list=categorymembers` (`cmtype=page`, `cmlimit=200`, with `cmcontinue` pagination). `wiki_images.py` has
   **no** category support today. Port that same helper so an image source can declare `categories: [...]`
   alongside (or instead of) `images: [...]`.
2. **Manifest shape (additive, back-compatible).** Keep explicit `images[]` for curated/flagged items; add an
   optional `categories[]` per source, e.g.
   `{"category": "Weapons", "kind": "item", "scope_prefix": "item:", "max": 500}`. For each crawled member,
   derive `scope = scope_prefix + slug(title)` (hyphenated lowercase, matching the current convention) and
   reuse the existing lead-image fetch + `_private/` write + provenance sidecar unchanged.
3. **Bound + de-dupe.** Honour a per-category `max`, skip pages already ingested (the tool is already
   idempotent on `_private/<world>/images/<scope>/`), and skip non-item members (category pages often include
   stray mechanic/quest pages — filter `cmtype=page` + a kind/namespace guard).
4. **Quality filter for "iconic" tiers.** If we only want legendary/very-rare, either (a) keep a curated
   allowlist file, or (b) crawl-then-filter by reading each page's rarity field. Start with (a) — cheaper and
   deterministic; the full crawl can run later behind `--all-items`.
5. **Politeness/licensing unchanged.** Same `rate_delay_seconds`, dual CC-BY-SA / CC-BY-NC-SA attribution,
   and **`_private/`-only, never-committed** storage discipline already enforced by `scripts/license_check.py`.

**Net:** one ~40-line category helper (lifted from `wiki_fetch.py`) turns the manifest from a per-item list into
a handful of category declarations, scaling to the full catalog without thousands of hand-written titles.
Any code change is out of scope here — this manifest stays title-driven until that mode lands.
