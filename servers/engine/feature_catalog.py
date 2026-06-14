"""Feature catalog: full SRD 5.2 rules text for class + subclass features.

Pure module (no MCP, no campaign I/O). Mirrors ``itemcatalog`` — it reads the
vendored SRD 5.2.1 dump (``data/srd/srd524/ClassFeature.json``, the Open5e
srd-2024 fixtures, CC-BY-4.0) and exposes each class/subclass feature's *full
rules text* by name so the viewer can show the complete rules on click-through
(the RRI-25e55fa optimizer's #1 min-maxer pain point: "every feature is static
text with no click-through to full rules text").

The curated ``data/srd/class_features.json`` table the engine levels from carries
only TERSE one-line descriptions (what the sheet shows inline); the rich, multi-
paragraph SRD rules text lives only in this srd524 dump. This module surfaces it
read-only — it never authors content, and a feature the dump doesn't carry simply
isn't found (the caller falls back to the curated short desc, never a fabrication).

Each ClassFeature record is a Django fixture ``{model, pk, fields}`` whose
``fields`` carry ``name``, ``desc`` (the full rules text) and ``parent`` — the
owning class or subclass slug (``srd-2024_fighter`` / ``srd-2024_champion``).
``CharacterClass.json`` maps a subclass slug to its parent class via
``subclass_of`` so a lookup can resolve "Champion" -> fighter.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2] / "data" / "srd"
_PRIMARY = _ROOT / "srd524"  # canonical SRD 5.2


def _dirs() -> list[Path]:
    """Feature-data dirs in PRECEDENCE order: srd524 first (canonical), then any
    later pack under data/srd/ that carries a ClassFeature.json (first-wins, exactly
    like itemcatalog._dirs)."""
    dirs = [_PRIMARY]
    if _ROOT.is_dir():
        for sub in sorted(_ROOT.iterdir()):
            if sub.is_dir() and sub != _PRIMARY:
                dirs.append(sub)
    return [d for d in dirs if (d / "ClassFeature.json").exists()]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _slug(name: str) -> str:
    """Lowercased, hyphenated slug tail of a class/subclass name ('Champion' ->
    'champion', 'Circle of the Land' -> 'circle-of-the-land') — how the srd524 pk
    encodes an owner. Matches the SRD pk convention so a class/subclass name resolves
    to its ``srd-2024_<slug>`` owner key."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


@functools.lru_cache(maxsize=None)
def _subclass_parent() -> dict[str, str]:
    """{subclass_slug: parent_class_slug} from CharacterClass.json's ``subclass_of``
    (e.g. 'champion' -> 'fighter'). Lets a subclass-feature lookup fall back to its
    parent class, and lets the class lookup know which subclass slugs belong to it."""
    out: dict[str, str] = {}
    for d in _dirs():
        for r in _rows(d / "CharacterClass.json"):
            if not isinstance(r, dict):
                continue
            f = r.get("fields") or {}
            sub_of = f.get("subclass_of")
            pk = r.get("pk") or ""
            if sub_of and isinstance(pk, str):
                sub_slug = pk.split("_", 1)[-1]
                parent_slug = str(sub_of).split("_", 1)[-1]
                out.setdefault(sub_slug, parent_slug)
    return out


def _owner_slug(pk: str) -> str:
    """The owner slug from a feature record's ``parent`` pk ('srd-2024_champion' ->
    'champion'). Tolerates a bare slug already."""
    s = str(pk or "")
    return s.split("_", 1)[-1] if "_" in s else s


@functools.lru_cache(maxsize=None)
def _index() -> dict:
    """Build the read indices once.

    Returns a dict with:
      - ``by_owner``: {owner_slug: [ {name, desc, owner} , … ]} in first-seen order
        (owner_slug is the class or subclass slug from the feature's parent pk).
      - ``by_owner_name``: {(owner_slug, name_lower): {name, desc, owner}} — the exact
        owner+name lookup.
    First-wins across dirs (srd524 canonical) and within a dir (a later duplicate
    name for the same owner is skipped)."""
    by_owner: dict[str, list[dict]] = {}
    by_owner_name: dict[tuple, dict] = {}
    for d in _dirs():
        for r in _rows(d / "ClassFeature.json"):
            if not isinstance(r, dict):
                continue
            f = r.get("fields") or {}
            name = str(f.get("name") or "").strip()
            desc = str(f.get("desc") or "").strip()
            owner = _owner_slug(f.get("parent") or "")
            if not name or not owner:
                continue
            key = (owner, name.lower())
            if key in by_owner_name:
                continue  # first-wins
            rec = {"name": name, "desc": desc, "owner": owner}
            by_owner_name[key] = rec
            by_owner.setdefault(owner, []).append(rec)
    return {"by_owner": by_owner, "by_owner_name": by_owner_name}


def count() -> int:
    """Total distinct (owner, feature) entries in the catalog."""
    return len(_index()["by_owner_name"])


def features_for(owner: str) -> list[dict]:
    """All SRD features owned by a class OR subclass NAME (e.g. 'fighter', 'Champion'),
    each ``{name, desc, owner}`` with FULL rules text. Empty if the owner is unknown.
    COPIES each record so a caller can't mutate the cache."""
    slug = _slug(owner)
    out = list(_index()["by_owner"].get(slug, []))
    return [dict(r) for r in out]


def lookup(owner: str, feature_name: str) -> Optional[dict]:
    """The full-rules-text record for a feature on a class/subclass by NAME, or None.

    Resolves ``owner`` as a class or subclass name. If the owner is a SUBCLASS and the
    feature isn't found on it, falls back to the subclass's PARENT class (a Champion's
    'Action Surge' lives on fighter). Returns a COPY ``{name, desc, owner}`` or None when
    neither the owner nor (for a subclass) its parent carries the feature — never a
    fabricated entry."""
    if not feature_name:
        return None
    name_key = feature_name.strip().lower()
    idx = _index()["by_owner_name"]
    slug = _slug(owner)
    rec = idx.get((slug, name_key))
    if rec is None:
        parent = _subclass_parent().get(slug)
        if parent:
            rec = idx.get((parent, name_key))
    return dict(rec) if rec is not None else None


def lookup_any(feature_name: str, class_hints: "tuple[str, ...]" = ()) -> Optional[dict]:
    """Resolve a feature's full rules text by NAME, preferring the owners in
    ``class_hints`` (a character's class/subclass names) so a name shared across
    classes (e.g. 'Extra Attack', 'Spellcasting') resolves to the right one. Falls back
    to the FIRST owner that carries the name when no hint matches. Returns a COPY or
    None. HONEST about ambiguity only in the no-hint case — with a class hint it is exact."""
    if not feature_name:
        return None
    for owner in class_hints:
        rec = lookup(owner, feature_name)
        if rec is not None:
            return rec
    name_key = feature_name.strip().lower()
    for (owner, nk), rec in _index()["by_owner_name"].items():
        if nk == name_key:
            return dict(rec)
    return None
