# tools/ingest — wiki → lore + character ingestion

Offline content tooling (NOT an MCP tool, NOT imported by the engine; **stdlib only** —
urllib + json). Pulls a curated, bounded slice of online wikis into a world seed: **lore**
pages (so the DM can `lookup_lore` deep canon) and **character** records (clean JSON NPC
profiles ClawDnD can later load as world NPCs). Two Stage-2 converters share one fetcher.

## The fetcher (`wiki_fetch.py`)

Resolves categories + titles via the MediaWiki API and caches each page's raw wikitext +
revid + source URL to `tools/ingest/.cache/<world_id>/...` (gitignored, regenerable).
Resumable/idempotent (skips cached), polite (rate-limited, `User-Agent`, `maxlag`).

It fetches from **both** MediaWiki flavors — the wiki host *and its API `script_path`* are
parameters:
- **Fandom** (`forgottenrealms.fandom.com`) — API at `/api.php`, `script_path: ""`.
- **standalone MediaWiki** (`bg3.wiki`) — API at `/w/api.php`, `script_path: "/w"`.

Two manifest shapes are supported:
- **single-source** (lore): top-level `wiki` + `categories`/`titles` (`manifest.json`).
- **multi-source** (characters): a `sources[]` list, each with its own `wiki`/`script_path`/
  `license`/`attribution` + `titles`/`categories` (`manifest_characters.json`). Per-source
  `license`/`attribution` is carried into the cache so Stage 2 stamps the right notice.
  An optional `cache_subdir` isolates a pipeline's cache (characters use `characters/`).

## Lore pipeline

1. **`manifest.json`** — scope: `wiki`, `world_id`, `categories[]`/`titles[]`, `max_pages`,
   `rate_delay_seconds`, `max_chars_per_page`.
2. **`wiki_to_lore.py`** — cached wikitext → clean markdown under
   `content/worlds/<world_id>/lore/wiki/`, each with `# Title` + a **source-URL + CC-BY-SA
   attribution footer**. `lorebook.py` indexes `lore/**/*.md`, so authored pages (`lore/*.md`)
   and ingested pages (`lore/wiki/*.md`) coexist.

```bash
python3 tools/ingest/wiki_fetch.py            # fetch (manifest.json; --max N, --refresh)
python3 tools/ingest/wiki_to_lore.py          # convert → content/worlds/<id>/lore/wiki/
```

## Character pipeline (S2.5)

1. **`manifest_characters.json`** — multi-source scope (bg3.wiki companions + key NPCs, plus
   FR-wiki figures), `cache_subdir: "characters"`, `max_chars_per_field`.
2. **`wiki_to_characters.py`** — cached wikitext → one clean JSON record per character under
   `content/worlds/<world_id>/characters/<slug>.json`, with fields: `name, race, class, level,
   alignment, appearance, personality, mannerisms, backstory, equipment[], relationships[],
   voice_hint, source_url, license, attribution`. The infobox is *parsed* (race/class/etc.);
   profile sections are mapped to fields; refs/templates/wikilinks are stripped using the same
   helpers as the lore stage (link-wrapper templates like `{{CharLink|Page|Name}}` are
   *unwrapped* to their display text so proper nouns survive). **TEXT/DATA ONLY** — no images
   are fetched or written (portraits are handled separately, local-only).

```bash
python3 tools/ingest/wiki_fetch.py        tools/ingest/manifest_characters.json
python3 tools/ingest/wiki_to_characters.py tools/ingest/manifest_characters.json
```

The parser is guarded by `servers/engine/tests/test_wiki_to_characters.py` (runs in CI's
engine pytest via a path-insert, like `test_wiki_ingest.py`).

## Image pipeline (W2b — wiki image ingest)

`wiki_images.py` fetches the **lead image** for each entry in `manifest_images.json`
via the MediaWiki API (pageimages → imageinfo fallback) and writes:

1. **Image file** at `content/worlds/_private/<world_id>/images/<safe-scope>/image.<ext>`
2. **Provenance sidecar** (`image.<ext>.provenance.json`) — `source_url`, `license`,
   `attribution`, `fetched_at`.
3. **Viewer descriptor** (`wiki_ingest.json`) — the shape `_latest_descriptor` in
   `viewer/server.py` reads: `{path, mime_type, scope, source_url, license, attribution}`.

**Licensing / storage discipline:** Official game/wiki art (© Larian / WotC) lands ONLY
under the gitignored `content/worlds/_private/` tree. CC-BY-SA wiki images are kept WITH
per-file attribution in the sidecar. The `_private/` path is covered by `.gitignore`;
`scripts/license_check.py` enforces no committed images from that tree. NEVER commit the
`_private/` directory.

The `/image?scope=<scope>` viewer endpoint resolves descriptors in this order:
  1. Ingested asset (`_private/<world>/images/<scope>/wiki_ingest.json`) — priority
  2. Generated imagegen cache (`<state_dir>/images/<scope>/*.json`)
  3. 404 / placeholder fallback

```bash
# Dry-run (preview what would be fetched, no downloads):
python3 tools/ingest/wiki_images.py --dry-run

# Fetch up to N images per source (for testing):
python3 tools/ingest/wiki_images.py --max 3

# Full ingest (all entries in manifest_images.json):
python3 tools/ingest/wiki_images.py

# Custom manifest:
python3 tools/ingest/wiki_images.py path/to/my_manifest_images.json
```

Resumable + idempotent: scopes already written to `_private/` are skipped.
The manifest format:

```json
{
  "world_id": "baldurs-gate",
  "rate_delay_seconds": 0.75,
  "sources": [{
    "wiki": "bg3.wiki",
    "script_path": "/w",
    "license": "CC BY-SA 4.0 / CC BY-NC-SA 4.0 (dual)",
    "attribution": "Image from bg3.wiki ...",
    "images": [
      {"title": "Shadowheart", "scope": "portrait:shadowheart", "kind": "portrait"},
      {"title": "Elfsong Tavern", "scope": "scene:elfsong-tavern", "kind": "scene"}
    ]
  }]
}
```

## Private compendium sidecar

`private_compendium_sidecar.py` is a local-only scaffold for user-owned books,
adventures, exports, and homebrew. It validates a manifest kept outside the git checkout
and prints planned gitignored private outputs; it does not ingest records into public
content or mutate engine campaign state.

```bash
python3 tools/ingest/private_compendium_sidecar.py --init
python3 tools/ingest/private_compendium_sidecar.py
```

The default sidecar root is `/Volumes/LEXAR/Codex/clawdnd-private-compendium`, overridable
with `CLAWDND_PRIVATE_COMPENDIUM_ROOT`. See `docs/PRIVATE_COMPENDIUM_SIDECAR.md`.

## Licensing (TEXT ONLY)

Each ingested record/page carries its **source URL + a per-source license + attribution**;
the world seed's `LICENSE.md` carries the Wizards (and, for BG3, Larian) Fan Content notice.
Sources differ — record the license **per source**:

- **Forgotten Realms Wiki (Fandom)** — **CC-BY-SA** (`https://www.fandom.com/licensing`).
- **bg3.wiki** — content submitted **on/after 2024-07-20 is dual-licensed CC BY-SA 4.0 *and*
  CC BY-NC-SA 4.0**; older revisions are **CC BY-NC-SA 4.0 (NonCommercial) only**. Also subject
  to Larian's and Wizards' Fan Content Policies. (Verified at
  `https://bg3.wiki/wiki/bg3wiki:Copyrights`; the API `rightsinfo` reports
  `"CC BY-NC-SA 4.0 or CC BY-SA 4.0"`.) ClawDnD is a non-commercial fan project, so both
  options are satisfied; we attribute with a back-link per the wiki's stated requirement and
  carry the NonCommercial caveat in the per-source `license` string.

Note CC-BY-SA (ShareAlike) differs from the original-seed prose (CC-BY-4.0). **Never ingest a
source whose license forbids redistribution. Never fetch or commit images** — text/data only.
The `.cache/` of raw responses is gitignored.
