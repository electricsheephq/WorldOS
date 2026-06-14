"""SRD 5.2 progression tables — pure, cached accessors over data/srd/.

Loads progression.json, classes.json, spell_slots.json once and exposes the
lookups the character/leveling tools need: proficiency bonus, XP thresholds,
class hit dice/saves/skills, ASI levels, multiclass effective caster level, and
spell-slot tables. No campaign state here, so it's trivially unit-testable.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "data" / "srd"


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))


def progression() -> dict:
    return _load("progression")


def classes() -> dict:
    return _load("classes")


def _slots() -> dict:
    return _load("spell_slots")


def proficiency_bonus(total_level: int) -> int:
    lvl = max(1, min(20, total_level))
    return progression()["proficiency_bonus_by_level"][str(lvl)]


def xp_for_level(n: int) -> int:
    return progression()["xp_thresholds"][str(max(1, min(20, n)))]


def level_for_xp(xp: int) -> int:
    thresholds = progression()["xp_thresholds"]
    lvl = 1
    for n in range(1, 21):
        if xp >= thresholds[str(n)]:
            lvl = n
        else:
            break
    return lvl


@functools.lru_cache(maxsize=None)
def finesse_weapon_names() -> tuple[str, ...]:
    """Lowercased display names of the SRD FINESSE weapons (audit F01-4 / #774), derived
    from the raw srd524 dumps: WeaponPropertyAssignment.json maps the finesse property
    slug (``srd-2024_finesse...`` — matched case-insensitively) to weapon slugs, and
    Weapon.json maps those slugs to display names (slug-strip fallback when a name is
    missing). Pure + cached; returns () when the data files are absent so the engine
    never crashes on a trimmed data dir."""
    base = _DIR / "srd524"
    try:
        assignments = json.loads((base / "WeaponPropertyAssignment.json").read_text(encoding="utf-8"))
        weapons = json.loads((base / "Weapon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    slug_to_name = {
        rec.get("pk", ""): str((rec.get("fields") or {}).get("name") or "")
        for rec in weapons
    }
    names: list[str] = []
    for rec in assignments:
        f = rec.get("fields") or {}
        if "finesse" not in str(f.get("property", "")).lower():
            continue
        slug = str(f.get("weapon", ""))
        display = slug_to_name.get(slug) or slug.split("_", 1)[-1]
        if display:
            names.append(display.casefold())
    return tuple(sorted(set(names)))


def is_finesse_weapon(item_name: str) -> bool:
    """Does this inventory item name a finesse weapon? Case-insensitive and
    substring-tolerant so magic-item naming still matches ("Rapier +1", "Daggers")."""
    n = (item_name or "").casefold()
    if not n:
        return False
    return any(w in n for w in finesse_weapon_names())


def class_data(name: str) -> dict:
    c = classes().get(name.lower())
    if c is None:
        raise ValueError(f"unknown class {name!r}")
    return c


def hit_die(name: str) -> int:
    return class_data(name)["hit_die"]


def class_saves(name: str) -> list[str]:
    return class_data(name)["saves"]


def class_skills(name: str) -> dict:
    return class_data(name)["skills"]


# Typical level-1 AC from a class's standard starting gear. A heuristic baseline
# (not raw SRD data) so apply_srd_defaults doesn't leave a martial PC unarmored
# at AC 10; the DM can always pass an explicit armor_class to override.
_BASE_AC = {
    "barbarian": 14, "bard": 14, "cleric": 16, "druid": 14, "fighter": 16,
    "monk": 13, "paladin": 16, "ranger": 14, "rogue": 14, "sorcerer": 12,
    "warlock": 13, "wizard": 12,
}


def class_base_ac(name: str) -> int:
    """A sensible level-1 AC for a class's typical starting gear (default 10)."""
    return _BASE_AC.get(name.lower(), 10)


def features_at(class_name: str, level: int) -> list[dict]:
    """The class/subclass features gained AT a given level (from the curated
    class_features table), each ``{name, desc, ...hints}``. Empty if none or the
    class is unknown. Hints include extra_attacks / sneak_attack_dice / rage_* for
    the engine to apply at level-up."""
    table = _load("class_features").get(class_name.lower(), {})
    return list(table.get(str(int(level)), []))


def features_through(class_name: str, level: int) -> list[dict]:
    """All features gained from level 1 through `level` (for a character created
    directly at a level rather than leveled up one step at a time)."""
    table = _load("class_features").get(class_name.lower(), {})
    out: list[dict] = []
    for lv in range(1, int(level) + 1):
        out.extend(table.get(str(lv), []))
    return out


# ── Subclass (Arcane Tradition / Domain / Archetype …) options — #624 ────────────
#
# SRD 5.2 every class picks its subclass at a fixed level (3). The engine OWNS the
# legal options + their choice-level features so the level-up surface can present a
# real list with previews instead of a free-text box, and so choosing one applies
# its features. Additive: a class/subclass the table doesn't know still round-trips
# (subclass stays a free string; nothing is forced).


def _subclasses() -> dict:
    return _load("subclasses")


def subclass_level(class_name: str) -> int | None:
    """The character level at which `class_name` chooses its subclass (SRD 5.2: 3),
    or None if the class is unknown to the subclass table."""
    return _subclasses().get("subclass_level", {}).get(class_name.lower())


def subclass_group_label(class_name: str) -> str:
    """The in-world name for this class's subclass category (e.g. 'Arcane Tradition'
    for wizard, 'Divine Domain' for cleric). Empty if unknown."""
    return _subclasses().get("classes", {}).get(class_name.lower(), {}).get("group_label", "")


# Canonical SRD 5.2 levels at which each subclass feature is gained, keyed by
# (subclass_name_lower, feature_name). The choice-level features (gained at
# subclass_level, 3) are derived automatically; this map pins the HIGHER-level
# features whose level the bare srd524 ClassFeature dump doesn't carry. Anything not
# in this map (and not a choice-level feature) resolves to level None — HONEST: we
# surface the feature's rules text but never fabricate a level we can't verify.
_SUBCLASS_FEATURE_LEVELS: dict[tuple[str, str], int] = {
    # Fighter — Champion (SRD 5.2 / 2024 PHB progression). Only levels we can state with
    # confidence are pinned; every OTHER subclass's higher feature resolves to level None
    # (HONEST — its rules text still surfaces; we never fabricate an unverifiable level).
    ("champion", "additional fighting style"): 7,
    ("champion", "heroic warrior"): 10,
    ("champion", "superior critical"): 15,
    ("champion", "survivor"): 18,
}


def subclass_full_features(class_name: str, subclass_name: str, choice_level: int) -> list[dict]:
    """Every SRD feature a subclass grants, each ``{name, desc, level}`` with the FULL
    SRD rules text (from feature_catalog) and its gained-at level where known.

    The choice-level features (curated in subclasses.json) are pinned to ``choice_level``;
    the remaining features come from the srd524 ClassFeature dump with the canonical SRD
    5.2 level from _SUBCLASS_FEATURE_LEVELS, or ``None`` when the dump+map don't pin one
    (HONEST — the rules text is real even when the level isn't verifiable). Ordered by
    level (None last), then name, so the picker reads as a progression. Additive: with no
    feature_catalog available it degrades to the curated choice-level features only."""
    sub_slug = name_to_slug(subclass_name)
    # Choice-level features (curated short list) — pinned to the choice level, with their
    # full rules text upgraded from feature_catalog when available.
    choice_names: set[str] = set()
    merged: dict[str, dict] = {}
    for opt in _subclasses().get("classes", {}).get(class_name.lower(), {}).get("options", []):
        if opt.get("name") != subclass_name:
            continue
        for f in opt.get("features", []):
            nm = f.get("name", "")
            if not nm:
                continue
            choice_names.add(nm.lower())
            merged[nm.lower()] = {"name": nm, "desc": f.get("desc", "") or "", "level": choice_level}
    # The richer/full feature set from the SRD dump (full rules text + higher features).
    try:
        import feature_catalog  # local import: pure module, no cycle with srd_tables
        full = feature_catalog.features_for(subclass_name)
    except Exception:
        full = []
    for f in full:
        nm = f.get("name", "")
        if not nm:
            continue
        key = nm.lower()
        if key in choice_names:
            # Upgrade the curated short desc to the full SRD rules text, keep choice level.
            if f.get("desc"):
                merged[key]["desc"] = f["desc"]
            continue
        lvl = _SUBCLASS_FEATURE_LEVELS.get((sub_slug, key))
        merged[key] = {"name": nm, "desc": f.get("desc", "") or "", "level": lvl}
    out = list(merged.values())
    out.sort(key=lambda f: (f["level"] is None, f["level"] if f["level"] is not None else 99, f["name"]))
    return out


def name_to_slug(name: str) -> str:
    """Lowercase hyphenated slug of a class/subclass name ('Circle of the Land' ->
    'circle-of-the-land'), matching the srd524 pk convention. Shared by the subclass +
    feature lookups so an owner name maps to the same key everywhere."""
    import re
    s = (name or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def subclass_options(class_name: str, *, full_features: bool = False) -> list[dict]:
    """Legal SRD subclass options for a class, each ``{name, desc, aliases, features}``
    where `features` are the choice-level (level-3) features that subclass grants.
    Empty if the class has no SRD subclass entry.

    With ``full_features=True`` each option's `features` is the FULL feature set (choice-
    level PLUS higher-level archetype features, each ``{name, desc, level}`` with full SRD
    rules text) — the data the level-up subclass picker shows so the optimizer can read
    every feature, not just the level-3 pair. Default (``False``) keeps the legacy curated
    choice-level list (additive — existing callers are unchanged)."""
    entry = _subclasses().get("classes", {}).get(class_name.lower())
    if not entry:
        return []
    choice_lvl = subclass_level(class_name) or 3
    out: list[dict] = []
    for o in entry.get("options", []):
        opt = dict(o)
        if full_features:
            opt["features"] = subclass_full_features(class_name, o["name"], choice_lvl)
        else:
            opt["features"] = [dict(f) for f in o.get("features", [])]
        out.append(opt)
    return out


def resolve_subclass(class_name: str, name: str | None) -> str | None:
    """Resolve a (possibly loose) subclass name to its canonical SRD name for a
    class. Matches the canonical name case-insensitively and a curated alias map
    ('Evocation' -> 'Evoker'). Returns None if the name doesn't match any option."""
    if not name:
        return None
    entry = _subclasses().get("classes", {}).get(class_name.lower())
    if not entry:
        return None
    want = name.strip().lower()
    for opt in entry.get("options", []):
        if opt["name"].lower() == want:
            return opt["name"]
    for alias, canonical in entry.get("aliases", {}).items():
        if alias.lower() == want:
            return canonical
    return None


def subclass_features_at(class_name: str, subclass: str | None, class_level: int) -> list[dict]:
    """The chosen subclass's features gained AT this class level. Currently the
    curated table carries the choice-level (level-3) features; later-level subclass
    features remain represented by the generic 'Subclass Feature' placeholders in
    class_features.json. Empty if no subclass, an unknown subclass, or no features
    at this level."""
    canonical = resolve_subclass(class_name, subclass)
    if not canonical:
        return []
    if class_level != subclass_level(class_name):
        return []
    for opt in subclass_options(class_name):
        if opt["name"] == canonical:
            return [dict(f) for f in opt.get("features", [])]
    return []


def caster_type(name: str) -> str:
    return class_data(name)["caster_type"]


def multiclass_prereq(name: str) -> list[dict]:
    return class_data(name)["multiclass_prereq"]


_CASTING_ABILITY = {
    "bard": "cha",
    "cleric": "wis",
    "druid": "wis",
    "paladin": "cha",
    "ranger": "wis",
    "sorcerer": "cha",
    "warlock": "cha",
    "wizard": "int",
}


def casting_ability(name: str) -> str | None:
    """The spellcasting ability for a class (None for non-casters)."""
    return _CASTING_ABILITY.get(name.lower())


def is_asi_level(class_name: str, class_level: int) -> bool:
    table = progression()["asi_levels"]
    return class_level in table.get(class_name.lower(), table["default"])


def average_hp(die: int) -> int:
    return progression()["average_hp_per_die"][str(die)]


def point_buy_cost() -> dict:
    return progression()["point_buy_cost"]


def standard_array() -> list[int]:
    return list(progression()["standard_array"])


def effective_caster_level(class_levels: list[tuple[str, int]]) -> int:
    """Effective caster level: full=+level, half=+ceil(level/2), third=+ceil(level/3)
    (per-class, summed). Pact/none contribute 0.

    SRD 5.2 (2024) rounds half- and third-casters UP — and with ceil the multiclass
    slot table reproduces the PHB half-caster CLASS column exactly at every
    single-class paladin/ranger level (L1->CL1->[2], L3->CL2->[3], L5->CL3->[4,2]),
    so half-casters effectively get their own progression column. The old 2014
    round-DOWN seated a L1 paladin/ranger with ZERO slots and under-slotted every
    odd level (audit F02-2). Third-casters are unused today — future-proofing."""
    total = 0
    for name, level in class_levels:
        try:
            ct = caster_type(name)
        except ValueError:
            continue
        if ct == "full":
            total += level
        elif ct == "half":
            total += (level + 1) // 2
        elif ct == "third":
            total += (level + 2) // 3
    return total


def multiclass_slots(class_levels: list[tuple[str, int]]) -> dict[int, int]:
    cl = effective_caster_level(class_levels)
    if cl < 1:
        return {}
    arr = _slots()["multiclass"].get(str(min(20, cl)), [])
    return {i + 1: n for i, n in enumerate(arr)}


def warlock_pact_slots(warlock_level: int) -> dict | None:
    if warlock_level < 1:
        return None
    return _slots()["warlock_pact"].get(str(min(20, warlock_level)))


# ---------------------------------------------------------------------------
# Class resource pools (Rage, Ki, Lay on Hands, Channel Divinity, …)
#
# Depletable per-rest pools, derived from class + level. Each spec is a callable
# (level, cha_mod) -> (max, recharge) or None when the class has no pool yet at
# that level. Values are sensible SRD 5e baselines (the engine is the authority;
# the DM can always restore manually for edge cases / homebrew). recharge is one
# of "short" (refresh on short OR long rest), "long" (long rest only), "none".
# ---------------------------------------------------------------------------

# Barbarian Rage uses by level (SRD: 2/3/3/3/4/4/4/4/4/4/4/5/5/5/5/5/6 ...).
_RAGE_USES = {1: 2, 3: 3, 6: 4, 12: 5, 17: 6}


def _rage_uses(level: int) -> int:
    n = 2
    for threshold, uses in sorted(_RAGE_USES.items()):
        if level >= threshold:
            n = uses
    return n


def _second_wind_uses(level: int) -> int:
    # SRD 5.2 Fighter: Second Wind has 2 uses at L1, 3 at L4, 4 at L10.
    if level >= 10:
        return 4
    if level >= 4:
        return 3
    return 2 if level >= 1 else 0


def _wild_shape_uses(level: int) -> int:
    # SRD 5.2 Druid: 2 uses at L2, 3 at L6, 4 at L17 — kept (does NOT drop) at L20.
    if level >= 17:
        return 4
    if level >= 6:
        return 3
    return 2 if level >= 2 else 0


def _channel_divinity_cleric(level: int) -> int:
    # Cleric: 2 uses at L2, 3 at L6, 4 at L18 (SRD 5.2).
    if level < 2:
        return 0
    if level >= 18:
        return 4
    if level >= 6:
        return 3
    return 2


def _channel_divinity_paladin(level: int) -> int:
    # Paladin: gains Channel Divinity at L3 (2 uses), 3 uses at L11 (SRD 5.2).
    if level < 3:
        return 0
    return 3 if level >= 11 else 2


# class name -> {resource_id: (label, recharge, fn(level, cha_mod) -> max)}
_CLASS_RESOURCES: dict[str, dict] = {
    "barbarian": {
        "rage": ("Rage", "long", lambda lvl, cha: _rage_uses(lvl)),
    },
    "monk": {
        # Monk's Focus / Ki points = monk level, from L2; short-rest recharge.
        "ki": ("Ki / Focus Points", "short", lambda lvl, cha: lvl if lvl >= 2 else 0),
    },
    "paladin": {
        # Lay on Hands pool = 5 x paladin level (hit points), long-rest recharge.
        "lay_on_hands": ("Lay on Hands", "long", lambda lvl, cha: 5 * lvl),
        "channel_divinity": ("Channel Divinity", "long", lambda lvl, cha: _channel_divinity_paladin(lvl)),
    },
    "cleric": {
        # Cleric Channel Divinity from L2; short-rest recharge.
        "channel_divinity": ("Channel Divinity", "short", lambda lvl, cha: _channel_divinity_cleric(lvl)),
    },
    "bard": {
        # Bardic Inspiration uses = CHA mod (min 1 once you have the feature).
        # Short-rest recharge from L5 (Font of Inspiration); long-rest before that.
        "bardic_inspiration": ("Bardic Inspiration", "long", lambda lvl, cha: max(1, cha)),
    },
    "sorcerer": {
        # Sorcery Points = sorcerer level, from L2; long-rest recharge.
        "sorcery_points": ("Sorcery Points", "long", lambda lvl, cha: lvl if lvl >= 2 else 0),
    },
    "fighter": {
        # Second Wind from L1; SRD 5.2 scales the pool 2/3/4 at L1/L4/L10 (the old flat
        # "1" undercounted every Fighter — audit F02-11). Action Surge from L2 (2 at L17).
        "second_wind": ("Second Wind", "short", lambda lvl, cha: _second_wind_uses(lvl)),
        "action_surge": ("Action Surge", "short", lambda lvl, cha: (2 if lvl >= 17 else 1) if lvl >= 2 else 0),
    },
    "druid": {
        # Wild Shape (SRD 5.2): 2 uses at L2, 3 at L6, 4 at L17 — and the pool PERSISTS at
        # L20 (the 2014 "Archdruid unlimited -> not pooled" zero was mixed-edition drift
        # against the engine's 2024 feature tables; under 5.2 the pool stays at 4 — F02-11).
        "wild_shape": ("Wild Shape", "short", lambda lvl, cha: _wild_shape_uses(lvl)),
    },
}

# Bard switches Bardic Inspiration to short-rest recharge at L5 (Font of Inspiration).
_BARD_FONT_OF_INSPIRATION_LEVEL = 5


def class_resources_through(class_name: str, level: int, cha_mod: int = 0) -> dict[str, dict]:
    """The depletable resource pools a single class grants by `level`, as
    ``{resource_id: {"max": int, "recharge": str}}``. ``cha_mod`` feeds pools sized
    by Charisma (Bardic Inspiration). Empty for an unknown class or a class with no
    pool yet at that level (e.g. a level-1 wizard). Pure — no campaign state."""
    spec = _CLASS_RESOURCES.get(class_name.lower())
    if not spec:
        return {}
    lvl = int(level)
    out: dict[str, dict] = {}
    for res_id, (_label, recharge, fn) in spec.items():
        mx = int(fn(lvl, cha_mod))
        if mx <= 0:
            continue
        # Bard's Bardic Inspiration becomes short-rest at L5 (Font of Inspiration).
        if res_id == "bardic_inspiration" and lvl >= _BARD_FONT_OF_INSPIRATION_LEVEL:
            recharge = "short"
        out[res_id] = {"max": mx, "recharge": recharge}
    return out
