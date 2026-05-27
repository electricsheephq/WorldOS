#!/usr/bin/env python3
"""Wiki INDEX builder — catalog a wiki's full ingestable scope (read-only).

OFFLINE content tooling (NOT an MCP tool, NOT imported by the engine). Stdlib only.
Before any bulk ingest, this enumerates the page + subcategory membership of a set of
root categories on a MediaWiki/Fandom wiki and writes a single `wiki_index.json` — the
"index of everything on the wiki we could pull from" (characters, items, mechanics,
quests, locations, creatures, spells, …). It only lists titles (category members); it
does NOT fetch page bodies — that's `wiki_fetch.py`'s job, driven by what this finds.

This answers "how much exists vs how much we've ingested" and becomes the manifest
source-of-truth for systematic, surgical ingest waves.

Usage:
    python3 tools/ingest/wiki_index.py            # default bg3.wiki root categories
    python3 tools/ingest/wiki_index.py --wiki bg3.wiki --script-path /w \
        --categories Characters Items Quests --out tools/ingest/wiki_index.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from wiki_fetch import _api  # noqa: E402 — reuse the polite MediaWiki client

# Default root categories to index on bg3.wiki (the owner's "everything from the wikis").
_DEFAULT_BG3_CATEGORIES = [
    "Characters",
    "Replacement_characters",
    "Companions",
    "Origin_characters",
    "Creatures",
    "Items",
    "Weapons",
    "Armour",
    "Equipment",
    "Spells",
    "Actions",
    "Gameplay_mechanics",
    "Conditions",
    "Quests",
    "Locations",
    "Maps",
]


def _members(wiki: str, cat: str, cmtype: str, delay: float, script_path: str) -> list[str]:
    """All member titles of Category:<cat> of the given type ('page' or 'subcat')."""
    out: list[str] = []
    cmcontinue = None
    while True:
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": f"Category:{cat}", "cmlimit": "500", "cmtype": cmtype,
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        data = _api(wiki, params, delay, script_path)
        members = data.get("query", {}).get("categorymembers", [])
        out.extend(m["title"] for m in members if "title" in m)
        cmcontinue = data.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break
    return out


def index_category(wiki: str, cat: str, delay: float, script_path: str) -> dict:
    pages = _members(wiki, cat, "page", delay, script_path)
    subcats = [s.replace("Category:", "") for s in _members(wiki, cat, "subcat", delay, script_path)]
    return {"page_count": len(pages), "subcat_count": len(subcats), "pages": pages, "subcats": subcats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default="bg3.wiki")
    ap.add_argument("--script-path", default="/w")
    ap.add_argument("--categories", nargs="*", default=_DEFAULT_BG3_CATEGORIES)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--out", default=str(_HERE / "wiki_index.json"))
    args = ap.parse_args()

    print(f"[index] wiki={args.wiki}{args.script_path} categories={len(args.categories)}")
    index: dict[str, dict] = {}
    for cat in args.categories:
        entry = index_category(args.wiki, cat, args.delay, args.script_path)
        index[cat] = entry
        print(f"  {cat:24} {entry['page_count']:5} pages  {entry['subcat_count']:3} subcats")

    total_pages = sum(e["page_count"] for e in index.values())
    out = {
        "wiki": args.wiki,
        "script_path": args.script_path,
        "generated_at": time.time(),
        "total_pages_indexed": total_pages,
        "categories": index,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[index] {total_pages} total pages across {len(index)} categories → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
