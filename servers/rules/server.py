"""WorldOS rules MCP server.

Read-only D&D 5e rules reference. Serves a bundled SRD 5.2 dataset from
data/srd/ first (offline, canonical, CC-BY-4.0) and falls back to the public
dnd5eapi.co API for anything not bundled. Lookups use fuzzy matching so
"fire ball" finds "Fireball" and "prnoe" finds "Prone".

Two on-disk layers are merged at startup:

  1. The hand-authored *starter* set (``data/srd/{spells,monsters,...}.json``),
     a small curated slice with rich engine-facing fields (e.g. spell
     ``mechanics``, monster ``abilities``).
  2. The *full* SRD 5.2.1 set (``data/srd/srd524/``), vendored verbatim from the
     Open5e conversion of the WotC SRD (CC-BY-4.0; see
     ``data/srd/LICENSE-DATA.md``). This is the Django-fixture JSON shape
     ``{"model", "pk", "fields": {...}}``, normalized here into the same
     lower-cased lookup dicts. The full set provides the long tail offline
     (Fireball, Tarrasque, ...).

On a name collision the full-set entry wins, but the original starter record is
preserved under ``_starter`` so the curated engine fields are never lost. Every
full-set entry also carries its untouched upstream record under ``_open5e``.

The live dnd5eapi.co fallback still covers anything absent from both layers. Set
WORLDOS_RULES_OFFLINE=1 to disable network lookups (used in CI).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from rapidfuzz import fuzz, process

from _env import env_var

mcp = FastMCP("worldos-rules")

_DATA_DIR = Path(
    env_var("SRD_DIR") or Path(__file__).resolve().parents[2] / "data" / "srd"
)
_FULL_DIR = Path(env_var("SRD524_DIR") or _DATA_DIR / "srd524")
_API_HOST = "https://www.dnd5eapi.co"


def _load(name: str) -> dict[str, dict]:
    """Load a hand-authored starter table: a flat ``[{"name": ...}, ...]`` list
    keyed by lower-cased name."""
    p = _DATA_DIR / f"{name}.json"
    if not p.exists():
        return {}
    rows = json.loads(p.read_text(encoding="utf-8"))
    return {row["name"].lower(): row for row in rows}


# ---------------------------------------------------------------------------
# Full SRD 5.2.1 set (Open5e Django-fixture JSON) — loading + normalization.
# ---------------------------------------------------------------------------


def _read_fixture(filename: str) -> list[dict]:
    """Read one Open5e fixture file, returning the list of raw records
    (each ``{"model", "pk", "fields": {...}}``). Missing file -> []."""
    p = _FULL_DIR / filename
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _strip_prefix(value: Any) -> Any:
    """Open5e foreign keys are prefixed pks like ``srd-2024_wizard``. Strip the
    ``srd-2024_`` document prefix and turn dashes into spaces for readability.
    Lists are mapped element-wise; non-strings pass through."""
    if isinstance(value, list):
        return [_strip_prefix(v) for v in value]
    if isinstance(value, str) and value.startswith("srd-2024_"):
        return value[len("srd-2024_"):].replace("-", " ")
    return value


def _title(value: Any) -> Any:
    return value.title() if isinstance(value, str) and value else value


def _norm_cr(raw: Any) -> str:
    """Open5e stores CR as a decimal string (``"0.250"``, ``"30.000"``). Render
    the familiar SRD form (``"1/4"``, ``"30"``)."""
    if raw is None:
        return ""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    fractions = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}
    if f in fractions:
        return fractions[f]
    if f == int(f):
        return str(int(f))
    return str(raw)


def _index_children(records: list[dict], parent_key: str = "parent") -> dict[str, list[dict]]:
    """Group child fixture records (creature actions/traits, feature items, ...)
    by their ``parent`` pk so they can be attached to the parent entity."""
    out: dict[str, list[dict]] = {}
    for r in records:
        f = r.get("fields", {})
        parent = f.get(parent_key)
        if parent:
            out.setdefault(parent, []).append(f)
    return out


def _entry(name: str, fields: dict, **mapped: Any) -> dict:
    """Build a normalized entry: always a top-level ``name``, the explicitly
    mapped fields, and the full raw Open5e ``fields`` under ``_open5e``."""
    entry: dict[str, Any] = {"name": name}
    entry.update({k: v for k, v in mapped.items() if v is not None})
    entry["_open5e"] = fields
    return entry


def _norm_spell(f: dict) -> dict:
    comp = [c for c, on in (("V", f.get("verbal")), ("S", f.get("somatic")), ("M", f.get("material"))) if on]
    return _entry(
        f["name"], f,
        level=f.get("level"),
        school=_title(f.get("school")),
        casting_time=f.get("casting_time"),
        range=f.get("range_text"),
        components=", ".join(comp) if comp else None,
        duration=f.get("duration"),
        concentration=f.get("concentration"),
        classes=[_title(c) for c in _strip_prefix(f.get("classes", []))],
        higher_level=f.get("higher_level") or None,
        description=f.get("desc"),
    )


def _norm_monster(f: dict, actions: list[dict], traits: list[dict]) -> dict:
    abilities = {
        "str": f.get("ability_score_strength"),
        "dex": f.get("ability_score_dexterity"),
        "con": f.get("ability_score_constitution"),
        "int": f.get("ability_score_intelligence"),
        "wis": f.get("ability_score_wisdom"),
        "cha": f.get("ability_score_charisma"),
    }
    speed_parts = [
        f"{f.get('walk')} ft." if f.get("walk") else None,
        f"fly {f['fly']} ft." if f.get("fly") else None,
        f"swim {f['swim']} ft." if f.get("swim") else None,
        f"climb {f['climb']} ft." if f.get("climb") else None,
        f"burrow {f['burrow']} ft." if f.get("burrow") else None,
    ]
    speed = ", ".join(p for p in speed_parts if p)
    return _entry(
        f["name"], f,
        size=_title(f.get("size")),
        type=f.get("type"),
        alignment=f.get("alignment"),
        ac=f.get("armor_class"),
        ac_note=f.get("armor_detail") or None,
        hp=f.get("hit_points"),
        hit_dice=f.get("hit_dice"),
        speed=speed or None,
        abilities={k: v for k, v in abilities.items() if v is not None},
        cr=_norm_cr(f.get("challenge_rating")),
        traits=[{"name": t.get("name"), "description": t.get("desc")} for t in traits] or None,
        actions=[{"name": a.get("name"), "description": a.get("desc")} for a in actions] or None,
    )


def _norm_item(f: dict, magic: bool) -> dict:
    return _entry(
        f["name"], f,
        category=f.get("category"),
        rarity=f.get("rarity") if magic else None,
        requires_attunement=f.get("requires_attunement") if magic else None,
        cost=f.get("cost"),
        weight=f.get("weight"),
        description=f.get("desc") or None,
    )


def _norm_weapon(f: dict) -> dict:
    return _entry(
        f["name"], f,
        category="weapon",
        damage=f.get("damage_dice"),
        damage_type=f.get("damage_type"),
        is_simple=f.get("is_simple"),
        range=f.get("range"),
        long_range=f.get("long_range"),
    )


def _norm_armor(f: dict) -> dict:
    return _entry(
        f["name"], f,
        category="armor",
        ac_base=f.get("ac_base"),
        ac_add_dexmod=f.get("ac_add_dexmod"),
        ac_cap_dexmod=f.get("ac_cap_dexmod"),
        strength_required=f.get("strength_score_required"),
        stealth_disadvantage=f.get("grants_stealth_disadvantage"),
    )


def _norm_rule(f: dict) -> dict:
    return _entry(f["name"], f, description=f.get("desc"))


def _norm_named(f: dict, **extra: Any) -> dict:
    """Generic mapper for entities that already expose name + desc (feats,
    backgrounds, species, classes)."""
    return _entry(f["name"], f, description=f.get("desc"), **extra)


def _load_full_spells() -> dict[str, dict]:
    return {r["fields"]["name"].lower(): _norm_spell(r["fields"]) for r in _read_fixture("Spell.json")}


def _load_full_monsters() -> dict[str, dict]:
    actions = _index_children(_read_fixture("CreatureAction.json"))
    traits = _index_children(_read_fixture("CreatureTrait.json"))
    out: dict[str, dict] = {}
    for r in _read_fixture("Creature.json"):
        f = r["fields"]
        pk = r.get("pk", "")
        out[f["name"].lower()] = _norm_monster(f, actions.get(pk, []), traits.get(pk, []))
    return out


def _load_full_items() -> dict[str, dict]:
    """Magic items + mundane equipment + the typed weapon/armor tables, merged
    into one ``item`` lookup. Typed weapon/armor entries win over the generic
    Item rows of the same name (they carry mechanical fields)."""
    out: dict[str, dict] = {}
    for r in _read_fixture("MagicItem.json"):
        f = r["fields"]
        out[f["name"].lower()] = _norm_item(f, magic=True)
    for r in _read_fixture("Item.json"):
        f = r["fields"]
        out.setdefault(f["name"].lower(), _norm_item(f, magic=False))
    for r in _read_fixture("Weapon.json"):
        f = r["fields"]
        out[f["name"].lower()] = _norm_weapon(f)
    for r in _read_fixture("Armor.json"):
        f = r["fields"]
        out[f["name"].lower()] = _norm_armor(f)
    return out


def _load_full_conditions() -> dict[str, dict]:
    # ConditionDescription has no `name`; the condition is `fields.describes`.
    out: dict[str, dict] = {}
    for r in _read_fixture("ConditionDescription.json"):
        f = r["fields"]
        cond = f.get("describes")
        if not cond:
            continue
        out[cond.lower()] = _entry(cond.title(), f, description=f.get("desc"))
    return out


def _load_full_rules() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in _read_fixture("RuleSet.json"):
        out[r["fields"]["name"].lower()] = _norm_rule(r["fields"])
    # Individual rules override rulesets of the same name (finer granularity).
    for r in _read_fixture("Rule.json"):
        out[r["fields"]["name"].lower()] = _norm_rule(r["fields"])
    return out


def _load_full_simple(filename: str, **extra_keys: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in _read_fixture(filename):
        f = r["fields"]
        if not f.get("name"):
            continue
        extra = {dest: f.get(src) for dest, src in extra_keys.items()}
        out[f["name"].lower()] = _norm_named(f, **extra)
    return out


def _merge(starter: dict[str, dict], full: dict[str, dict]) -> dict[str, dict]:
    """Merge starter + full tables. Full-set entries win on a name collision,
    but the curated starter record is retained under ``_starter`` so its
    engine-facing fields (spell mechanics, monster abilities) remain reachable.
    Entries present only in the starter set pass through unchanged."""
    merged: dict[str, dict] = dict(starter)
    for key, entry in full.items():
        if key in starter:
            entry = {**entry, "_starter": starter[key]}
        merged[key] = entry
    return merged


# Starter (curated) tables.
_CONDITIONS = _load("conditions")
_SPELLS = _load("spells")
_MONSTERS = _load("monsters")
_RULES = _load("rules")

# Full SRD 5.2.1 set (best-effort: missing files just contribute nothing).
_FULL_SPELLS = _load_full_spells()
_FULL_MONSTERS = _load_full_monsters()
_FULL_ITEMS = _load_full_items()
_FULL_CONDITIONS = _load_full_conditions()
_FULL_RULES = _load_full_rules()
_FULL_FEATS = _load_full_simple("Feat.json", prerequisite="prerequisite")
_FULL_BACKGROUNDS = _load_full_simple("Background.json")
_FULL_SPECIES = _load_full_simple("Species.json")
_FULL_CLASSES = _load_full_simple(
    "CharacterClass.json", hit_dice="hit_dice", caster_type="caster_type"
)

# Merge full set into the lookup dicts (full preferred, starter retained).
_CONDITIONS = _merge(_CONDITIONS, _FULL_CONDITIONS)
_SPELLS = _merge(_SPELLS, _FULL_SPELLS)
_MONSTERS = _merge(_MONSTERS, _FULL_MONSTERS)
_RULES = _merge(_RULES, _FULL_RULES)
# New full-set-only categories (no starter equivalent).
_ITEMS = _FULL_ITEMS
_FEATS = _FULL_FEATS
_BACKGROUNDS = _FULL_BACKGROUNDS
_SPECIES = _FULL_SPECIES
_CLASSES = _FULL_CLASSES


def _fuzzy_get(query: str, table: dict[str, dict]) -> Optional[dict]:
    if not table:
        return None
    q = query.strip().lower()
    if q in table:
        return table[q]
    match = process.extractOne(q, list(table.keys()), scorer=fuzz.WRatio, score_cutoff=70)
    return table[match[0]] if match else None


def _api_lookup(category: str, query: str) -> Optional[dict]:
    if env_var("RULES_OFFLINE"):
        return None
    try:
        r = httpx.get(f"{_API_HOST}/api/{category}", params={"name": query}, timeout=8.0)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        detail = httpx.get(f"{_API_HOST}{results[0]['url']}", timeout=8.0)
        detail.raise_for_status()
        data = detail.json()
        data["_source"] = "dnd5eapi"
        return data
    except Exception:
        return None


def _find(table: dict[str, dict], query: str, api_category: str) -> Optional[dict]:
    hit = _fuzzy_get(query, table)
    if hit is not None:
        out = dict(hit)
        out["_source"] = "srd-bundled"
        return out
    return _api_lookup(api_category, query)


def find_condition(name: str) -> Optional[dict]:
    return _find(_CONDITIONS, name, "conditions")


def find_spell(name: str) -> Optional[dict]:
    return _find(_SPELLS, name, "spells")


def find_monster(name: str) -> Optional[dict]:
    return _find(_MONSTERS, name, "monsters")


def find_rule(name: str) -> Optional[dict]:
    return _find(_RULES, name, "rule-sections")


def find_item(name: str) -> Optional[dict]:
    return _find(_ITEMS, name, "magic-items")


def find_feat(name: str) -> Optional[dict]:
    return _find(_FEATS, name, "feats")


def find_background(name: str) -> Optional[dict]:
    return _find(_BACKGROUNDS, name, "backgrounds")


def find_species(name: str) -> Optional[dict]:
    return _find(_SPECIES, name, "races")


def find_class(name: str) -> Optional[dict]:
    return _find(_CLASSES, name, "classes")


_SEARCH_TABLES = {
    "spell": lambda: _SPELLS,
    "monster": lambda: _MONSTERS,
    "condition": lambda: _CONDITIONS,
    "rule": lambda: _RULES,
    "item": lambda: _ITEMS,
    "feat": lambda: _FEATS,
    "background": lambda: _BACKGROUNDS,
    "species": lambda: _SPECIES,
    "class": lambda: _CLASSES,
}


def search(query: str, category: Optional[str] = None) -> list[dict]:
    if category:
        getter = _SEARCH_TABLES.get(category)
        tables = {category: getter()} if getter else {}
    else:
        tables = {cat: getter() for cat, getter in _SEARCH_TABLES.items()}
    q = query.strip().lower()
    out: list[dict] = []
    for cat, tbl in tables.items():
        for key, row in tbl.items():
            if q in key:
                out.append({"category": cat, "name": row["name"]})
    return out


def _wrap(result: Optional[dict], query: str) -> dict:
    return {"found": True, **result} if result else {"found": False, "query": query}


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok and the bundled SRD dataset sizes."""
    return (
        f"worldos-rules: ok (v0.0.1) — bundled SRD 5.2.1: {len(_CONDITIONS)} conditions, "
        f"{len(_SPELLS)} spells, {len(_MONSTERS)} monsters, {len(_RULES)} rules, "
        f"{len(_ITEMS)} items, {len(_FEATS)} feats, {len(_BACKGROUNDS)} backgrounds, "
        f"{len(_SPECIES)} species, {len(_CLASSES)} classes"
    )


@mcp.tool()
def lookup_condition(name: str) -> dict:
    """Look up a D&D 5e condition (e.g. 'prone', 'poisoned', 'grappled'). Fuzzy
    matching tolerates typos. Returns the SRD effect text."""
    return _wrap(find_condition(name), name)


@mcp.tool()
def lookup_spell(name: str) -> dict:
    """Look up a D&D 5e spell by name (fuzzy). Returns level, school, casting
    time, range, components, duration, classes, and description."""
    return _wrap(find_spell(name), name)


@mcp.tool()
def lookup_monster(name: str) -> dict:
    """Look up a D&D 5e monster/creature by name (fuzzy). Returns its stat block
    (AC, HP, abilities, actions, CR)."""
    return _wrap(find_monster(name), name)


@mcp.tool()
def lookup_rule(name: str) -> dict:
    """Look up a D&D 5e rule (e.g. 'advantage', 'cover', 'resting', 'death
    saving throws'). Fuzzy matching tolerates partial names."""
    return _wrap(find_rule(name), name)


@mcp.tool()
def lookup_item(name: str) -> dict:
    """Look up a D&D 5e item by name (fuzzy): magic items, weapons, armor, and
    mundane equipment. Returns category, rarity/attunement (magic items),
    damage (weapons), AC (armor), cost, and description."""
    return _wrap(find_item(name), name)


@mcp.tool()
def lookup_feat(name: str) -> dict:
    """Look up a D&D 5e feat by name (fuzzy). Returns its prerequisite and
    benefit description."""
    return _wrap(find_feat(name), name)


@mcp.tool()
def lookup_background(name: str) -> dict:
    """Look up a D&D 5e character background by name (fuzzy). Returns its
    description and granted benefits."""
    return _wrap(find_background(name), name)


@mcp.tool()
def lookup_species(name: str) -> dict:
    """Look up a D&D 5e species/race by name (fuzzy). Returns its traits and
    description."""
    return _wrap(find_species(name), name)


@mcp.tool()
def lookup_class(name: str) -> dict:
    """Look up a D&D 5e character class by name (fuzzy). Returns hit dice,
    caster type, and description."""
    return _wrap(find_class(name), name)


@mcp.tool()
def search_srd(query: str, category: str = "") -> list[dict]:
    """Search the bundled SRD by substring across spells, monsters, conditions,
    rules, items, feats, backgrounds, species, and classes. Optionally restrict
    to a category: spell | monster | condition | rule | item | feat | background
    | species | class."""
    return search(query, category or None)


if __name__ == "__main__":
    mcp.run()
