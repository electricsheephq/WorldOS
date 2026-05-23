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
    """Multiclass effective caster level: full=+level, half=+level//2,
    third=+level//3 (per-class floor, summed). Pact/none contribute 0."""
    total = 0
    for name, level in class_levels:
        try:
            ct = caster_type(name)
        except ValueError:
            continue
        if ct == "full":
            total += level
        elif ct == "half":
            total += level // 2
        elif ct == "third":
            total += level // 3
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
