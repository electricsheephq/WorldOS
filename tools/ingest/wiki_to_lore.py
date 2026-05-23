#!/usr/bin/env python3
"""Stage 2 of the lore-ingestion pipeline: raw wiki cache → clean markdown lore pages.

Reads the cache written by `wiki_fetch.py` and turns each page's wikitext into a clean
markdown "wiki page" under `content/worlds/<world_id>/lore/`, where `lorebook.py`'s
`lookup_lore` already indexes `*.md`. Each page gets a `# Title` heading and a footer
with the **source URL + CC-BY-SA attribution** (the Forgotten Realms wiki is Fandom =
CC-BY-SA; the folder's LICENSE.md carries the full notice).

Pure, stdlib only. `to_markdown()` is unit-tested on a wikitext fixture.

Usage:  python3 tools/ingest/wiki_to_lore.py [manifest.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]


def _remove_balanced(s: str, open_tok: str, close_tok: str) -> str:
    """Drop balanced open/close spans (handles nesting): {{templates}}, {|tables|}."""
    out: list[str] = []
    depth = 0
    i, n = 0, len(s)
    while i < n:
        if s.startswith(open_tok, i):
            depth += 1
            i += len(open_tok)
        elif s.startswith(close_tok, i) and depth > 0:
            depth -= 1
            i += len(close_tok)
        else:
            if depth == 0:
                out.append(s[i])
            i += 1
    return "".join(out)


def _render_wikilinks(s: str) -> str:
    """[[target|text]] -> text, [[target]] -> target; drop File:/Image:/Category: links.
    Bracket-aware (handles nested links in captions)."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s.startswith("[[", i):
            depth, j = 1, i + 2
            while j < n and depth > 0:
                if s.startswith("[[", j):
                    depth += 1; j += 2
                elif s.startswith("]]", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            inner = s[i + 2:j - 2]
            head = inner.split("|", 1)[0].strip().lower()
            if not head.startswith(("file:", "image:", "category:")):
                disp = inner.split("|")[-1] if "|" in inner else inner
                out.append(_render_wikilinks(disp))
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def to_markdown(title: str, wikitext: str, source_url: str, max_chars: int = 9000) -> str:
    """Convert one page's wikitext to a clean markdown lore page (title + body + attribution)."""
    s = wikitext
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<gallery[^>]*>.*?</gallery>", "", s, flags=re.S)
    s = _remove_balanced(s, "{{", "}}")   # infoboxes / templates
    s = _remove_balanced(s, "{|", "|}")   # wikitables
    s = _render_wikilinks(s)
    # List/indent markers FIRST — before bold conversion, so a line-leading '''bold'''
    # (which becomes **bold**) isn't mistaken for a '*' bullet and eaten.
    s = re.sub(r"^[*#:;]+\s*", "- ", s, flags=re.M)
    s = re.sub(r"'''(.+?)'''", r"**\1**", s)
    s = re.sub(r"''(.+?)''", r"*\1*", s)
    for n_eq, hashes in ((6, "######"), (5, "#####"), (4, "####"), (3, "###"), (2, "##")):
        s = re.sub(rf"^={{{n_eq}}}\s*(.+?)\s*={{{n_eq}}}\s*$", rf"{hashes} \1", s, flags=re.M)
    s = re.sub(r"<[^>]+>", "", s)                               # stray html (<br/> etc.)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    # Drop trailing noise sections (citations / nav), keep the lore body.
    s = re.split(r"\n#+\s*(?:References|External [Ll]inks|Appendix|Notes|Sources|See also)\b", s)[0].strip()
    if len(s) > max_chars:
        s = s[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n\n…(truncated — see source)"
    footer = (
        f"\n\n---\n*Source: {source_url} — Forgotten Realms Wiki (Fandom), "
        f"CC-BY-SA. Unofficial fan content; see LICENSE.md in this folder.*\n"
    )
    return f"# {title}\n\n{s}\n{footer}"


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title.lower()).strip("-")
    return (s or "page")[:80]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default=str(_HERE / "manifest.json"))
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    world_id = manifest["world_id"]
    max_chars = int(manifest.get("max_chars_per_page", 9000))

    cache = _HERE / ".cache" / world_id
    if not cache.is_dir():
        print(f"[to_lore] no cache at {cache} — run wiki_fetch.py first", file=sys.stderr)
        return 1
    # Ingested pages live in a wiki/ subfolder so they sit alongside (never clobber)
    # the curated authored pages at lore/*.md; lorebook indexes both (rglob).
    out_dir = _REPO / "content" / "worlds" / world_id / "lore" / "wiki"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for cf in sorted(cache.glob("*.json")):
        page = json.loads(cf.read_text(encoding="utf-8"))
        md = to_markdown(page["title"], page.get("wikitext", ""), page.get("source_url", ""), max_chars)
        # Skip near-empty pages (redirects/stubs that cleaned to nothing).
        if len(md.split("---", 1)[0].strip()) < 80:
            continue
        (out_dir / f"{_slug(page['title'])}.md").write_text(md, encoding="utf-8")
        written += 1
    print(f"[to_lore] wrote {written} lore pages → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
