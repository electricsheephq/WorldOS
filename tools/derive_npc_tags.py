#!/usr/bin/env python3
"""Derive STRUCTURED NPC tags onto the canon character JSONs — surgically, high-confidence only.

The 2,076 canon character records under content/worlds/baldurs-gate/characters/ are 99% empty
for categorical fields, so the engine can't pull "the merchant in this region" / "this Harper"
structurally. Hand-editing 2,076 files is a non-starter; this script DERIVES a meaningful slice
from text the records already carry (backstory / personality / class), writing only the new
additive tagging fields the Character model now defines:

    tags, faction_id, is_merchant, canon_location_id, arc_role, quest_ties

DISCIPLINE (the owner's "only set what you can derive with HIGH confidence"):
  * MERCHANT: a clear trade cue in backstory/personality/class ("merchant", "quartermaster",
    "trader", "shopkeeper", "vendor", "proprietor", "sells …", "wares") -> is_merchant=True + the
    "merchant" tag. (A passing "the merchant district" mention is rare in these bios and is an
    acceptable, conservative over-tag — it still surfaces a plausibly-commercial NPC.)
  * FACTION: an unambiguous membership phrasing for the three load-bearing BG3 factions. In this
    corpus the bare faction nouns ARE membership signals ("X is a Flaming Fist", "a Harper
    quartermaster", "a Zhentarim agent") — calibrated against the actual text. We map:
        "harper"/"harpers"            -> harpers
        "flaming fist"                -> flaming-fist
        "zhentarim"/"black network"   -> zhentarim
  * ORIGIN HEROES: the 7 BG3 origin companions -> arc_role="origin-hero" + the "companion" tag.

It does NOT invent locations or quest ties (no high-confidence signal in the bios), so those stay
empty. It is ADDITIVE + IDEMPOTENT: it only ADDS/overwrites these specific derived fields with the
SAME value on a re-run, never removes other data, and writes a file only when something changed.
No long copied wiki prose is added — tags are short tokens.

Usage:
    python3 tools/derive_npc_tags.py            # apply, print a stats report
    python3 tools/derive_npc_tags.py --dry-run  # report only, write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

CHARS_DIR = Path(__file__).resolve().parents[1] / "content" / "worlds" / "baldurs-gate" / "characters"

# The 7 BG3 origin companions (file slugs). arc_role="origin-hero" + tag "companion".
ORIGIN_HEROES = {"astarion", "gale", "karlach", "lae-zel", "shadowheart", "wyll", "halsin"}

# High-confidence merchant cues (word-boundaried where a substring would over-match).
_MERCHANT_PATTERNS = [
    r"\bmerchant\b",
    r"\bquartermaster\b",
    r"\btrader\b",
    r"\bshopkeeper\b",
    r"\bvendor\b",
    r"\bproprietor\b",
    r"\bsells\b",
    r"\bwares\b",
]
_MERCHANT_RE = re.compile("|".join(_MERCHANT_PATTERNS), re.IGNORECASE)

# Faction membership cues -> canonical faction key. Order is irrelevant (a record could match
# more than one; the FIRST match in this list wins so the tagging is deterministic).
_FACTION_CUES: list[tuple[str, re.Pattern]] = [
    ("harpers", re.compile(r"\bharpers?\b", re.IGNORECASE)),
    ("flaming-fist", re.compile(r"flaming fist", re.IGNORECASE)),
    ("zhentarim", re.compile(r"zhentarim|black network", re.IGNORECASE)),
]


def _text_blob(rec: dict) -> str:
    """The fields we read for derivation — bio prose + class. Joined lowercase."""
    return " ".join(
        str(rec.get(k, "") or "") for k in ("backstory", "personality", "class", "appearance")
    )


def _derive(rec: dict, slug: str) -> dict:
    """Return the derived tagging fields for one record (only the keys we can set with
    confidence). Mutates nothing; the caller merges + decides whether the file changed."""
    blob = _text_blob(rec)
    derived: dict = {}
    tags: list[str] = []

    # Merchant.
    if _MERCHANT_RE.search(blob):
        derived["is_merchant"] = True
        tags.append("merchant")

    # Faction (first matching cue wins).
    for key, pat in _FACTION_CUES:
        if pat.search(blob):
            derived["faction_id"] = key
            break

    # Origin heroes (by file slug — the canonical handle).
    if slug in ORIGIN_HEROES:
        derived["arc_role"] = "origin-hero"
        if "companion" not in tags:
            tags.append("companion")

    if tags:
        derived["tags"] = tags
    return derived


def _merge(rec: dict, derived: dict) -> bool:
    """Merge `derived` into `rec` IN PLACE. Returns True if anything actually changed.

    `tags` is UNION-merged (preserve any existing/hand-authored tags, add derived ones without
    duplicating); scalar fields are set only when we derived a value (we never blank a field the
    content author set). Idempotent: a second run with the same derivation reports no change."""
    changed = False

    # tags: union, order-stable (existing first, then new derived ones).
    new_tags = derived.get("tags", [])
    if new_tags:
        existing = list(rec.get("tags") or [])
        merged = list(existing)
        for t in new_tags:
            if t not in merged:
                merged.append(t)
        if merged != existing:
            rec["tags"] = merged
            changed = True

    for field in ("faction_id", "is_merchant", "arc_role"):
        if field in derived and rec.get(field) != derived[field]:
            rec[field] = derived[field]
            changed = True

    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument(
        "--dir", default=str(CHARS_DIR), help="characters dir (default: BG canon characters)"
    )
    args = ap.parse_args(argv)

    cdir = Path(args.dir)
    if not cdir.is_dir():
        print(f"error: no characters dir at {cdir}", file=sys.stderr)
        return 2

    files = sorted(cdir.glob("*.json"))
    stats = Counter()
    tag_counts: Counter = Counter()
    faction_counts: Counter = Counter()
    written = 0

    for p in files:
        stats["scanned"] += 1
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            stats["unreadable"] += 1
            print(f"  skip {p.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(rec, dict):
            stats["unreadable"] += 1
            continue

        derived = _derive(rec, p.stem.lower())
        if not derived:
            continue

        # Tally what we'd set (independent of whether the file already had it — this is the
        # "how many got each tag" report the owner asked for).
        if derived.get("is_merchant"):
            stats["merchant"] += 1
        if derived.get("faction_id"):
            faction_counts[derived["faction_id"]] += 1
            stats["faction_any"] += 1
        if derived.get("arc_role") == "origin-hero":
            stats["origin_hero"] += 1
        for t in derived.get("tags", []):
            tag_counts[t] += 1

        if _merge(rec, derived):
            stats["changed"] += 1
            if not args.dry_run:
                # Compact, stable formatting (2-space indent, trailing newline) consistent with the
                # existing canon JSONs; ensure_ascii=False keeps accented names (Lae'zel) intact.
                p.write_text(
                    json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                written += 1

    print("=== derive_npc_tags report ===")
    print(f"scanned:        {stats['scanned']}")
    print(f"unreadable:     {stats['unreadable']}")
    print(f"merchant:       {stats['merchant']}  (is_merchant=True + tag 'merchant')")
    print(f"faction (any):  {stats['faction_any']}")
    for fac, n in sorted(faction_counts.items()):
        print(f"    {fac:<14} {n}")
    print(f"origin-hero:    {stats['origin_hero']}  (arc_role='origin-hero' + tag 'companion')")
    print("tag totals:")
    for t, n in sorted(tag_counts.items()):
        print(f"    {t:<14} {n}")
    print(f"files {'WOULD change' if args.dry_run else 'changed'}: {stats['changed']}")
    if not args.dry_run:
        print(f"files written:  {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
