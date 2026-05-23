# tools/ingest — wiki → lore-corpus ingestion

Offline content tooling (NOT an MCP tool, NOT imported by the engine; **stdlib only**).
Pulls a curated, bounded slice of an online wiki into a world seed's lore corpus so the
DM can `lookup_lore` deep canon on demand.

## Pipeline

1. **`manifest.json`** — the scope: target `wiki`, `world_id`, `categories[]` + `titles[]`,
   `max_pages` (cap), `rate_delay_seconds`, `max_chars_per_page`. Edit this to widen/narrow.
2. **`wiki_fetch.py`** — resolves categories + titles via the MediaWiki API and caches each
   page's raw wikitext + revid + source URL to `tools/ingest/.cache/<world_id>/` (gitignored,
   regenerable). Resumable/idempotent (skips cached), polite (rate-limited, `User-Agent`, `maxlag`).
3. **`wiki_to_lore.py`** — converts cached wikitext → clean markdown pages under
   `content/worlds/<world_id>/lore/wiki/`, each with a `# Title` + a **source-URL + CC-BY-SA
   attribution footer**. `lorebook.py` indexes `lore/**/*.md`, so authored pages (`lore/*.md`)
   and ingested pages (`lore/wiki/*.md`) coexist.

## Run

```bash
python3 tools/ingest/wiki_fetch.py            # fetch (uses manifest.json; --max N, --refresh)
python3 tools/ingest/wiki_to_lore.py          # convert cache → content/worlds/<id>/lore/wiki/
```

## Licensing

The default target (Forgotten Realms / Baldur's Gate Fandom wiki) is **CC-BY-SA**. Ingested
pages are **free, unofficial fan content**: each carries its source URL + CC-BY-SA attribution,
and the world seed's `LICENSE.md` carries the Wizards Fan Content Policy notice. Note CC-BY-SA
(ShareAlike) differs from the original-seed CC-BY-4.0. The `.cache/` of raw responses is
gitignored. Never ingest a source whose license forbids redistribution.
