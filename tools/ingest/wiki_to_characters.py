#!/usr/bin/env python3
"""Stage 2 (characters) of the ingestion pipeline: raw wiki cache → clean NPC JSON records.

Reads the cache written by `wiki_fetch.py` (the `characters/` subdir) and turns each
cached character page's wikitext into a clean JSON record under
`content/worlds/<world_id>/characters/<slug>.json`, which ClawDnD can later load as an
NPC of the "baldurs-gate" world.

Each record has: name, race, class, level, alignment, appearance, personality,
mannerisms, backstory, equipment (list), relationships (list), voice_hint, source_url,
license, attribution. Wikitext/templates/refs are stripped using the SAME cleaning
helpers as the lore stage (`wiki_to_lore._render_wikilinks` / `_remove_balanced`), but
the infobox is PARSED first (it holds the structured race/class/alignment data) instead
of being thrown away.

TEXT/DATA ONLY. No images are read or written (portraits are handled separately,
local-only). The license/attribution carried per-source by the fetcher is stamped onto
each record verbatim, so bg3.wiki (dual CC BY-SA / CC BY-NC-SA) and the Forgotten Realms
Wiki (Fandom, CC-BY-SA) are each attributed correctly.

Pure, stdlib only. `parse_character()` is unit-tested on a wikitext fixture.

Usage:  python3 tools/ingest/wiki_to_characters.py [manifest_characters.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

# Reuse the lore stage's wikitext-cleaning primitives (no side effects on import; its
# main() is guarded by __main__). This keeps one source of truth for link/template handling.
sys.path.insert(0, str(_HERE))
from wiki_to_lore import _remove_balanced, _render_wikilinks  # noqa: E402


# ── infobox keys → record fields ────────────────────────────────────────────────────
# bg3.wiki and the FR wiki both put structured data in an infobox ({{...}} with | key =
# value rows). Map the keys we care about (case/space/underscore-insensitive) to fields.
_INFOBOX_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "race": ("race", "species", "ancestry"),
    "class": ("class", "classes"),
    "level": ("level",),
    "alignment": ("alignment",),
    "voice_actor": ("voiceactor", "voice", "voicedby", "va"),
}
# Section heading (normalized: lowercase, collapsed spaces) → record field, matched by
# EQUALITY. Equality (not substring) is deliberate: bg3.wiki has walkthrough headings
# like "As an origin character" that a loose "character" substring would wrongly slurp
# into personality. Multiple matching headings feed the same field (joined in order).
_SECTION_FIELD_KEYS: dict[str, frozenset[str]] = {
    "personality": frozenset({"personality", "characterisation", "characterization"}),
    "appearance": frozenset({"appearance", "description", "physical description"}),
    "backstory": frozenset({"biography", "background", "backstory", "history",
                            "early life", "early adventures"}),
    "mannerisms": frozenset({"mannerisms", "behaviour", "behavior", "quirks", "demeanor",
                             "demeanour"}),
}


# Inline templates whose LAST argument is display text worth keeping. Two kinds:
#  - text-formatting: {{nowrap|200 years}} → "200 years"
#  - LINK wrappers (very common on bg3.wiki): {{CharLink|Arnell Hallowleaf|Arnell}} →
#    "Arnell", {{Quest|The Chosen of Shar}} → "The Chosen of Shar", {{deity|Selûne|
#    Selûne's}} → "Selûne's". Without this they'd be deleted wholesale and proper nouns
#    would vanish from the prose. Everything else ({{Infobox}}, {{cite}}, {{ref}}) is
#    dropped by _remove_balanced, like the lore stage does.
_TEXT_TEMPLATES = (
    # text formatting
    "nowrap", "w", "small", "nobr", "lang", "nihongo", "abbr", "tooltip",
    # bg3.wiki / FR link wrappers (last pipe arg is the display text)
    "charlink", "quest", "deity", "link", "pagelink", "iconlink", "itemlink",
    "spelllink", "loclink", "creaturelink", "classlink", "racelink", "actionlink",
)


def _unwrap_text_templates(s: str) -> str:
    """Replace `{{tmpl|…|text}}` with `text` for known text-formatting templates.

    Depth-aware so nested templates resolve inside-out; non-allowlisted templates are
    left intact for _remove_balanced to strip. Repeats until stable (handles nesting).
    """
    for _ in range(5):  # bounded fixed-point; 5 levels of nesting is plenty
        out: list[str] = []
        i, n, changed = 0, len(s), False
        while i < n:
            if s.startswith("{{", i):
                depth, j = 1, i + 2
                while j < n and depth > 0:
                    if s.startswith("{{", j):
                        depth += 1; j += 2
                    elif s.startswith("}}", j):
                        depth -= 1; j += 2
                    else:
                        j += 1
                inner = s[i + 2:j - 2]
                name = re.sub(r"[\s_]+", "", inner.split("|", 1)[0].strip().lower())
                if name in _TEXT_TEMPLATES and "|" in inner:
                    # Last POSITIONAL arg is the display text; skip trailing key=value
                    # params (e.g. {{ItemLink|Sword|icon=yes}} → "Sword").
                    pos = [a for a in inner.split("|")[1:] if "=" not in a]
                    out.append(pos[-1] if pos else inner.split("|")[-1])
                    changed = True
                else:
                    out.append(s[i:j])
                i = j
            else:
                out.append(s[i]); i += 1
        s = "".join(out)
        if not changed:
            break
    return s


def _strip_inline(s: str) -> str:
    """Render links + drop refs/templates/html from a short INLINE value (no markdown)."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = _unwrap_text_templates(s)
    s = _remove_balanced(s, "{{", "}}")
    s = _render_wikilinks(s)
    s = re.sub(r"'''?(.+?)'''?", r"\1", s)          # drop bold/italic emphasis markers
    s = re.sub(r"<[^>]+>", " ", s)                   # stray html (<br/>, <small>…)
    s = re.sub(r"\s+", " ", s).strip(" \t\n*-—–:;,")
    return s


def _clean_block(s: str, max_chars: int) -> str:
    """Render a multi-line prose BLOCK (a section body) to clean plain text."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<gallery[^>]*>.*?</gallery>", "", s, flags=re.S)
    s = _unwrap_text_templates(s)
    s = _remove_balanced(s, "{{", "}}")
    s = _remove_balanced(s, "{|", "|}")
    s = _render_wikilinks(s)
    s = re.sub(r"'''(.+?)'''", r"\1", s)
    s = re.sub(r"''(.+?)''", r"\1", s)
    s = re.sub(r"^[*#:;]+\s*", "", s, flags=re.M)    # drop list/indent markers
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n\n", s).strip()
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(" ", 1)[0].rstrip() + " …(truncated — see source)"
    return s


# Template names that ARE the character infobox (the one holding race/class/etc.).
# bg3.wiki uses {{Infobox creature}}; the FR wiki uses {{Person}} (or {{Infobox …}}).
# Matched case-insensitively; "infobox" as a prefix also matches. NON-infobox lead
# templates ({{PageSeo}}, {{Companion tab}}, {{GA}}) are skipped so we read the RIGHT box.
_INFOBOX_NAMES = ("person", "infobox character", "infobox creature", "infobox npc",
                  "infobox person", "character infobox", "creature infobox")


def _find_infobox_span(wikitext: str) -> tuple[int, int] | None:
    """Return (start, end) byte span of the character infobox `{{…}}`, or None.

    Scans top-level templates, picks the first whose name matches `_INFOBOX_NAMES` or
    starts with "infobox"; falls back to the LARGEST top-level template (infoboxes dwarf
    inline ones) if no name matches.
    """
    i, n = 0, len(wikitext)
    best: tuple[int, int] | None = None  # largest-template fallback
    while i < n:
        if wikitext.startswith("{{", i):
            depth, j = 1, i + 2
            while j < n and depth > 0:
                if wikitext.startswith("{{", j):
                    depth += 1; j += 2
                elif wikitext.startswith("}}", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            inner = wikitext[i + 2:j - 2]
            name = inner.split("|", 1)[0].strip().lower()
            if name in _INFOBOX_NAMES or name.startswith("infobox"):
                return (i, j)
            if best is None or (j - i) > (best[1] - best[0]):
                best = (i, j)
            i = j
        else:
            i += 1
    return best


def _parse_infobox(wikitext: str) -> dict[str, str]:
    """Pull `| key = value` pairs out of the character infobox template into a flat dict.

    Splits the infobox body on pipes that are at template depth 0 (so a `{{template}}` or
    `[[link|text]]` inside a value doesn't split it), then on the first '=' per row.
    """
    # Drop HTML comments first — a commented-out block can contain stray }} that would
    # confuse span-matching (Astarion's page has exactly this).
    wikitext = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    span = _find_infobox_span(wikitext)
    if span is None:
        return {}
    start, end = span
    body = wikitext[start + 2:end - 2]

    # Split on top-level pipes (not inside {{…}} / [[…]] / {|…|}).
    rows: list[str] = []
    buf: list[str] = []
    td = lb = 0
    j, m = 0, len(body)
    while j < m:
        two = body[j:j + 2]
        if two == "{{" or two == "{|":
            td += 1; buf.append(two); j += 2
        elif two == "}}" or two == "|}":
            td = max(0, td - 1); buf.append(two); j += 2
        elif two == "[[":
            lb += 1; buf.append(two); j += 2
        elif two == "]]":
            lb = max(0, lb - 1); buf.append(two); j += 2
        elif body[j] == "|" and td == 0 and lb == 0:
            rows.append("".join(buf)); buf = []; j += 1
        else:
            buf.append(body[j]); j += 1
    rows.append("".join(buf))

    out: dict[str, str] = {}
    for row in rows:
        if "=" not in row:
            continue
        k, v = row.split("=", 1)
        key = re.sub(r"[\s_]+", "", k.strip().lower())
        val = _clean_infobox_value(v)
        if key and val:
            out[key] = val
    return out


def _clean_infobox_value(v: str) -> str:
    """Clean one infobox value: drop galleries, unwrap {{Class table}}, then inline-clean."""
    v = re.sub(r"<gallery[^>]*>.*?</gallery>", "", v, flags=re.S)
    v = re.sub(r"<br\s*/?>", ", ", v)
    # {{Class table|edition=5e|[[Ranger]] ([[Hunter]])|6}} → keep the class-name args,
    # drop edition= / bare-number level args (level lives in its own field).
    def _classtable(m: re.Match) -> str:
        args = m.group(1).split("|")[1:]  # drop the template name
        keep = [a for a in args if "=" not in a and not a.strip().isdigit()]
        return " ".join(keep)
    v = re.sub(r"\{\{\s*[Cc]lass table\b(.*?)\}\}", _classtable, v, flags=re.S)
    return _strip_inline(v)


# Edition suffixes the FR-wiki {{Person}} template appends (prefer the newest, 5e).
_EDITIONS = ("5e", "35", "3e", "2e", "1e", "4e")


def _infobox_field(box: dict[str, str], aliases: tuple[str, ...]) -> str:
    for a in aliases:
        norm = re.sub(r"[\s_]+", "", a.lower())
        if box.get(norm):
            return box[norm]
        # FR {{Person}} uses edition-suffixed keys (alignment5e, class2e, …); prefer 5e.
        for ed in _EDITIONS:
            if box.get(norm + ed):
                return box[norm + ed]
    return ""


def _split_sections(wikitext: str) -> list[tuple[str, str]]:
    """Split wikitext into (heading, body) sections on == … == headers (any level).

    The text before the first heading is the lead, returned with heading "" (empty).
    """
    # Normalize === / ==== down for matching but keep the heading text.
    parts = re.split(r"^=+\s*(.+?)\s*=+\s*$", wikitext, flags=re.M)
    sections: list[tuple[str, str]] = [("", parts[0])]
    for k in range(1, len(parts), 2):
        heading = parts[k].strip()
        body = parts[k + 1] if k + 1 < len(parts) else ""
        sections.append((heading, body))
    return sections


def _extract_list(section_body: str, max_items: int = 25) -> list[str]:
    """Pull bullet items (`* …`) from a section body into a clean string list."""
    items: list[str] = []
    for line in section_body.splitlines():
        m = re.match(r"^[*#]+\s*(.+)$", line.strip())
        if not m:
            continue
        item = _strip_inline(m.group(1))
        if item and not _is_junk_item(item):
            items.append(item)
        if len(items) >= max_items:
            break
    return items


# Headings (normalized: lowercase, spaces collapsed) whose bullets are equipment, and
# whose bullets are relationships. Matched by EQUALITY, not substring, so "Equipment
# proficiencies" (a Gameplay subsection) does NOT count as equipment.
_EQUIP_HEADINGS = frozenset({"equipment", "inventory", "gear", "starting equipment",
                             "starting gear", "loadout"})
_REL_HEADINGS = frozenset({"relationships", "relations", "associations", "allies"})


def _norm_heading(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower())


# Reject list items that are internal asset IDs or leftover markup, not real content.
def _is_junk_item(item: str) -> bool:
    if len(item) <= 2:
        return True
    if re.fullmatch(r"[A-Za-z]*\d+", item):          # x2, EQ123-style
        return True
    if re.fullmatch(r"[A-Z][A-Za-z]*_[A-Za-z0-9_]+", item):  # EQ_Astarion internal IDs
        return True
    if not re.search(r"[A-Za-z]", item):              # pure punctuation / "}}"
        return True
    return False


def _voice_hint(race: str, cls: str, personality: str, voice_actor: str) -> str:
    """A short, model-facing hint for how the NPC should SOUND.

    Heuristic (the wikis carry no canonical 'voice' field): combine race/class register
    with one personality adjective, and note the real-world voice actor if the infobox
    gave one. Kept terse — it's a nudge for the DM/TTS layer, not lore.
    """
    bits: list[str] = []
    reg = " ".join(x for x in (race, cls) if x).strip()
    if reg:
        bits.append(reg.lower())
    p = personality.lower()
    for adj in ("dry", "wry", "sardonic", "fierce", "warm", "guarded", "theatrical",
                "booming", "weary", "haughty", "gentle", "cold", "playful", "grim",
                "earnest", "vain", "melancholy", "commanding"):
        if adj in p:
            bits.append(adj)
            break
    hint = "; ".join(bits) if bits else "even, characterful"
    if voice_actor:
        hint += f" (voiced by {voice_actor})"
    return hint[:200]


def parse_character(title: str, wikitext: str, source_url: str = "",
                    license: str = "", attribution: str = "",
                    max_chars: int = 2500) -> dict:
    """Convert one character page's wikitext into a clean NPC JSON record (dict)."""
    box = _parse_infobox(wikitext)
    sections = _split_sections(wikitext)

    # Bucket section bodies by target field.
    field_text: dict[str, list[str]] = {}
    equipment: list[str] = []
    relationships: list[str] = []
    lead_body = ""
    for heading, body in sections:
        if heading == "":
            lead_body = body
            continue
        h = _norm_heading(heading)
        if h in _EQUIP_HEADINGS:
            equipment.extend(_extract_list(body))
        if h in _REL_HEADINGS:
            items = _extract_list(body)
            if items:
                relationships.extend(items)
            else:
                # No bullets (FR "Relationships" sections are prose) — split the cleaned
                # prose into sentence-ish entries so the field stays a useful list.
                prose = _clean_block(body, max_chars)
                for sent in re.split(r"(?<=[.!?])\s+", prose):
                    sent = sent.strip()
                    if len(sent) > 15:
                        relationships.append(sent)
                    if len(relationships) >= 12:
                        break
        for field, aliases in _SECTION_FIELD_KEYS.items():
            if h in aliases:
                cleaned = _clean_block(body, max_chars)
                if cleaned:
                    field_text.setdefault(field, []).append(cleaned)

    def _joined(field: str) -> str:
        chunks = field_text.get(field, [])
        return _clean_block("\n\n".join(chunks), max_chars) if chunks else ""

    race = _infobox_field(box, _INFOBOX_FIELD_KEYS["race"])
    cls = _infobox_field(box, _INFOBOX_FIELD_KEYS["class"])
    level = _infobox_field(box, _INFOBOX_FIELD_KEYS["level"])
    alignment = _infobox_field(box, _INFOBOX_FIELD_KEYS["alignment"])
    voice_actor = _infobox_field(box, _INFOBOX_FIELD_KEYS["voice_actor"])

    personality = _joined("personality")
    appearance = _joined("appearance")
    backstory = _joined("backstory")
    mannerisms = _joined("mannerisms")

    # Fall back to the lead paragraph for backstory if no biography/background section
    # produced anything — the lead is usually a solid one-paragraph summary.
    if not backstory and lead_body:
        backstory = _clean_block(lead_body, max_chars)

    # De-dupe list items (preserve order).
    def _dedupe(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            key = x.lower()
            if key not in seen:
                seen.add(key)
                out.append(x)
        return out

    return {
        "name": _strip_inline(title) or title,
        "race": race,
        "class": cls,
        "level": level,
        "alignment": alignment,
        "appearance": appearance,
        "personality": personality,
        "mannerisms": mannerisms,
        "backstory": backstory,
        "equipment": _dedupe(equipment),
        "relationships": _dedupe(relationships),
        "voice_hint": _voice_hint(race, cls, personality, voice_actor),
        "source_url": source_url,
        "license": license,
        "attribution": attribution,
    }


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title.lower()).strip("-")
    return (s or "character")[:80]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default=str(_HERE / "manifest_characters.json"))
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    world_id = manifest["world_id"]
    subdir = str(manifest.get("cache_subdir", "characters"))
    max_chars = int(manifest.get("max_chars_per_field", 2500))

    cache = _HERE / ".cache" / world_id / subdir
    if not cache.is_dir():
        print(f"[to_chars] no cache at {cache} — run wiki_fetch.py {Path(args.manifest).name} first",
              file=sys.stderr)
        return 1

    out_dir = _REPO / "content" / "worlds" / world_id / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for cf in sorted(cache.glob("*.json")):
        page = json.loads(cf.read_text(encoding="utf-8"))
        rec = parse_character(
            page["title"], page.get("wikitext", ""), page.get("source_url", ""),
            page.get("license", ""), page.get("attribution", ""), max_chars,
        )
        # Skip records that cleaned to essentially nothing (redirects/stubs).
        if not rec["backstory"] and not rec["personality"] and not rec["appearance"]:
            print(f"  [skip] {page['title']}: no usable profile text")
            continue
        (out_dir / f"{_slug(page['title'])}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written += 1
    print(f"[to_chars] wrote {written} character records → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
