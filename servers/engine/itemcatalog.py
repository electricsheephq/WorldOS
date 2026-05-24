"""Item catalog: grant REAL SRD gear by name from the bundled SRD 5.2.1 data.

Pure module (no MCP, no campaign I/O). It reads the vendored SRD item dump —
``data/srd/srd524/{MagicItem,Item,Weapon,Armor}.json`` (the Open5e srd-2024
fixtures, CC-BY-4.0) — and flattens every record into one common, engine-shaped
item dict so the DM can hand a party a "Bag of Holding" or a "Longsword" straight
from data instead of free-texting name + description every time (the same
consistency gap the bestiary closed for monsters).

Each source is a Django fixture (``{model, pk, fields}``). The four shapes differ:

* ``MagicItem`` / ``Item`` carry the rich fields (``category``, ``cost``,
  ``rarity``, ``requires_attunement``, ``desc``, ``weight``) and an optional
  ``weapon`` / ``armor`` FK string (e.g. ``"srd-2024_glaive"``) that points at a
  ``Weapon.json`` / ``Armor.json`` ``pk``. Damage / AC are NOT stored inline —
  they come from joining that FK.
* ``Weapon`` carries ``damage_dice`` / ``damage_type`` (no cost/desc).
* ``Armor`` carries ``ac_base`` (+ dex-mod rules; no cost/desc).

So a magic weapon's damage and a magic armor's AC are recovered by FK-joining to
the Weapon/Armor tables; the bare Weapon/Armor records also enter the catalog so a
mundane "Longsword" or "Plate Armor" still resolves with damage/ac present.

Precedence is multi-dir, first-wins (exactly like ``bestiary._dirs``): srd524
first, then any later pack under ``data/srd/`` only fills gaps — it never
overrides an SRD item of the same name. Within srd524 the file precedence is
MagicItem -> Item -> Weapon -> Armor, so the magic variant of a name wins over the
mundane one, and the mundane Item entry (which has cost + the weapon/armor FK)
wins over the bare Weapon/Armor combat-stat record.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2] / "data" / "srd"
_PRIMARY = _ROOT / "srd524"  # canonical SRD 5.2 — always wins a name collision

# File precedence WITHIN a dir. MagicItem first (a magic "Longsword" beats the
# mundane one); Item before Weapon/Armor (Item carries cost + the FK, so it
# resolves damage/ac too while keeping its price/weight/description).
_SOURCES = ("MagicItem.json", "Item.json", "Weapon.json", "Armor.json")

# category (open5e slug) -> the catalog's coarse `kind`. Anything unmapped keeps
# its slug (with '-item' trimmed and underscores normalized) so packs that add a
# new category degrade gracefully rather than vanishing.
_CATEGORY_KIND = {
    "weapon": "weapon",
    "armor": "armor",
    "wondrous-item": "wondrous",
    "ring": "ring",
    "rod": "rod",
    "scroll": "scroll",
    "staff": "staff",
    "wand": "wand",
    "potion": "potion",
    "adventuring-gear": "gear",
    "tools": "gear",
    "equipment-pack": "gear",
    "ammunition": "gear",
    "spellcasting-focus": "gear",
    "mount": "gear",
    "land-vehicle": "gear",
    "waterborne-vehicle": "gear",
}


def _dirs() -> list[Path]:
    """Item-data dirs in PRECEDENCE order: srd524 first (canonical), then any
    additional packs under data/srd/ (e.g. an ingested homebrew pack). Each later
    pack only fills gaps — it never overrides an SRD item of the same name
    (first-wins). Only dirs that actually carry at least one of the item sources
    are included."""
    dirs = [_PRIMARY]
    if _ROOT.is_dir():
        for sub in sorted(_ROOT.iterdir()):
            if sub.is_dir() and sub != _PRIMARY:
                dirs.append(sub)
    return [d for d in dirs if any((d / s).exists() for s in _SOURCES)]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _kind(category: Optional[str]) -> str:
    if not category:
        return "gear"
    slug = str(category).strip().lower()
    if slug in _CATEGORY_KIND:
        return _CATEGORY_KIND[slug]
    return slug.replace("-item", "").replace("-", "_") or "gear"


def _num(value, default: float = 0.0) -> float:
    """SRD stores cost/weight as 2-decimal strings ('25.00', '0.500'). Parse to a
    plain float; round to drop the fixture's trailing zeros."""
    if value in (None, ""):
        return default
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return default


@functools.lru_cache(maxsize=None)
def _weapon_armor_join() -> tuple[dict, dict]:
    """({weapon_pk: weapon_fields}, {armor_pk: armor_fields}) merged across all
    dirs (first-wins by pk) so a MagicItem/Item ``weapon``/``armor`` FK resolves to
    its damage / AC. Keyed by pk because that is what the FK stores."""
    weapons: dict[str, dict] = {}
    armors: dict[str, dict] = {}
    for d in _dirs():
        for r in _rows(d / "Weapon.json"):
            pk = r.get("pk")
            if pk and pk not in weapons:
                weapons[pk] = r.get("fields", {})
        for r in _rows(d / "Armor.json"):
            pk = r.get("pk")
            if pk and pk not in armors:
                armors[pk] = r.get("fields", {})
    return weapons, armors


def _flatten(model: str, fields: dict) -> dict:
    """Flatten one source record (any of the 4 shapes) into the common catalog
    item dict. ``model`` is the fixture model tail ('magicitem'/'item'/'weapon'/
    'armor') used to pick the right shape."""
    weapons, armors = _weapon_armor_join()
    name = fields.get("name", "")

    if model == "weapon":
        # Bare Weapon.json record: damage inline, no cost/desc/category.
        return {
            "name": name,
            "kind": "weapon",
            "rarity": "",
            "requires_attunement": False,
            "weight": _num(fields.get("weight")),
            "cost": _num(fields.get("cost")),
            "description": fields.get("desc", "") or "",
            "properties": [
                p for p, on in (("simple", fields.get("is_simple")),
                                ("improvised", fields.get("is_improvised"))) if on
            ],
            "damage": fields.get("damage_dice") or "",
            "damage_type": fields.get("damage_type") or "",
        }

    if model == "armor":
        # Bare Armor.json record: ac_base inline, no cost/desc/category.
        props = []
        if fields.get("grants_stealth_disadvantage"):
            props.append("stealth-disadvantage")
        if fields.get("strength_score_required"):
            props.append(f"str-{fields['strength_score_required']}")
        return {
            "name": name,
            "kind": "armor",
            "rarity": "",
            "requires_attunement": False,
            "weight": _num(fields.get("weight")),
            "cost": _num(fields.get("cost")),
            "description": fields.get("desc", "") or "",
            "properties": props,
            "ac": int(fields.get("ac_base") or 0),
        }

    # MagicItem / Item share a shape. Resolve damage/ac via the weapon/armor FK.
    kind = _kind(fields.get("category"))
    record = {
        "name": name,
        "kind": kind,
        "rarity": (fields.get("rarity") or "").strip(),
        "requires_attunement": bool(fields.get("requires_attunement")),
        "weight": _num(fields.get("weight")),
        "cost": _num(fields.get("cost")),
        "description": fields.get("desc", "") or "",
        "properties": [],
    }
    # NB: the open5e Item.json stamps a blanket `damage_immunities:[poison,psychic]`
    # on ~all mundane gear (a fixture default, not real game data — a Longsword is
    # not psychic-immune) and MagicItem carries no resistances in this dump, so we
    # deliberately do NOT fold those list fields into `properties`. The one real,
    # item-specific datum is the attunement clause, kept below.
    if fields.get("attunement_detail"):
        record["properties"].append(f"attune:{fields['attunement_detail']}")

    wfk = fields.get("weapon")
    if wfk and wfk in weapons:
        wf = weapons[wfk]
        record["damage"] = wf.get("damage_dice") or ""
        record["damage_type"] = wf.get("damage_type") or ""
    afk = fields.get("armor")
    if afk and afk in armors:
        af = armors[afk]
        record["ac"] = int(af.get("ac_base") or 0)
    elif fields.get("armor_class"):
        record["ac"] = int(fields.get("armor_class") or 0)
    return record


@functools.lru_cache(maxsize=None)
def _index() -> dict[str, dict]:
    """name (lowercased) -> flattened catalog record. FIRST-WINS across dirs (in
    precedence order, srd524 first) AND across the source files within a dir
    (MagicItem -> Item -> Weapon -> Armor): an item whose lowercased name is
    already present is skipped, so SRD items — and the richer source for a given
    name — are never silently overwritten."""
    out: dict[str, dict] = {}
    for d in _dirs():
        for src in _SOURCES:
            model = src[:-5].lower()  # 'MagicItem.json' -> 'magicitem'
            for r in _rows(d / src):
                fields = r.get("fields", {})
                name = fields.get("name")
                if not name:
                    continue
                key = name.lower()
                if key not in out:  # FIRST-WINS — earlier dir/source takes precedence
                    out[key] = _flatten(model, fields)
    return out


def resolve(name: str) -> Optional[dict]:
    """The catalog record for an item by (case-insensitive) name, or None.

    Tries an exact normalized match first, then a single unambiguous substring
    match (so 'bag of holding' and ' Longsword ' both resolve). Returns None when
    absent or ambiguous — the caller should then offer ``find()`` suggestions."""
    if not name:
        return None
    idx = _index()
    rec = idx.get(name.strip().lower())
    if rec is not None:
        return rec
    matches = find(name, limit=2)
    if len(matches) == 1:
        return idx.get(matches[0].lower())
    return None


def find(query: str, limit: int = 10) -> list[dict]:
    """Catalog records matching `query` (case-insensitive substring over the name),
    sorted by name. Empty query returns the first `limit` records (the catalog
    is deduped by the first-wins index, so no name appears twice)."""
    q = (query or "").strip().lower()
    records = sorted(_index().values(), key=lambda r: r["name"])
    if not q:
        return records[:limit]
    return [r for r in records if q in r["name"].lower()][:limit]


def suggest(name: str, limit: int = 5) -> list[str]:
    """Up-to-`limit` candidate item names for a miss: substring matches first,
    then a loose token overlap so 'cloak protection' still surfaces 'Cloak of
    Protection'. Names only (the lightweight payload for an error message)."""
    sub = [r["name"] for r in find(name, limit=limit)]
    if sub:
        return sub
    tokens = {t for t in (name or "").lower().split() if len(t) > 2}
    if not tokens:
        return []
    scored = []
    for rec in _index().values():
        words = set(rec["name"].lower().split())
        overlap = len(tokens & words)
        if overlap:
            scored.append((overlap, rec["name"]))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [n for _, n in scored[:limit]]


def count() -> int:
    return len(_index())
