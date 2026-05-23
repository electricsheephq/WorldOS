#!/usr/bin/env python3
"""Stage 1 of the lore-ingestion pipeline: fetch wiki pages → a raw cache.

OFFLINE content tooling (NOT an MCP tool, NOT imported by the engine). Stdlib only
(urllib + json), so it adds no dependency and can run anywhere. It pulls a curated,
BOUNDED set of pages from a MediaWiki/Fandom wiki (default: the Forgotten Realms wiki,
CC-BY-SA) named by `manifest.json`, and caches each page's raw wikitext + metadata to
`tools/ingest/.cache/<world_id>/<sha1(title)>.json`.

Resumable + idempotent (skips already-cached pages), polite (rate-limited, sets a
User-Agent + maxlag). Stage 2 (`wiki_to_lore.py`) turns the cache into clean markdown
lore pages under `content/worlds/<world_id>/lore/`.

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
_UA = "ClawDnD-lore-ingest/0.1 (private, non-commercial fan project; MediaWiki API)"


def _cache_dir(world_id: str) -> Path:
    return _HERE / ".cache" / world_id


def _api(wiki: str, params: dict, delay: float) -> dict:
    """One polite GET against the wiki's api.php. Returns parsed JSON (or {} on error)."""
    qs = urllib.parse.urlencode({**params, "format": "json", "maxlag": "5"})
    url = f"https://{wiki}/api.php?{qs}"
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


def resolve_titles(wiki: str, manifest: dict, delay: float) -> list[str]:
    """Explicit titles + every page member of the manifest's categories (deduped)."""
    titles: list[str] = list(manifest.get("titles", []))
    for cat in manifest.get("categories", []):
        cmcontinue = None
        while True:
            params = {
                "action": "query", "list": "categorymembers",
                "cmtitle": f"Category:{cat}", "cmlimit": "200", "cmtype": "page",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue
            data = _api(wiki, params, delay)
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


def fetch_page(wiki: str, title: str, delay: float) -> dict | None:
    """Fetch one page's wikitext + revid (following redirects). None if missing."""
    data = _api(wiki, {"action": "parse", "page": title, "prop": "wikitext|revid", "redirects": "1"}, delay)
    parse = data.get("parse")
    if not parse or "wikitext" not in parse:
        return None
    return {
        "title": parse.get("title", title),
        "revid": parse.get("revid"),
        "wikitext": parse["wikitext"].get("*", ""),
        "source_url": f"https://{wiki}/wiki/{urllib.parse.quote(parse.get('title', title).replace(' ', '_'))}",
        "wiki": wiki,
        "fetched_at": time.time(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default=str(_HERE / "manifest.json"))
    ap.add_argument("--max", type=int, default=None, help="override manifest max_pages")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    wiki = manifest["wiki"]
    world_id = manifest["world_id"]
    delay = float(manifest.get("rate_delay_seconds", 0.5))
    max_pages = args.max or int(manifest.get("max_pages", 250))

    cache = _cache_dir(world_id)
    cache.mkdir(parents=True, exist_ok=True)

    print(f"[fetch] wiki={wiki} world={world_id} max={max_pages}")
    titles = resolve_titles(wiki, manifest, delay)[:max_pages]
    print(f"[fetch] {len(titles)} pages to consider")

    fetched = skipped = missing = 0
    for i, title in enumerate(titles, 1):
        cf = cache / (hashlib.sha1(title.encode("utf-8")).hexdigest() + ".json")
        if cf.exists() and not args.refresh:
            skipped += 1
            continue
        page = fetch_page(wiki, title, delay)
        if page is None:
            missing += 1
            print(f"  [{i}/{len(titles)}] MISSING: {title}")
            continue
        cf.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        fetched += 1
        if fetched % 10 == 0:
            print(f"  [{i}/{len(titles)}] fetched {fetched}…")
    print(f"[fetch] done: {fetched} fetched, {skipped} cached-skip, {missing} missing → {cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
