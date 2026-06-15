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


def _cost(value) -> Optional[float]:
    """Tri-state listed price (F09-3): a positive price parses like _num; anything
    else — missing, null, '', unparseable, or 0/'0.00' (how the SRD dump marks
    every magic item) — is None, meaning "no listed price". Priceless is NOT free:
    buy_item demands an explicit cost_gp when this is None instead of silently
    charging 0 gp for a Bag of Holding."""
    n = _num(value, default=0.0)
    return n if n > 0 else None


def _int(value, default: int = 0) -> int:
    """Integer fields (AC). Like _num but coerces to int and tolerates strings/floats/
    None — a non-numeric value (e.g. a homebrew pack with ac_base:'plate') degrades to
    the default instead of raising and crashing the whole catalog index (H1)."""
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _weapon_range(fields: dict) -> dict:
    """RRI-25e55fa optimizer #3 — recover the SRD weapon RANGE the bare flatten threw away.

    SRD ``Weapon.json`` carries ``range`` (normal range in feet) and ``long_range`` (the
    disadvantaged-attack bracket). A ranged weapon (Heavy Crossbow 100/400) AND a thrown
    melee weapon (Dagger 20/60) both carry these; a pure melee weapon (Longsword) carries
    0/0. We surface them verbatim so the inspector can read the real "100/400 ft" bracket —
    and a 0 range honestly hides the row (never a fabricated number)."""
    return {
        "range": _int(fields.get("range")),
        "range_long": _int(fields.get("long_range")),
    }


def _weapon_category(fields: dict) -> str:
    """#888 — the SRD 5.2 weapon CATEGORY ("Simple" / "Martial") from the Weapon record's
    ``is_simple`` flag. The veteran/optimizer Examine wants the proficiency tier ("is this a
    Martial weapon I'm proficient with?"). The SRD encodes it ONLY as ``is_simple`` (True =>
    Simple, False => Martial); there is no separate category string. Returns "" when the field
    is absent (a non-Weapon record), so the inspector row is hidden — never fabricated."""
    flag = fields.get("is_simple")
    if flag is None:
        return ""
    return "Simple" if flag else "Martial"


def _armor_dex_rule(fields: dict, ac_base: int, name: str) -> dict:
    """F09-6 — recover the SRD armor DEX-mod rule the bare ``ac_base`` throws away.

    SRD ``Armor.json`` carries two rule fields the old flatten ignored:
    ``ac_add_dexmod`` (does DEX add to AC at all) and ``ac_cap_dexmod`` (the +N cap on
    that DEX bonus, or null for no cap). Together with ``ac_base`` they reconstruct the
    armor's category and how a wearer's effective AC is computed — so a Breastplate is
    "14 + DEX (max +2)", a Plate is a flat 18 (no DEX), and a Shield is a +2 BONUS on top
    of the wearer's AC, not a base AC of 2.

    Returns the additive keys ``{armor_category, ac_dex_mod, ac_dex_cap, ac_bonus?}`` to
    fold onto the catalog record. ``armor_category`` ∈ {light, medium, heavy, shield};
    ``ac_dex_mod`` ∈ {full, capped, none}; ``ac_dex_cap`` is the int cap when capped, else
    None. A shield (the SRD's flat +2 that ``ac_base`` smuggles in as "2") gets
    ``ac_bonus`` so callers add it rather than treating 2 as a base AC."""
    add_dex = bool(fields.get("ac_add_dexmod"))
    cap = fields.get("ac_cap_dexmod")
    cap = _int(cap) if cap not in (None, "") else None
    # Shield: the SRD stores a +2 BONUS as ac_base=2 (no DEX). Detect by name (the only
    # SRD "armor" whose ac_base is the bonus, not a worn-armor base) so we never mistake a
    # low-AC body armor for a shield. Heavy/medium/light all carry ac_base >= 11.
    if "shield" in (name or "").lower() and not add_dex and ac_base <= 3:
        return {"armor_category": "shield", "ac_dex_mod": "none", "ac_dex_cap": None,
                "ac_bonus": ac_base}
    if not add_dex:
        # Heavy armor — AC is the flat ac_base, DEX never applies.
        return {"armor_category": "heavy", "ac_dex_mod": "none", "ac_dex_cap": None}
    if cap is None:
        # Light armor — full DEX modifier, uncapped.
        return {"armor_category": "light", "ac_dex_mod": "full", "ac_dex_cap": None}
    # Medium armor — DEX modifier applies but is capped (SRD: +2).
    return {"armor_category": "medium", "ac_dex_mod": "capped", "ac_dex_cap": cap}


@functools.lru_cache(maxsize=None)
def _weapon_armor_join() -> tuple[dict, dict]:
    """({weapon_pk: weapon_fields}, {armor_pk: armor_fields}) merged across all
    dirs (first-wins by pk) so a MagicItem/Item ``weapon``/``armor`` FK resolves to
    its damage / AC. Keyed by pk because that is what the FK stores."""
    weapons: dict[str, dict] = {}
    armors: dict[str, dict] = {}
    for d in _dirs():
        for r in _rows(d / "Weapon.json"):
            if not isinstance(r, dict):
                continue
            pk = r.get("pk")
            if pk and pk not in weapons:
                weapons[pk] = r.get("fields") or {}
        for r in _rows(d / "Armor.json"):
            if not isinstance(r, dict):
                continue
            pk = r.get("pk")
            if pk and pk not in armors:
                armors[pk] = r.get("fields") or {}
    return weapons, armors


@functools.lru_cache(maxsize=None)
def _weapon_property_join() -> dict[str, dict]:
    """{weapon_pk: {"properties": [name, …], "versatile": "1d8", "mastery": "Topple"}}
    from the SRD ``WeaponProperty.json`` + ``WeaponPropertyAssignment.json`` tables (#756).

    A weapon's classic properties (Versatile, Finesse, Light, Thrown, Two-Handed,
    Heavy, Reach, Ammunition, Loading, Reach) and — for a Versatile weapon — its
    two-handed damage die are stored ONLY in the assignment join, not inline on the
    Weapon record. The flatten dropped them, so the inspector showed "no Versatile
    property" and no 1d8 two-handed damage (the RRI-5e98e6f optimizer findings). We
    recover them read-only here.

    The 2024 weapon-MASTERY property (Topple/Nick/Sap/Graze/Slow/Vex/Cleave/Push,
    ``type == "Mastery"``) is an advanced combat-option layer, kept OUT of the base
    ``properties`` chip list (a buyer evaluating descriptors) but surfaced SEPARATELY
    as ``mastery`` (#888 — the veteran/optimizer "Examine is missing the Weapon Mastery
    property"): a weapon has exactly one in SRD 5.2, so it's a single string ("" when
    none). ``versatile`` carries the assignment ``detail`` (the two-handed die, e.g.
    "1d8"); absent → "". Keyed by pk (the FK)."""
    # property_pk -> {name, type}
    prop_meta: dict[str, dict] = {}
    for d in _dirs():
        for r in _rows(d / "WeaponProperty.json"):
            if not isinstance(r, dict):
                continue
            pk = r.get("pk")
            f = r.get("fields") or {}
            if pk and pk not in prop_meta:
                prop_meta[pk] = {"name": str(f.get("name") or ""), "type": f.get("type")}
    out: dict[str, dict] = {}
    seen: dict[str, set] = {}
    for d in _dirs():
        for r in _rows(d / "WeaponPropertyAssignment.json"):
            if not isinstance(r, dict):
                continue
            f = r.get("fields") or {}
            wpk = f.get("weapon")
            ppk = f.get("property")
            if not wpk or not ppk:
                continue
            meta = prop_meta.get(ppk)
            if not meta or not meta["name"]:
                continue
            slot = out.setdefault(wpk, {"properties": [], "versatile": "", "mastery": ""})
            seen_for = seen.setdefault(wpk, set())
            name = meta["name"]
            # Versatile: keep its two-handed die (the assignment `detail`).
            if name.lower() == "versatile" and not slot["versatile"]:
                slot["versatile"] = str(f.get("detail") or "")
            # The Mastery property is surfaced separately (not a descriptor chip). SRD 5.2
            # gives a weapon exactly one; keep the first seen.
            if meta["type"] == "Mastery":
                if not slot["mastery"]:
                    slot["mastery"] = name
                continue
            # Base (non-Mastery) properties become item-descriptor chips; Mastery
            # options are excluded. De-dupe per weapon, preserve first-seen order.
            if meta["type"] is None and name not in seen_for:
                seen_for.add(name)
                slot["properties"].append(name)
    return out


def _flatten(model: str, fields: dict, pk: str = "") -> dict:
    """Flatten one source record (any of the 4 shapes) into the common catalog
    item dict. ``model`` is the fixture model tail ('magicitem'/'item'/'weapon'/
    'armor') used to pick the right shape. ``pk`` is the source record's primary
    key — used (#756) to look up a weapon's property assignments (Versatile/Finesse/
    Two-Handed + the versatile two-handed die) by FK."""
    weapons, armors = _weapon_armor_join()
    wprops = _weapon_property_join()
    name = fields.get("name", "")

    if model == "weapon":
        # Bare Weapon.json record: damage inline, no cost/desc/category.
        props = [
            p for p, on in (("simple", fields.get("is_simple")),
                            ("improvised", fields.get("is_improvised"))) if on
        ]
        # #756: fold in the SRD weapon properties (Versatile/Finesse/…) + the
        # versatile two-handed die from the assignment join, keyed by this pk.
        extra = wprops.get(pk, {})
        for chip in extra.get("properties", []):
            if chip not in props:
                props.append(chip)
        return {
            "name": name,
            "kind": "weapon",
            "rarity": "",
            "requires_attunement": False,
            "weight": _num(fields.get("weight")),
            "cost": _cost(fields.get("cost")),
            "description": fields.get("desc", "") or "",
            "properties": props,
            "damage": fields.get("damage_dice") or "",
            "damage_type": fields.get("damage_type") or "",
            "versatile": extra.get("versatile", ""),
            # #888 (veteran/optimizer Examine depth): the weapon CATEGORY (Simple/Martial,
            # from the SRD `is_simple` flag) + the 2024 Weapon MASTERY property (Topple/Vex/…)
            # so the Stash/Market inspector reads "Martial Weapon · Mastery: Sap" — additive.
            "weapon_category": _weapon_category(fields),
            "mastery": extra.get("mastery", ""),
            # RRI-25e55fa optimizer #3: the SRD weapon range bracket (0/0 for pure melee).
            **_weapon_range(fields),
        }

    if model == "armor":
        # Bare Armor.json record: ac_base inline, no cost/desc/category.
        props = []
        if fields.get("grants_stealth_disadvantage"):
            props.append("stealth-disadvantage")
        if fields.get("strength_score_required"):
            props.append(f"str-{fields['strength_score_required']}")
        ac_base = _int(fields.get("ac_base"))
        return {
            "name": name,
            "kind": "armor",
            "rarity": "",
            "requires_attunement": False,
            "weight": _num(fields.get("weight")),
            "cost": _cost(fields.get("cost")),
            "description": fields.get("desc", "") or "",
            "properties": props,
            "ac": ac_base,
            # F09-6: carry the DEX-mod rule (light/medium/heavy/shield) so the
            # effective-AC path can apply it and the description reads correctly.
            **_armor_dex_rule(fields, ac_base, name),
        }

    # MagicItem / Item share a shape. Resolve damage/ac via the weapon/armor FK.
    kind = _kind(fields.get("category"))
    record = {
        "name": name,
        "kind": kind,
        "rarity": (fields.get("rarity") or "").strip(),
        "requires_attunement": bool(fields.get("requires_attunement")),
        "weight": _num(fields.get("weight")),
        "cost": _cost(fields.get("cost")),
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
        # RRI-25e55fa optimizer #3: a magic weapon inherits its base weapon's range bracket
        # via the same FK (0/0 for a melee polearm — never a fabricated thrown range).
        record.update(_weapon_range(wf))
        # #888: a magic weapon inherits its base weapon's CATEGORY + MASTERY via the same FK.
        record["weapon_category"] = _weapon_category(wf)
        # #756: a magic weapon inherits its base weapon's SRD properties + versatile
        # two-handed die via the same FK (de-duped onto the attunement clause above).
        wp = wprops.get(wfk, {})
        for chip in wp.get("properties", []):
            if chip not in record["properties"]:
                record["properties"].append(chip)
        if wp.get("versatile"):
            record["versatile"] = wp["versatile"]
        if wp.get("mastery"):
            record["mastery"] = wp["mastery"]
    afk = fields.get("armor")
    if afk and afk in armors:
        af = armors[afk]
        ac_base = _int(af.get("ac_base"))
        record["ac"] = ac_base
        # F09-6: a magic armor inherits its base armor's DEX-mod rule via the FK join.
        record.update(_armor_dex_rule(af, ac_base, record["name"]))
        # An Item/MagicItem armor inherits its base armor's SRD stealth-disadvantage
        # + STR-requirement via the same FK (mirrors the model=="armor" path above;
        # SRD Armor: e.g. Chain Mail grants_stealth_disadvantage=true str=13). De-duped
        # onto any prior props, like the weapon-FK block just above.
        if af.get("grants_stealth_disadvantage") and "stealth-disadvantage" not in record["properties"]:
            record["properties"].append("stealth-disadvantage")
        if af.get("strength_score_required") and f"str-{af['strength_score_required']}" not in record["properties"]:
            record["properties"].append(f"str-{af['strength_score_required']}")
    elif fields.get("armor_class"):
        # Homebrew/inline armor_class with no DEX-rule data: keep the bare AC. Without
        # ac_add_dexmod we can't infer the category — leave the rule keys absent so the
        # describe/effective-AC path treats it as a flat AC (today's behavior).
        record["ac"] = _int(fields.get("armor_class"))
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
                if not isinstance(r, dict):
                    continue
                fields = r.get("fields") or {}
                name = fields.get("name") if isinstance(fields, dict) else None
                if not isinstance(name, str) or not name:
                    continue
                key = name.lower()
                if key not in out:  # FIRST-WINS — earlier dir/source takes precedence
                    try:  # one malformed record must never sink the whole catalog (H1)
                        out[key] = _flatten(model, fields, r.get("pk") or "")
                    except (TypeError, ValueError, AttributeError, KeyError):
                        continue
    return out


# #756: the canonical-name suffixes a base item name is shortened FROM. Markets/
# inventories carry the colloquial short form ("Studded Leather", "Leather", "Plate")
# but the SRD catalog keys the full canonical name ("Studded Leather Armor"). A bare
# substring of the short form is AMBIGUOUS (it also matches "Glamoured Studded
# Leather", "Armor of Resistance (Studded Leather)", …) -> None -> no AC value, the
# CRITICAL gate-flipper. Trying the base name + a canonical suffix gives an EXACT key
# hit, which is precise and never ambiguous.
_BASE_NAME_SUFFIXES = (" Armor", " Weapon")


def resolve(name: str) -> Optional[dict]:
    """The catalog record for an item by (case-insensitive) name, or None.

    Tries an exact normalized match first, then a base-name + canonical-suffix exact
    match (so 'Studded Leather' -> 'Studded Leather Armor'; #756), then a single
    unambiguous substring match (so 'bag of holding' and ' Longsword ' both resolve).
    Returns None when absent or ambiguous — the caller should then offer ``find()``
    suggestions."""
    if not name:
        return None
    idx = _index()
    norm = name.strip().lower()
    rec = idx.get(norm)
    if rec is not None:
        return rec
    # #756: the colloquial short form + a canonical suffix is an EXACT (unambiguous)
    # key — preferred over the fuzzy substring branch so "Studded Leather" resolves to
    # "Studded Leather Armor" (carrying its real AC) instead of going ambiguous->None.
    for suffix in _BASE_NAME_SUFFIXES:
        rec = idx.get(norm + suffix.lower())
        if rec is not None:
            return rec
    matches = find(name, limit=2)
    if len(matches) == 1:
        # find() returns the flattened records themselves (F09-1: this used to
        # call .lower() on the dict and crash every unique-substring match).
        return matches[0]
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
