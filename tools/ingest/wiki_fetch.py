#!/usr/bin/env python3
"""Stage 1 of the lore/character-ingestion pipeline: fetch wiki pages → a raw cache.

OFFLINE content tooling (NOT an MCP tool, NOT imported by the engine). Stdlib only
(urllib + json), so it adds no dependency and can run anywhere. It pulls a curated,
BOUNDED set of pages from a MediaWiki/Fandom wiki named by a manifest, and caches each
page's raw wikitext + metadata to `tools/ingest/.cache/<world_id>/<sha1(wiki+title)>.json`.

The wiki HOST and its MediaWiki `script_path` are parameters, so this fetches from BOTH:
  - Fandom wikis        (e.g. forgottenrealms.fandom.com — API at /api.php, script_path "")
  - standalone MediaWiki (e.g. bg3.wiki — API at /w/api.php, script_path "/w")

Two manifest shapes are supported:
  - single-source (lore):  top-level "wiki" + "categories"/"titles" (manifest.json).
  - multi-source (chars):  a "sources" list, each with its own "wiki"/"script_path"/
    "license"/"attribution" + "titles"/"categories" (manifest_characters.json). Per-source
    license/attribution is carried into the cache so Stage 2 can stamp it per record.

Resumable + idempotent (skips already-cached pages), polite (rate-limited, sets a
User-Agent + maxlag). Stage 2 turns the cache into clean output:
`wiki_to_lore.py` → markdown lore pages; `wiki_to_characters.py` → JSON NPC records.

Usage:  python3 tools/ingest/wiki_fetch.py [manifest.json] [--max N] [--refresh]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_UA = "WorldOS-lore-ingest/0.1 (private, non-commercial fan project; MediaWiki API)"


def _cache_dir(world_id: str) -> Path:
    return _HERE / ".cache" / world_id


def _api(wiki: str, params: dict, delay: float, script_path: str = "") -> dict:
    """One polite GET against the wiki's api.php. Returns parsed JSON (or {} on error).

    `script_path` is the MediaWiki script prefix: "" for Fandom (API at /api.php), "/w"
    for bg3.wiki (API at /w/api.php). It's normalized to start with "/" and not end with one.
    """
    sp = ("/" + script_path.strip("/")) if script_path.strip("/") else ""
    qs = urllib.parse.urlencode({**params, "format": "json", "maxlag": "5"})
    url = f"https://{wiki}{sp}/api.php?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(delay)
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — network is best-effort; retry then skip
            print(f"  ! api error ({attempt + 1}/3): {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return {}


def resolve_titles(wiki: str, source: dict, delay: float, script_path: str = "") -> list[str]:
    """Explicit titles + every page member of the source's categories (deduped)."""
    titles: list[str] = list(source.get("titles", []))
    for cat in source.get("categories", []):
        cmcontinue = None
        while True:
            params = {
                "action": "query", "list": "categorymembers",
                "cmtitle": f"Category:{cat}", "cmlimit": "200", "cmtype": "page",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue
            data = _api(wiki, params, delay, script_path)
            members = data.get("query", {}).get("categorymembers", [])
            titles.extend(m["title"] for m in members if "title" in m)
            cmcontinue = data.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break
    # dedupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def fetch_page(wiki: str, title: str, delay: float, script_path: str = "",
               license: str = "", attribution: str = "") -> dict | None:
    """Fetch one page's wikitext + revid (following redirects). None if missing.

    `license`/`attribution` (from the manifest source) are carried into the cached record
    so Stage 2 can stamp the right per-source notice — bg3.wiki and Fandom differ.
    """
    data = _api(wiki, {"action": "parse", "page": title, "prop": "wikitext|revid", "redirects": "1"}, delay, script_path)
    parse = data.get("parse")
    if not parse or "wikitext" not in parse:
        return None
    return {
        "title": parse.get("title", title),
        "revid": parse.get("revid"),
        "wikitext": parse["wikitext"].get("*", ""),
        "source_url": f"https://{wiki}/wiki/{urllib.parse.quote(parse.get('title', title).replace(' ', '_'))}",
        "wiki": wiki,
        "license": license,
        "attribution": attribution,
        "fetched_at": time.time(),
    }


def _sources(manifest: dict) -> list[dict]:
    """Normalize either manifest shape into a list of source dicts.

    Multi-source manifests carry a "sources" list (each with its own wiki/script_path/
    license/attribution). Single-source (lore) manifests are wrapped into a one-element
    list using the top-level wiki + titles/categories.
    """
    if "sources" in manifest:
        return list(manifest["sources"])
    return [{
        "wiki": manifest["wiki"],
        "script_path": manifest.get("script_path", ""),
        "license": manifest.get("license", ""),
        "attribution": manifest.get("attribution", ""),
        "titles": manifest.get("titles", []),
        "categories": manifest.get("categories", []),
    }]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default=str(_HERE / "manifest.json"))
    ap.add_argument("--max", type=int, default=None, help="override manifest max_pages (per source)")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    world_id = manifest["world_id"]
    delay = float(manifest.get("rate_delay_seconds", 0.5))
    max_pages = args.max or int(manifest.get("max_pages", 250))

    # Optional cache subdir isolates pipelines that share a world_id: the lore manifest
    # caches to .cache/<world_id>/, the characters manifest to .cache/<world_id>/characters/,
    # so each Stage-2 converter globs only its own pages.
    cache = _cache_dir(world_id)
    if manifest.get("cache_subdir"):
        cache = cache / str(manifest["cache_subdir"])
    cache.mkdir(parents=True, exist_ok=True)

    sources = _sources(manifest)
    # Multi-source manifests namespace the cache key by wiki so the same title on two wikis
    # (e.g. "Jaheira" on bg3.wiki AND forgottenrealms.fandom.com) never collides. Single-source
    # (lore) manifests keep the legacy sha1(title) key, so existing lore caches stay valid.
    multi = "sources" in manifest
    total_fetched = total_skipped = total_missing = 0
    for src in sources:
        wiki = src["wiki"]
        script_path = src.get("script_path", "")
        license = src.get("license", "")
        attribution = src.get("attribution", "")
        print(f"[fetch] wiki={wiki}{script_path or ''} world={world_id} max={max_pages}")
        titles = resolve_titles(wiki, src, delay, script_path)[:max_pages]
        print(f"[fetch] {len(titles)} pages to consider from {wiki}")

        fetched = skipped = missing = 0
        for i, title in enumerate(titles, 1):
            key = f"{wiki}\x00{title}" if multi else title
            cf = cache / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
            if cf.exists() and not args.refresh:
                skipped += 1
                continue
            page = fetch_page(wiki, title, delay, script_path, license, attribution)
            if page is None:
                missing += 1
                print(f"  [{i}/{len(titles)}] MISSING: {title}")
                continue
            cf.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
            fetched += 1
            if fetched % 10 == 0:
                print(f"  [{i}/{len(titles)}] fetched {fetched}…")
        print(f"[fetch] {wiki}: {fetched} fetched, {skipped} cached-skip, {missing} missing")
        total_fetched += fetched
        total_skipped += skipped
        total_missing += missing

    print(f"[fetch] done: {total_fetched} fetched, {total_skipped} cached-skip, "
          f"{total_missing} missing → {cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
