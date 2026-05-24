#!/usr/bin/env python3
"""Stage 2 (areas) of the ingestion pipeline: raw wiki cache → navigable Location JSON.

Reads the cache written by `wiki_fetch.py` (the `areas/` subdir) and turns each cached
area/location page's wikitext into a clean JSON record under
`content/worlds/<world_id>/areas/<slug>.json`, which ClawDnD's `seed_world` loads as a
navigable `Location` of the "baldurs-gate" world. Today the ~248 ingested location pages
land only in `lookup_lore` (lore markdown); this stage makes them PLACES the party can
travel to.

Each record is Location-shaped: name, description (cleaned + summarized), region (the
parent area from the infobox, if present), connections (linked area/place names pulled
from the infobox AND the body — kept as NAMES, resolved to ids at seed time), tags,
source_url, license, attribution. Wikitext/templates/refs are stripped with the SAME
cleaning helpers as the lore + characters stages (`wiki_to_lore._render_wikilinks` /
`_remove_balanced`), so there is one source of truth for link/template handling. The
infobox is PARSED first (it holds the structured parent-region + nearby-place data)
instead of being thrown away.

TEXT/DATA ONLY. No images/maps are read or written. The license/attribution carried
per-source by the fetcher is stamped onto each record verbatim, so bg3.wiki (dual CC
BY-SA / CC BY-NC-SA) and the Forgotten Realms Wiki (Fandom, CC-BY-SA) are each
attributed correctly.

Pure, stdlib only. `to_area_record()` is unit-tested on a wikitext fixture.

Usage:  python3 tools/ingest/wiki_to_areas.py [manifest_areas.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

# Reuse the lore + characters stages' wikitext-cleaning primitives (no side effects on
# import; their main()s are guarded by __main__). One source of truth for link/template
# handling — _remove_balanced/_render_wikilinks from lore, and the richer inline helpers
# (_strip_inline / _clean_block / section + infobox machinery) from characters.
sys.path.insert(0, str(_HERE))
from wiki_to_lore import _remove_balanced, _render_wikilinks  # noqa: E402,F401
from wiki_to_characters import (  # noqa: E402
    _clean_block,
    _norm_heading,
    _parse_infobox,
    _split_sections,
    _strip_inline,
)


# ── infobox keys → record fields ─────────────────────────────────────────────────────
# Area/settlement/location infoboxes on bg3.wiki and the FR wiki put the parent zone in
# one of these keys (case/space/underscore-insensitive). The first that resolves wins.
_REGION_KEYS: tuple[str, ...] = (
    "region", "area", "location", "parent", "subregion", "subarea",
    "continent", "country", "realm", "province",
)

# Infobox keys whose VALUE lists nearby/connected places (kept as connection hints). FR
# settlement infoboxes use these for adjacency; bg3.wiki area boxes use "connects"/"exits".
_CONNECTION_KEYS: tuple[str, ...] = (
    "connects", "connections", "exits", "adjacent", "nearby", "borders",
    "leadsto", "linkedlocations", "neighbors", "neighbours",
)

# Section headings (normalized: lowercase, collapsed spaces) whose body feeds the area
# DESCRIPTION, matched by EQUALITY (not substring) for the same reason as the characters
# stage — a loose match would slurp walkthrough/gameplay headings into the description.
_DESC_HEADINGS: frozenset[str] = frozenset({
    "description", "overview", "geography", "layout", "environment",
    "setting", "the area", "about",
})

# Headings whose body holds adjacency prose / a "connected locations" list.
_CONNECTION_HEADINGS: frozenset[str] = frozenset({
    "connections", "connected locations", "nearby locations", "adjacent areas",
    "exits", "nearby", "travel", "geography",
})


def _infobox_value(box: dict[str, str], aliases: tuple[str, ...]) -> str:
    """First non-empty infobox value among `aliases` (normalized keys)."""
    for a in aliases:
        norm = re.sub(r"[\s_]+", "", a.lower())
        if box.get(norm):
            return box[norm]
    return ""


def _strip_templates_and_tables(s: str) -> str:
    """Drop {{templates}} (incl. the infobox) and {|tables|} from a body before link
    extraction, so links INSIDE the infobox/templates don't leak into connections.

    `_split_sections` returns the lead as the raw text BEFORE the first heading, which on
    most pages still contains the leading infobox template; without this strip a
    `[[Region]]` in the infobox would be mistaken for a body connection (the prose links
    we DO want survive). Comments + refs are stripped first for the same reason.
    """
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = _remove_balanced(s, "{{", "}}")
    s = _remove_balanced(s, "{|", "|}")
    return s


def _link_names(s: str, max_items: int = 30) -> list[str]:
    """Pull the DISPLAY names of every prose `[[wikilink]]` in `s` (deduped, in order).

    Strips templates/tables (and their internal links) FIRST, then drops
    File:/Image:/Category: links and self-links; cleans each name inline so a
    `[[Baldur's Gate|the Gate]]` yields "the Gate" (the display text the page chose).
    These are connection HINTS — resolved to location ids at seed time where possible.
    """
    s = _strip_templates_and_tables(s)
    names: list[str] = []
    seen: set[str] = set()
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
                name = _strip_inline(disp)
                key = name.lower()
                if name and key not in seen and not _is_junk_name(name):
                    seen.add(key)
                    names.append(name)
            i = j
        else:
            i += 1
        if len(names) >= max_items:
            break
    return names


def _is_junk_name(name: str) -> bool:
    """Reject connection names that are markup leftovers or non-places."""
    if len(name) <= 2:
        return True
    if not re.search(r"[A-Za-z]", name):       # pure punctuation / "}}"
        return True
    if re.fullmatch(r"\d{1,4}( ?(DR|CE|BCE))?", name):   # bare years (1492 DR)
        return True
    return False


def _infobox_connection_names(box: dict[str, str]) -> list[str]:
    """Connection names declared in the infobox's adjacency keys (comma/link-split)."""
    out: list[str] = []
    for key in _CONNECTION_KEYS:
        norm = re.sub(r"[\s_]+", "", key.lower())
        val = box.get(norm, "")
        if not val:
            continue
        # The value was already inline-cleaned by _parse_infobox (links → display text),
        # so split on commas / "and" / semicolons / bullets into individual place names.
        for piece in re.split(r"\s*(?:,|;|·|•|/| and )\s*", val):
            piece = piece.strip(" \t.-—–")
            if piece and not _is_junk_name(piece):
                out.append(piece)
    return out


def _dedupe(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def to_area_record(title: str, wikitext: str, source_url: str = "",
                   license: str = "", attribution: str = "",
                   max_chars: int = 1500) -> dict:
    """Convert one area/location page's wikitext into a Location-shaped JSON record.

    The returned dict mirrors the engine's `Location` fields that `seed_world` reads:
    name, description, region (parent zone), connections (place-name hints), plus tags
    and per-source license/attribution. Connections are NAMES (not ids) — `seed_world`
    resolves them by name→id, leaving unresolved ones as hints.
    """
    # Drop HTML comments up front (a commented-out }} would confuse infobox span-matching).
    wikitext = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    box = _parse_infobox(wikitext)
    sections = _split_sections(wikitext)

    region = _strip_inline(_infobox_value(box, _REGION_KEYS))

    # Description: prefer the matching section bodies; fall back to the lead paragraph
    # (the lead is usually a solid one-paragraph summary of the place).
    desc_chunks: list[str] = []
    connection_section_text: list[str] = []
    lead_body = ""
    for heading, body in sections:
        if heading == "":
            lead_body = body
            continue
        h = _norm_heading(heading)
        if h in _DESC_HEADINGS:
            cleaned = _clean_block(body, max_chars)
            if cleaned:
                desc_chunks.append(cleaned)
        if h in _CONNECTION_HEADINGS:
            connection_section_text.append(body)

    if desc_chunks:
        description = _clean_block("\n\n".join(desc_chunks), max_chars)
    else:
        description = _clean_block(lead_body, max_chars)

    # Connections: infobox adjacency keys first (most reliable), then links in any
    # connection/geography section, then a bounded set of links from the lead paragraph
    # (the lead routinely names the places a location sits between). All kept as NAMES.
    connections: list[str] = list(_infobox_connection_names(box))
    for body in connection_section_text:
        connections.extend(_link_names(body))
    if lead_body:
        connections.extend(_link_names(lead_body, max_items=12))
    # Never list the page itself as its own connection.
    self_name = (_strip_inline(title) or title).strip().lower()
    connections = [c for c in _dedupe(connections) if c.strip().lower() != self_name]

    # Tags: the page's categories (lightweight, for the DM/notes) — bounded + cleaned.
    tags: list[str] = []
    for m in re.finditer(r"\[\[Category:([^\]\|]+)", wikitext):
        tag = _strip_inline(m.group(1))
        if tag and not _is_junk_name(tag):
            tags.append(tag)
    tags = _dedupe(tags)[:12]

    return {
        "name": _strip_inline(title) or title,
        "description": description,
        "region": region,
        "connections": connections,
        "tags": tags,
        "source_url": source_url,
        "license": license,
        "attribution": attribution,
    }


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title.lower()).strip("-")
    return (s or "area")[:80]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default=str(_HERE / "manifest_areas.json"))
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    world_id = manifest["world_id"]
    subdir = str(manifest.get("cache_subdir", "areas"))
    max_chars = int(manifest.get("max_chars_per_field", 1500))

    cache = _HERE / ".cache" / world_id / subdir
    if not cache.is_dir():
        print(f"[to_areas] no cache at {cache} — run wiki_fetch.py {Path(args.manifest).name} first",
              file=sys.stderr)
        return 1

    out_dir = _REPO / "content" / "worlds" / world_id / "areas"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for cf in sorted(cache.glob("*.json")):
        page = json.loads(cf.read_text(encoding="utf-8"))
        rec = to_area_record(
            page["title"], page.get("wikitext", ""), page.get("source_url", ""),
            page.get("license", ""), page.get("attribution", ""), max_chars,
        )
        # Skip records that cleaned to essentially nothing (redirects/stubs) — without a
        # description they're not a usable place.
        if len(rec["description"]) < 40:
            print(f"  [skip] {page['title']}: no usable description")
            continue
        (out_dir / f"{_slug(page['title'])}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written += 1
    print(f"[to_areas] wrote {written} area records → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
